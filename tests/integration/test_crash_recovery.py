"""Cierre abrupto de `ejecutar` y recuperación (escenario 4 de quickstart; US3).

`ejecutar` corre en un subproceso real, que no registra `fake_extractor`: las URLs son
directas al MP4 y las resuelve el extractor genérico de yt-dlp.
"""

import os
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.xfail(strict=True, reason="pendiente de la Fase 5 (US3)")

RAIZ = Path(__file__).resolve().parents[2]
LIMITE = 60


def downloader(*argumentos, env, **kwargs):
    comando = [shutil.which("uv"), "run", "--quiet", "downloader", *map(str, argumentos)]
    return subprocess.Popen(comando, cwd=RAIZ, env=env, start_new_session=True, **kwargs)


def esperar(proceso):
    try:
        return proceso.wait(timeout=LIMITE)
    except subprocess.TimeoutExpired:
        os.killpg(proceso.pid, signal.SIGKILL)
        raise


def test_sigkill_a_mitad_y_relanzar(app_home, destino, servidor, medios):
    from downloader import store  # noqa: F401  (falla hasta que exista la implementación)

    datos = medios.datos_mp4
    servidor.registrar("rapido.mp4", datos, tipo_contenido="video/mp4")
    servidor.registrar("lento.mp4", datos, tipo_contenido="video/mp4", velocidad=400_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    env = {**os.environ, "DOWNLOADER_HOME": str(app_home)}

    agregar = downloader(
        "agregar", servidor.url("rapido.mp4"), servidor.url("lento.mp4"),
        "--carpeta", destino, env=env,
    )
    assert esperar(agregar) == 0

    ejecutar = downloader("ejecutar", env=env)
    trabajo = destino / ".downloader-tmp"
    fin = time.monotonic() + LIMITE
    while True:
        parciales = list(trabajo.glob("*/lento*.part")) if trabajo.exists() else []
        if parciales and parciales[0].stat().st_size > 0.3 * len(datos):
            break
        assert ejecutar.poll() is None, "ejecutar terminó antes de poder interrumpirlo"
        assert time.monotonic() < fin, "la descarga lenta no avanzó"
        time.sleep(0.05)
    os.killpg(ejecutar.pid, signal.SIGKILL)
    esperar(ejecutar)

    conexion = sqlite3.connect(f"file:{app_home / 'cola.db'}?mode=ro", uri=True)
    filas = dict(conexion.execute("SELECT url, estado FROM descargas"))
    conexion.close()
    assert filas[servidor.url("lento.mp4")] == "downloading"
    assert filas[servidor.url("rapido.mp4")] == "completed"
    assert parciales[0].exists()
    peticiones_rapido = servidor.peticiones["rapido.mp4"]

    relanzado = downloader("ejecutar", env=env)
    assert esperar(relanzado) == 0

    publicados = sorted(p.name for p in destino.iterdir() if p.is_file())
    assert len(publicados) == 2
    for nombre in publicados:
        assert (destino / nombre).read_bytes() == datos
    assert servidor.bytes_servidos["lento.mp4"] <= 1.01 * len(datos)  # SC-002, SC-003
    assert servidor.peticiones["rapido.mp4"] == peticiones_rapido
