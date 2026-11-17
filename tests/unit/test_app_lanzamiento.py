"""El trabajador que lanza la ventana, visto por `api/estado` (specs/002-desktop-app, FR-008;
research R5, R6; tasks T082).

`arrancando` hasta que se anuncia con `procesador.json` o termina; si pierde el bloqueo de la
cola sale con 2 y la ventana pasa a observar al que lo tiene, sin tocar el resultado. Con
procesos simulados, como en `test_app_detener.py`.
"""

import subprocess
import sys

from downloader.app import api as api_app
from downloader.app import procesos, resultado
from tests.unit.test_app_detener import (  # noqa: F401  (fixtures y ayudas)
    NINGUNO,
    anunciar,
    api,
    estado,
    proceso,
)

OBSERVANDO = {"estado": "en_marcha", "origen": "cli", "puede_iniciar": False,
              "puede_detener": False, "motivo": api_app.MOTIVO_CLI}
EN_MARCHA = {"estado": "en_marcha", "origen": "app", "puede_iniciar": False,
             "puede_detener": True, "motivo": None}


def _terminado(codigo):
    hecho = subprocess.Popen([sys.executable, "-c", f"import sys; sys.exit({codigo})"])
    hecho.wait()
    return hecho


def test_arrancando_hasta_que_se_anuncia(api, tmp_path, proceso):  # noqa: F811
    simulado = proceso()
    api.lanzamiento = procesos.Lanzamiento(simulado, "e1")
    assert estado(api) == {"estado": "arrancando", "origen": "app", "puede_iniciar": False,
                           "puede_detener": False, "motivo": api_app.MOTIVO_ARRANCANDO}
    assert api.lanzamiento.anunciado is False
    anunciar(tmp_path, simulado, "app", "e1")
    assert estado(api) == EN_MARCHA
    assert api.lanzamiento.anunciado is True


def test_mientras_arranca_no_se_lanza_otro(api, proceso):  # noqa: F811
    api.lanzamiento = procesos.Lanzamiento(proceso(), "e1")
    respuesta = api.atender("POST", "iniciar", b"", {})  # `lanzar_trabajador` está prohibido
    assert (respuesta.estado, respuesta.datos) == (409, {"motivo": api_app.MOTIVO_ARRANCANDO})


def test_si_termina_sin_anunciarse_no_se_queda_arrancando(api, tmp_path):  # noqa: F811
    api.lanzamiento = procesos.Lanzamiento(_terminado(2), "e1")
    assert estado(api) == NINGUNO
    assert resultado.leer(tmp_path) is None


def test_si_pierde_el_bloqueo_la_ventana_observa_al_que_lo_tiene(api, tmp_path,  # noqa: F811
                                                                 proceso):  # noqa: F811
    """R6: sale con 2 y no deja resultado; lo que se ve es el `ejecutar` de la terminal."""
    resultado.escribir(tmp_path, "anterior", None, 3, [], 0, None)
    anterior = resultado.leer(tmp_path)
    api.lanzamiento = procesos.Lanzamiento(_terminado(2), "e1")
    anunciar(tmp_path, proceso(), "cli", None)
    assert estado(api) == OBSERVANDO
    assert resultado.leer(tmp_path) == anterior  # el de la ejecución anterior, intacto
    assert api.lanzamiento.proceso.returncode == 2 and api.lanzamiento.anunciado is False


def test_el_anuncio_de_otra_ejecucion_no_es_el_suyo(api, tmp_path, proceso):  # noqa: F811
    """Otra sesión de la ventana ganó el bloqueo: se ve su procesador, que se puede pausar, y
    el lanzado aquí sigue sin anunciarse hasta que sale."""
    api.lanzamiento = procesos.Lanzamiento(proceso(), "e1")
    anunciar(tmp_path, proceso(), "app", "de-otra-sesion")
    assert estado(api) == EN_MARCHA
    assert api.lanzamiento.anunciado is False
