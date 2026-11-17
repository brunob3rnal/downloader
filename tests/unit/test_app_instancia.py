"""Una sola ventana (specs/002-desktop-app, FR-018; research R12; tasks T097).

El bloqueo de `ventana.lock` y la petición de activación entre procesos están en
`tests/integration/test_app_instancia.py`; aquí, el módulo dentro de un proceso.
"""

import threading

from downloader.app import instancia
from tests.support.esperas import hasta


def test_el_bloqueo_lo_tiene_uno_y_se_libera(tmp_path):
    primera = instancia.tomar(tmp_path)
    assert primera is not None
    assert instancia.tomar(tmp_path) is None
    assert instancia.libre(tmp_path) is False
    primera.liberar()
    assert instancia.libre(tmp_path) is True
    otra = instancia.tomar(tmp_path)
    assert otra is not None
    otra.liberar()
    otra.liberar()  # dos veces no falla


def test_pedir_activacion_deja_el_archivo(tmp_path):
    instancia.pedir_activacion(tmp_path)
    assert (tmp_path / instancia.SOLICITUD).exists()
    assert instancia.SOLICITUD == "activar.solicitud"


def test_el_vigilante_enfoca_y_borra_la_peticion(tmp_path):
    enfocadas = []
    vigilante = instancia.Vigilante(tmp_path, lambda: enfocadas.append(1), intervalo=0.02)
    vigilante.start()
    try:
        instancia.pedir_activacion(tmp_path)
        hasta(lambda: enfocadas, 5, "que enfoque")
        hasta(lambda: not (tmp_path / instancia.SOLICITUD).exists(), 5, "que la borre")
        instancia.pedir_activacion(tmp_path)
        hasta(lambda: len(enfocadas) == 2, 5, "que enfoque otra vez")
    finally:
        vigilante.detener()
    assert not vigilante.is_alive()


def test_el_vigilante_consulta_cada_segundo(tmp_path):
    assert instancia.Vigilante(tmp_path, lambda: None).intervalo == instancia.INTERVALO == 1


def test_un_fallo_al_enfocar_no_para_el_vigilante(tmp_path, caplog):
    llamadas = []

    def falla():
        llamadas.append(1)
        raise RuntimeError("ventana ocupada")

    vigilante = instancia.Vigilante(tmp_path, falla, intervalo=0.02)
    vigilante.start()
    try:
        instancia.pedir_activacion(tmp_path)
        hasta(lambda: llamadas, 5, "el primer intento")
        instancia.pedir_activacion(tmp_path)
        hasta(lambda: len(llamadas) == 2, 5, "que siga vigilando")
    finally:
        vigilante.detener()
    assert "ventana ocupada" in caplog.text


def test_una_peticion_anterior_a_la_ventana_se_descarta(tmp_path):
    """Una petición que quedó de antes (la ventana murió sin atenderla) no enfoca la nueva."""
    instancia.pedir_activacion(tmp_path)
    unica = instancia.tomar(tmp_path)
    try:
        assert not (tmp_path / instancia.SOLICITUD).exists()
    finally:
        unica.liberar()
