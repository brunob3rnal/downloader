"""Cookies del navegador desde la ventana (specs/002-desktop-app, US5, FR-014, FR-015, FR-016,
Principio VI; research R17; contracts/api-local.md; tasks T101).

La lectura de cookies se sustituye por `NavegadorFalso` dentro del trabajador (un subproceso):
`procesos.MODULO` apunta al envoltorio `tests.support.trabajador_con_navegador_falso`. El
servidor de prueba exige la cookie ficticia con `exigir_sesion`, así que el video solo se
descarga si yt-dlp la envía.
"""

import json

import pytest

from downloader.app import api as api_app
from downloader.app import procesos
from downloader.models import NAVEGADORES
from downloader.store import Store
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta
from tests.support.navegador_falso import NOMBRE, VALOR

AVISOS = {
    "firefox": "Fiable, abierto o cerrado.",
    "edge": "Solo con Edge cerrado del todo, también en segundo plano.",
    "chrome": "Chrome 127 o posterior no funciona, ni cerrado.",
}
COMUN = "Usa el mismo código que Chrome y Edge."


@pytest.fixture
def app(app_local, servidor, medios, monkeypatch):
    monkeypatch.setattr(procesos, "MODULO", "tests.support.trabajador_con_navegador_falso")
    (app_local.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    servidor.registrar("privado.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.exigir_sesion(NOMBRE, VALOR)
    respuestas = []
    peticion = app_local.cliente.peticion

    def guardando(*a, **o):
        respuesta = peticion(*a, **o)
        respuestas.append(respuesta.cuerpo)
        return respuesta

    app_local.cliente.peticion = guardando
    app_local.respuestas = respuestas
    return app_local


def _agregar(app, url, destino, navegador=None):
    respuesta = app.cliente.post("api/agregar", {"texto": url, "calidad": "mejor",
                                                 "carpeta": str(destino), "navegador": navegador})
    assert respuesta.estado == 200, respuesta.cuerpo
    return respuesta.json()


def _procesar(app):
    """Inicia y espera a que termine la ejecución; devuelve su resultado."""
    respuesta = app.cliente.post("api/iniciar")
    assert respuesta.estado == 202, respuesta.cuerpo
    ejecucion = respuesta.json()["ejecucion"]

    def terminada():
        datos = app.cliente.get("api/estado").json()
        ultima = datos["ultima_ejecucion"]
        listo = datos["procesador"]["estado"] == "ninguno" and ultima and (
            ultima["ejecucion"] == ejecucion)
        return datos if listo else None

    return hasta(terminada, 120, "el final de la ejecución")


def _fila(datos, id_):
    return next(f for f in datos["cola"]["filas"] if f["id"] == id_)


def test_opciones_lista_los_navegadores_con_su_aviso(app):
    navegadores = app.cliente.get("api/opciones").json()["navegadores"]
    assert {n["id"] for n in navegadores} == set(NAVEGADORES)
    for navegador in navegadores:
        assert navegador["aviso"] == AVISOS.get(navegador["id"], COMUN), navegador


def test_agregar_con_navegador_lo_guarda_en_la_fila(app, servidor, destino):
    hecho = _agregar(app, servidor.url("privado.mp4"), destino, navegador="firefox")
    (agregada,) = hecho["agregadas"]
    descarga = Store.leer(app.home)[0]
    assert (descarga.id, descarga.navegador_cookies) == (agregada["id"], "firefox")


def test_agregar_con_un_navegador_desconocido_400(app, servidor, destino):
    respuesta = app.cliente.post("api/agregar", {"texto": servidor.url("privado.mp4"),
                                                 "calidad": "mejor", "carpeta": str(destino),
                                                 "navegador": "netscape"})
    assert respuesta.estado == 400 and "navegador no admitido" in respuesta.json()["error"]
    assert Store.leer(app.home) == []


def test_reintentar_con_cookies_tras_un_403_por_sesion(app, servidor, destino):
    privado = _agregar(app, servidor.url("privado.mp4"), destino)["agregadas"][0]["id"]
    # Con cookies desde el principio: el servidor responde 404 y no 403 por falta de sesión.
    ausente = _agregar(app, servidor.url("no-existe.mp4"), destino,
                       navegador="firefox")["agregadas"][0]["id"]
    datos = _procesar(app)
    assert _fila(datos, privado)["estado"] == "failed"
    assert _fila(datos, privado)["reintentar_con_cookies"] is True  # 403: pide sesión (R17)
    assert _fila(datos, ausente)["estado"] == "failed"
    assert _fila(datos, ausente)["reintentar_con_cookies"] is False  # 404: las cookies no ayudan

    # Lo que no se puede reintentar.
    assert app.cliente.post("api/reintentar", {"id": privado, "navegador": "netscape"}).estado == 400
    assert app.cliente.post("api/reintentar", {"id": ausente, "navegador": "firefox"}).estado == 409
    assert app.cliente.post("api/reintentar", {"id": 999, "navegador": "firefox"}).estado == 404
    assert app.cliente.post("api/reintentar", {"id": privado}).estado == 400

    respuesta = app.cliente.post("api/reintentar", {"id": privado, "navegador": "firefox"})
    assert (respuesta.estado, respuesta.json()) == (200, {"accion": "reencolada", "id": privado})
    fila = Store.leer(app.home)[privado - 1]
    assert (fila.estado.value, fila.navegador_cookies) == ("queued", "firefox")
    # Una descarga en cola ya no se puede reintentar.
    assert app.cliente.post("api/reintentar", {"id": privado, "navegador": "firefox"}).estado == 409

    datos = _procesar(app)  # ahora con la cookie ficticia del navegador falso
    assert _fila(datos, privado)["estado"] == "completed"
    assert len([p for p in destino.iterdir() if p.is_file()]) == 1

    # Principio VI: la cookie no queda en ningún archivo ni en ninguna respuesta de la API.
    for archivo in app.home.rglob("*"):
        if archivo.is_file():
            assert VALOR.encode() not in archivo.read_bytes(), archivo
    for cuerpo in app.respuestas:
        assert VALOR.encode() not in cuerpo


def test_ningun_archivo_guarda_si_la_opcion_estaba_activada(app, servidor, destino):
    """La casilla empieza desmarcada en cada apertura: no hay preferencia guardada (FR-014)."""
    config_antes = (app.home / "config.toml").read_text(encoding="utf-8")
    _agregar(app, servidor.url("privado.mp4"), destino, navegador="edge")
    assert (app.home / "config.toml").read_text(encoding="utf-8") == config_antes
    for archivo in app.home.glob("*.json"):
        texto = archivo.read_text(encoding="utf-8")
        assert "cookies" not in texto and "edge" not in texto, archivo
    assert set(app.cliente.get("api/opciones").json()) == {
        "calidades", "calidad_inicial", "carpeta_inicial", "navegadores", "examinar_disponible"}


def test_reintentar_pide_cuerpo_valido(app):
    for cuerpo in (None, {"navegador": "firefox"}, {"id": "uno", "navegador": "firefox"}):
        assert app.cliente.post("api/reintentar", cuerpo).estado == 400, cuerpo
    assert api_app.MOTIVO_NO_REINTENTABLE
