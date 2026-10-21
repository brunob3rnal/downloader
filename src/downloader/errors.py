"""Clasificación de errores en TEMPORAL, PERMANENTE y FATAL, y filtro de secretos (R4, R12)."""

import errno
import re
from enum import Enum

from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError


class Tipo(Enum):
    TEMPORAL = "temporal"  # se reintenta indefinidamente
    PERMANENTE = "permanente"  # la descarga pasa a fallida y la cola sigue
    FATAL = "fatal"  # afecta a todo el entorno: detiene ejecutar


class ErrorPermanente(Exception):
    """Error propio que impide esta descarga (directo, carpeta inválida…)."""


class ErrorFatal(Exception):
    """Error propio que impide seguir con cualquier descarga (base corrupta, ffmpeg ausente…)."""


# Durante la descarga, 403 y 410 suelen ser una URL temporal caducada: la siguiente
# extracción obtiene una nueva (R2).
_HTTP_TEMPORALES = {403, 410}


def _cadena(exc):
    """La excepción y todas sus causas: `exc_info` de yt-dlp, `cause`, `__cause__`, `__context__`."""
    vistas = []
    pendientes = [exc]
    while pendientes:
        actual = pendientes.pop(0)
        if actual is None or any(actual is v for v in vistas):
            continue
        vistas.append(actual)
        exc_info = getattr(actual, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            pendientes.append(exc_info[1])
        pendientes += [getattr(actual, "cause", None), actual.__cause__, actual.__context__]
    return vistas


def _tipo_de(exc):
    """Tipo de una excepción concreta, o `None` si no la reconoce."""
    if isinstance(exc, ErrorFatal):
        return Tipo.FATAL
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return Tipo.FATAL
    if isinstance(exc, ErrorPermanente):
        return Tipo.PERMANENTE
    if isinstance(exc, HTTPError):
        temporal = exc.status >= 500 or exc.status in _HTTP_TEMPORALES
        return Tipo.TEMPORAL if temporal else Tipo.PERMANENTE
    if isinstance(exc, (TransportError, TimeoutError, ConnectionError)):
        return Tipo.TEMPORAL
    if isinstance(exc, UnsupportedError):
        return Tipo.PERMANENTE
    if isinstance(exc, ExtractorError) and exc.expected:
        return Tipo.PERMANENTE
    return None


def clasificar(exc: BaseException, en_linea: bool) -> Tipo:
    """Clasifica un error según research R4.

    Lo FATAL prevalece siempre. Sin conexión, cualquier otro error es TEMPORAL: yt-dlp envuelve
    los cortes en errores de extracción que, si no, parecerían permanentes. Con conexión, es
    TEMPORAL si alguna causa de la cadena lo es; en otro caso, PERMANENTE.
    """
    tipos = [_tipo_de(e) for e in _cadena(exc)]
    if Tipo.FATAL in tipos:
        return Tipo.FATAL
    if not en_linea:
        return Tipo.TEMPORAL
    if Tipo.TEMPORAL in tipos:
        return Tipo.TEMPORAL
    return Tipo.PERMANENTE


_CABECERA = re.compile(
    r"""(?P<clave>\b(?:cookie|authorization)\b['"]?\s*[:=]\s*)(?P<comilla>['"])?"""
    r"""(?P<valor>(?(comilla)[^'"\n]*|[^\n]*))""",
    re.IGNORECASE,
)
_PARAMETRO = re.compile(
    r"(?P<clave>[?&](?:token|sig|signature|key|auth)=)[^&\s#'\"]*", re.IGNORECASE
)
OCULTO = "***"


def ocultar_secretos(texto: str) -> str:
    """Elimina valores de cabeceras Cookie/Authorization y parámetros de URL con aspecto de token."""
    texto = _CABECERA.sub(lambda m: f"{m['clave']}{m['comilla'] or ''}{OCULTO}", texto)
    return _PARAMETRO.sub(lambda m: f"{m['clave']}{OCULTO}", texto)
