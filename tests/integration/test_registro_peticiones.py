"""El registro de la última petición clasifica los errores que yt-dlp entrega sin causa (R4).

Cada caso falla más veces que los 3 reintentos internos de yt-dlp, que entonces lanza un
`DownloadError` sin la excepción original.
"""

import pytest
from yt_dlp.utils import DownloadError

from downloader import ytdl
from downloader.errors import Tipo, clasificar
from downloader.models import Calidad, Descarga, Estado
from tests.support import fake_extractor


@pytest.fixture
def descargar(destino, servidor, tiempos_rapidos, monkeypatch):
    fake_extractor.instalar(monkeypatch)

    def descargar():
        descarga = Descarga(
            id=1, url=servidor.url("watch/v"), calidad=Calidad.MEJOR,
            carpeta_destino=str(destino), estado=Estado.DOWNLOADING,
        )
        with pytest.raises(DownloadError) as error:
            ytdl.descargar(
                descarga, ytdl.directorio_trabajo(descarga), None, lambda _: None,
                socket_timeout=tiempos_rapidos.socket_timeout,
            )
        assert error.value.exc_info is None or error.value.exc_info[0] is None
        return error.value

    return descargar


def test_corte_repetido_es_temporal(descargar, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, cortar_tras_bytes=1000, veces=4)
    servidor.registrar_video("v", "v.mp4")
    error = descargar()
    assert error.ultima_peticion.error is not None
    assert clasificar(error, en_linea=True) is Tipo.TEMPORAL


def test_503_repetido_es_temporal(descargar, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, errores=[503] * 4)
    servidor.registrar_video("v", "v.mp4")
    error = descargar()
    assert error.ultima_peticion.estado == 503
    assert clasificar(error, en_linea=True) is Tipo.TEMPORAL


def test_fragmento_hls_ausente_es_permanente(descargar, servidor, medios):
    manifiesto = servidor.registrar_hls("hls", medios.hls, fragmento_ausente=2)
    servidor.registrar_video("v", manifiesto)
    error = descargar()
    assert error.ultima_peticion.estado == 404
    assert clasificar(error, en_linea=True) is Tipo.PERMANENTE


def test_el_registro_empieza_vacio_en_cada_intento(descargar, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, errores=[503] * 4)
    servidor.registrar_video("v", "v.mp4")
    descargar()
    ydl = ytdl.crear_ydl({})
    assert ydl.ultima_peticion is None
