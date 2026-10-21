"""La calidad pedida no existe (edge case de la spec; research R6)."""

import pytest
from yt_dlp import YoutubeDL

from downloader import ytdl
from downloader.models import Calidad, Estado
from downloader.store import Store


def elegida(calidad, alturas):
    """Formato que elige yt-dlp con nuestro selector, sin red."""
    info = {
        "id": "x", "title": "t", "extractor": "prueba", "extractor_key": "Prueba",
        "webpage_url": "http://127.0.0.1/watch/x",
        "formats": [
            {"format_id": f"{h}p", "url": f"http://127.0.0.1/{h}.mp4", "ext": "mp4",
             "height": h, "vcodec": "h264", "acodec": "aac"}
            for h in alturas
        ],
    }
    with YoutubeDL({"format": ytdl.FORMATOS[calidad], "quiet": True}) as ydl:
        return ydl.process_ie_result(info, download=False)


def test_elige_la_mas_cercana_inferior_y_avisa():
    info = elegida(Calidad.P720, [240, 360, 480])
    assert info["height"] == 480
    aviso = ytdl.aviso_calidad(Calidad.P720, info)
    assert "720p" in aviso and "480p" in aviso


def test_solo_superiores_usa_la_peor_y_avisa():
    info = elegida(Calidad.P1080, [1440, 2160])
    assert info["height"] == 1440
    aviso = ytdl.aviso_calidad(Calidad.P1080, info)
    assert "1080p" in aviso and "1440p" in aviso


def test_sin_aviso_si_coincide_o_no_aplica():
    assert ytdl.aviso_calidad(Calidad.P720, elegida(Calidad.P720, [480, 720, 1080])) is None
    assert ytdl.aviso_calidad(Calidad.MEJOR, {"height": 480}) is None
    assert ytdl.aviso_calidad(Calidad.AUDIO, {"height": None}) is None
    assert ytdl.aviso_calidad(Calidad.P720, {}) is None


@pytest.mark.ffmpeg
def test_el_aviso_se_guarda_y_se_muestra(cli, app_home, destino, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v", "v.mp4", alturas=[360, 480])
    cli("agregar", servidor.url("watch/v"), "--calidad", "720p", "--carpeta", destino)
    resultado = cli("ejecutar")
    assert resultado.exit_code == 0, resultado.output
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.COMPLETED
    assert "480p" in descarga.ultimo_aviso
    assert "#1 aviso  " in resultado.output
