"""Terminación abrupta del procesador de la aplicación (specs/002-desktop-app, FR-012, FR-020;
research R10; tasks T088)."""

import json
import subprocess
import sys

import psutil
import pytest

from downloader import archivos_json
from downloader.app import registro, resultado
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta

NOMBRE = "lento.mp4"


@pytest.fixture
def registro_abierto(app_home):
    manejo = registro.configurar("ventana", app_home)
    yield app_home / "registro" / "ventana.log"
    manejo.cerrar()


def _matar_el_trabajador(app):
    """Mata el intérprete que procesa (en Windows, el del entorno virtual es un lanzador que
    lo arranca como hijo: matar solo el lanzador no lo pararía)."""
    anunciado = archivos_json.leer(app.home / "procesador.json")
    interprete = psutil.Process(anunciado["pid"])
    interprete.kill()
    psutil.wait_procs([interprete], timeout=30)
    app.servidor.api.lanzamiento.proceso.wait(timeout=30)
    return anunciado


def _iniciar_lenta(app, servidor, destino, datos=None):
    (app.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    datos = bytes(20_000_000) if datos is None else datos
    servidor.registrar(NOMBRE, datos, tipo_contenido="video/mp4", velocidad=200_000)
    respuesta = app.cliente.post("api/agregar", {
        "texto": servidor.url(NOMBRE), "calidad": "mejor", "carpeta": str(destino),
        "navegador": None})
    assert respuesta.estado == 200, respuesta.cuerpo
    ejecucion = app.cliente.post("api/iniciar").json()["ejecucion"]
    hasta(lambda: app.cliente.get("api/estado").json()["procesador"]["estado"] == "en_marcha",
          60, "que el trabajador arranque")
    return ejecucion


def test_tras_matar_el_trabajador_queda_un_resultado_abrupto_y_el_registro(
        app_local, servidor, destino, registro_abierto):
    app = app_local
    ejecucion = _iniciar_lenta(app, servidor, destino)
    _matar_el_trabajador(app)

    datos = app.cliente.get("api/estado").json()  # la siguiente consulta lo escribe
    ultima = resultado.leer(app.home)
    assert ultima["ejecucion"] == ejecucion
    assert (ultima["codigo"], ultima["error"]["tipo"]) == (None, "abrupto")
    assert ultima["error"]["mensaje"] == resultado.ABRUPTO
    assert datos["ultima_ejecucion"] == ultima
    assert datos["procesador"]["estado"] == "ninguno"
    texto = registro_abierto.read_text(encoding="utf-8")
    assert "ERROR" in texto and ejecucion in texto and "inesperada" in texto

    # Se escribe una vez: las consultas siguientes no lo repiten ni lo cambian.
    app.cliente.get("api/estado")
    assert resultado.leer(app.home) == ultima
    assert registro_abierto.read_text(encoding="utf-8") == texto


def test_un_resultado_ya_escrito_de_esa_ejecucion_no_se_sobrescribe(app_local):
    """El trabajador escribió su resultado y murió antes de retirar `procesador.json`."""
    app = app_local
    muerto = subprocess.Popen([sys.executable, "-c", "pass"])
    muerto.wait()
    (app.home / "procesador.json").write_text(json.dumps({
        "pid": muerto.pid, "creado": 1.0, "origen": "app", "ejecucion": "e1",
        "inicio": "2026-11-17T10:00:00.000+00:00"}), encoding="utf-8")
    resultado.escribir(app.home, "e1", None, 2, [], 0, None)
    escrito = resultado.leer(app.home)
    assert app.cliente.get("api/estado").json()["ultima_ejecucion"] == escrito
    assert resultado.leer(app.home) == escrito


def test_un_abrupto_de_la_cli_no_escribe_resultado(app_local):
    """El resultado es solo de la aplicación: un `ejecutar` de la CLI no lo tiene (FR-012)."""
    app = app_local
    muerto = subprocess.Popen([sys.executable, "-c", "pass"])
    muerto.wait()
    (app.home / "procesador.json").write_text(json.dumps({
        "pid": muerto.pid, "creado": 1.0, "origen": "cli", "ejecucion": None,
        "inicio": "2026-11-17T10:00:00.000+00:00"}), encoding="utf-8")
    assert app.cliente.get("api/estado").json()["ultima_ejecucion"] is None
    assert resultado.leer(app.home) is None


def test_el_resultado_se_ve_al_reabrir_hasta_la_siguiente_ejecucion(app_local, servidor, destino,
                                                                   medios, registro_abierto):
    from downloader.app import principal

    app = app_local
    _iniciar_lenta(app, servidor, destino, medios.datos_mp4)  # un MP4 válido: se completará
    _matar_el_trabajador(app)
    app.cliente.get("api/estado")
    abrupto = resultado.leer(app.home)

    # Otra sesión de la ventana (la primera se cerró): lo sigue mostrando.
    app.servidor.cerrar()
    servidor_nuevo, url = principal.arrancar_servidor(None, app.home)
    try:
        from tests.support.cliente_app import ClienteApp

        cliente = ClienteApp(url)
        assert cliente.get("api/estado").json()["ultima_ejecucion"] == abrupto
        # Hasta que otra ejecución termina y lo sustituye.
        servidor.configurar(NOMBRE, velocidad=None)
        nueva = cliente.post("api/iniciar").json()["ejecucion"]

        def sustituido():
            ultima = cliente.get("api/estado").json()["ultima_ejecucion"]
            return ultima if ultima and ultima["ejecucion"] == nueva else None

        ultima = hasta(sustituido, 120, "el resultado de la ejecución nueva")
        assert (ultima["codigo"], ultima["completadas"], ultima["error"]) == (0, 1, None), json.dumps(ultima, ensure_ascii=False)
    finally:
        servidor_nuevo.cerrar()
