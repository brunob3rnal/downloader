"""Bucle de `ejecutar`: tomar de la cola, extraer, descargar, publicar (FR-021)."""

import os
import shutil
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from downloader import errors, publish, retry, ytdl
from downloader.errors import Clasificacion, ErrorFatal, ErrorPermanente, Tipo
from downloader.models import Descarga, Estado, TipoDescarga
from downloader.network import Monitor
from downloader.progress import Presentador
from downloader.store import Store
from downloader.ytdl import Progreso

PASO_ESPERA = 0.25  # s entre comprobaciones del monitor durante la espera de un reintento
AVISO_REINICIO = "reiniciada desde cero: el servidor no permite reanudar"


@dataclass
class Tiempos:
    backoff_base: float = 2
    backoff_tope: float = 300
    socket_timeout: float = 20
    espera_cola: float = 0.5  # s entre consultas cuando no hay nada en cola pero sí pendientes


@dataclass
class Resultado:
    completadas: list[int] = field(default_factory=list)
    fallidas: list[int] = field(default_factory=list)


class _Seguimiento:
    """Lo que el trabajador sabe de una descarga entre sus intentos (research R3, R4, R10).

    `sin_avance` cuenta los intentos fallidos seguidos sin bytes nuevos (vuelve a 0 con
    cualquier avance y no crece sin conexión). Por cada archivo se lleva el máximo de bytes
    conocido en el intento, que parte del tamaño de su `.part`: un evento por debajo de ese
    máximo indica que yt-dlp volvió a empezar desde cero.
    """

    def __init__(self, descarga: Descarga):
        self.descarga = descarga
        self.tope = retry.TopeSinAvance()
        self.sin_avance = descarga.intentos_sin_avance
        self.avisos: list[str] = []
        self.ultimo: Progreso | None = None  # último progreso con bytes, para Ctrl+C
        self._maximos: dict[str | None, int] = {}
        self._avance = False

    def empezar_intento(self, trabajo: Path) -> None:
        self._maximos = {
            p.name.removesuffix(".part"): p.stat().st_size for p in trabajo.glob("*.part")
        }

    def observar(self, progreso: Progreso) -> bool:
        """Anota el progreso; devuelve `True` si el archivo volvió a empezar desde cero."""
        if progreso.bytes is None:
            return False
        self.ultimo = progreso
        self.tope.observar(progreso.archivo, progreso.bytes)
        nombre = Path(progreso.archivo).name if progreso.archivo else None
        anterior = self._maximos.get(nombre, 0)
        self._maximos[nombre] = progreso.bytes
        self._avance |= progreso.bytes > anterior
        return progreso.bytes < anterior

    def cerrar_intento(self, clasificacion: Clasificacion, en_linea: bool) -> retry.Decision:
        avance, self._avance = self._avance, False
        if avance or en_linea:  # sin conexión, el intento no cuenta (FR-007)
            self.sin_avance = retry.siguiente_intento(self.sin_avance, avance)
        return self.tope.cerrar_intento(clasificacion)


def ejecutar(
    store: Store,
    monitor: Monitor,
    presentador: Presentador,
    tiempos: Tiempos | None = None,
    navegador: str | None = None,
) -> Resultado:
    """Procesa la cola hasta que no queden descargas pendientes.

    Recoge las URLs que se agreguen mientras corre. Un error FATAL devuelve la descarga a la
    cola y se lanza como `ErrorFatal`.
    """
    tiempos = tiempos or Tiempos()
    resultado = Resultado()
    presentador.iniciar()
    try:
        while store.hay_pendientes():
            descarga = store.siguiente()
            if descarga is None:
                time.sleep(tiempos.espera_cola)
                continue
            _procesar(store, monitor, presentador, tiempos, navegador, descarga, resultado)
    finally:
        presentador.detener()
    presentador.resumen()
    return resultado


def _procesar(store, monitor, presentador, tiempos, navegador, descarga, resultado):
    descarga = store.transicion(descarga.id, Estado.DOWNLOADING)
    presentador.inicio(descarga)
    navegador = navegador or descarga.navegador_cookies
    seguimiento = _Seguimiento(descarga)
    try:
        while True:
            try:
                ruta = _intentar(store, presentador, tiempos, navegador, seguimiento)
            except Exception as error:
                if _fallo(store, monitor, presentador, tiempos, seguimiento, error, resultado):
                    continue
                return
            if ruta is not None:
                resultado.completadas.append(descarga.id)
                presentador.completada(seguimiento.descarga, ruta)
            return
    except KeyboardInterrupt:
        _interrumpida(store, seguimiento)
        raise


def _interrumpida(store, seguimiento) -> None:
    """Ctrl+C: guarda el último progreso y devuelve la descarga a la cola (FR-020).

    Los bytes ya descargados siguen en el `.part`; `bytes_descargados` es informativo.
    """
    id_ = seguimiento.descarga.id
    if seguimiento.ultimo is not None:
        store.guardar_progreso(id_, seguimiento.ultimo.bytes, seguimiento.ultimo.total, forzar=True)
    if store.obtener(id_).estado in (Estado.DOWNLOADING, Estado.WAITING_NETWORK):
        store.transicion(id_, Estado.QUEUED)


def _intentar(store, presentador, tiempos, navegador, seguimiento):
    """Un intento: volver a extraer (R2) y descargar; `None` si la URL era una lista."""
    descarga = seguimiento.descarga
    info = ytdl.extraer_plano(descarga.url, navegador, tiempos.socket_timeout)
    descarga = seguimiento.descarga = replace(descarga, titulo=info.get("title") or descarga.titulo)
    presentador.titulo(descarga)
    if info.get("_type") == "playlist":
        _expandir_lista(store, presentador, descarga, info)
        return None
    return _descargar(store, presentador, tiempos, navegador, seguimiento)


def _expandir_lista(store, presentador, descarga, info):
    """Una descarga hija por entrada, con la misma calidad, carpeta y navegador (R13)."""
    entradas = [e for e in info.get("entries") or [] if e and e.get("url")]
    for entrada in entradas:
        store.agregar(entrada["url"], descarga.calidad, descarga.carpeta_destino,
                      descarga.navegador_cookies, padre_id=descarga.id)
    store.transicion(descarga.id, Estado.COMPLETED, tipo=TipoDescarga.PLAYLIST,
                     titulo=descarga.titulo)
    presentador.mensaje(
        f"#{descarga.id} lista «{descarga.titulo or descarga.url}»: "
        f"{len(entradas)} videos agregados a la cola"
    )


def _comprobar_carpeta(carpeta: str) -> None:
    """La carpeta pudo desaparecer o perder permisos desde `agregar` (FR-018)."""
    if not os.path.isdir(carpeta):
        raise ErrorPermanente(f"La carpeta de destino {carpeta} ya no existe")
    if not publish.escribible(carpeta):
        raise ErrorPermanente(f"Sin permiso de escritura en la carpeta de destino {carpeta}")


def _descargar(store, presentador, tiempos, navegador, seguimiento):
    descarga = seguimiento.descarga
    _comprobar_carpeta(descarga.carpeta_destino)
    trabajo = ytdl.directorio_trabajo(descarga)
    trabajo.mkdir(parents=True, exist_ok=True)
    seguimiento.empezar_intento(trabajo)

    def al_progresar(progreso):
        if seguimiento.observar(progreso):
            # El servidor ignoró `Range` y yt-dlp borró el `.part` (FR-024, R3).
            if AVISO_REINICIO not in seguimiento.avisos:
                seguimiento.avisos.append(AVISO_REINICIO)
                store.guardar_aviso(descarga.id, "; ".join(seguimiento.avisos))
            presentador.aviso(descarga, AVISO_REINICIO)
        presentador.progreso(descarga, progreso)
        if progreso.bytes is not None:
            store.guardar_progreso(descarga.id, progreso.bytes, progreso.total)

    info = ytdl.descargar(descarga, trabajo, navegador, al_progresar, tiempos.socket_timeout)
    archivo = ytdl.archivo_descargado(info)
    nombre = publish.nombre_publicado(
        info.get("title") or info["id"], info["id"], archivo.suffix.lstrip("."),
        descarga.carpeta_destino,
    )
    publicacion = publish.publicar(archivo, descarga.carpeta_destino, nombre)

    avisos = [a for a in (ytdl.aviso_calidad(descarga.calidad, info), publicacion.aviso) if a]
    for aviso in avisos:
        presentador.aviso(descarga, aviso)
    store.transicion(
        descarga.id, Estado.COMPLETED, archivo_final=str(publicacion.ruta),
        titulo=info.get("title"), ultimo_aviso="; ".join(seguimiento.avisos + avisos) or None,
        intentos_sin_avance=0,
    )
    # Solo tras publicar se borra el directorio de trabajo (data-model.md).
    shutil.rmtree(trabajo, ignore_errors=True)
    try:
        trabajo.parent.rmdir()  # .downloader-tmp, si ya no queda ninguna otra descarga
    except OSError:
        pass
    return publicacion.ruta


def _mensaje(error: BaseException) -> str:
    return errors.ocultar_secretos(str(error).removeprefix("ERROR: ").strip())


def _fallo(store, monitor, presentador, tiempos, seguimiento, error, resultado) -> bool:
    """Decide qué hacer tras un intento fallido; `True` si hay que volver a intentarlo.

    PERMANENTE (o el tope de errores ambiguos) deja la descarga fallida. TEMPORAL la pasa a
    `waiting_network` hasta el próximo intento (R10) y la devuelve a `downloading`.
    """
    descarga = seguimiento.descarga
    en_linea = monitor.en_linea.is_set()
    clasificacion = errors.clasificar_detalle(error, en_linea)
    mensaje = _mensaje(error)
    if clasificacion.tipo is Tipo.FATAL:
        store.transicion(descarga.id, Estado.QUEUED, ultimo_error=mensaje)
        raise ErrorFatal(mensaje) from error
    decision = seguimiento.cerrar_intento(clasificacion, en_linea)
    if decision.tipo is Tipo.PERMANENTE:
        mensaje = decision.mensaje or mensaje
        store.transicion(descarga.id, Estado.FAILED, ultimo_error=mensaje, titulo=descarga.titulo)
        resultado.fallidas.append(descarga.id)
        presentador.fallida(descarga, mensaje)
        return False

    segundos = retry.espera(seguimiento.sin_avance, tiempos.backoff_base, tiempos.backoff_tope)
    proximo = datetime.now(UTC) + timedelta(seconds=segundos)
    store.transicion(
        descarga.id, Estado.WAITING_NETWORK, ultimo_error=mensaje, titulo=descarga.titulo,
        intentos_sin_avance=seguimiento.sin_avance,
        proximo_intento=proximo.isoformat(timespec="milliseconds"),
    )
    _esperar(monitor, presentador, descarga, time.monotonic() + segundos, mensaje)
    seguimiento.descarga = store.transicion(descarga.id, Estado.DOWNLOADING)
    presentador.inicio(seguimiento.descarga)
    return True


def _esperar(monitor, presentador, descarga, hasta: float, causa: str) -> None:
    """Espera hasta `hasta` (reloj monotónico) para reintentar.

    Sin conexión no se reintenta: se espera a que vuelva la red y entonces se reintenta de
    inmediato, aunque no haya llegado `hasta` (R10). Se comprueba el monitor cada
    `PASO_ESPERA` s, así Ctrl+C también interrumpe la espera en Windows.
    """
    presentador.esperando(descarga, hasta, causa)
    sin_red = False
    while True:
        if not monitor.en_linea.is_set():
            if not sin_red:
                sin_red = True
                presentador.esperando(descarga, None, causa)
            monitor.en_linea.wait(PASO_ESPERA)
            continue
        resto = hasta - time.monotonic()
        if sin_red or resto <= 0:
            return
        time.sleep(min(resto, PASO_ESPERA))
