"""Bucle de `ejecutar`: tomar de la cola, extraer, descargar, publicar (FR-021)."""

import os
import shutil
import time
from dataclasses import dataclass, field, replace

from downloader import errors, publish, ytdl
from downloader.errors import ErrorFatal, ErrorPermanente, Tipo
from downloader.models import Descarga, Estado, TipoDescarga
from downloader.network import Monitor
from downloader.progress import Presentador
from downloader.store import Store


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
    try:
        info = ytdl.extraer_plano(descarga.url, navegador, tiempos.socket_timeout)
        descarga = replace(descarga, titulo=info.get("title") or descarga.titulo)
        presentador.titulo(descarga)
        if info.get("_type") == "playlist":
            _expandir_lista(store, presentador, descarga, info)
            return
        ruta = _descargar(store, presentador, tiempos, navegador, descarga)
    except Exception as error:
        _fallo(store, monitor, presentador, descarga, error, resultado)
        return
    resultado.completadas.append(descarga.id)
    presentador.completada(descarga, ruta)


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
    if not os.access(carpeta, os.W_OK | os.X_OK):
        raise ErrorPermanente(f"Sin permiso de escritura en la carpeta de destino {carpeta}")


def _descargar(store, presentador, tiempos, navegador, descarga):
    _comprobar_carpeta(descarga.carpeta_destino)
    trabajo = ytdl.directorio_trabajo(descarga)
    trabajo.mkdir(parents=True, exist_ok=True)

    def al_progresar(progreso):
        presentador.progreso(descarga, progreso)
        if progreso.bytes is not None:
            store.guardar_progreso(descarga.id, progreso.bytes, progreso.total)

    info = ytdl.descargar(descarga, trabajo, navegador, al_progresar, tiempos.socket_timeout)
    archivo = ytdl.archivo_descargado(info)
    nombre = publish.nombre_publicado(
        info.get("title") or info["id"], info["id"], archivo.suffix.lstrip(".")
    )
    publicacion = publish.publicar(archivo, descarga.carpeta_destino, nombre)

    avisos = [a for a in (ytdl.aviso_calidad(descarga.calidad, info), publicacion.aviso) if a]
    for aviso in avisos:
        presentador.aviso(descarga, aviso)
    store.transicion(
        descarga.id, Estado.COMPLETED, archivo_final=str(publicacion.ruta),
        titulo=info.get("title"), ultimo_aviso="; ".join(avisos) or None,
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


def _fallo(store, monitor, presentador, descarga: Descarga, error, resultado):
    tipo = errors.clasificar(error, monitor.en_linea.is_set())
    mensaje = _mensaje(error)
    if tipo is Tipo.FATAL:
        store.transicion(descarga.id, Estado.QUEUED, ultimo_error=mensaje)
        raise ErrorFatal(mensaje) from error
    # Hasta que existan los reintentos (US2), un error temporal también deja la descarga
    # fallida; el usuario puede volver a agregarla.
    store.transicion(descarga.id, Estado.FAILED, ultimo_error=mensaje, titulo=descarga.titulo)
    resultado.fallidas.append(descarga.id)
    presentador.fallida(descarga, mensaje)
