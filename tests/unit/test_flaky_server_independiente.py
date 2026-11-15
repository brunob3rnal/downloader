"""Modo independiente de `tests/support/flaky_server.py` (specs/002-desktop-app, T005)."""

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def test_sirve_un_archivo_generado_a_velocidad_limitada():
    proceso = subprocess.Popen(
        [sys.executable, "-m", "tests.support.flaky_server", "--servir", "0",
         "--limite", "200000", "--archivo", "prueba.mp4", "--tamano", "300000"],
        cwd=RAIZ, stdout=subprocess.PIPE, text=True,
    )
    try:
        linea = proceso.stdout.readline().strip()
        assert linea.startswith("sirviendo http://127.0.0.1:")
        url = linea.removeprefix("sirviendo ")
        assert url.endswith("/prueba.mp4")

        inicio = time.monotonic()
        with urllib.request.urlopen(url, timeout=10) as respuesta:
            assert respuesta.headers["Content-Type"] == "video/mp4"
            datos = respuesta.read()
        assert len(datos) == 300_000
        assert time.monotonic() - inicio >= 1.2  # 300 kB a 200 kB/s: 1,5 s

        peticion = urllib.request.Request(url, headers={"Range": "bytes=299000-"})
        with urllib.request.urlopen(peticion, timeout=10) as respuesta:
            assert respuesta.status == 206
            assert len(respuesta.read()) == 1000
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)


def test_sirve_el_contenido_de_un_archivo_real(tmp_path):
    """`--fuente`: sirve un archivo del disco, para que la descarga pase la verificación de
    ffprobe en los escenarios manuales (quickstart V2)."""
    fuente = tmp_path / "real.mp4"
    fuente.write_bytes(bytes(range(256)) * 40)
    proceso = subprocess.Popen(
        [sys.executable, "-m", "tests.support.flaky_server", "--servir", "0",
         "--fuente", str(fuente)],
        cwd=RAIZ, stdout=subprocess.PIPE, text=True,
    )
    try:
        url = proceso.stdout.readline().strip().removeprefix("sirviendo ")
        assert url.endswith("/video.mp4")
        with urllib.request.urlopen(url, timeout=10) as respuesta:
            assert respuesta.read() == fuente.read_bytes()
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)
