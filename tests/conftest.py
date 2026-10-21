import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from tests.support.flaky_server import FlakyServer

FFMPEG_DISPONIBLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def pytest_runtest_setup(item):
    if item.get_closest_marker("ffmpeg") and not FFMPEG_DISPONIBLE:
        pytest.skip("requiere ffmpeg y ffprobe en el PATH")


class MonitorFalso:
    """Monitor de red controlado a mano; mismo protocolo que `downloader.network.MonitorRed`."""

    def __init__(self):
        self.en_linea = threading.Event()
        self.en_linea.set()

    def iniciar(self):
        pass

    def detener(self):
        pass

    def poner_en_linea(self):
        self.en_linea.set()

    def poner_sin_conexion(self):
        self.en_linea.clear()


@pytest.fixture
def app_home(tmp_path, monkeypatch):
    directorio = tmp_path / "app"
    directorio.mkdir()
    monkeypatch.setenv("DOWNLOADER_HOME", str(directorio))
    return directorio


@pytest.fixture
def destino(tmp_path):
    carpeta = tmp_path / "destino"
    carpeta.mkdir()
    return carpeta


@pytest.fixture
def servidor():
    servidor = FlakyServer()
    yield servidor
    servidor.cerrar()


@pytest.fixture
def monitor_falso():
    return MonitorFalso()


@pytest.fixture
def tiempos_rapidos():
    return SimpleNamespace(backoff_base=0.05, backoff_tope=0.5, socket_timeout=1)


@pytest.fixture(scope="session")
def medios(tmp_path_factory):
    """MP4 válido de unos 2 MB y su versión HLS en 5 fragmentos, generados con ffmpeg."""
    if not FFMPEG_DISPONIBLE:
        pytest.skip("requiere ffmpeg y ffprobe en el PATH")
    directorio = tmp_path_factory.mktemp("medios")
    mp4 = directorio / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
         "-t", "10", "-c:v", "libx264", "-b:v", "1500k", "-g", "50",
         "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(mp4)],
        check=True,
    )
    hls = directorio / "hls"
    hls.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-c", "copy",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
         "-hls_segment_filename", str(hls / "fragmento%d.ts"), str(hls / "index.m3u8")],
        check=True,
    )
    return SimpleNamespace(mp4=mp4, datos_mp4=mp4.read_bytes(), hls=hls)


@pytest.fixture
def cli(app_home, monkeypatch):
    """Ejecuta la app Typer en proceso con el extractor de prueba registrado."""
    from typer.testing import CliRunner

    from downloader.cli import app
    from tests.support import fake_extractor

    fake_extractor.instalar(monkeypatch)
    runner = CliRunner()

    def invocar(*argumentos):
        return runner.invoke(app, [str(a) for a in argumentos])

    return invocar
