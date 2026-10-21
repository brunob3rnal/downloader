"""Clasificación de errores en TEMPORAL, PERMANENTE y FATAL, y filtro de secretos (R4, R12)."""

import errno
import re
from dataclasses import dataclass
from enum import Enum, StrEnum

from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import ExtractorError, UnsupportedError


class Tipo(Enum):
    TEMPORAL = "temporal"  # se reintenta indefinidamente (salvo el tope de los ambiguos)
    PERMANENTE = "permanente"  # la descarga pasa a fallida y la cola sigue
    FATAL = "fatal"  # afecta a todo el entorno: detiene ejecutar


class Fase(StrEnum):
    EXTRACCION = "extraccion"  # obtener la información del video
    DESCARGA = "descarga"  # transferir los archivos de medios


class ErrorPermanente(Exception):
    """Error propio que impide esta descarga (directo, carpeta inválida…)."""


class ErrorFatal(Exception):
    """Error propio que impide seguir con cualquier descarga (base corrupta, ffmpeg ausente…)."""


@dataclass
class UltimaPeticion:
    """Resultado de la última petición de red de un intento.

    `estado` es el código HTTP recibido; `error`, la excepción si no hubo respuesta o si falló
    la lectura del cuerpo.
    """

    estado: int | None = None
    error: BaseException | None = None


@dataclass
class RegistroRed:
    """Lo que `ytdl` sabe de un intento; se adjunta como `registro` al error que lo termina.

    `externo` indica que la descarga la hace un programa externo (ffmpeg) y no `urlopen`, así
    que el registro no ve sus peticiones.
    """

    ultima: UltimaPeticion | None = None
    fase: Fase = Fase.EXTRACCION
    externo: bool = False


@dataclass(frozen=True)
class Clasificacion:
    tipo: Tipo
    # Puede ser un fallo pasajero o uno que no se arregla reintentando; se reintenta, pero con
    # el tope de intentos seguidos sin avance de `retry.TopeSinAvance`.
    ambiguo: bool = False
    motivo: str | None = None  # p. ej. "HTTP 403" o "descargador externo"


_TRANSPORTE = (TransportError, TimeoutError, ConnectionError)
TEMPORAL = Clasificacion(Tipo.TEMPORAL)
PERMANENTE = Clasificacion(Tipo.PERMANENTE)
FATAL = Clasificacion(Tipo.FATAL)


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


def _es_fatal(exc) -> bool:
    return isinstance(exc, ErrorFatal) or (isinstance(exc, OSError) and exc.errno == errno.ENOSPC)


def _es_permanente(exc) -> bool:
    """Errores sin relación con la red que impiden la descarga."""
    return (
        isinstance(exc, (ErrorPermanente, UnsupportedError))
        or (isinstance(exc, ExtractorError) and exc.expected)
    )


def _senal_de_la_cadena(cadena):
    """El primer código HTTP o error de transporte de la cadena, o `None`."""
    for exc in cadena:
        if isinstance(exc, HTTPError):
            return exc.status
        if isinstance(exc, _TRANSPORTE):
            return exc
    return None


def _senal_del_registro(registro: RegistroRed | None):
    """El código HTTP de error o el error de transporte de la última petición, o `None`."""
    ultima = registro.ultima if registro else None
    if ultima is None:
        return None
    if ultima.error is not None:
        return ultima.error if isinstance(ultima.error, _TRANSPORTE) else None
    if ultima.estado is not None and ultima.estado >= 400:
        return ultima.estado
    return None


def _por_senal(senal, fase: Fase) -> Clasificacion:
    """La tabla de R4: un código HTTP o un error de transporte, según la fase."""
    if not isinstance(senal, int):
        return TEMPORAL  # transporte, timeout, cuerpo incompleto
    if senal == 429 or senal >= 500:
        return TEMPORAL
    if senal in (403, 410) and fase is Fase.DESCARGA:
        # Suele ser una URL temporal caducada: el siguiente intento vuelve a extraer (FR-012).
        return Clasificacion(Tipo.TEMPORAL, ambiguo=True, motivo=f"HTTP {senal}")
    return PERMANENTE  # 404 en cualquier fase; 403 y 410 al extraer; otros 4xx


def clasificar_detalle(exc: BaseException, en_linea: bool) -> Clasificacion:
    """Clasifica un error según research R4.

    1. FATAL prevalece siempre; después, sin conexión, todo es TEMPORAL.
    2. El código HTTP o el error de transporte se toma de la cadena de causas y, si no hay, del
       registro de la última petición (`exc.registro`): yt-dlp descarta la excepción original
       cuando agota sus reintentos. La fase es extracción si la cadena tiene un
       `ExtractorError`; si no, la del registro (descarga si no hay registro).
    3. Sin señal de red: los errores permanentes reconocidos (video borrado, DRM…) son
       PERMANENTE; un fallo de un descargador externo (ffmpeg) es TEMPORAL ambiguo; el resto,
       PERMANENTE.
    """
    cadena = _cadena(exc)
    if any(_es_fatal(e) for e in cadena):
        return FATAL
    if not en_linea:
        return TEMPORAL

    registro = getattr(exc, "registro", None)
    if any(isinstance(e, ExtractorError) for e in cadena):
        fase = Fase.EXTRACCION
    else:
        fase = registro.fase if registro else Fase.DESCARGA

    senal = _senal_de_la_cadena(cadena)
    if senal is not None:
        return _por_senal(senal, fase)
    if any(_es_permanente(e) for e in cadena):
        return PERMANENTE
    senal = _senal_del_registro(registro)
    if senal is not None:
        return _por_senal(senal, fase)
    if registro and registro.fase is Fase.DESCARGA and registro.externo:
        return Clasificacion(Tipo.TEMPORAL, ambiguo=True, motivo="descargador externo (ffmpeg)")
    return PERMANENTE


def clasificar(exc: BaseException, en_linea: bool) -> Tipo:
    return clasificar_detalle(exc, en_linea).tipo


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
