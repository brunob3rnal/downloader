"""Arranque de la aplicación (specs/002-desktop-app, research R19; tasks T046)."""

import sys

import pytest

from downloader.app import principal
from tests.support.cliente_app import ClienteApp


class VentanaQueSeCierra:
    """Ventana falsa que, al mostrarse, comprueba que el servidor responde y se cierra."""

    def __init__(self, url):
        self.url = url
        self.respuesta = None

    def mostrar(self):
        self.respuesta = ClienteApp(self.url).get().estado


def test_arrancar_sirve_la_pagina_y_cierra_al_cerrar_la_ventana(app_home):
    creadas = []

    def crear(url):
        creadas.append(VentanaQueSeCierra(url))
        return creadas[-1]

    assert principal.arrancar(crear, directorio=app_home) == 0
    assert creadas[0].respuesta == 200


def test_sin_motor_no_abre_ninguna_ventana(app_home):
    creadas = []
    codigo = principal.arrancar(lambda url: creadas.append(url), comprobar_motor=lambda: False,
                                directorio=app_home)
    assert codigo == principal.SIN_MOTOR == 3
    assert creadas == []


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows abre la ventana real")
def test_main_fuera_de_windows_sale_con_2_y_el_mensaje(app_home, capsys):
    assert principal.main() == 2
    assert principal.SOLO_WINDOWS in capsys.readouterr().err
