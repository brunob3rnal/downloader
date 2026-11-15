"""Registro de errores de la aplicación (FR-020, research R10, contracts/archivos.md).

Un par de archivos por proceso escritor, `registro/<origen>.log` y `.log.1`, de 500 000 bytes
como mucho cada uno: 2 MB en total con la ventana y el procesador. Recoge los loggers
`downloader.app` (la aplicación) y `downloader.vivo` (los archivos informativos de `ejecutar`)
y las excepciones no capturadas; no recoge los mensajes de yt-dlp, que no son fallos de la
aplicación. Todo pasa por `ocultar_secretos`. Si no se puede escribir, no lanza:
`disponible()` lo dice para que la ventana avise.
"""

import logging
import logging.handlers
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from downloader.secretos import ocultar_secretos

MAXIMO = 500_000  # bytes por archivo
LOGGERS = ("downloader.app", "downloader.vivo")
_disponible = True


def disponible() -> bool:
    return _disponible


class _Formato(logging.Formatter):
    def __init__(self, origen: str):
        super().__init__(f"%(asctime)s %(levelname)s {origen}: %(message)s")

    def formatTime(self, registro, formato=None):
        return datetime.fromtimestamp(registro.created, UTC).isoformat(timespec="milliseconds")

    def format(self, registro):
        return ocultar_secretos(super().format(registro))


class _Archivo(logging.handlers.RotatingFileHandler):
    def handleError(self, registro):
        global _disponible
        _disponible = False  # sin `sys.stderr` con pythonw: no se imprime nada


class Manejo:
    """Lo instalado por `configurar`; `cerrar` lo quita (para los tests y al salir)."""

    def __init__(self, manejador, ganchos):
        self._manejador = manejador
        self._ganchos = ganchos

    def cerrar(self) -> None:
        if self._manejador is not None:
            for nombre in LOGGERS:
                logging.getLogger(nombre).removeHandler(self._manejador)
            self._manejador.close()
        sys.excepthook, threading.excepthook = self._ganchos


def configurar(origen: str, directorio) -> Manejo:
    """Envía los errores de la aplicación a `registro/<origen>.log` (`ventana` o `procesador`)."""
    global _disponible
    _disponible = True
    ganchos = (sys.excepthook, threading.excepthook)
    manejador = None
    try:
        carpeta = Path(directorio) / "registro"
        carpeta.mkdir(parents=True, exist_ok=True)
        manejador = _Archivo(carpeta / f"{origen}.log", maxBytes=MAXIMO, backupCount=1,
                             encoding="utf-8", delay=True)
    except OSError:
        _disponible = False
    if manejador is not None:
        manejador.setLevel(logging.INFO)
        manejador.setFormatter(_Formato(origen))
        for nombre in LOGGERS:
            logger = logging.getLogger(nombre)
            logger.addHandler(manejador)
            logger.setLevel(logging.INFO)
    log = logging.getLogger("downloader.app")

    def excepthook(tipo, valor, traza):
        log.error("excepción no capturada", exc_info=(tipo, valor, traza))

    def threading_excepthook(argumentos):
        if argumentos.exc_type is not SystemExit:
            log.error("excepción no capturada en el hilo %s",
                      getattr(argumentos.thread, "name", "?"),
                      exc_info=(argumentos.exc_type, argumentos.exc_value,
                                argumentos.exc_traceback))

    sys.excepthook, threading.excepthook = excepthook, threading_excepthook
    return Manejo(manejador, ganchos)
