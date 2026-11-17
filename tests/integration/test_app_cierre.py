"""Cerrar la ventana con un procesamiento en marcha (specs/002-desktop-app, FR-010; research R18;
contracts/api-local.md, `POST api/cerrar`; tasks T093)."""

import json
import subprocess
import sys
import time

import psutil
import pytest

from downloader import archivos_json
from downloader.app import api as api_app
from downloader.app import cierre, parada
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta

NOMBRE = "lento.mp4"


@pytest.fixture
def en_marcha(app_local, servidor, destino):
    """La aplicación procesando de verdad una descarga que no termina durante el test."""
    app = app_local
    (app.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    servidor.registrar(NOMBRE, bytes(50_000_000), tipo_contenido="video/mp4", velocidad=1_000_000)
    for n in (1, 2, 3):
        respuesta = app.cliente.post("api/agregar", {
            "texto": servidor.url(NOMBRE) + f"?n={n}", "calidad": "mejor",
            "carpeta": str(destino), "navegador": None})
        assert respuesta.estado == 200, respuesta.cuerpo
    assert app.cliente.post("api/iniciar").estado == 202
    hasta(lambda: app.cliente.get("api/estado").json()["procesador"]["estado"] == "en_marcha",
          60, "que el trabajador arranque")
    return app


@pytest.fixture
def simulado(app_local):
    """Un procesador simulado: un proceso cualquiera anunciado en `procesador.json`, que no
    atiende la parada. Recibe el origen."""
    creados = []

    def crear(origen):
        proceso = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        creados.append(proceso)
        (app_local.home / "procesador.json").write_text(json.dumps({
            "pid": proceso.pid, "creado": psutil.Process(proceso.pid).create_time(),
            "origen": origen, "ejecucion": "e1" if origen == "app" else None,
            "inicio": "2026-11-17T10:00:00.000+00:00"}), encoding="utf-8")
        return proceso

    yield crear
    for proceso in creados:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait()


def cerrar(app, decision):
    return app.cliente.post("api/cerrar", {"decision": decision})


def test_seguir_en_segundo_plano_cierra_la_ventana_y_el_trabajador_sigue(en_marcha):
    app = en_marcha
    pid = archivos_json.leer(app.home / "procesador.json")["pid"]
    respuesta = cerrar(app, "segundo_plano")
    assert respuesta.estado == 202, respuesta.cuerpo
    assert app.ventana.destruida
    time.sleep(1)
    assert psutil.Process(pid).is_running()
    assert archivos_json.leer(app.home / "procesador.json")["pid"] == pid
    assert app.cliente.post("api/detener").estado == 202  # limpieza
    app.servidor.api.lanzamiento.proceso.wait(timeout=60)


def test_pausar_y_cerrar_cierra_cuando_el_procesamiento_termina(en_marcha):
    app = en_marcha
    proceso = app.servidor.api.lanzamiento.proceso
    respuesta = cerrar(app, "pausar")
    assert respuesta.estado == 202, respuesta.cuerpo
    datos = app.cliente.get("api/estado").json()
    if proceso.poll() is None:  # mientras se detiene, la ventana sigue abierta
        assert datos["procesador"]["estado"] == "deteniendo"
        assert datos["cierre"] == {"estado": "deteniendo", "cerrar_igualmente": False}
        assert not app.ventana.destruida
    assert proceso.wait(timeout=60) == 130
    hasta(lambda: app.ventana.destruida, 5, "que la ventana se cierre al terminar")
    ultima = archivos_json.leer(app.home / "ultima-ejecucion.json")
    assert ultima["error"]["tipo"] == "detenida"


def test_si_no_termina_en_el_plazo_ofrece_cerrar_igualmente(app_local, simulado):
    app = app_local
    proceso = simulado("app")
    app.servidor.api.plazo_cierre = 1  # en la aplicación, 30 s
    assert cerrar(app, "pausar").estado == 202
    assert (app.home / parada.ARCHIVO).exists()  # la parada se pidió
    datos = hasta(lambda: (d := app.cliente.get("api/estado").json())["cierre"][
        "cerrar_igualmente"] and d, 5, "que ofrezca cerrar igualmente")
    assert datos["cierre"]["estado"] == "deteniendo"
    assert not app.ventana.destruida
    assert cerrar(app, "cerrar_igualmente").estado == 202
    assert app.ventana.destruida
    assert proceso.poll() is None  # cerrar igualmente no mata nada: el progreso está a salvo


def test_cancelar_no_hace_nada(app_local, simulado):
    app = app_local
    simulado("app")
    respuesta = cerrar(app, "cancelar")
    assert respuesta.estado == 202
    assert not app.ventana.destruida and app.ventana.llamadas == []
    assert not (app.home / parada.ARCHIVO).exists()
    assert app.cliente.get("api/estado").json()["cierre"] is None


def test_pausar_un_procesamiento_de_la_terminal_409(app_local, simulado):
    app = app_local
    simulado("cli")
    respuesta = cerrar(app, "pausar")
    assert (respuesta.estado, respuesta.json()) == (409, {"motivo": api_app.MOTIVO_CLI})
    assert not app.ventana.destruida
    assert not (app.home / parada.ARCHIVO).exists()


def test_cerrar_igualmente_sin_pausar_antes_409(app_local, simulado):
    app = app_local
    simulado("app")
    assert cerrar(app, "cerrar_igualmente").estado == 409
    assert not app.ventana.destruida


@pytest.mark.parametrize("cuerpo", [{"decision": "otra"}, {}, None])
def test_decision_desconocida_400(app_local, cuerpo):
    respuesta = app_local.cliente.post("api/cerrar", cuerpo)
    assert respuesta.estado == 400
    assert not app_local.ventana.destruida


# --- el aviso al pulsar la X (lo llama el evento de cierre de la ventana real) ---------


def test_sin_procesamiento_la_ventana_se_cierra_sin_preguntar(app_local):
    assert app_local.servidor.api.al_cerrar_ventana() is True
    assert app_local.ventana.llamadas == []


def test_con_un_procesamiento_de_la_terminal_tampoco_pregunta(app_local, simulado):
    simulado("cli")
    assert app_local.servidor.api.al_cerrar_ventana() is True
    assert app_local.ventana.llamadas == []


def test_con_un_procesamiento_propio_muestra_el_dialogo(app_local, simulado, servidor, destino):
    app = app_local
    for n in (1, 2, 3):
        app.cliente.post("api/agregar", {"texto": f"https://ejemplo.com/v/{n}",
                                         "calidad": "mejor", "carpeta": str(destino),
                                         "navegador": None})
    simulado("app")
    assert app.servidor.api.al_cerrar_ventana() is False  # cancela el cierre
    llamada = hasta(lambda: [a for n, a in app.ventana.llamadas if n == "ejecutar_js"], 5,
                    "que muestre el diálogo")
    assert llamada == [(f"mostrarDialogoCierre({json.dumps(cierre.texto(3))})",)]
    assert not app.ventana.destruida
