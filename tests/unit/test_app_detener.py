"""`POST api/detener` y el estado `deteniendo` (specs/002-desktop-app, FR-009;
contracts/api-local.md; tasks T077).

Con procesadores simulados: un proceso cualquiera y un `procesador.json` escrito a mano, para
que cada estado dure lo que el test necesita. La parada real, con el trabajador, está en
`tests/integration/test_app_parada.py`.
"""

import json
import logging
import subprocess
import sys

import psutil
import pytest

from downloader.app import api as api_app
from downloader.app import parada, procesos

DETENIENDO = {"estado": "deteniendo", "origen": "app", "puede_iniciar": False,
              "puede_detener": False, "motivo": api_app.MOTIVO_DETENIENDO, "terminando": None}
NINGUNO = {"estado": "ninguno", "origen": None, "puede_iniciar": True, "puede_detener": False,
           "motivo": None, "terminando": None}


@pytest.fixture
def api(tmp_path, monkeypatch):
    def prohibido(*argumentos, **opciones):
        raise AssertionError("no debe lanzarse ningún trabajador")

    monkeypatch.setattr(procesos, "lanzar_trabajador", prohibido)
    return api_app.Api(tmp_path)


@pytest.fixture
def proceso():
    """Crea procesos que viven hasta que el test los mata."""
    creados = []

    def crear():
        nuevo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        creados.append(nuevo)
        return nuevo

    yield crear
    for creado in creados:
        if creado.poll() is None:
            creado.kill()
        creado.wait()


def anunciar(directorio, proceso, origen, ejecucion):
    """Lo que escribe `ejecutar_cola` al tomar el bloqueo."""
    (directorio / "procesador.json").write_text(json.dumps({
        "pid": proceso.pid, "creado": psutil.Process(proceso.pid).create_time(),
        "origen": origen, "ejecucion": ejecucion, "inicio": "2026-11-17T10:00:00.000+00:00",
    }), encoding="utf-8")


def estado(api):
    return api.atender("GET", "estado", b"", {}).datos["procesador"]


def detener(api):
    return api.atender("POST", "detener", b"", {})


def pedida(directorio):
    ruta = directorio / parada.ARCHIVO
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def test_sin_procesador_409(api, tmp_path):
    respuesta = detener(api)
    assert (respuesta.estado, respuesta.datos) == (409, {"motivo": api_app.MOTIVO_NINGUNO})
    assert pedida(tmp_path) is None and estado(api) == NINGUNO


def test_un_procesador_de_la_cli_no_se_puede_detener(api, tmp_path, proceso):
    """FR-009: la aplicación no detiene un `ejecutar` de la terminal."""
    anunciar(tmp_path, proceso(), "cli", None)
    respuesta = detener(api)
    assert (respuesta.estado, respuesta.datos) == (409, {"motivo": api_app.MOTIVO_CLI})
    assert pedida(tmp_path) is None
    assert estado(api)["estado"] == "en_marcha"


def test_detener_escribe_la_parada_y_pasa_a_deteniendo(api, tmp_path, proceso):
    anunciar(tmp_path, proceso(), "app", "e1")
    antes = estado(api)
    assert (antes["estado"], antes["puede_detener"], antes["motivo"]) == ("en_marcha", True, None)
    respuesta = detener(api)
    assert (respuesta.estado, respuesta.datos) == (202, {"estado": "deteniendo"})
    assert pedida(tmp_path)["ejecucion"] == "e1"
    assert estado(api) == DETENIENDO


def test_detener_otra_vez_durante_deteniendo_no_tiene_efecto(api, tmp_path, proceso):
    anunciar(tmp_path, proceso(), "app", "e1")
    assert detener(api).estado == 202
    (tmp_path / parada.ARCHIVO).unlink()  # el vigilante ya la recogió
    respuesta = detener(api)
    assert (respuesta.estado, respuesta.datos) == (202, {"estado": "deteniendo"})
    assert pedida(tmp_path) is None  # no se vuelve a escribir
    assert estado(api) == DETENIENDO


def test_durante_deteniendo_no_se_puede_iniciar(api, tmp_path, proceso):
    anunciar(tmp_path, proceso(), "app", "e1")
    detener(api)
    respuesta = api.atender("POST", "iniciar", b"", {})
    assert (respuesta.estado, respuesta.datos) == (409, {"motivo": api_app.MOTIVO_DETENIENDO})


def test_cuando_el_proceso_termina_ya_no_hay_procesador(api, tmp_path, proceso):
    simulado = proceso()
    anunciar(tmp_path, simulado, "app", "e1")
    detener(api)
    simulado.kill()
    simulado.wait()
    assert estado(api) == NINGUNO
    assert detener(api).estado == 409


def test_otra_ejecucion_no_hereda_el_deteniendo(api, tmp_path, proceso):
    primero = proceso()
    anunciar(tmp_path, primero, "app", "e1")
    detener(api)
    primero.kill()
    primero.wait()
    anunciar(tmp_path, proceso(), "app", "e2")
    nuevo = estado(api)
    assert (nuevo["estado"], nuevo["puede_detener"]) == ("en_marcha", True)


def test_si_no_se_puede_escribir_la_parada_500_y_sigue_en_marcha(api, tmp_path, proceso,
                                                                 monkeypatch, caplog):
    anunciar(tmp_path, proceso(), "app", "e1")
    monkeypatch.setattr(parada, "pedir", lambda directorio, ejecucion: False)
    with caplog.at_level(logging.ERROR, logger="downloader.app"):
        respuesta = detener(api)
    assert respuesta.estado == 500 and respuesta.datos["error"]
    assert estado(api)["estado"] == "en_marcha"
    assert "parada" in caplog.text


# --- el trabajador que lanzó esta ventana (research R5, R6) -------------------------------


def test_arrancando_no_se_puede_detener(api, tmp_path, proceso):
    api.lanzamiento = procesos.Lanzamiento(proceso(), "e1")  # lanzado, aún sin anunciarse
    arrancando = estado(api)
    assert (arrancando["estado"], arrancando["origen"]) == ("arrancando", "app")
    assert (arrancando["puede_iniciar"], arrancando["puede_detener"]) == (False, False)
    respuesta = detener(api)
    assert (respuesta.estado, respuesta.datos) == (409, {"motivo": api_app.MOTIVO_ARRANCANDO})
    assert pedida(tmp_path) is None


def test_al_terminar_no_vuelve_a_arrancando(api, tmp_path, proceso):
    """Entre que el trabajador retira `procesador.json` y el proceso termina, sigue en marcha:
    ni `arrancando` otra vez ni `ninguno` antes de tiempo (otro Iniciar perdería el bloqueo)."""
    simulado = proceso()
    api.lanzamiento = procesos.Lanzamiento(simulado, "e1")
    anunciar(tmp_path, simulado, "app", "e1")
    assert estado(api)["estado"] == "en_marcha"
    (tmp_path / "procesador.json").unlink()  # fin ordenado: lo retira antes de salir
    terminando = estado(api)
    assert terminando == {"estado": "en_marcha", "origen": "app", "puede_iniciar": False,
                          "puede_detener": False, "motivo": api_app.MOTIVO_TERMINANDO, "terminando": None}
    assert detener(api).estado == 409
    simulado.kill()
    simulado.wait()
    assert estado(api) == NINGUNO


def test_deteniendo_dura_hasta_que_el_proceso_termina(api, tmp_path, proceso):
    """`deteniendo` va desde la petición hasta que el proceso termina (data-model), no hasta
    que retira `procesador.json`."""
    simulado = proceso()
    api.lanzamiento = procesos.Lanzamiento(simulado, "e1")
    anunciar(tmp_path, simulado, "app", "e1")
    assert detener(api).estado == 202
    (tmp_path / "procesador.json").unlink()
    assert estado(api) == DETENIENDO
    simulado.kill()
    simulado.wait()
    assert estado(api) == NINGUNO


def test_deteniendo_dice_que_esta_terminando(api, tmp_path, proceso, monkeypatch):
    """SC-004 enmendado: si el trabajador espera a ffmpeg o ffprobe, se dice qué termina."""
    simulado = proceso()
    anunciar(tmp_path, simulado, "app", "e1")
    vistos = []
    monkeypatch.setattr(procesos, "terminando",
                        lambda pid: vistos.append(pid) or procesos.TERMINANDO["union"])
    assert estado(api)["terminando"] is None  # en marcha no se mira
    assert vistos == []
    detener(api)
    assert estado(api) == dict(DETENIENDO, terminando=procesos.TERMINANDO["union"])
    assert vistos == [simulado.pid]


def test_la_etiqueta_de_estado_cambia_con_lo_que_termina_y_es_ascii(api, tmp_path, proceso,
                                                                    monkeypatch):
    """La ETag va en una cabecera HTTP: nada de "…" en ella (latin-1)."""
    anunciar(tmp_path, proceso(), "app", "e1")
    detener(api)
    monkeypatch.setattr(procesos, "terminando", lambda pid: procesos.TERMINANDO["union"])
    union = api.atender("GET", "estado", b"", {}).cabeceras["ETag"]
    monkeypatch.setattr(procesos, "terminando", lambda pid: procesos.TERMINANDO["verificacion"])
    verificacion = api.atender("GET", "estado", b"", {}).cabeceras["ETag"]
    assert union != verificacion
    assert union.isascii() and verificacion.isascii()
