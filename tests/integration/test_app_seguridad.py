"""Solo la ventana puede usar la interfaz (specs/002-desktop-app, FR-013, SC-007, research R4).

Cada intento que no viene de la ventana recibe 403, sin cuerpo y sin cabeceras CORS, no cambia
la cola y no revela nada de ella. Corre en Linux y en Windows.
"""

import socket

import pytest

from downloader.models import Calidad
from downloader.store import Store
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.cliente_app import QUITAR as _SIN

MARCA = "marca-de-la-cola-que-no-debe-salir"


@pytest.fixture
def cola_con_marca(app_local):
    store = Store.abrir(app_local.home)
    try:
        store.agregar(f"https://v.ejemplo/{MARCA}", Calidad.MEJOR, str(app_local.home))
    finally:
        store.cerrar()
    return (app_local.home / "cola.db").read_bytes()


def _rechazada(respuesta):
    assert respuesta.estado == 403
    assert respuesta.cuerpo == b""
    assert not any(c.lower().startswith("access-control-") for c in respuesta.cabeceras)


@pytest.mark.parametrize(
    "caso",
    [
        "sin_secreto", "secreto_incorrecto", "host_localhost", "host_ajeno", "sin_host",
        "origin_ajeno", "post_sin_origin", "api_sin_x_gestor", "options", "put",
        "recorrido_puntos", "recorrido_codificado", "recorrido_barra_invertida",
    ],
)
def test_rechazos(app_local, cola_con_marca, caso):
    cliente, puerto = app_local.cliente, app_local.cliente.puerto
    otro = "x" * len(cliente.secreto)
    peticiones = {
        "sin_secreto": lambda: cliente.get(ruta_cruda="/"),
        "secreto_incorrecto": lambda: cliente.get(secreto=otro),
        "host_localhost": lambda: cliente.get(host=f"localhost:{puerto}"),
        "host_ajeno": lambda: cliente.get(host=f"evil.example:{puerto}"),
        "sin_host": lambda: cliente.get(host=_SIN),
        "origin_ajeno": lambda: cliente.get("api/estado", origin="http://evil.example"),
        "post_sin_origin": lambda: cliente.post("api/agregar", {"texto": "x"}, origin=_SIN),
        "api_sin_x_gestor": lambda: cliente.get("api/estado", x_gestor=_SIN),
        "options": lambda: cliente.peticion("OPTIONS", "api/estado", cabeceras={
            "Access-Control-Request-Method": "POST", "Origin": "http://evil.example"}),
        "put": lambda: cliente.peticion("PUT", "api/estado"),
        "recorrido_puntos": lambda: cliente.get(ruta_cruda=f"/{cliente.secreto}/../pyproject.toml"),
        "recorrido_codificado": lambda: cliente.get(
            ruta_cruda=f"/{cliente.secreto}/%2e%2e/pyproject.toml"),
        "recorrido_barra_invertida": lambda: cliente.get(
            ruta_cruda=f"/{cliente.secreto}/..%5cpyproject.toml"),
    }
    respuesta = peticiones[caso]()
    _rechazada(respuesta)
    assert MARCA.encode() not in respuesta.cuerpo
    assert (app_local.home / "cola.db").read_bytes() == cola_con_marca  # la cola no cambió


def test_con_todo_correcto_la_pagina_se_sirve_con_csp(app_local):
    respuesta = app_local.cliente.get()
    assert respuesta.estado == 200
    assert "default-src 'self'" in respuesta.cabeceras["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in respuesta.cabeceras["Content-Security-Policy"]
    assert respuesta.cabeceras["Cache-Control"] == "no-store"
    assert respuesta.cabeceras["X-Content-Type-Options"] == "nosniff"
    assert not any(c.lower().startswith("access-control-") for c in respuesta.cabeceras)


def test_un_archivo_que_no_esta_en_la_lista_da_404(app_local):
    respuesta = app_local.cliente.get("pyproject.toml")
    assert respuesta.estado == 404
    assert respuesta.cabeceras["Cache-Control"] == "no-store"


def test_el_secreto_es_largo_y_cambia_en_cada_arranque(app_local, app_home):
    from downloader.app import principal
    from tests.support.ventana_falsa import VentanaFalsa

    assert len(app_local.cliente.secreto) >= 43  # 256 bits en base64 urlsafe
    otro, url = principal.arrancar_servidor(VentanaFalsa(), app_home)
    try:
        assert url.rstrip("/").rsplit("/", 1)[1] != app_local.cliente.secreto
    finally:
        otro.cerrar()


def test_escucha_solo_en_127_0_0_1(app_local):
    assert app_local.servidor.server_address[0] == "127.0.0.1"


def _ip_no_local():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonda:
            sonda.connect(("192.0.2.1", 80))  # no envía nada: solo elige la interfaz de salida
            ip = sonda.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def test_no_acepta_conexiones_desde_la_ip_de_red_del_equipo(app_local):
    ip = _ip_no_local()
    if ip is None:
        pytest.skip("el equipo no tiene una dirección IP de red distinta de 127.0.0.1")
    with pytest.raises(OSError):  # rechazada, o descartada por el cortafuegos (timeout)
        socket.create_connection((ip, app_local.cliente.puerto), timeout=3).close()
