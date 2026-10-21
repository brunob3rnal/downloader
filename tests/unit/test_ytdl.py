import logging
from pathlib import Path

import pytest

from downloader import ytdl
from downloader.models import Calidad, Descarga, Estado, TipoDescarga


def descarga(calidad=Calidad.MEJOR, carpeta="/tmp/destino", id_=7, navegador=None):
    return Descarga(
        id=id_,
        url="https://videos.ejemplo/watch?v=abc",
        calidad=calidad,
        carpeta_destino=carpeta,
        navegador_cookies=navegador,
        tipo=TipoDescarga.VIDEO,
        estado=Estado.DOWNLOADING,
    )


def opciones(calidad=Calidad.MEJOR, navegador=None, **kwargs):
    d = descarga(calidad)
    return ytdl.opciones(d, ytdl.directorio_trabajo(d), navegador, hook=lambda _: None, **kwargs)


def test_opciones_fijas():
    o = opciones()
    assert o["continuedl"] is True
    assert o["nopart"] is False
    assert o["retries"] == 3
    assert o["fragment_retries"] == 3
    assert o["socket_timeout"] == 20
    assert o["noplaylist"] is True
    assert o["quiet"] is True
    assert isinstance(o["logger"], ytdl.Logger)
    assert len(o["progress_hooks"]) == 1


def test_skip_unavailable_fragments_explicitamente_false():
    o = opciones()
    assert "skip_unavailable_fragments" in o
    assert o["skip_unavailable_fragments"] is False


def test_socket_timeout_parametrizable():
    assert opciones(socket_timeout=1)["socket_timeout"] == 1


def test_nunca_cookiefile_y_sin_navegador_no_lee_cookies():
    o = opciones()
    assert "cookiefile" not in o
    assert "cookiesfrombrowser" not in o


def test_con_navegador_lee_cookies_en_memoria():
    o = opciones(navegador="firefox")
    assert o["cookiesfrombrowser"] == ("firefox",)
    assert "cookiefile" not in o


@pytest.mark.parametrize(
    "calidad, formato",
    [
        (Calidad.MEJOR, "bv*+ba/b"),
        (Calidad.P1080, "bv*[height<=1080]+ba/b[height<=1080]/wv*+ba/w"),
        (Calidad.P720, "bv*[height<=720]+ba/b[height<=720]/wv*+ba/w"),
        (Calidad.AUDIO, "ba/b"),
    ],
)
def test_selectores_de_formato(calidad, formato):
    o = opciones(calidad)
    assert o["format"] == formato
    claves_pp = [pp["key"] for pp in o.get("postprocessors", [])]
    if calidad is Calidad.AUDIO:
        assert claves_pp == ["FFmpegExtractAudio"]
    else:
        assert "FFmpegExtractAudio" not in claves_pp


def test_plantilla_de_salida_en_el_directorio_de_trabajo():
    d = descarga(carpeta="/tmp/destino", id_=42)
    trabajo = ytdl.directorio_trabajo(d)
    assert trabajo == Path("/tmp/destino/.downloader-tmp/42")
    o = ytdl.opciones(d, trabajo, None, hook=lambda _: None)
    assert o["outtmpl"]["default"] == str(trabajo / "descarga.%(ext)s")
    assert o["windowsfilenames"] is True


def test_logger_oculta_secretos(caplog):
    logger = opciones()["logger"]
    with caplog.at_level(logging.DEBUG, logger="downloader.ytdl"):
        logger.debug("GET https://cdn.x/v.mp4?token=secreto1")
        logger.info("Cookie: SID=secreto2")
        logger.warning("https://cdn.x/v.mp4?sig=secreto3")
        logger.error("Authorization: Bearer secreto4")
    texto = caplog.text
    assert texto
    for secreto in ("secreto1", "secreto2", "secreto3", "secreto4"):
        assert secreto not in texto


def test_crear_ydl_prefiere_hls_nativo():
    from yt_dlp.downloader import get_suitable_downloader
    from yt_dlp.downloader.hls import HlsFD

    ydl = ytdl.crear_ydl({"quiet": True})
    assert ydl.params["hls_prefer_native"] is True
    for protocolo in ("m3u8", "m3u8_native"):
        info = {"url": "http://127.0.0.1/index.m3u8", "protocol": protocolo, "to_stdout": False}
        assert get_suitable_downloader(info, ydl.params) is HlsFD


def test_el_registro_empieza_vacio():
    from downloader.errors import Fase, RegistroRed

    ydl = ytdl.crear_ydl({"quiet": True})
    assert ydl.registro == RegistroRed()
    assert ydl.registro.fase is Fase.EXTRACCION


@pytest.mark.parametrize(
    "info, externo",
    [
        ({"url": "http://127.0.0.1/v.mp4", "protocol": "https"}, False),
        ({"url": "http://127.0.0.1/i.m3u8", "protocol": "m3u8_native"}, False),
        ({"url": "http://127.0.0.1/i.m3u8", "protocol": "m3u8", "is_live": True}, True),
        ({"url": "rtmp://127.0.0.1/v", "protocol": "rtmp_ffmpeg"}, True),
    ],
    ids=["http", "hls-nativo", "hls-en-directo", "rtmp-ffmpeg"],
)
def test_dl_registra_fase_de_descarga_y_descargador_externo(info, externo, monkeypatch):
    from yt_dlp import YoutubeDL

    from downloader.errors import Fase

    monkeypatch.setattr(YoutubeDL, "dl", lambda self, *a, **k: (True, True))
    ydl = ytdl.crear_ydl({"quiet": True})
    ydl.dl("destino.mp4", {**info, "to_stdout": False})
    assert ydl.registro.fase is Fase.DESCARGA
    assert ydl.registro.externo is externo


# --- Lectura del cuerpo en trozos: no perder bytes en un corte (research R18) ---


class RespuestaCortada:
    """Cuerpo que se corta tras `n` de `total` bytes.

    `descarta=False` imita a http.client: una lectura corta devuelve lo que hay y la siguiente
    falla. `descarta=True` imita a urllib3: una lectura que no puede completarse falla y pierde
    lo que ya había recibido de ella.
    """

    def __init__(self, n, total, descarta=False):
        self.datos = bytes(i % 251 for i in range(n))
        self.total = total
        self.descarta = descarta
        self.posicion = 0
        self.headers = {"Content-Length": str(total)}

    def read(self, amt=None):
        from yt_dlp.networking.exceptions import IncompleteRead

        quedan = len(self.datos) - self.posicion
        if self.posicion == self.total:
            return b""
        if quedan == 0 or (self.descarta and quedan < amt):
            self.posicion = len(self.datos)
            raise IncompleteRead(partial=self.posicion, expected=self.total)
        trozo = self.datos[self.posicion : self.posicion + amt]
        self.posicion += len(trozo)
        return trozo


def vigilada(respuesta):
    ydl = ytdl.YoutubeDLRegistrado({"quiet": True})
    ydl._vigilar_cuerpo(respuesta)
    return ydl


def test_corte_tras_n_bytes_entrega_los_n_y_falla_en_la_lectura_siguiente():
    from yt_dlp.networking.exceptions import IncompleteRead

    respuesta = RespuestaCortada(n=10_000, total=100_000)
    ydl = vigilada(respuesta)
    assert respuesta.read(1 << 20) == respuesta.datos
    with pytest.raises(IncompleteRead):
        respuesta.read(1 << 20)
    assert isinstance(ydl.registro.ultima.error, IncompleteRead)


def test_corte_sin_bytes_falla_de_inmediato():
    from yt_dlp.networking.exceptions import IncompleteRead

    respuesta = RespuestaCortada(n=0, total=100_000)
    ydl = vigilada(respuesta)
    with pytest.raises(IncompleteRead):
        respuesta.read(1 << 20)
    assert isinstance(ydl.registro.ultima.error, IncompleteRead)


def test_si_la_libreria_descarta_el_trozo_cortado_se_pierde_como_mucho_un_trozo():
    from yt_dlp.networking.exceptions import IncompleteRead

    respuesta = RespuestaCortada(n=10_000, total=100_000, descarta=True)
    vigilada(respuesta)
    entregado = respuesta.read(1 << 20)
    assert respuesta.datos.startswith(entregado)
    assert len(entregado) >= 10_000 - ytdl.TROZO_LECTURA
    with pytest.raises(IncompleteRead):
        respuesta.read(1 << 20)


def test_sin_corte_entrega_lo_pedido_en_una_llamada():
    respuesta = RespuestaCortada(n=100_000, total=100_000)
    vigilada(respuesta)
    assert respuesta.read(50_000) == respuesta.datos[:50_000]
    assert respuesta.read(1 << 20) == respuesta.datos[50_000:]
    assert respuesta.read(1 << 20) == b""
