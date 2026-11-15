"""`POST api/examinar` con la ventana falsa (specs/002-desktop-app, FR-003; hallazgo medio)."""

from tests.support.app_local import app_local  # noqa: F401  (fixture)


def test_devuelve_la_carpeta_elegida(app_local, destino):
    app_local.ventana.carpeta = str(destino)
    respuesta = app_local.cliente.post("api/examinar")
    assert respuesta.estado == 200
    assert respuesta.json() == {"carpeta": str(destino)}
    assert app_local.ventana.operaciones() == ["examinar_carpeta"]


def test_cancelado(app_local):
    app_local.ventana.carpeta = None
    respuesta = app_local.cliente.post("api/examinar")
    assert respuesta.estado == 200 and respuesta.json() == {"carpeta": None}


def test_sin_dialogo_de_carpeta_501(app_local):
    app_local.ventana.con_dialogo = False
    respuesta = app_local.cliente.post("api/examinar")
    assert respuesta.estado == 501
    assert app_local.ventana.operaciones() == []
    assert app_local.cliente.get("api/opciones").json()["examinar_disponible"] is False


def test_sin_ventana_501(app_local):
    app_local.servidor.api.ventana = None
    assert app_local.cliente.post("api/examinar").estado == 501
