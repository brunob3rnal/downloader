"""Una sola ventana a la vez (FR-018; research R12; contracts/archivos.md).

La ventana abierta tiene un bloqueo exclusivo de `ventana.lock` (el mismo mecanismo que
`ejecutar.lock`, que el sistema libera si el proceso muere). Una segunda apertura que no lo
consigue deja `activar.solicitud` y termina; la ventana abierta lo ve en menos de un segundo,
lo borra y se pone al frente.
"""

import logging
import os
import threading
from pathlib import Path

from downloader import bloqueo

ARCHIVO = "ventana.lock"
SOLICITUD = "activar.solicitud"
INTERVALO = 1  # s entre consultas de la ventana abierta
_registro = logging.getLogger("downloader.app")


class Instancia:
    """El bloqueo de la ventana abierta."""

    def __init__(self, descriptor: int):
        self._descriptor = descriptor

    def liberar(self) -> None:
        if self._descriptor is not None:
            try:
                bloqueo.desbloquear(self._descriptor)
            finally:
                os.close(self._descriptor)
                self._descriptor = None


def tomar(directorio) -> Instancia | None:
    """El bloqueo, o `None` si ya hay una ventana abierta. Al tomarlo, descarta una petición
    de activación que quedara de antes (la ventana anterior murió sin atenderla)."""
    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directorio / ARCHIVO, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        tomado = bloqueo.bloquear(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if not tomado:
        os.close(descriptor)
        return None
    try:
        (directorio / SOLICITUD).unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        _registro.warning("no se puede borrar %s: %s", directorio / SOLICITUD, error)
    return Instancia(descriptor)


def libre(directorio) -> bool:
    """Si no hay ninguna ventana abierta (toma el bloqueo y lo suelta enseguida)."""
    unica = tomar(directorio)
    if unica is None:
        return False
    unica.liberar()
    return True


def pedir_activacion(directorio) -> None:
    """Pide a la ventana abierta que se ponga al frente."""
    Path(directorio, SOLICITUD).touch()


class Vigilante(threading.Thread):
    """En la ventana abierta: atiende las peticiones de activación con `enfocar`."""

    def __init__(self, directorio, enfocar, intervalo: float = INTERVALO):
        super().__init__(name="vigilante-de-activacion", daemon=True)
        self.ruta = Path(directorio) / SOLICITUD
        self.enfocar = enfocar
        self.intervalo = intervalo
        self._parar = threading.Event()

    def run(self) -> None:
        while not self._parar.wait(self.intervalo):
            try:
                self.ruta.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                _registro.warning("no se puede borrar %s: %s", self.ruta, error)
                continue
            try:
                self.enfocar()
            except Exception:  # noqa: BLE001 — una ventana que no se deja enfocar no para nada
                _registro.exception("no se pudo poner la ventana al frente")

    def detener(self) -> None:
        self._parar.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join()
