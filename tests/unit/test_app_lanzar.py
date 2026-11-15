"""Cómo lanza la aplicación el trabajador (specs/002-desktop-app, research R5, R8; T054).

Corre en los dos sistemas: simula los indicadores de Windows sin lanzar nada.
"""

import logging
import subprocess

import pytest

from downloader.app import procesos
from tests.support.app_local import app_local  # noqa: F401  (fixture)


class _Registro:
    def __init__(self, error=None):
        self.llamadas = []
        self.error = error

    def __call__(self, orden, **opciones):
        self.llamadas.append((orden, opciones))
        if self.error is not None:
            raise self.error
        return "popen"


def _acceso_denegado():
    error = OSError(13, "Acceso denegado")
    error.winerror = 5  # ERROR_ACCESS_DENIED: el objeto de trabajo no deja salir (R8)
    return error


def test_en_windows_sale_del_objeto_de_trabajo_sin_consola(monkeypatch, tmp_path):
    falso = _Registro()
    monkeypatch.setattr(procesos.sys, "platform", "win32")
    monkeypatch.setattr(procesos.subprocess, "Popen", falso)
    assert procesos.lanzar_trabajador(tmp_path, "e1") == "popen"
    orden, opciones = falso.llamadas[0]
    assert orden[1:] == ["-m", "downloader.app.procesador", "--ejecucion", "e1"]
    assert opciones["creationflags"] == (procesos.CREATE_NO_WINDOW
                                         | procesos.CREATE_NEW_PROCESS_GROUP
                                         | procesos.CREATE_BREAKAWAY_FROM_JOB)
    assert opciones["stdout"] is subprocess.DEVNULL and opciones["stderr"] is subprocess.DEVNULL
    assert opciones["env"]["DOWNLOADER_HOME"] == str(tmp_path)


def test_fuera_de_windows_en_otra_sesion(monkeypatch, tmp_path):
    falso = _Registro()
    monkeypatch.setattr(procesos.sys, "platform", "linux")
    monkeypatch.setattr(procesos.subprocess, "Popen", falso)
    procesos.lanzar_trabajador(tmp_path, "e1")
    _, opciones = falso.llamadas[0]
    assert opciones["start_new_session"] is True and "creationflags" not in opciones


def test_con_pythonw_usa_python(monkeypatch, tmp_path):
    falso = _Registro()
    monkeypatch.setattr(procesos.sys, "executable", str(tmp_path / "Scripts" / "pythonw.exe"))
    monkeypatch.setattr(procesos.subprocess, "Popen", falso)
    procesos.lanzar_trabajador(tmp_path, "e1")
    assert falso.llamadas[0][0][0] == str(tmp_path / "Scripts" / "python.exe")


def test_sin_poder_salir_del_objeto_de_trabajo_no_reintenta(monkeypatch, tmp_path, caplog):
    falso = _Registro(_acceso_denegado())
    monkeypatch.setattr(procesos.sys, "platform", "win32")
    monkeypatch.setattr(procesos.subprocess, "Popen", falso)
    with caplog.at_level(logging.ERROR, logger="downloader.app"):
        with pytest.raises(procesos.SinSegundoPlano) as error:
            procesos.lanzar_trabajador(tmp_path, "e1")
    assert len(falso.llamadas) == 1  # sin un segundo intento sin el indicador
    assert str(error.value) == procesos.SIN_SEGUNDO_PLANO
    assert any(procesos.SIN_SEGUNDO_PLANO in r.getMessage() for r in caplog.records)


def test_otros_errores_se_lanzan_tal_cual(monkeypatch, tmp_path):
    monkeypatch.setattr(procesos.subprocess, "Popen", _Registro(FileNotFoundError("no python")))
    with pytest.raises(FileNotFoundError):
        procesos.lanzar_trabajador(tmp_path, "e1")


def test_api_iniciar_responde_409_con_el_motivo(app_local, monkeypatch):
    def denegado(directorio, ejecucion):
        raise procesos.SinSegundoPlano(procesos.SIN_SEGUNDO_PLANO)

    monkeypatch.setattr(procesos, "lanzar_trabajador", denegado)
    respuesta = app_local.cliente.post("api/iniciar")
    assert respuesta.estado == 409
    assert respuesta.json()["motivo"] == procesos.SIN_SEGUNDO_PLANO
