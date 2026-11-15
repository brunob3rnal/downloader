"""`POST api/agregar` y `GET api/opciones` (specs/002-desktop-app, FR-002, FR-003, FR-004;
contracts/api-local.md)."""

import os
import subprocess
import sys

import pytest

from downloader.models import Estado
from downloader.store import Store
from tests.support.app_local import app_local  # noqa: F401  (fixture)


def _cola(directorio):
    return [(d.url, d.calidad.value, d.carpeta_destino, d.estado.value, d.navegador_cookies)
            for d in Store.leer(directorio)]


def test_agrega_las_validas_y_devuelve_las_invalidas(app_local, destino):
    texto = "https://a.ejemplo/1\n\nhola\nhttps://a.ejemplo/2?token=secreto9\n"
    respuesta = app_local.cliente.post("api/agregar", {
        "texto": texto, "calidad": "720p", "carpeta": str(destino), "navegador": None})
    assert respuesta.estado == 200
    datos = respuesta.json()
    assert [a["url"] for a in datos["agregadas"]] == [
        "https://a.ejemplo/1", "https://a.ejemplo/2?token=***"]
    assert datos["lineas_invalidas"] == [{"linea": 3, "texto": "hola"}]
    assert datos["ya_en_cola"] == [] and datos["reencoladas"] == []


def test_mismo_resultado_que_downloader_agregar(app_local, destino, tmp_path):
    urls = ["https://a.ejemplo/1", "https://a.ejemplo/2"]
    app_local.cliente.post("api/agregar", {"texto": "\n".join(urls), "calidad": "audio",
                                           "carpeta": str(destino), "navegador": "firefox"})
    otro = tmp_path / "otro"
    otro.mkdir()
    subprocess.run([sys.executable, "-m", "downloader", "agregar", *urls, "--calidad", "audio",
                    "--carpeta", str(destino), "--cookies-de", "firefox"],
                   env={**os.environ, "DOWNLOADER_HOME": str(otro)}, check=True,
                   capture_output=True)
    assert _cola(app_local.home) == _cola(otro)


def test_duplicados_y_reencoladas(app_local, destino):
    cuerpo = {"texto": "https://a.ejemplo/1\nhttps://a.ejemplo/2", "calidad": "mejor",
              "carpeta": str(destino), "navegador": None}
    app_local.cliente.post("api/agregar", cuerpo)
    store = Store.abrir(app_local.home)
    try:
        store.transicion(2, Estado.DOWNLOADING)
        store.transicion(2, Estado.FAILED, ultimo_error="HTTP 403")
    finally:
        store.cerrar()
    datos = app_local.cliente.post("api/agregar", cuerpo).json()
    assert [d["id"] for d in datos["ya_en_cola"]] == [1]
    assert datos["ya_en_cola"][0]["estado"] == "queued"
    assert [d["id"] for d in datos["reencoladas"]] == [2]


@pytest.mark.parametrize("cambio, mensaje", [
    ({"calidad": "480p"}, "calidad desconocida «480p»"),
    ({"navegador": "netscape"}, "navegador no admitido en --cookies-de: «netscape»"),
    ({"carpeta": "NO-EXISTE"}, "no existe"),
])
def test_opciones_invalidas_no_agregan_nada(app_local, destino, cambio, mensaje):
    cuerpo = {"texto": "https://a.ejemplo/1", "calidad": "mejor", "carpeta": str(destino),
              "navegador": None}
    if cambio.get("carpeta") == "NO-EXISTE":
        cambio = {"carpeta": str(destino / "no-existe")}
    cuerpo.update(cambio)
    respuesta = app_local.cliente.post("api/agregar", cuerpo)
    assert respuesta.estado == 400
    assert mensaje in respuesta.json()["error"]
    assert Store.leer(app_local.home) == []


def test_carpeta_sin_permiso(app_local, destino, monkeypatch):
    from downloader import publish

    monkeypatch.setattr(publish, "escribible", lambda carpeta: False)
    respuesta = app_local.cliente.post("api/agregar", {
        "texto": "https://a.ejemplo/1", "calidad": "mejor", "carpeta": str(destino),
        "navegador": None})
    assert respuesta.estado == 400
    assert "sin permiso de escritura en la carpeta de destino" in respuesta.json()["error"]


@pytest.mark.parametrize("texto", ["", "  \n \n"])
def test_texto_vacio(app_local, destino, texto):
    respuesta = app_local.cliente.post("api/agregar", {
        "texto": texto, "calidad": "mejor", "carpeta": str(destino), "navegador": None})
    assert respuesta.estado == 400
    assert respuesta.json()["error"] == "Pegue al menos un enlace"


def test_cuerpo_que_no_es_json(app_local):
    import http.client

    conexion = http.client.HTTPConnection("127.0.0.1", app_local.cliente.puerto, timeout=10)
    try:
        conexion.putrequest("POST", f"/{app_local.cliente.secreto}/api/agregar", skip_host=True)
        conexion.putheader("Host", f"127.0.0.1:{app_local.cliente.puerto}")
        conexion.putheader("Origin", app_local.cliente.origen)
        conexion.putheader("X-Gestor", "1")
        conexion.putheader("Content-Length", "3")
        conexion.endheaders(b"{x}")
        respuesta = conexion.getresponse()
        assert respuesta.status == 400
    finally:
        conexion.close()


def test_opciones_con_la_configuracion(app_local, destino):
    (app_local.home / "config.toml").write_text(
        f"calidad = '720p'\ncarpeta_destino = '{destino.as_posix()}'\n", encoding="utf-8")
    datos = app_local.cliente.get("api/opciones").json()
    assert datos["calidad_inicial"] == "720p"
    assert datos["carpeta_inicial"] == str(destino)
    assert [c["id"] for c in datos["calidades"]] == ["mejor", "1080p", "720p", "audio"]
    assert [c["nombre"] for c in datos["calidades"]] == [
        "Mejor calidad", "1080p", "720p", "Solo audio"]
    navegadores = {n["id"]: n for n in datos["navegadores"]}
    assert navegadores["firefox"]["aviso"] == "Fiable, abierto o cerrado."
    assert navegadores["edge"]["aviso"] == (
        "Solo con Edge cerrado del todo, también en segundo plano.")
    assert navegadores["chrome"]["aviso"] == "Chrome 127 o posterior no funciona, ni cerrado."
    assert navegadores["brave"]["aviso"] == "Usa el mismo código que Chrome y Edge."
    assert datos["examinar_disponible"] is True
    assert "aviso_config" not in datos


def test_opciones_con_configuracion_invalida(app_local):
    (app_local.home / "config.toml").write_text("calidad = '480p'\n", encoding="utf-8")
    datos = app_local.cliente.get("api/opciones").json()
    assert datos["calidad_inicial"] == "mejor"
    assert "calidad desconocida" in datos["aviso_config"]
