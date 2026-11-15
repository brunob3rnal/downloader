"""Si no se pueden escribir los archivos informativos, `ejecutar` no cambia (specs/002-desktop-app,
T031; hallazgo P2; research R9).

`procesador.json` y `progreso-vivo.json` se sustituyen por directorios, así que escribirlos y
borrarlos falla en los dos sistemas, también como root. La salida y el código deben ser los de
las referencias de `test_cli_caracterizacion.py`. El fallo no se silencia: va al logger
`downloader.vivo`, que el procesador de la aplicación lleva a su registro de errores; la CLI no
tiene manejador para él, así que su salida no cambia.
"""

import logging
import os
import subprocess
import sys
import time

import pytest

from downloader.models import Calidad
from downloader.store import Store
from tests.integration.test_cli_caracterizacion import _video, comprobar
from tests.integration.test_ctrl_c import interrumpir

ARCHIVOS = ("procesador.json", "progreso-vivo.json")


@pytest.fixture
def bloqueados(app_home):
    for nombre in ARCHIVOS:
        (app_home / nombre).mkdir()
    yield
    for nombre in ARCHIVOS:
        assert (app_home / nombre).is_dir()  # nunca se borra ni se sustituye lo que había


def _registros(caplog):
    return [r for r in caplog.records if r.name == "downloader.vivo"]


def test_todas_completadas(cli, destino, servidor, medios, tmp_path, bloqueados, caplog):
    cli("agregar", _video(servidor, medios), "--carpeta", destino)
    with caplog.at_level(logging.INFO, logger="downloader.vivo"):
        resultado = cli("ejecutar")
    comprobar("ejecutar_todas_completadas", resultado.exit_code, resultado.stdout,
              resultado.stderr, tmp_path)
    registros = _registros(caplog)
    textos = [r.getMessage() for r in registros]
    assert any("procesador.json" in t for t in textos)
    assert any("progreso-vivo.json" in t for t in textos)
    assert len(registros) <= 4  # al empezar a fallar y al no poder borrar; no uno por segundo


def test_con_fallidas(cli, destino, servidor, medios, tmp_path, bloqueados):
    cli("agregar", _video(servidor, medios), "--carpeta", destino)
    cli("agregar", servidor.url("watch/privado"), "--carpeta", destino)
    resultado = cli("ejecutar")
    comprobar("ejecutar_con_fallidas", resultado.exit_code, resultado.stdout, resultado.stderr,
              tmp_path)


def test_sin_ffmpeg(cli, destino, servidor, tmp_path, monkeypatch, bloqueados):
    cli("agregar", servidor.url("watch/a"), "--carpeta", destino)
    vacio = tmp_path / "bin-vacio"
    vacio.mkdir()
    monkeypatch.setenv("PATH", str(vacio))
    resultado = cli("ejecutar")
    comprobar("ejecutar_sin_ffmpeg", resultado.exit_code, resultado.stdout, resultado.stderr,
              tmp_path)


def test_ctrl_c(app_home, destino, servidor, medios, tmp_path, bloqueados):
    servidor.registrar("lento.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       velocidad=200_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    store.agregar(servidor.url("lento.mp4"), Calidad.MEJOR, str(destino))
    store.cerrar()
    proceso = subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        trabajo = destino / ".downloader-tmp"
        limite = time.monotonic() + 60
        while not (trabajo.exists() and any(p.stat().st_size > 50_000
                                            for p in trabajo.glob("*/*.part"))):
            assert proceso.poll() is None, "ejecutar terminó antes de interrumpirlo"
            assert time.monotonic() < limite, "la descarga lenta no avanzó"
            time.sleep(0.05)
        interrumpir(proceso)
        salida, errores = proceso.communicate(timeout=30)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
    comprobar("ejecutar_ctrl_c", proceso.returncode, salida, errores, tmp_path)
