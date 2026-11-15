"""Bloqueo exclusivo de un archivo, liberado por el sistema si el proceso muere (research R11).

Interfaz pública del bloqueo de `ejecutar.lock` para que la ventana de la aplicación tome el
suyo, `ventana.lock` (research R12 de la feature 002). La implementación (`msvcrt.locking` en
Windows, `fcntl.flock` en el resto) sigue en `store`, donde la prueba
`tests/unit/test_store.py` con un `msvcrt` simulado.
"""

from downloader.store import _bloquear as bloquear
from downloader.store import _desbloquear as desbloquear

__all__ = ["bloquear", "desbloquear"]
