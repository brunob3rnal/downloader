"""El procesador de la cola visto desde la aplicación (research R5, R6, R8).

Quién procesa lo dice `procesador.json`, que escribe `ejecutar_cola` tras tomar
`ejecutar.lock`. La aplicación nunca toma ese bloqueo: solo lee el archivo y comprueba con
`psutil` que el proceso existe y es el mismo (`pid` e instante de creación).
"""

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


NINGUNO = EstadoProcesador("ninguno")


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
    return EstadoProcesador("en_marcha", datos["origen"], datos.get("ejecucion"), datos["pid"])
