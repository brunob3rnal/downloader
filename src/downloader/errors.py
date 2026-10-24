"""Clasificación de errores en TEMPORAL, PERMANENTE y FATAL, y filtro de secretos (R4, R12)."""

import errno
import re
from dataclasses import dataclass
from enum import Enum, StrEnum

from yt_dlp.cookies import CookieLoadError
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


class CookiesIlegibles(ErrorPermanente):
    """yt-dlp no pudo leer las cookies del navegador de `--cookies-de` (research R25)."""


class SinDatos(TransportError):
    """La conexión sigue abierta pero no llegan datos, o llegan a goteo (research R21).

    Es un error de transporte para que yt-dlp cierre el `.part` como en un corte.
    """


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
    # Conexión estancada: no cuenta para el tope de los ambiguos ni como intento sin avance, y
    # se reintenta tras una espera corta (R21).
    estancamiento: bool = False
    consejo: str | None = None  # qué puede hacer el usuario, para añadir al mensaje del fallo


# Un acceso denegado suele deberse a un video que exige la sesión del usuario (FR-016, FR-026).
CONSEJO_SESION = (
    "Puede que el video exija una sesión iniciada: vuelva a agregarlo con --cookies-de "
    "NAVEGADOR (por ejemplo, --cookies-de firefox)."
)


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


def _sin_espacio(exc) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.ENOSPC


def _es_fatal(exc) -> bool:
    return isinstance(exc, ErrorFatal) or _sin_espacio(exc)


def disco_lleno(exc: BaseException) -> bool:
    """Si en la cadena de `exc` hay un `OSError(ENOSPC)`: no queda espacio en el disco."""
    return any(_sin_espacio(e) for e in _cadena(exc))


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
    if senal == 416 and fase is Fase.DESCARGA:
        # Rango no satisfacible sin un `Content-Range */TOTAL` que lo explique (los que lo
        # traen los resuelve `ytdl`, R23): puede ser pasajero, con tope (FR-026).
        return Clasificacion(Tipo.TEMPORAL, ambiguo=True, motivo="HTTP 416")
    if senal in (403, 410):  # al extraer: sin reintentos, pero con qué hacer (SC-005)
        return Clasificacion(Tipo.PERMANENTE, motivo=f"HTTP {senal}", consejo=CONSEJO_SESION)
    return PERMANENTE  # 404 en cualquier fase; 416 al extraer; otros 4xx


def clasificar_detalle(exc: BaseException, en_linea: bool) -> Clasificacion:
    """Clasifica un error según research R4.

    1. FATAL prevalece siempre; después, sin conexión, todo es TEMPORAL.
       Una conexión estancada (`SinDatos`, en la cadena o en el registro) es TEMPORAL con
       `estancamiento=True`.
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
    ultimo_error = registro.ultima.error if registro and registro.ultima else None
    estancamiento = next((e for e in [*cadena, ultimo_error] if isinstance(e, SinDatos)), None)
    if estancamiento is not None:
        return Clasificacion(Tipo.TEMPORAL, motivo=str(estancamiento), estancamiento=True)
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


_COOKIES_BLOQUEADAS = (
    "su base de datos de cookies está bloqueada o sin permiso de lectura. En Windows, Chrome, "
    "Edge y los demás navegadores basados en Chromium la bloquean mientras están abiertos: "
    "cierre el navegador del todo (también en segundo plano) y vuelva a intentarlo"
)
_COOKIES_CIFRADAS = (
    "no se pudieron descifrar con la protección de datos de Windows (DPAPI). Chrome 127 y "
    "posteriores las cifran con una clave ligada a la aplicación que yt-dlp no sabe leer: use "
    "otro navegador, por ejemplo --cookies-de firefox"
)
_COOKIES_NO_ENCONTRADAS = (
    "no se encontró su base de datos de cookies. Compruebe que el navegador está instalado "
    "para este usuario y que se ha abierto al menos una vez"
)


def cookies_ilegibles(exc: BaseException, navegador: str) -> CookiesIlegibles | None:
    """El fallo de yt-dlp al leer las cookies del navegador, con el navegador y la causa.

    yt-dlp envuelve cualquier fallo de la lectura en `CookieLoadError("failed to load
    cookies")`, con la excepción original como `__context__`, y lo relanza como un
    `DownloadError` cuyo mensaje no siempre nombra el navegador ni la causa (research R25).
    `None` si `exc` no viene de la lectura de cookies.
    """
    carga = next((e for e in _cadena(exc) if isinstance(e, CookieLoadError)), None)
    if carga is None:
        return None
    causas = _cadena(carga.__context__)
    if any(isinstance(e, PermissionError) for e in causas):
        causa = _COOKIES_BLOQUEADAS
    elif any("DPAPI" in str(e) for e in causas):
        causa = _COOKIES_CIFRADAS
    elif any(isinstance(e, FileNotFoundError) for e in causas):
        causa = _COOKIES_NO_ENCONTRADAS
    else:  # el mensaje original, sin los «ERROR: » que antepone `report_error`
        causa = re.sub(r"^(ERROR: )+", "", str(causas[0] if causas else carga))
    return CookiesIlegibles(f"No se pudieron leer las cookies de {navegador}: {causa}")


_CABECERA = re.compile(
    r"""(?P<clave>\b(?:cookie|authorization)\b['"]?\s*[:=]\s*)(?P<comilla>['"])?"""
    r"""(?P<valor>(?(comilla)[^'"\n]*|[^\n]*))""",
    re.IGNORECASE,
)
_PARAMETRO = re.compile(r"(?P<clave>[?&;](?P<nombre>[\w.~-]+)=)(?P<valor>[^&\s#'\"]*)")
# Palabras que delatan un secreto en el nombre de un parámetro, separadas por `-`, `_` o `.`
# (`X-Amz-Signature`, `access_token`, `Key-Pair-Id`); `monkey` o `itag` no lo son. Los nombres
# que terminan en `token`, `sig` o `signature` (`lsig`) también.
_PALABRAS_SECRETAS = {
    "token", "sig", "signature", "key", "auth", "credential", "policy", "hmac", "hdnts",
    "password", "secret",
}
_SUFIJOS_SECRETOS = ("token", "sig", "signature")
OCULTO = "***"


def _parametro_secreto(nombre: str) -> bool:
    nombre = nombre.lower()
    return (
        not _PALABRAS_SECRETAS.isdisjoint(re.split(r"[-_.]", nombre))
        or nombre.endswith(_SUFIJOS_SECRETOS)
    )


def ocultar_secretos(texto: str) -> str:
    """Elimina valores de cabeceras Cookie/Authorization y de parámetros de URL de token o
    firma (`token`, `sig`, `X-Amz-Signature`, `Key-Pair-Id`, `access_token`…)."""
    texto = _CABECERA.sub(lambda m: f"{m['clave']}{m['comilla'] or ''}{OCULTO}", texto)
    return _PARAMETRO.sub(
        lambda m: f"{m['clave']}{OCULTO}" if _parametro_secreto(m["nombre"]) else m[0], texto
    )
