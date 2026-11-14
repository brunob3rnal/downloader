"""Marcador `solo_windows` (specs/002-desktop-app, T002; constitución v3.1.0).

Solo los tests de la ventana y del lanzador lo llevan; fuera de Windows se omiten con el
motivo. Se prueba el gancho de `tests/conftest.py` con un elemento falso.
"""

import sys

import pytest

from tests.conftest import pytest_runtest_setup


class _Elemento:
    def __init__(self, *marcadores):
        self._marcadores = set(marcadores)

    def get_closest_marker(self, nombre):
        return nombre if nombre in self._marcadores else None


def test_solo_windows_se_omite_fuera_de_windows_con_el_motivo():
    if sys.platform == "win32":
        pytest_runtest_setup(_Elemento("solo_windows"))  # no se omite
    else:
        with pytest.raises(pytest.skip.Exception, match=r"solo Windows \(constitución v3\.1\.0\)"):
            pytest_runtest_setup(_Elemento("solo_windows"))


def test_sin_marcador_no_se_omite():
    pytest_runtest_setup(_Elemento())
