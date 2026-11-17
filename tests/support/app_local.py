"""Fixture `app_local`: el servidor de la aplicación con una ventana falsa (specs/002-desktop-app).

Va en un módulo de soporte y no en `tests/integration/conftest.py` (de la 001), que no se
modifica. Se usa importándolo en el test: `from tests.support.app_local import app_local`.
"""

from types import SimpleNamespace

import psutil
import pytest

from tests.support.cliente_app import ClienteApp
from tests.support.ventana_falsa import VentanaFalsa

MODULO_TRABAJADOR = "downloader.app.procesador"


def terminar_trabajadores() -> list[int]:
    """Mata los trabajadores de la aplicación que este proceso haya dejado vivos.

    Red de seguridad para cuando un test falla a medias: el trabajador se lanza desacoplado
    (research R8) y seguiría esperando a un servidor de prueba que ya no existe. Devuelve los
    pid que ha matado.
    """
    vivos = []
    for hijo in psutil.Process().children(recursive=True):
        try:
            if MODULO_TRABAJADOR in hijo.cmdline() and hijo.status() != psutil.STATUS_ZOMBIE:
                hijo.kill()
                vivos.append(hijo)
        except psutil.Error:
            continue
    psutil.wait_procs(vivos, timeout=10)
    return [hijo.pid for hijo in vivos]


@pytest.fixture
def app_local(app_home):
    """Arranca `principal.arrancar_servidor` en un hilo con el `app_home` del test."""
    from downloader.app import principal

    ventana = VentanaFalsa()
    servidor, url = principal.arrancar_servidor(ventana, app_home)
    try:
        yield SimpleNamespace(servidor=servidor, url=url, cliente=ClienteApp(url),
                              ventana=ventana, home=app_home)
    finally:
        servidor.cerrar()
        terminar_trabajadores()
