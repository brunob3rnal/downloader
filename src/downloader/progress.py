"""Presentación del progreso de `ejecutar` y de la cola en `estado` (contracts/cli.md, FR-005,
SC-007, FR-032)."""

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from downloader import archivos_json
from downloader.errors import ocultar_secretos
from downloader.models import ETIQUETAS, Descarga, Estado, TipoDescarga
from downloader.ytdl import Progreso

INTERVALO = 1.0  # s entre líneas de una misma descarga cuando la salida no es una terminal
SIN_DATOS = 5  # s sin bytes nuevos a partir de los que la línea lo indica (R21)
_PASO = 0.1


@dataclass
class _Activa:
    descarga: Descarga
    progreso: Progreso | None = None
    ultima_linea: float | None = None
    ultimo_avance: float = field(default_factory=time.monotonic)  # último byte nuevo informado
    # En `waiting_network`: la causa y el instante del reintento (reloj monotónico), o `None`
    # en `reintento` si se espera a que vuelva la conexión.
    causa_espera: str | None = None
    reintento: float | None = None


def _tiempo(segundos) -> str:
    if segundos is None:
        return "--:--:--"
    segundos = int(segundos)
    return f"{segundos // 3600:02d}:{segundos // 60 % 60:02d}:{segundos % 60:02d}"


def _ruta_visible(ruta) -> str:
    ruta = Path(ruta)
    try:
        return str(Path("~") / ruta.relative_to(Path.home()))  # `~\Downloads\…` en Windows
    except ValueError:
        return str(ruta)


def _nombre(descarga: Descarga) -> str:
    return ocultar_secretos(descarga.titulo or descarga.url)


def linea_activa(activa: _Activa) -> str:
    """`#12 descargando  43.1%  3.2 MiB/s  ETA 00:01:52  Título`.

    En espera: `#13 esperando red  reintento en 40 s  (causa)`, con la cuenta atrás calculada
    al dibujar la línea.
    """
    descarga, progreso = activa.descarga, activa.progreso
    if activa.causa_espera is not None:
        if activa.reintento is None:
            cuando = "sin conexión, reintento al volver la red"
        else:
            # Al milisegundo: la resta de dos lecturas del reloj arrastra errores de coma
            # flotante (300.0000000000001) que `ceil` convertiría en un segundo de más.
            resto = round(activa.reintento - time.monotonic(), 3)
            cuando = f"reintento en {max(0, math.ceil(resto))} s"
        return f"#{descarga.id} {ETIQUETAS[Estado.WAITING_NETWORK]}  {cuando}  ({activa.causa_espera})"
    prefijo = f"#{descarga.id} {ETIQUETAS[Estado.DOWNLOADING]}"
    if progreso is None or progreso.bytes is None:
        return f"{prefijo}  {_nombre(descarga)}"
    silencio = time.monotonic() - activa.ultimo_avance
    if silencio >= SIN_DATOS:
        return f"{prefijo}  sin datos hace {silencio:.0f} s, reintentando  {_nombre(descarga)}"
    if progreso.total:
        cantidad = f"{min(100.0, 100 * progreso.bytes / progreso.total):.1f}%"
    else:
        cantidad = f"{progreso.bytes / 2**20:.1f} MiB"
    velocidad = f"{progreso.velocidad / 2**20:.1f} MiB/s" if progreso.velocidad else "--- MiB/s"
    return f"{prefijo}  {cantidad}  {velocidad}  ETA {_tiempo(progreso.eta)}  {_nombre(descarga)}"


def _progreso_guardado(descarga: Descarga, hijas: int) -> str:
    """El progreso que guardó `ejecutar` (como mucho cada 2 s), o lo que la descarga es."""
    if descarga.tipo is TipoDescarga.PLAYLIST:
        return f"lista ({hijas} video{'s' * (hijas != 1)})"
    if descarga.estado is Estado.COMPLETED:
        return "100%"
    if descarga.bytes_totales:
        return f"{min(100.0, 100 * descarga.bytes_descargados / descarga.bytes_totales):.1f}%"
    if descarga.bytes_descargados:
        return f"{descarga.bytes_descargados / 2**20:.1f} MiB"
    return "—"


def linea_estado(descarga: Descarga, hijas: int = 0) -> str:
    """`#13 esperando red  12.0%  Título  (causa)`: la línea de `estado` (contracts/cli.md).

    Tras el progreso y el título (o la URL), el error si está fallida o esperando la red; si
    no, el archivo publicado y el último aviso. `hijas` es el número de videos de una lista.
    """
    partes = [
        f"#{descarga.id} {ETIQUETAS[descarga.estado]}", _progreso_guardado(descarga, hijas),
        _nombre(descarga),
    ]
    error = ocultar_secretos(descarga.ultimo_error or "")
    if error and descarga.estado is Estado.FAILED:
        partes.append(error)
    elif error and descarga.estado is Estado.WAITING_NETWORK:
        partes.append(f"({error})")
    else:
        if descarga.archivo_final:
            partes.append(_ruta_visible(descarga.archivo_final))
        if descarga.ultimo_aviso:
            partes.append(f"aviso: {ocultar_secretos(descarga.ultimo_aviso)}")
    return "  ".join(partes)


class Presentador:
    """Muestra una línea por descarga activa y una por cada evento (completada, fallida, aviso).

    En una terminal, las líneas activas se redibujan con `rich.live`. Si la salida no es una
    terminal (un archivo, una tubería), `rich.live` solo mostraría el estado final, así que se
    escribe una línea nueva por descarga activa cada `INTERVALO` s.
    """

    def __init__(self, consola: Console | None = None):
        self.consola = consola or Console()
        self._activas: dict[int, _Activa] = {}
        self._completadas = 0
        self._fallidas: list[tuple[Descarga, str]] = []
        self._cerrojo = threading.Lock()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._live: Live | None = None

    # --- ciclo de vida -------------------------------------------------------------

    def iniciar(self) -> None:
        self._parar.clear()
        if self.consola.is_terminal:
            self._live = Live(
                get_renderable=self._activas_renderizables, console=self.consola,
                refresh_per_second=4, transient=True,
            )
            self._live.start()
        else:
            self._hilo = threading.Thread(target=self._latido, name="progreso", daemon=True)
            self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join()
            self._hilo = None
        if self._live is not None:
            self._live.stop()
            self._live = None

    # --- eventos -------------------------------------------------------------------

    def inicio(self, descarga: Descarga) -> None:
        with self._cerrojo:
            self._activas[descarga.id] = _Activa(descarga)

    def titulo(self, descarga: Descarga) -> None:
        with self._cerrojo:
            if descarga.id in self._activas:
                self._activas[descarga.id].descarga = descarga

    def progreso(self, descarga: Descarga, progreso: Progreso) -> None:
        with self._cerrojo:
            activa = self._activas.setdefault(descarga.id, _Activa(descarga))
            anterior = activa.progreso
            if (
                anterior is None or anterior.archivo != progreso.archivo
                or (progreso.bytes or 0) > (anterior.bytes or 0)
            ):
                activa.ultimo_avance = time.monotonic()
            activa.progreso = progreso

    def esperando(self, descarga: Descarga, reintento: float | None, causa: str) -> None:
        """La descarga espera para reintentar hasta `reintento` (`time.monotonic()`), o hasta
        que vuelva la conexión si es `None`."""
        with self._cerrojo:
            activa = self._activas.setdefault(descarga.id, _Activa(descarga))
            activa.causa_espera = ocultar_secretos(causa)
            activa.reintento = reintento

    def completada(self, descarga: Descarga, ruta) -> None:
        with self._cerrojo:
            self._activas.pop(descarga.id, None)
            self._completadas += 1
        self._linea(f"#{descarga.id} {ETIQUETAS[Estado.COMPLETED]}  {_ruta_visible(ruta)}")

    def fallida(self, descarga: Descarga, causa: str) -> None:
        causa = ocultar_secretos(causa)
        with self._cerrojo:
            self._activas.pop(descarga.id, None)
            self._fallidas.append((descarga, causa))
        self._linea(f"#{descarga.id} {ETIQUETAS[Estado.FAILED]}  {causa}")

    def aviso(self, descarga: Descarga, texto: str) -> None:
        self._linea(f"#{descarga.id} aviso  {ocultar_secretos(texto)}")

    def mensaje(self, texto: str) -> None:
        self._linea(ocultar_secretos(texto))

    def resumen(self) -> None:
        fallidas = len(self._fallidas)
        self._linea(
            f"Resumen: {self._completadas} completada{'s' * (self._completadas != 1)}, "
            f"{fallidas} fallida{'s' * (fallidas != 1)}"
        )
        for descarga, causa in self._fallidas:
            self._linea(f"  #{descarga.id} {ocultar_secretos(descarga.url)}: {causa}")

    # --- salida --------------------------------------------------------------------

    def _linea(self, texto: str) -> None:
        self.consola.print(Text(texto), soft_wrap=True, highlight=False)

    def _activas_renderizables(self):
        with self._cerrojo:
            # Una línea por descarga, recortada al ancho de la terminal: un título largo no
            # debe partirla en dos (rich.live redibujaría mal las líneas partidas).
            return Group(*(
                Text(linea_activa(a), no_wrap=True, overflow="ellipsis")
                for a in self._activas.values()
            ))

    def _latido(self) -> None:
        """Sin terminal: una línea por descarga activa como mucho cada `INTERVALO` s."""
        while not self._parar.wait(_PASO):
            ahora = time.monotonic()
            with self._cerrojo:
                pendientes = [
                    a for a in self._activas.values()
                    if a.ultima_linea is None or ahora - a.ultima_linea >= INTERVALO
                ]
                for activa in pendientes:
                    activa.ultima_linea = ahora
                lineas = [linea_activa(a) for a in pendientes]
            for linea in lineas:
                self._linea(linea)


# --- progreso en directo para la aplicación de escritorio (research R9 de la feature 002) ---

ARCHIVO_VIVO = archivos_json.ARCHIVO_VIVO
FALLOS_PARA_AVISAR = 3  # escrituras seguidas sin conseguirlo antes de anotarlo en el registro
_registro_vivo = logging.getLogger("downloader.vivo")


@dataclass
class _Viva:
    id: int
    ultimo_avance: float
    progreso: Progreso | None = None
    espera: tuple[str, float | None] | None = None  # (causa, instante del reintento o None)


class Publicador:
    """Escribe `progreso-vivo.json` como mucho una vez por `intervalo` s (contracts/archivos.md).

    Recibe los mismos eventos que `Presentador` y no escribe nada en la salida: la de la CLI no
    cambia. Si no puede escribir o borrar el archivo, no lanza; lo anota en el logger
    `downloader.vivo` al empezar a fallar (tras `FALLOS_PARA_AVISAR` intentos seguidos, para no
    contar un reemplazo negado ocasional en Windows) y al recuperarse. Sin manejadores (la CLI)
    eso no aparece en ninguna parte; el procesador de la aplicación lo lleva a su registro.
    Solo guarda ids y cifras: ninguna URL ni título.
    """

    def __init__(self, directorio, pid: int, creado: float, monitor=None,
                 reloj=time.monotonic, escribir=archivos_json.escribir_atomico,
                 intervalo: float = 1.0):
        self.ruta = Path(directorio) / ARCHIVO_VIVO
        self._pid, self._creado, self._monitor = pid, creado, monitor
        self._reloj, self._escribir, self._intervalo = reloj, escribir, intervalo
        self._cerrojo = threading.Lock()
        self._activa: _Viva | None = None
        self._ultima_escritura: float | None = None
        self._fallos = 0
        self._avisado = False
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None

    # --- ciclo de vida -------------------------------------------------------------

    def iniciar(self) -> None:
        self._parar.clear()
        self._hilo = threading.Thread(target=self._latido, name="progreso-vivo", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join()
            self._hilo = None
        try:
            archivos_json.borrar(self.ruta)
        except OSError as error:
            _registro_vivo.warning("no se puede borrar %s: %s", self.ruta, error)

    # --- eventos -------------------------------------------------------------------

    def inicio(self, descarga: Descarga) -> None:
        with self._cerrojo:
            self._activa = _Viva(descarga.id, self._reloj())
        self._publicar_si_toca()

    def titulo(self, descarga: Descarga) -> None:
        pass

    def progreso(self, descarga: Descarga, progreso: Progreso) -> None:
        with self._cerrojo:
            activa = self._activa
            if activa is None or activa.id != descarga.id:
                activa = self._activa = _Viva(descarga.id, self._reloj())
            anterior = activa.progreso
            if (anterior is None or anterior.archivo != progreso.archivo
                    or (progreso.bytes or 0) > (anterior.bytes or 0)):
                activa.ultimo_avance = self._reloj()
            activa.progreso = progreso
            activa.espera = None
        self._publicar_si_toca()

    def esperando(self, descarga: Descarga, reintento: float | None, causa: str) -> None:
        with self._cerrojo:
            if self._activa is None or self._activa.id != descarga.id:
                self._activa = _Viva(descarga.id, self._reloj())
            self._activa.espera = (ocultar_secretos(causa), reintento)
        self._publicar_si_toca()

    def completada(self, descarga: Descarga, ruta) -> None:
        self._terminada()

    def fallida(self, descarga: Descarga, causa: str) -> None:
        self._terminada()

    def aviso(self, descarga: Descarga, texto: str) -> None:
        pass

    def mensaje(self, texto: str) -> None:
        pass

    def resumen(self) -> None:
        pass

    # --- escritura -----------------------------------------------------------------

    def publicar(self) -> None:
        """Escribe ya el estado actual."""
        datos = self._datos()
        self._ultima_escritura = self._reloj()
        error = None
        try:
            escrito = self._escribir(self.ruta, datos, reintentar=False)
        except OSError as excepcion:
            escrito, error = False, excepcion
        self._anotar(escrito is not False, error)

    def _terminada(self) -> None:
        with self._cerrojo:
            self._activa = None
        self._publicar_si_toca()

    def _publicar_si_toca(self) -> None:
        ultima = self._ultima_escritura
        if ultima is None or self._reloj() - ultima >= self._intervalo:
            self.publicar()

    def _latido(self) -> None:
        while not self._parar.wait(_PASO):
            self._publicar_si_toca()

    def _datos(self) -> dict:
        ahora = self._reloj()
        with self._cerrojo:
            activa = self._activa
            datos_activa = None
            if activa is not None:
                progreso = activa.progreso
                silencio = ahora - activa.ultimo_avance
                espera = None
                if activa.espera is not None:
                    causa, reintento = activa.espera
                    reintento_en = None
                    if reintento is not None:
                        reintento_en = max(0, math.ceil(round(reintento - ahora, 3)))
                    espera = {"causa": causa, "reintento_en": reintento_en}
                datos_activa = {
                    "id": activa.id,
                    "bytes": progreso.bytes if progreso else None,
                    "total": progreso.total if progreso else None,
                    "velocidad": progreso.velocidad if progreso else None,
                    "eta": progreso.eta if progreso else None,
                    "sin_datos": int(silencio) if progreso and silencio >= SIN_DATOS else None,
                    "espera": espera,
                }
        en_linea = self._monitor.en_linea.is_set() if self._monitor is not None else None
        return {
            "pid": self._pid,
            "creado": self._creado,
            "actualizado": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "en_linea": en_linea,
            "activa": datos_activa,
        }

    def _anotar(self, escrito: bool, error: OSError | None) -> None:
        if escrito:
            if self._avisado:
                _registro_vivo.info("se vuelve a escribir %s", self.ruta)
            self._fallos, self._avisado = 0, False
            return
        self._fallos += 1
        if self._fallos >= FALLOS_PARA_AVISAR and not self._avisado:
            self._avisado = True
            _registro_vivo.warning("no se puede escribir %s: %s", self.ruta,
                                   error or "el archivo está en uso")


class Observadores:
    """Reparte cada evento del trabajador a varios observadores, en orden."""

    def __init__(self, observadores):
        self._observadores = list(observadores)

    def __getattr__(self, nombre):
        metodos = [getattr(observador, nombre) for observador in self._observadores]

        def repartir(*argumentos):
            for metodo in metodos:
                metodo(*argumentos)

        return repartir

