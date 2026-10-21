import errno
import sys

import pytest
from yt_dlp.networking.exceptions import HTTPError, IncompleteRead, TransportError
from yt_dlp.networking.common import Response
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from downloader.errors import (
    ErrorFatal,
    ErrorPermanente,
    Tipo,
    UltimaPeticion,
    clasificar,
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


# --- DownloadError sin causa: se clasifica por el registro de la última petición (R4) ---


def sin_causa(ultima="sin atributo"):
    """`DownloadError` como el que lanza yt-dlp al agotar sus reintentos: sin `exc_info`."""
    error = DownloadError("ERROR: [download] Got error: ... Giving up after 3 retries")
    if ultima != "sin atributo":
        error.ultima_peticion = ultima
    return error


@pytest.mark.parametrize(
    "ultima, esperado",
    [
        # transporte y timeout
        (UltimaPeticion(error=TransportError("Connection reset by peer")), Tipo.TEMPORAL),
        (UltimaPeticion(error=TransportError("timed out")), Tipo.TEMPORAL),
        (UltimaPeticion(error=TimeoutError("timed out")), Tipo.TEMPORAL),
        (UltimaPeticion(error=ConnectionError("refused")), Tipo.TEMPORAL),
        (UltimaPeticion(error=IncompleteRead(partial=400, expected=1000)), Tipo.TEMPORAL),
        # 5xx y 429
        (UltimaPeticion(estado=500), Tipo.TEMPORAL),
        (UltimaPeticion(estado=503), Tipo.TEMPORAL),
        (UltimaPeticion(estado=429), Tipo.TEMPORAL),
        # 404, 410, 403
        (UltimaPeticion(estado=404), Tipo.PERMANENTE),
        (UltimaPeticion(estado=410), Tipo.PERMANENTE),
        (UltimaPeticion(estado=403), Tipo.PERMANENTE),
        # última petición correcta o ninguna
        (UltimaPeticion(estado=200), Tipo.PERMANENTE),
        (UltimaPeticion(estado=206), Tipo.PERMANENTE),
        (None, Tipo.PERMANENTE),
        ("sin atributo", Tipo.PERMANENTE),
        # una excepción que no es de red
        (UltimaPeticion(error=ValueError("petición mal formada")), Tipo.PERMANENTE),
    ],
    ids=lambda v: repr(v)[:50],
)
def test_download_error_sin_causa_usa_el_registro(ultima, esperado):
    assert clasificar(sin_causa(ultima), en_linea=True) is esperado


@pytest.mark.parametrize("estado", [404, 403, 410, 200])
def test_registro_sin_conexion_es_temporal(estado):
    assert clasificar(sin_causa(UltimaPeticion(estado=estado)), en_linea=False) is Tipo.TEMPORAL


def test_registro_no_sustituye_a_una_causa_encadenada():
    # Con causa en exc_info manda la cadena (un 403 de descarga HTTP es una URL caducada).
    error = download_error(error_http(403))
    error.ultima_peticion = UltimaPeticion(estado=403)
    assert clasificar(error, en_linea=True) is Tipo.TEMPORAL


def test_fatal_prevalece_sobre_el_registro():
    error = download_error(OSError(errno.ENOSPC, "lleno"))
    error.ultima_peticion = UltimaPeticion(estado=503)
    assert clasificar(error, en_linea=True) is Tipo.FATAL
