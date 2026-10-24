import errno
import sys

import pytest
from yt_dlp.networking.exceptions import HTTPError, IncompleteRead, TransportError
from yt_dlp.networking.common import Response
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from downloader.errors import (
    Clasificacion,
    ErrorFatal,
    ErrorPermanente,
    Fase,
    RegistroRed,
    Tipo,
    UltimaPeticion,
    clasificar,
    clasificar_detalle,
    ocultar_secretos,
)


def error_http(codigo):
    import io

    respuesta = Response(io.BytesIO(b""), "http://127.0.0.1/v.mp4", {}, status=codigo)
    return HTTPError(respuesta)


def download_error(causa):
    """`DownloadError` como lo lanza yt-dlp: con la excepción original en `exc_info`."""
    try:
        raise causa
    except BaseException:
        return DownloadError(f"ERROR: {causa}", sys.exc_info())


@pytest.mark.parametrize(
    "exc",
    [
        TransportError("conexión reiniciada"),
        IncompleteRead(partial=10, expected=100),
        TimeoutError("timed out"),
        ConnectionError("conexión rechazada"),
        ConnectionResetError(errno.ECONNRESET, "reset"),
        error_http(500),
        error_http(503),
        error_http(403),
        error_http(410),
    ],
    ids=lambda e: type(e).__name__ + str(getattr(e, "status", "")),
)
def test_temporales(exc):
    assert clasificar(exc, en_linea=True) is Tipo.TEMPORAL


@pytest.mark.parametrize(
    "exc",
    [
        download_error(TransportError("reset")),
        download_error(error_http(503)),
        ExtractorError("Unable to download webpage", cause=TransportError("reset")),
        download_error(ExtractorError("Unable to download JSON", cause=TimeoutError())),
    ],
)
def test_recorre_la_cadena_exc_info_y_cause(exc):
    assert clasificar(exc, en_linea=True) is Tipo.TEMPORAL


def test_encadenado_con_raise_from():
    try:
        try:
            raise ConnectionError("caída")
        except ConnectionError as interno:
            raise RuntimeError("envoltorio") from interno
    except RuntimeError as exc:
        assert clasificar(exc, en_linea=True) is Tipo.TEMPORAL


@pytest.mark.parametrize(
    "exc",
    [
        UnsupportedError("http://ejemplo.invalido/x"),
        ExtractorError("Video privado", expected=True),
        ExtractorError("This video is DRM protected", expected=True),
        download_error(ExtractorError("Video borrado", expected=True)),
        ErrorPermanente("Contenido en directo: no se puede descargar"),
        ErrorPermanente("La carpeta de destino /no/existe no existe"),
        ErrorPermanente("Sin permiso de escritura en /root"),
        error_http(404),
    ],
    ids=lambda e: str(e)[:30],
)
def test_permanentes(exc):
    assert clasificar(exc, en_linea=True) is Tipo.PERMANENTE


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.ENOSPC, "No space left on device"),
        download_error(OSError(errno.ENOSPC, "No space left on device")),
        ErrorFatal("El archivo de estado de la cola está corrupto"),
        ErrorFatal("ffmpeg no está instalado"),
        ErrorFatal("ffprobe no está instalado"),
    ],
    ids=lambda e: str(e)[:30],
)
def test_fatales(exc):
    assert clasificar(exc, en_linea=True) is Tipo.FATAL


def test_fatal_aunque_no_haya_red():
    assert clasificar(OSError(errno.ENOSPC, "lleno"), en_linea=False) is Tipo.FATAL


@pytest.mark.parametrize(
    "exc",
    [
        ExtractorError("Unable to download webpage", expected=True),
        UnsupportedError("http://x"),
        DownloadError("ERROR: fragment 3 not found, unable to continue"),
        ValueError("algo raro"),
    ],
)
def test_sin_conexion_todo_es_temporal(exc):
    assert clasificar(exc, en_linea=False) is Tipo.TEMPORAL


def test_otro_error_es_permanente_con_red():
    # Un fragmento HLS ausente: yt-dlp no se lo salta y abandona sin causa encadenada.
    assert clasificar(
        DownloadError("ERROR: fragment 3 not found, unable to continue"), en_linea=True
    ) is Tipo.PERMANENTE
    assert clasificar(ValueError("algo raro"), en_linea=True) is Tipo.PERMANENTE


@pytest.mark.parametrize(
    "texto, secreto",
    [
        ("Cookie: SID=abc123secreto; HSID=xyz", "abc123secreto"),
        ("cookie: SID=abc123secreto", "abc123secreto"),
        ("Authorization: Bearer abc123secreto", "abc123secreto"),
        ("{'Cookie': 'SID=abc123secreto', 'Accept': '*/*'}", "abc123secreto"),
        ('{"Authorization": "Basic abc123secreto"}', "abc123secreto"),
        ("https://cdn.x/v.mp4?token=abc123secreto&itag=22", "abc123secreto"),
        ("https://cdn.x/v.mp4?itag=22&sig=abc123secreto", "abc123secreto"),
        ("https://cdn.x/v.mp4?signature=abc123secreto", "abc123secreto"),
        ("https://cdn.x/v.mp4?a=1&key=abc123secreto#t", "abc123secreto"),
        ("https://cdn.x/v.mp4?AUTH=abc123secreto", "abc123secreto"),
        # URLs prefirmadas (T078): el nombre del parámetro contiene la palabra del secreto.
        ("https://s3.x/v.mp4?X-Amz-Date=1&X-Amz-Signature=abc123secreto", "abc123secreto"),
        ("https://s3.x/v.mp4?X-Amz-Security-Token=abc123secreto", "abc123secreto"),
        ("https://s3.x/v.mp4?X-Amz-Credential=abc123secreto%2F2026", "abc123secreto"),
        ("https://cf.x/v.mp4?Expires=1&Signature=abc123secreto&Key-Pair-Id=K2", "abc123secreto"),
        ("https://cf.x/v.mp4?Policy=abc123secreto&Signature=x", "abc123secreto"),
        ("https://cf.x/v.mp4?Expires=1&Key-Pair-Id=abc123secreto", "abc123secreto"),
        ("https://gv.x/videoplayback?itag=22&lsig=abc123secreto", "abc123secreto"),
        ("https://api.x/v.mp4?access_token=abc123secreto", "abc123secreto"),
        ("https://api.x/v.mp4?api_key=abc123secreto", "abc123secreto"),
        ("https://ak.x/v.m3u8?hdnts=exp%3D1~hmac%3Dabc123secreto", "abc123secreto"),
    ],
)
def test_ocultar_secretos(texto, secreto):
    resultado = ocultar_secretos(texto)
    assert secreto not in resultado


def test_ocultar_secretos_conserva_el_resto():
    resultado = ocultar_secretos(
        "ERROR: HTTP Error 403 en https://cdn.x/v.mp4?itag=22&token=abc123&mime=video"
    )
    assert "abc123" not in resultado
    assert "HTTP Error 403" in resultado
    assert "itag=22" in resultado
    assert "mime=video" in resultado
    assert "monkey=1" in ocultar_secretos("https://x/?monkey=1")
    firmada = ocultar_secretos("https://s3.x/v.mp4?X-Amz-Date=20261024&X-Amz-Expires=300"
                               "&X-Amz-Signature=abc")
    assert "X-Amz-Date=20261024" in firmada and "X-Amz-Expires=300" in firmada
    assert "X-Amz-Signature=***" in firmada


# --- Tabla única de R4: código HTTP de la cadena o, si no hay, del registro ---

EXTRACCION, DESCARGA = Fase.EXTRACCION, Fase.DESCARGA
TEMPORAL, PERMANENTE = Tipo.TEMPORAL, Tipo.PERMANENTE

# (señal, fase, tipo esperado, ambiguo)
TABLA = [
    *[(senal, fase, TEMPORAL, False)
      for fase in (EXTRACCION, DESCARGA)
      for senal in ("transporte", "timeout", "conexion", "incompleto", 429, 500, 503)],
    (404, EXTRACCION, PERMANENTE, False),
    (404, DESCARGA, PERMANENTE, False),
    (403, EXTRACCION, PERMANENTE, False),
    (410, EXTRACCION, PERMANENTE, False),
    (403, DESCARGA, TEMPORAL, True),
    (410, DESCARGA, TEMPORAL, True),
    # 416 que llega como error (sin `Content-Range */TOTAL` utilizable, research R23).
    (416, EXTRACCION, PERMANENTE, False),
    (416, DESCARGA, TEMPORAL, True),
]


def excepcion(senal):
    return {
        "transporte": lambda: TransportError("Connection reset by peer"),
        "timeout": lambda: TimeoutError("timed out"),
        "conexion": lambda: ConnectionError("refused"),
        "incompleto": lambda: IncompleteRead(partial=400, expected=1000),
    }.get(senal, lambda: error_http(senal))()


def desde_la_cadena(senal, fase):
    """Como llega cuando yt-dlp conserva la causa: al extraer, envuelta en `ExtractorError`."""
    causa = excepcion(senal)
    if fase is EXTRACCION:
        causa = ExtractorError("Unable to download webpage", cause=causa)
    return download_error(causa)


def sin_causa(ultima=None, fase=DESCARGA, externo=False, registro=True):
    """`DownloadError` como el que lanza yt-dlp al agotar sus reintentos: sin `exc_info`."""
    error = DownloadError("ERROR: [download] Got error: ... Giving up after 3 retries")
    if registro:
        error.registro = RegistroRed(ultima=ultima, fase=fase, externo=externo)
    return error


def desde_el_registro(senal, fase):
    if isinstance(senal, int):
        return sin_causa(UltimaPeticion(estado=senal), fase)
    return sin_causa(UltimaPeticion(error=excepcion(senal)), fase)


ids_tabla = [f"{senal}-{fase}" for senal, fase, *_ in TABLA]


@pytest.mark.parametrize("senal, fase, tipo, ambiguo", TABLA, ids=ids_tabla)
@pytest.mark.parametrize("origen", [desde_la_cadena, desde_el_registro], ids=["cadena", "registro"])
def test_tabla_de_r4(origen, senal, fase, tipo, ambiguo):
    resultado = clasificar_detalle(origen(senal, fase), en_linea=True)
    assert resultado.tipo is tipo
    assert resultado.ambiguo is ambiguo
    if ambiguo:
        assert resultado.motivo == f"HTTP {senal}"


@pytest.mark.parametrize("senal", [403, 404, 410, 429, 503, "transporte"])
@pytest.mark.parametrize("fase", [EXTRACCION, DESCARGA])
def test_sin_conexion_siempre_temporal(senal, fase):
    for error in (desde_la_cadena(senal, fase), desde_el_registro(senal, fase)):
        resultado = clasificar_detalle(error, en_linea=False)
        assert resultado.tipo is TEMPORAL
        assert not resultado.ambiguo


@pytest.mark.parametrize(
    "error",
    [
        sin_causa(UltimaPeticion(estado=200), EXTRACCION),
        sin_causa(UltimaPeticion(estado=206), DESCARGA),
        sin_causa(None, DESCARGA),
        sin_causa(registro=False),
        sin_causa(UltimaPeticion(error=ValueError("petición mal formada")), DESCARGA),
    ],
    ids=["200-extraccion", "206-descarga", "vacio", "sin-registro", "error-no-de-red"],
)
def test_registro_correcto_o_vacio_es_permanente(error):
    assert clasificar_detalle(error, en_linea=True) == Clasificacion(PERMANENTE)


@pytest.mark.parametrize("ultima", [UltimaPeticion(estado=200), None], ids=["correcto", "vacio"])
def test_descargador_externo_con_registro_correcto_o_vacio_es_temporal_ambiguo(ultima):
    resultado = clasificar_detalle(sin_causa(ultima, DESCARGA, externo=True), en_linea=True)
    assert resultado.tipo is TEMPORAL
    assert resultado.ambiguo
    assert "ffmpeg" in resultado.motivo


def test_descargador_externo_no_oculta_una_senal_de_red():
    resultado = clasificar_detalle(
        sin_causa(UltimaPeticion(estado=404), DESCARGA, externo=True), en_linea=True
    )
    assert resultado.tipo is PERMANENTE


def test_la_cadena_prevalece_sobre_el_registro():
    error = download_error(error_http(503))
    error.registro = RegistroRed(UltimaPeticion(estado=404), DESCARGA)
    assert clasificar(error, en_linea=True) is TEMPORAL
    error = download_error(error_http(404))
    error.registro = RegistroRed(UltimaPeticion(estado=503), DESCARGA)
    assert clasificar(error, en_linea=True) is PERMANENTE


def test_un_permanente_reconocido_prevalece_sobre_el_registro():
    error = download_error(ExtractorError("Video privado", expected=True))
    error.registro = RegistroRed(UltimaPeticion(estado=503), EXTRACCION)
    assert clasificar(error, en_linea=True) is PERMANENTE


def test_la_fase_de_la_cadena_prevalece_sobre_la_del_registro():
    error = desde_la_cadena(403, EXTRACCION)
    error.registro = RegistroRed(UltimaPeticion(estado=403), DESCARGA)
    assert clasificar(error, en_linea=True) is PERMANENTE


def test_fatal_prevalece_sobre_el_registro():
    error = download_error(OSError(errno.ENOSPC, "lleno"))
    error.registro = RegistroRed(UltimaPeticion(estado=503), DESCARGA)
    assert clasificar(error, en_linea=True) is Tipo.FATAL


# --- Conexión estancada (research R21) ---


def test_sin_datos_es_temporal_por_estancamiento():
    from downloader.errors import SinDatos, clasificar_detalle

    clasificacion = clasificar_detalle(SinDatos("sin datos hace 30 s"), en_linea=True)
    assert clasificacion.tipo is Tipo.TEMPORAL
    assert clasificacion.estancamiento and not clasificacion.ambiguo
    assert clasificacion.motivo == "sin datos hace 30 s"


def test_sin_datos_en_el_registro_de_un_download_error_sin_causa():
    from downloader.errors import (
        Fase, RegistroRed, SinDatos, UltimaPeticion, clasificar_detalle,
    )

    error = DownloadError("ERROR: [download] Got error: sin datos. Giving up after 1 retries")
    error.registro = RegistroRed(UltimaPeticion(error=SinDatos("sin datos hace 31 s")),
                                 Fase.DESCARGA)
    clasificacion = clasificar_detalle(error, en_linea=True)
    assert clasificacion.estancamiento
    assert clasificacion.motivo == "sin datos hace 31 s"


# --- consejo para un acceso denegado al extraer (T076; SC-005, FR-016) ------------------


@pytest.mark.parametrize("senal", [403, 410])
@pytest.mark.parametrize("origen", [desde_la_cadena, desde_el_registro], ids=["cadena", "registro"])
def test_acceso_denegado_al_extraer_sugiere_cookies_de(origen, senal):
    resultado = clasificar_detalle(origen(senal, EXTRACCION), en_linea=True)
    assert resultado.tipo is PERMANENTE
    assert "--cookies-de" in resultado.consejo


@pytest.mark.parametrize("senal", [404, 500, "transporte"])
def test_los_demas_errores_al_extraer_no_llevan_consejo(senal):
    assert clasificar_detalle(desde_la_cadena(senal, EXTRACCION), en_linea=True).consejo is None
