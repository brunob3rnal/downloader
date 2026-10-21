"""Presentación del progreso de `ejecutar` (contracts/cli.md, FR-005, SC-007)."""

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from downloader.errors import ocultar_secretos
from downloader.models import ETIQUETAS, Descarga, Estado
from downloader.ytdl import Progreso

INTERVALO = 1.0  # s entre líneas de una misma descarga cuando la salida no es una terminal
_PASO = 0.1


@dataclass
class _Activa:
    descarga: Descarga
    progreso: Progreso | None = None
    ultima_linea: float | None = None


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
    """`#12 descargando  43.1%  3.2 MiB/s  ETA 00:01:52  Título`."""
    descarga, progreso = activa.descarga, activa.progreso
    prefijo = f"#{descarga.id} {ETIQUETAS[Estado.DOWNLOADING]}"
    if progreso is None or progreso.bytes is None:
        return f"{prefijo}  {_nombre(descarga)}"
    if progreso.total:
        cantidad = f"{min(100.0, 100 * progreso.bytes / progreso.total):.1f}%"
    else:
        cantidad = f"{progreso.bytes / 2**20:.1f} MiB"
    velocidad = f"{progreso.velocidad / 2**20:.1f} MiB/s" if progreso.velocidad else "--- MiB/s"
    return f"{prefijo}  {cantidad}  {velocidad}  ETA {_tiempo(progreso.eta)}  {_nombre(descarga)}"


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
            activa.progreso = progreso

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
            return Group(*(Text(linea_activa(a)) for a in self._activas.values()))

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
