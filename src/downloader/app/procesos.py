"""El procesador de la cola visto desde la aplicación (research R5, R6, R8).

Quién procesa lo dice `procesador.json`, que escribe `ejecutar_cola` tras tomar
`ejecutar.lock`. La aplicación nunca toma ese bloqueo: solo lee el archivo y comprueba con
`psutil` que el proceso existe y es el mismo (`pid` e instante de creación).
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

from downloader import archivos_json
from downloader.operaciones import ARCHIVO_PROCESADOR


@dataclass(frozen=True)
class EstadoProcesador:
    estado: str  # "ninguno" | "en_marcha"
    origen: str | None = None  # "cli" | "app"
    ejecucion: str | None = None
    pid: int | None = None
    abrupto: bool = False  # el archivo es de un procesador que ya no existe
    creado: float | None = None  # instante de creación del proceso (lo usa la vista, R9)


NINGUNO = EstadoProcesador("ninguno")


@dataclass
class Lanzamiento:
    """El trabajador que lanzó esta ventana (research R5, R6).

    Solo quien lo lanza tiene el proceso: una ventana abierta después lo conoce únicamente por
    `procesador.json` (FR-011). `anunciado` pasa a ser cierto cuando se ha visto ese archivo
    con su ejecución; desde entonces ya no está arrancando, aunque lo retire antes de terminar.
    """

    proceso: subprocess.Popen
    ejecucion: str
    anunciado: bool = False

    def vivo(self) -> bool:
        return self.proceso.poll() is None

    def observar(self, estado: EstadoProcesador) -> None:
        if estado.estado == "en_marcha" and estado.origen == "app" and (
                estado.ejecucion == self.ejecucion):
            self.anunciado = True


def _vive(pid: int, creado) -> bool:
    try:
        proceso = psutil.Process(pid)
        if not proceso.is_running() or proceso.status() == psutil.STATUS_ZOMBIE:
            return False
        return creado is None or proceso.create_time() == creado
    except psutil.Error:
        return False


def estado_procesador(directorio) -> EstadoProcesador:
    datos = archivos_json.leer(Path(directorio) / ARCHIVO_PROCESADOR)
    if datos is None or not isinstance(datos.get("pid"), int) or datos.get("origen") not in (
        "cli", "app"
    ):
        return NINGUNO
    if not _vive(datos["pid"], datos.get("creado")):
        return EstadoProcesador("ninguno", datos["origen"], datos.get("ejecucion"),
                                datos["pid"], abrupto=True)
    return EstadoProcesador("en_marcha", datos["origen"], datos.get("ejecucion"), datos["pid"],
                            creado=datos.get("creado"))


# --- lanzar el trabajador (research R5, R8) ------------------------------------------------

# Indicadores de `CreateProcess` (API de Windows), con su valor para poder simularlos en Linux.
CREATE_NO_WINDOW = 0x08000000  # consola sin ventana
CREATE_NEW_PROCESS_GROUP = 0x00000200  # fuera de los eventos de consola de quien lo lanza
CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # fuera del objeto de trabajo: sobrevive a la ventana
ERROR_ACCESS_DENIED = 5
SIN_SEGUNDO_PLANO = (
    "No se puede dejar el procesamiento en segundo plano: Windows no permite separarlo de la "
    "ventana en este entorno (objeto de trabajo sin permiso para salir). No se ha iniciado nada."
)
_registro = logging.getLogger("downloader.app")


class SinSegundoPlano(Exception):
    pass


def _interprete() -> str:
    """El `python.exe` del entorno: con `pythonw.exe` (la ventana) no habría salida estándar."""
    ejecutable = Path(sys.executable)
    if ejecutable.name.lower() == "pythonw.exe":
        ejecutable = ejecutable.with_name("python.exe")
    return str(ejecutable)


def lanzar_trabajador(directorio, ejecucion: str):
    """Lanza `python -m downloader.app.procesador --ejecucion ID` sin consola visible y de modo
    que sobreviva al cierre de la ventana (R8). Sus salidas no se leen: el estado llega por la
    cola y los archivos informativos, y los errores, por el registro."""
    orden = [_interprete(), "-m", "downloader.app.procesador", "--ejecucion", ejecucion]
    opciones = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={**os.environ, "DOWNLOADER_HOME": str(directorio)})
    if sys.platform == "win32":
        opciones["creationflags"] = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
                                     | CREATE_BREAKAWAY_FROM_JOB)
    else:
        opciones["start_new_session"] = True
    try:
        return subprocess.Popen(orden, **opciones)
    except OSError as error:
        if getattr(error, "winerror", None) == ERROR_ACCESS_DENIED:
            # Sin reintentar sin el indicador: moriría al cerrar la ventana (R8).
            _registro.error("%s (%s)", SIN_SEGUNDO_PLANO, error)
            raise SinSegundoPlano(SIN_SEGUNDO_PLANO) from error
        raise
