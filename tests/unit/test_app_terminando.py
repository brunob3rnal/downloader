"""Qué está terminando el trabajador mientras se detiene (specs/002-desktop-app, SC-004
enmendado; research R7).

En Windows, Pausar espera a que ffmpeg acabe de unir o ffprobe de verificar. La ventana lo
dice en lugar de solo "Deteniendo…": lo deduce de los procesos hijos del trabajador.
"""

import pytest

from downloader.app import procesos

UNION = ["ffmpeg", "-y", "-loglevel", "repeat+info", "-i", "file:v.f1.mp4", "-i",
         "file:v.f2.m4a", "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", "file:v.temp.mp4"]
VERIFICACION = ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
                "format=duration:stream=nb_read_packets", "-of", "json", "descarga.mp4"]


@pytest.mark.parametrize("nombre, orden, que", [
    ("ffmpeg", UNION, "union"),
    ("ffmpeg.exe", UNION, "union"),
    ("ffprobe.exe", VERIFICACION, "verificacion"),
    ("ffmpeg", ["ffmpeg", "-version"], None),  # la comprobación de versión de yt-dlp
    ("ffprobe", ["ffprobe", "-version"], None),
    ("ffmpeg", ["ffmpeg", "-i", "file:a.m4a", "-c", "copy", "file:b.m4a"], None),  # otra cosa
    ("python", ["python", "-m", "x"], None),
])
def test_que_hace_un_proceso(nombre, orden, que):
    assert procesos.que_termina(nombre, orden) == que


class _Hijo:
    def __init__(self, nombre, orden):
        self._nombre, self._orden = nombre, orden

    def name(self):
        return self._nombre

    def cmdline(self):
        return self._orden


class _Trabajador:
    def __init__(self, *hijos):
        self.hijos = list(hijos)

    def children(self, recursive=False):
        assert recursive
        return self.hijos


def test_terminando_mira_los_hijos_del_trabajador(monkeypatch):
    trabajador = _Trabajador(_Hijo("ffmpeg", ["ffmpeg", "-version"]), _Hijo("ffmpeg", UNION))
    monkeypatch.setattr(procesos.psutil, "Process", lambda pid: trabajador)
    assert procesos.terminando(4321) == procesos.TERMINANDO["union"]
    trabajador.hijos = [_Hijo("ffprobe", VERIFICACION)]
    assert procesos.terminando(4321) == procesos.TERMINANDO["verificacion"]
    trabajador.hijos = []
    assert procesos.terminando(4321) is None


def test_textos():
    assert procesos.TERMINANDO == {"union": "Terminando la unión del vídeo…",
                                   "verificacion": "Terminando la verificación del archivo…"}


def test_si_el_trabajador_ya_no_existe_none(monkeypatch):
    def no_existe(pid):
        raise procesos.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(procesos.psutil, "Process", no_existe)
    assert procesos.terminando(4321) is None
