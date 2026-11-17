"""Una sola ventana, entre procesos y sin parches (specs/002-desktop-app, FR-018; research R12;
tasks T094)."""

import os
import subprocess
import sys
import time

import pytest

from downloader.app import instancia, principal
from tests.support.procesos_app import RAIZ, VentanaEnProceso

SEGUNDA_MAXIMA = 3  # s para que una segunda apertura termine
ENFOQUE_MAXIMO = 2  # s para que la primera pase al frente


def _abrir_otra(home):
    """`python -m downloader.app`, como el lanzador, con otra ventana ya abierta."""
    inicio = time.monotonic()
    resultado = subprocess.run([sys.executable, "-m", "downloader.app"], cwd=RAIZ,
                               env={**os.environ, "DOWNLOADER_HOME": str(home)},
                               capture_output=True, text=True, timeout=60)
    return resultado, time.monotonic() - inicio


def test_una_segunda_apertura_enfoca_la_primera_y_termina(app_home):
    primera = VentanaEnProceso(app_home)
    try:
        resultado, duracion = _abrir_otra(app_home)
        assert resultado.returncode == 0, resultado.stderr
        assert duracion <= SEGUNDA_MAXIMA, duracion
        momento = time.monotonic()
        primera.esperar_linea(lambda l: l == "enfocar", ENFOQUE_MAXIMO)
        print(f"\n[instancia] segunda apertura {duracion:.2f} s; enfoque "
              f"{time.monotonic() - momento:.2f} s después")
        assert not (app_home / instancia.SOLICITUD).exists()  # la primera la borró
        assert primera.proceso.poll() is None  # y sigue abierta
    finally:
        primera.matar()


def test_otra_ventana_en_proceso_tampoco_se_abre(app_home):
    primera = VentanaEnProceso(app_home)
    try:
        segunda = subprocess.run([sys.executable, "-m", "tests.support.ventana_headless"],
                                 cwd=RAIZ, env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
                                 capture_output=True, text=True, timeout=60)
        assert segunda.returncode == 0, segunda.stderr
        assert "url " not in segunda.stdout  # no ha abierto ninguna ventana
        primera.esperar_linea(lambda l: l == "enfocar", ENFOQUE_MAXIMO)
    finally:
        primera.matar()


def test_si_la_primera_muere_el_bloqueo_se_libera(app_home):
    primera = VentanaEnProceso(app_home)
    primera.matar()
    nueva = VentanaEnProceso(app_home)  # arranca y escribe su URL
    try:
        assert instancia.libre(app_home) is False  # el bloqueo es suyo
    finally:
        nueva.matar()
    assert instancia.libre(app_home) is True


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows abre la ventana real")
def test_fuera_de_windows_con_el_bloqueo_libre_sale_con_2(app_home):
    resultado, _ = _abrir_otra(app_home)
    assert resultado.returncode == 2
    assert principal.SOLO_WINDOWS in resultado.stderr
    assert not (app_home / instancia.SOLICITUD).exists()
