import logging
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

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
    # Un solo reintento interno: el fallo sube pronto a los reintentos del trabajador (R21).
    assert o["retries"] == 1
    assert o["fragment_retries"] == 1
    assert o["socket_timeout"] == 15
    assert o["downloader_vigilancia"] is None
    assert o["noplaylist"] is True
    assert o["quiet"] is True
    assert isinstance(o["logger"], ytdl.Logger)
    assert len(o["progress_hooks"]) == 1


def test_la_vigilancia_llega_a_las_opciones():
    vigilancia = ytdl.Vigilancia(segundos=10, velocidad_minima=512)
    assert opciones(vigilancia=vigilancia)["downloader_vigilancia"] is vigilancia


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


@pytest.mark.ytdlp_internals  # R1
def test_crear_ydl_prefiere_hls_nativo():
    from yt_dlp.downloader import get_suitable_downloader
    from yt_dlp.downloader.hls import HlsFD

    ydl = ytdl.crear_ydl({"quiet": True})
    assert ydl.params["hls_prefer_native"] is True
    for protocolo in ("m3u8", "m3u8_native"):
        info = {"url": "http://127.0.0.1/index.m3u8", "protocol": protocolo, "to_stdout": False}
        assert get_suitable_downloader(info, ydl.params) is HlsFD


@pytest.mark.ytdlp_internals  # R4
def test_el_registro_empieza_vacio():
    from downloader.errors import Fase, RegistroRed

    ydl = ytdl.crear_ydl({"quiet": True})
    assert ydl.registro == RegistroRed()
    assert ydl.registro.fase is Fase.EXTRACCION


@pytest.mark.ytdlp_internals  # R4
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


BLOQUE_MAXIMO = 4 * 1024 * 1024  # el bloque más grande que pide yt-dlp (research R18, R20)


@pytest.mark.ytdlp_internals  # R20: `best_block_size` no pasa de 4 MiB
def test_yt_dlp_no_pide_bloques_de_mas_de_4_mib():
    from yt_dlp.downloader.common import FileDownloader

    assert FileDownloader.best_block_size(0.0001, 1 << 30) == BLOQUE_MAXIMO
    assert FileDownloader.best_block_size(1.0, 1 << 30) == BLOQUE_MAXIMO


def test_con_un_bloque_de_4_mib_no_acumula_mas_de_lo_pedido():
    """Lo acumulado sin entregar no pasa del bloque pedido: es lo más que pierde una
    terminación abrupta en mitad de un bloque (SC-002, research R20)."""
    from yt_dlp.networking.exceptions import IncompleteRead

    respuesta = RespuestaCortada(n=BLOQUE_MAXIMO + 100_000, total=2 * BLOQUE_MAXIMO)
    vigilada(respuesta)
    assert respuesta.read(BLOQUE_MAXIMO) == respuesta.datos[:BLOQUE_MAXIMO]
    assert respuesta.posicion == BLOQUE_MAXIMO  # no leyó más de la conexión
    assert respuesta.read(BLOQUE_MAXIMO) == respuesta.datos[BLOQUE_MAXIMO:]  # corte: lo recibido
    with pytest.raises(IncompleteRead):
        respuesta.read(BLOQUE_MAXIMO)


# --- Ctrl+C a mitad de un bloque: entregar lo recibido (research R18) ---


class RespuestaInterrumpida:
    """Cuerpo de `total` bytes cuya lectura recibe Ctrl+C una vez, tras entregar `n` bytes."""

    def __init__(self, n, total=100_000):
        self.datos = bytes(i % 251 for i in range(total))
        self.n = n
        self.posicion = 0
        self.interrumpida = False
        self.headers = {"Content-Length": str(total)}

    def read(self, amt=None):
        if self.posicion == self.n and not self.interrumpida:
            self.interrumpida = True
            raise KeyboardInterrupt
        hasta = len(self.datos) if self.interrumpida else self.n
        trozo = self.datos[self.posicion : min(hasta, self.posicion + amt)]
        self.posicion += len(trozo)
        return trozo


def aplazada(ydl, n=10_000):
    """Lee de una `RespuestaInterrumpida` hasta que el envoltorio aplaza la interrupción."""
    respuesta = RespuestaInterrumpida(n)
    ydl._vigilar_cuerpo(respuesta)
    try:
        entregado = respuesta.read(1 << 20)
    except KeyboardInterrupt:  # sin esto, pytest pararía la sesión entera
        pytest.fail("Ctrl+C se relanzó sin entregar lo recibido")
    assert entregado == respuesta.datos[:n]
    return respuesta


def test_ctrl_c_tras_n_bytes_entrega_los_n_y_se_relanza_en_la_lectura_siguiente():
    ydl = ytdl.YoutubeDLRegistrado({"quiet": True})
    respuesta = aplazada(ydl)
    with pytest.raises(KeyboardInterrupt):
        respuesta.read(1 << 20)
    assert respuesta.read(1 << 20) == respuesta.datos[10_000:]  # se relanza una sola vez


def test_ctrl_c_sin_bytes_se_relanza_de_inmediato():
    respuesta = RespuestaInterrumpida(n=0)
    vigilada(respuesta)
    with pytest.raises(KeyboardInterrupt):
        respuesta.read(1 << 20)


def test_ctrl_c_aplazado_se_relanza_al_leer_otra_respuesta():
    """En HLS y DASH cada fragmento tiene su respuesta: la del fragmento terminado no se
    vuelve a leer."""
    ydl = ytdl.YoutubeDLRegistrado({"quiet": True})
    aplazada(ydl)
    otra = RespuestaCortada(n=1_000, total=1_000)
    ydl._vigilar_cuerpo(otra)
    with pytest.raises(KeyboardInterrupt):
        otra.read(1 << 20)


def test_ctrl_c_aplazado_se_relanza_en_la_peticion_siguiente(ydl_con_respuestas):
    ydl, respuestas = ydl_con_respuestas
    aplazada(ydl)
    respuestas.append(RespuestaFalsa())
    with pytest.raises(KeyboardInterrupt):
        ydl.urlopen("http://127.0.0.1/fragmento2.ts")


def test_ctrl_c_aplazado_se_relanza_al_terminar_la_descarga(ydl_con_respuestas):
    """Si lo entregado completa el archivo, yt-dlp no vuelve a leer (`HttpFD`)."""
    ydl, _ = ydl_con_respuestas
    aplazada(ydl)
    with pytest.raises(KeyboardInterrupt):
        ydl.dl("descarga.mp4", dict(INFO_HTTP))


def test_ctrl_c_aplazado_se_relanza_al_terminar_la_extraccion(monkeypatch):
    from yt_dlp import YoutubeDL

    def extract_info(self, url, download=True):
        aplazada(self)
        return {"id": "v", "title": "Video"}

    monkeypatch.setattr(YoutubeDL, "extract_info", extract_info)
    with pytest.raises(KeyboardInterrupt):
        ytdl.extraer_plano("http://127.0.0.1:9/v.mp4")


# --- Cierre de las respuestas abiertas al extraer (research R19) ---


class RespuestaFalsa:
    def __init__(self, cerrada=False):
        self.status = 200
        self.headers = {}
        self.cerrada = cerrada
        self.cierres = 0

    def read(self, amt=None):
        return b""

    def close(self):
        if self.cerrada:
            raise ValueError("I/O operation on closed file")
        self.cerrada = True
        self.cierres += 1


@pytest.fixture
def ydl_con_respuestas(monkeypatch):
    """`YoutubeDLRegistrado` cuyo `urlopen` devuelve, en orden, las respuestas falsas dadas."""
    from yt_dlp import YoutubeDL

    respuestas = []
    monkeypatch.setattr(YoutubeDL, "urlopen", lambda self, req: respuestas.pop(0))
    monkeypatch.setattr(YoutubeDL, "dl", lambda self, *a, **k: (True, True))
    ydl = ytdl.crear_ydl({"quiet": True})
    return ydl, respuestas


INFO_HTTP = {"url": "http://127.0.0.1/v.mp4", "protocol": "https", "to_stdout": False}


@pytest.mark.ytdlp_internals  # R19
def test_dl_cierra_las_respuestas_abiertas_al_extraer(ydl_con_respuestas):
    ydl, respuestas = ydl_con_respuestas
    sondeo = RespuestaFalsa()
    respuestas.append(sondeo)
    ydl.urlopen("http://127.0.0.1/v.mp4")
    assert not sondeo.cerrada
    ydl.dl("destino.mp4", INFO_HTTP)
    assert sondeo.cerrada and sondeo.cierres == 1


@pytest.mark.ytdlp_internals  # R19
def test_una_respuesta_ya_cerrada_no_da_error(ydl_con_respuestas):
    ydl, respuestas = ydl_con_respuestas
    respuestas.append(RespuestaFalsa())
    ydl.urlopen("http://127.0.0.1/v.mp4")
    respuestas_abiertas = [r() for r in ydl._respuestas_extraccion]
    respuestas_abiertas[0].cerrada = True  # la cerró otro: un segundo `close` lanza
    ydl.dl("destino.mp4", INFO_HTTP)
    ydl.close()


@pytest.mark.ytdlp_internals  # R19
def test_no_toca_las_respuestas_abiertas_por_la_descarga(ydl_con_respuestas):
    ydl, respuestas = ydl_con_respuestas
    ydl.dl("destino.mp4", INFO_HTTP)
    de_la_descarga = RespuestaFalsa()
    respuestas.append(de_la_descarga)
    ydl.urlopen("http://127.0.0.1/v.mp4")
    ydl.dl("destino.f2.mp4", INFO_HTTP)  # p. ej. el segundo formato de una fusión
    ydl.close()
    assert not de_la_descarga.cerrada


@pytest.mark.ytdlp_internals  # R19
def test_close_cierra_las_respuestas_de_una_extraccion_sin_descarga(ydl_con_respuestas):
    ydl, respuestas = ydl_con_respuestas
    sondeo = RespuestaFalsa()
    respuestas.append(sondeo)
    ydl.urlopen("http://127.0.0.1/v.mp4")
    ydl.close()
    assert sondeo.cerrada


# --- cookies del navegador que no se pueden leer (research R25) -------------------------

COOKIES = [
    # (fallo de `NavegadorFalso`, navegador, fragmento de la causa)
    ("no_encontrada", "firefox", "no se encontró su base de datos de cookies"),
    ("bloqueada", "chrome", "su base de datos de cookies está bloqueada o sin permiso"),
    ("bloqueada", "edge", "cierre el navegador del todo"),
    ("sin_descifrar", "chrome", "no se pudieron descifrar"),
    ("sin_descifrar", "edge", "--cookies-de firefox"),
    ("plataforma", "safari", "unsupported platform: linux"),
    ("otro", "brave", "no se pudo leer el perfil"),
]


@pytest.mark.ytdlp_internals  # R25
@pytest.mark.parametrize("fallo, navegador, causa", COOKIES)
def test_cookies_ilegibles_indican_el_navegador_y_la_causa(monkeypatch, fallo, navegador, causa):
    from downloader import errors
    from tests.support import navegador_falso

    falso = navegador_falso.NavegadorFalso().instalar(monkeypatch)
    falso.fallo = getattr(navegador_falso, fallo)
    # La carga de cookies falla antes de la primera petición: el puerto 9 no se llega a usar.
    with pytest.raises(errors.CookiesIlegibles) as error:
        ytdl.extraer_plano("http://127.0.0.1:9/watch/v", navegador, socket_timeout=1)
    mensaje = str(error.value)
    assert mensaje.startswith(f"No se pudieron leer las cookies de {navegador}: ")
    assert causa in mensaje
    assert "ERROR:" not in mensaje and "failed to load cookies" not in mensaje
    assert falso.leidos == [navegador]
    assert errors.clasificar_detalle(error.value, en_linea=True).tipo is errors.Tipo.PERMANENTE


@pytest.mark.ytdlp_internals  # R25
def test_los_demas_errores_con_cookies_no_se_confunden(monkeypatch):
    """Con las cookies leídas, un fallo de red sigue siendo un fallo de red."""
    from downloader import errors
    from tests.support import navegador_falso

    navegador_falso.NavegadorFalso().instalar(monkeypatch)
    with pytest.raises(DownloadError) as error:
        ytdl.extraer_plano("http://127.0.0.1:9/watch/v", "firefox", socket_timeout=1)
    assert not isinstance(error.value, errors.CookiesIlegibles)
    assert errors.clasificar_detalle(error.value, en_linea=True).tipo is errors.Tipo.TEMPORAL


def test_navegadores_admitidos_son_los_de_yt_dlp():
    from yt_dlp.cookies import SUPPORTED_BROWSERS

    assert ytdl.NAVEGADORES == SUPPORTED_BROWSERS
    assert {"firefox", "chrome", "edge"} <= ytdl.NAVEGADORES


# --- Manifiestos HLS con DRM o un cifrado no soportado (research R5) ---


def manifiesto_vigilado(cuerpo: bytes):
    import io

    from yt_dlp.networking.common import Response

    ydl = ytdl.YoutubeDLRegistrado({"quiet": True})
    respuesta = Response(io.BytesIO(cuerpo), "http://127.0.0.1/maestro.m3u8", {}, 200)
    ydl._vigilar_manifiesto(respuesta)
    return ydl, respuesta


MAESTRO_WIDEVINE = (
    b"#EXTM3U\n"
    b'#EXT-X-SESSION-KEY:METHOD=AES-128,URI="clave",'
    b'KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"\n'
    + b"#EXT-X-STREAM-INF:BANDWIDTH=1000000\nindex.m3u8\n" * 40
)


def test_el_rechazo_se_lanza_al_leer_las_lineas_completas():
    from downloader.errors import ErrorPermanente

    ydl, respuesta = manifiesto_vigilado(MAESTRO_WIDEVINE)
    with pytest.raises(ErrorPermanente, match=r"DRM \(Widevine\)"):
        respuesta.read(512)  # como el extractor, que lee primero 512 bytes
    assert isinstance(ydl.registro.ultima.error, ErrorPermanente)  # registrado como última


def test_process_info_lo_vuelve_a_lanzar_aunque_el_extractor_lo_capture():
    from downloader.errors import ErrorPermanente

    ydl, respuesta = manifiesto_vigilado(MAESTRO_WIDEVINE)
    try:
        respuesta.read()
    except ErrorPermanente:
        pass  # un extractor que sigue con otros formatos
    with pytest.raises(ErrorPermanente, match=r"DRM \(Widevine\)"):
        ydl.process_info({"id": "v", "title": "v", "url": "http://127.0.0.1/v.mp4", "ext": "mp4"})


@pytest.mark.parametrize(
    "cuerpo",
    [
        b"\x00\x00\x00\x18ftypmp42" + b'#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="identity"\n' * 3,
        b"#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"clave.key\"\n#EXTINF:2,\nf0.ts\n",
        b"",
    ],
    ids=["medios", "aes-128", "vacio"],
)
def test_los_demas_cuerpos_se_entregan_sin_cambios(cuerpo):
    _, respuesta = manifiesto_vigilado(cuerpo)
    assert respuesta.read(7) + respuesta.read() == cuerpo
