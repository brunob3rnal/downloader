"""Fixture `app_local`: el servidor de la aplicación con una ventana falsa (specs/002-desktop-app).

Va en un módulo de soporte y no en `tests/integration/conftest.py` (de la 001), que no se
modifica. Se usa importándolo en el test: `from tests.support.app_local import app_local`.
"""

from types import SimpleNamespace

import pytest

from tests.support.cliente_app import ClienteApp
from tests.support.ventana_falsa import VentanaFalsa


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
