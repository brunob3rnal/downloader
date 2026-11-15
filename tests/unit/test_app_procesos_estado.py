"""Quién procesa la cola (specs/002-desktop-app, research R6, contracts/archivos.md)."""

import json
import os
import subprocess
import sys

import psutil

from downloader.app import procesos


def _escribir(directorio, **datos):
    (directorio / "procesador.json").write_text(json.dumps(datos), encoding="utf-8")


def test_sin_archivo(tmp_path):
    estado = procesos.estado_procesador(tmp_path)
    assert estado.estado == "ninguno" and estado.abrupto is False


def test_proceso_vivo(tmp_path):
    yo = psutil.Process()
    _escribir(tmp_path, pid=yo.pid, creado=yo.create_time(), origen="app", ejecucion="e1",
              inicio="2026-11-15T10:00:00.000+00:00")
    estado = procesos.estado_procesador(tmp_path)
    assert (estado.estado, estado.origen, estado.ejecucion, estado.pid) == (
        "en_marcha", "app", "e1", yo.pid)
    assert estado.abrupto is False


def test_pid_reutilizado(tmp_path):
    yo = psutil.Process()
    _escribir(tmp_path, pid=yo.pid, creado=yo.create_time() - 1000, origen="cli",
              ejecucion=None, inicio="x")
    estado = procesos.estado_procesador(tmp_path)
    assert estado.estado == "ninguno" and estado.abrupto is True


def test_proceso_muerto(tmp_path):
    hijo = subprocess.Popen([sys.executable, "-c", "pass"])
    hijo.wait()
    _escribir(tmp_path, pid=hijo.pid, creado=1.0, origen="app", ejecucion="e2", inicio="x")
    estado = procesos.estado_procesador(tmp_path)
    assert estado.estado == "ninguno" and estado.abrupto is True
    assert estado.origen == "app" and estado.ejecucion == "e2"


def test_json_invalido(tmp_path):
    (tmp_path / "procesador.json").write_text("{roto", encoding="utf-8")
    assert procesos.estado_procesador(tmp_path).estado == "ninguno"


def test_campos_que_faltan(tmp_path):
    _escribir(tmp_path, origen="app")
    assert procesos.estado_procesador(tmp_path).estado == "ninguno"


def test_creado_nulo_se_trata_como_vivo_si_el_pid_vive(tmp_path):
    # `ejecutar` escribe `creado: null` si psutil falló; el pid vivo basta entonces.
    _escribir(tmp_path, pid=os.getpid(), creado=None, origen="cli", ejecucion=None, inicio="x")
    assert procesos.estado_procesador(tmp_path).estado == "en_marcha"
