"""Adaptador de yt-dlp: opciones, formatos, extracción y progreso (research R1, R2, R6, R12, R13)."""

import io
import logging
import re
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from yt_dlp import YoutubeDL
from yt_dlp.cookies import SUPPORTED_BROWSERS
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.external import ExternalFD
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError, IncompleteRead, TransportError
from yt_dlp.utils import DownloadError

from downloader import drm
from downloader.errors import (
    ErrorPermanente,
    Fase,
    RegistroRed,
    SinDatos,
    UltimaPeticion,
    cookies_ilegibles,
    ocultar_secretos,
)
from downloader.models import Calidad, Descarga

DIRECTORIO_TEMPORAL = ".downloader-tmp"
NAVEGADORES = SUPPORTED_BROWSERS  # los que admite `--cookies-de` (research R12, R25)
# Tamaño de cada lectura del cuerpo de una respuesta: lo máximo que se pierde en un corte (R18).
TROZO_LECTURA = 4096
# `retries` y `fragment_retries`: un fallo sube enseguida a los reintentos del trabajador, que
# son indefinidos (FR-010, R21).
REINTENTOS_INTERNOS = 1
SOCKET_TIMEOUT = 15  # s por lectura de socket (R21)
PASO_MUESTRAS = 0.25  # s entre muestras de la ventana de velocidad del vigilante (R21)
# Nombre fijo y corto en el directorio de trabajo (que ya es uno por descarga): el id del video
# podría ser largo o un nombre reservado de Windows (`CON`, `NUL`…), que `windowsfilenames` no
# sanea. Cada intento usa el mismo nombre, así que yt-dlp continúa el mismo `.part` (R2, R7).
PLANTILLA = "descarga.%(ext)s"

_RANGO_PEDIDO = re.compile(r"bytes=(\d+)-$")  # reanudación: desde el tamaño del `.part`
_RANGO_416 = re.compile(r"bytes \*/(\d+)$")  # `Content-Range` de un 416: tamaño real
CABECERA_M3U = b"#EXTM3U"  # así empieza todo manifiesto HLS (RFC 8216 §4.3.1.1)

DIRECTO = (
    "Es una transmisión en directo: no se descargan directos. Vuelva a agregar la URL cuando "
    "la emisión haya terminado."
)

FORMATOS = {
    Calidad.MEJOR: "bv*+ba/b",
    Calidad.P1080: "bv*[height<=1080]+ba/b[height<=1080]/wv*+ba/w",
    Calidad.P720: "bv*[height<=720]+ba/b[height<=720]/wv*+ba/w",
    Calidad.AUDIO: "ba/b",
}


@dataclass(frozen=True)
class Vigilancia:
    """Umbrales del vigilante de estancamiento de la descarga (research R21).

    El intento se aborta si pasan `segundos` sin recibir ningún byte, o si en los últimos
    `segundos` llegan menos de `velocidad_minima` bytes/s (goteo).
    """

    segundos: float = 30
    velocidad_minima: float = 1024


class Progreso(NamedTuple):
    bytes: int | None
    total: int | None
    velocidad: float | None
    eta: float | None
    archivo: str | None
    estado: str  # "downloading" | "finished" | "error"
    # `total_bytes` de yt-dlp: el tamaño exacto anunciado; `None` si solo hay una estimación
    # (`total`, entonces, es `total_bytes_estimate`). La verificación solo compara con este (R8).
    total_exacto: int | None = None
    # Por qué yt-dlp vuelve a empezar este archivo desde cero, si lo sabemos (R23).
    motivo_reinicio: str | None = None


class Logger:
    """Logger para yt-dlp que filtra secretos antes de registrar nada (Principio VI)."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger("downloader.ytdl")

    def debug(self, mensaje: str) -> None:
        self._logger.debug(ocultar_secretos(mensaje))

    def info(self, mensaje: str) -> None:
        self._logger.info(ocultar_secretos(mensaje))

    def warning(self, mensaje: str) -> None:
        self._logger.warning(ocultar_secretos(mensaje))

    def error(self, mensaje: str) -> None:
        self._logger.error(ocultar_secretos(mensaje))


@dataclass
class _Tras416:
    """Qué responder a la petición que yt-dlp repite tras un 416 (research R23)."""

    url: str
    total: int | None = None  # el `.part` está completo: respuesta sin red de este tamaño
    error: HTTPError | None = None  # si no: el mismo 416 otra vez, sin red

    def responder(self):
        if self.error is not None:
            raise self.error
        return Response(io.BytesIO(), self.url, {"Content-Length": str(self.total)}, 200, "OK")


class YoutubeDLRegistrado(YoutubeDL):
    """`YoutubeDL` que lleva un `RegistroRed` del intento (research R4).

    Cuando yt-dlp agota sus reintentos internos lanza un `DownloadError` sin la excepción
    original; `errors.clasificar` usa este registro para saber qué falló. Se registra el
    resultado de `urlopen` (código HTTP o excepción) y también el de la lectura del cuerpo: en un
    corte, `urlopen` responde bien y el error llega al leer. También la fase (extracción o
    descarga) y si la descarga la hace un programa externo (ffmpeg), cuyas peticiones no pasan
    por `urlopen`. Cada intento crea su propia instancia, así que el registro empieza vacío.

    Fija `hls_prefer_native=True` para que HLS use el descargador nativo de yt-dlp, que sí pasa
    por `urlopen`, en lugar de ffmpeg.

    Con el parámetro `downloader_vigilancia` (una `Vigilancia`), vigila durante la descarga los
    bytes recibidos y aborta el intento con `SinDatos` si la conexión se estanca (R21).

    Resuelve el 416 al reanudar con comparación exacta en lugar de la de yt-dlp (R23); el motivo
    de un reinicio se añade a la lista del parámetro `downloader_reinicios`.

    Rechaza los directos antes de pedir ningún medio, y el HLS con DRM o con un cifrado que
    solo ffmpeg sabría descargar antes de pedir la clave o un fragmento (R5, `drm`). Los formatos
    que yt-dlp marca `has_drm` los rechaza él al elegir el formato.
    """

    def __init__(self, params=None, *args, **kwargs):
        self.registro = RegistroRed()
        self._cerrojo_registro = threading.Lock()
        # Respuestas abiertas al extraer, que se cierran al empezar la descarga (research R19).
        self._respuestas_extraccion: list[weakref.ref] = []
        super().__init__({"hls_prefer_native": True, **(params or {})}, *args, **kwargs)
        self._vigilancia: Vigilancia | None = self.params.get("downloader_vigilancia")
        self._ultimo_byte: float | None = None  # None: la vigilancia aún no empezó
        self._recibidos = 0
        self._muestras: deque[tuple[float, int]] = deque()  # (instante, bytes recibidos)
        self._estancado: SinDatos | None = None
        self._tras_416: _Tras416 | None = None
        self._reinicios: list[str] | None = self.params.get("downloader_reinicios")
        # El primer manifiesto rechazado (R5): `process_info` vuelve a lanzarlo por si el
        # extractor capturó el error de la lectura.
        self._rechazo: ErrorPermanente | None = None

    # --- vigilante de estancamiento (R21) ---------------------------------------------

    def _empezar_vigilancia(self) -> None:
        if self._vigilancia is not None and self._ultimo_byte is None:
            ahora = time.monotonic()
            self._ultimo_byte = ahora
            self._muestras.append((ahora, 0))

    def _anotar_bytes(self, cantidad: int) -> None:
        if self._ultimo_byte is None:
            return
        ahora = time.monotonic()
        self._ultimo_byte = ahora
        self._recibidos += cantidad
        if ahora - self._muestras[-1][0] >= PASO_MUESTRAS:
            self._muestras.append((ahora, self._recibidos))

    def _comprobar_estancamiento(self) -> SinDatos | None:
        """`SinDatos` si la conexión está estancada; una vez estancado, el intento lo está."""
        if self._estancado is not None or self._ultimo_byte is None:
            return self._estancado
        ahora = time.monotonic()
        umbral = self._vigilancia.segundos
        silencio = ahora - self._ultimo_byte
        if silencio >= umbral:
            error = SinDatos(f"sin datos hace {silencio:.0f} s")
        else:
            limite = ahora - umbral
            while len(self._muestras) > 1 and self._muestras[1][0] <= limite:
                self._muestras.popleft()
            desde, base = self._muestras[0]
            if desde > limite:  # aún no hay una ventana completa
                return None
            if self._recibidos - base >= self._vigilancia.velocidad_minima * umbral:
                return None
            error = SinDatos(
                f"menos de {self._vigilancia.velocidad_minima / 1024:g} KiB/s "
                f"durante {umbral:.0f} s"
            )
        self._estancado = error
        self._registrar(UltimaPeticion(error=error))
        return error

    def _registrar(self, resultado: UltimaPeticion) -> None:
        with self._cerrojo_registro:
            self.registro.ultima = resultado

    def _cerrar_respuestas_extraccion(self) -> None:
        """Cierra las respuestas de la extracción que nadie leyó ni cerró (research R19).

        El extractor genérico, con una URL directa de `Content-Type: video/*`, hace un GET y no
        cierra la respuesta; la conexión seguiría recibiendo el video hasta que la recogiera el
        GC. Ninguna descarga usa esas respuestas: abre las suyas.
        """
        referencias, self._respuestas_extraccion = self._respuestas_extraccion, []
        for referencia in referencias:
            respuesta = referencia()
            if respuesta is None:
                continue
            try:
                respuesta.close()
            except Exception:  # ya cerrada o conexión rota: no hay nada que liberar
                pass

    def close(self):
        self._cerrar_respuestas_extraccion()
        super().close()

    def process_ie_result(self, ie_result, *args, **kwargs):
        # El extractor ya devolvió su resultado: lo que siga abierto de él sobra. Cerrarlo aquí
        # y no solo en `close` evita recibir datos mientras se procesan los formatos.
        self._cerrar_respuestas_extraccion()
        return super().process_ie_result(ie_result, *args, **kwargs)

    def process_info(self, info_dict):
        # Lo último antes de descargar un video ya resuelto: aún no se ha pedido ningún medio.
        if info_dict.get("is_live"):
            raise ErrorPermanente(DIRECTO)  # Principio V, FR-015
        if self._rechazo is not None:
            raise self._rechazo
        # Lo que la descarga usaría sin pedirlo: el manifiesto ya leído y la clave del extractor.
        self._comprobar_cifrado(drm.analizar(info_dict.get("hls_media_playlist_data") or ""))
        self._comprobar_cifrado(drm.analizar_hls_aes(info_dict.get("hls_aes") or {}))
        return super().process_info(info_dict)

    def _comprobar_cifrado(self, decision: drm.Decision) -> None:
        """Lanza `ErrorPermanente` si el cifrado es DRM o no está soportado (R5, FR-015).

        Se registra como última petición, para que la clasificación no dependa de cómo yt-dlp
        envuelva el error.
        """
        if decision.veredicto is drm.Veredicto.ACEPTADO:
            return
        error = ErrorPermanente(decision.mensaje)
        self._rechazo = self._rechazo or error
        self._registrar(UltimaPeticion(error=error))
        raise error

    def dl(self, name, info, subtitle=False, test=False):
        if not test:
            self._cerrar_respuestas_extraccion()
            self._empezar_vigilancia()
            descargador = get_suitable_downloader(info, self.params, to_stdout=(name == "-"))
            self.registro.fase = Fase.DESCARGA
            self.registro.externo = issubclass(descargador, ExternalFD)
        return super().dl(name, info, subtitle, test)

    def urlopen(self, req):
        if self._estancado is not None:
            # Intento abortado: sin esto, el reintento interno de yt-dlp volvería a esperar.
            raise self._estancado
        tras_416, self._tras_416 = self._tras_416, None
        if tras_416 is not None and getattr(req, "url", req) == tras_416.url:
            return tras_416.responder()
        try:
            respuesta = super().urlopen(req)
        except HTTPError as error:
            self._registrar(UltimaPeticion(estado=error.status))
            if error.status == 416 and self.registro.fase is Fase.DESCARGA:
                sustituta = self._rango_no_satisfacible(req, error)
                if sustituta is not None:
                    return sustituta
            raise
        except Exception as error:
            self._registrar(UltimaPeticion(error=error))
            if isinstance(error, TransportError) and (estancado := self._comprobar_estancamiento()):
                raise estancado from error
            raise
        self._registrar(UltimaPeticion(estado=respuesta.status))
        if self.registro.fase is Fase.EXTRACCION:
            self._respuestas_extraccion.append(weakref.ref(respuesta))
        self._vigilar_cuerpo(respuesta)
        self._vigilar_manifiesto(respuesta)
        return respuesta

    def _vigilar_manifiesto(self, respuesta) -> None:
        """Envuelve `respuesta.read` para analizar el cifrado de un manifiesto HLS (R5).

        Si el cuerpo empieza por `#EXTM3U`, cada lectura analiza las líneas completas recibidas
        (todo, al terminar) y lanza el rechazo antes de entregarlas: al extraer, antes de que el
        extractor analice el maestro; al descargar, antes de que `HlsFD` pida la clave o un
        fragmento, o pase la descarga a ffmpeg. El resto de los cuerpos no se copia.
        """
        leer = respuesta.read
        leido = bytearray()
        manifiesto: bool | None = None  # `None`: aún no hay bytes suficientes para saberlo

        def read(amt=None):
            nonlocal manifiesto
            datos = leer(amt)
            if manifiesto is False:
                return datos
            leido.extend(datos)
            final = not datos or amt is None or amt < 0
            if manifiesto is None and (final or len(leido) >= len(CABECERA_M3U)):
                manifiesto = leido.startswith(CABECERA_M3U)
                if not manifiesto:
                    leido.clear()
                    return datos
            texto = leido.decode("utf-8", "ignore")
            self._comprobar_cifrado(drm.analizar(texto if final else texto[:texto.rfind("\n") + 1]))
            return datos

        respuesta.read = read

    def _rango_no_satisfacible(self, req, error: HTTPError):
        """Un 416 al reanudar desde el byte N del `.part` (research R23).

        Tras un 416, yt-dlp repite la petición para pedir el archivo entero (aunque, por un
        error suyo, con el mismo `Range`) y lo da por completo si su tamaño difiere menos de
        100 bytes del `.part`. Aquí se decide con el tamaño exacto, `Content-Range: bytes */TOTAL`:

        - TOTAL == N: el `.part` está completo. La petición repetida recibe, sin red, una
          respuesta vacía con `Content-Length: TOTAL`, y yt-dlp lo da por terminado.
        - TOTAL < N: el `.part` no encaja con el archivo. En lugar del 416 se devuelve el
          archivo entero sin `Range`, y yt-dlp lo descarga desde cero (FR-024).
        - Sin `Content-Range`, o TOTAL > N: la petición repetida recibe otra vez el 416, sin
          red, y el error sube como ambiguo (`errors.clasificar_detalle`).
        """
        url = getattr(req, "url", req)
        pedido = _RANGO_PEDIDO.match(getattr(req, "headers", {}).get("Range", ""))
        real = _RANGO_416.match(error.response.headers.get("Content-Range") or "")
        if pedido and real and int(real[1]) == int(pedido[1]):
            self._tras_416 = _Tras416(url, total=int(real[1]))
            return None
        if pedido and real and int(real[1]) < int(pedido[1]):
            if self._reinicios is not None:
                self._reinicios.append(
                    f"el parcial ({pedido[1]} bytes) es mayor que el archivo del servidor "
                    f"({real[1]} bytes)"
                )
            completo = req.copy()
            del completo.headers["Range"]
            return self.urlopen(completo)
        self._tras_416 = _Tras416(url, error=error)
        return None

    def _vigilar_cuerpo(self, respuesta) -> None:
        """Envuelve `respuesta.read` para registrar sus errores y no perder bytes en un corte.

        yt-dlp pide bloques de hasta 4 MiB, y urllib3 descarta lo ya recibido de un bloque si
        la conexión se corta antes de completarlo (research R18). Por eso se lee en trozos de
        `TROZO_LECTURA` bytes: si un trozo falla con un error de transporte tras haber
        acumulado bytes, se entregan esos bytes y el error se relanza en la lectura siguiente.

        Con el manejador de `requests` cada trozo se lee con `read1`, que vuelve en cuanto llega
        algún dato: así el vigilante de estancamiento comprueba también un goteo de bytes,
        con el que `read(4096)` no volvería en horas (R21).
        """
        leer_real = getattr(respuesta, "_real_read", None)
        fp = getattr(respuesta, "fp", None)
        if leer_real is not None and hasattr(fp, "read1"):
            # `read` de yt-dlp sigue traduciendo las excepciones de urllib3.
            respuesta._real_read = (
                lambda amt=None: fp.read1(amt, decode_content=True) if amt else leer_real(amt)
            )
        leer = respuesta.read
        esperado = respuesta.headers.get("Content-Length")
        esperado = int(esperado) if esperado and esperado.isdigit() else None
        recibidos = 0
        aplazado: TransportError | None = None

        def leer_registrando(amt):
            nonlocal recibidos
            try:
                datos = leer(amt)
            except Exception as error:
                self._registrar(UltimaPeticion(error=error))
                raise
            recibidos += len(datos)
            if not datos and amt != 0 and esperado is not None and recibidos < esperado:
                # El servidor cerró antes de enviar todo el cuerpo: respuesta truncada.
                self._registrar(UltimaPeticion(error=IncompleteRead(recibidos, esperado)))
            return datos

        def abortar(error: SinDatos) -> SinDatos:
            try:
                respuesta.close()  # que el servidor deje de enviar a una conexión abandonada
            except Exception:
                pass
            return error

        def read(amt=None):
            nonlocal aplazado
            if aplazado is not None:
                error, aplazado = aplazado, None
                raise error
            if amt is None or amt < 0:
                return leer_registrando(amt)
            acumulado = bytearray()
            while len(acumulado) < amt:
                try:
                    trozo = leer_registrando(min(TROZO_LECTURA, amt - len(acumulado)))
                except TransportError as error:
                    # Un timeout de socket puede completar un estancamiento.
                    estancado = self._comprobar_estancamiento()
                    if estancado is not None:
                        error = abortar(estancado)
                    if not acumulado:
                        raise error
                    aplazado = error
                    break
                if not trozo:
                    break
                acumulado += trozo
                self._anotar_bytes(len(trozo))
                estancado = self._comprobar_estancamiento()
                if estancado is not None:
                    aplazado = abortar(estancado)
                    break
            return bytes(acumulado)

        respuesta.read = read


def crear_ydl(opciones: dict) -> YoutubeDLRegistrado:
    """Único punto donde se instancia `YoutubeDL` (los tests lo sustituyen)."""
    return YoutubeDLRegistrado(opciones)


def _ejecutar(opciones: dict, url: str, download: bool) -> dict:
    """`extract_info` que, si falla, adjunta al error el registro del intento.

    Si lo que falló fue la lectura de las cookies del navegador (yt-dlp las lee en la primera
    petición), lanza `CookiesIlegibles` con el navegador y la causa (research R25).
    """
    with crear_ydl(opciones) as ydl:
        try:
            return ydl.sanitize_info(ydl.extract_info(url, download=download))
        except DownloadError as error:
            navegador = (opciones.get("cookiesfrombrowser") or (None,))[0]
            if navegador and (cookies := cookies_ilegibles(error, navegador)) is not None:
                raise cookies from error
            error.registro = ydl.registro
            raise


def directorio_trabajo(descarga: Descarga) -> Path:
    return Path(descarga.carpeta_destino) / DIRECTORIO_TEMPORAL / str(descarga.id)


def _comunes(navegador: str | None, socket_timeout: float) -> dict:
    opciones = {
        "quiet": True,
        "logger": Logger(),
        "noplaylist": True,
        "socket_timeout": socket_timeout,
    }
    if navegador:
        # En memoria: nunca `cookiefile`, así yt-dlp no escribe cookies en disco (R12).
        opciones["cookiesfrombrowser"] = (navegador,)
    return opciones


def opciones(
    descarga: Descarga,
    directorio_trabajo: Path,
    navegador: str | None,
    hook: Callable[[dict], None],
    socket_timeout: float = SOCKET_TIMEOUT,
    vigilancia: Vigilancia | None = None,
) -> dict:
    opciones = _comunes(navegador, socket_timeout) | {
        "format": FORMATOS[descarga.calidad],
        "outtmpl": {"default": str(Path(directorio_trabajo) / PLANTILLA)},
        # Nombres válidos en Windows también desde Linux (p. ej. en una unidad exFAT o NTFS).
        "windowsfilenames": True,
        "continuedl": True,
        "nopart": False,
        # yt-dlp lo activa por defecto y produciría archivos HLS/DASH incompletos (R1).
        "skip_unavailable_fragments": False,
        # Pocos reintentos internos: los reintentos indefinidos son nuestros (FR-010, R21).
        "retries": REINTENTOS_INTERNOS,
        "fragment_retries": REINTENTOS_INTERNOS,
        "progress_hooks": [hook],
        "downloader_vigilancia": vigilancia,
    }
    if descarga.calidad is Calidad.AUDIO:
        opciones["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}]
    return opciones


def extraer_plano(
    url: str, navegador: str | None = None, socket_timeout: float = SOCKET_TIMEOUT
) -> dict:
    """Información de la URL sin descargar; las listas traen sus entradas sin resolver (R13)."""
    opciones = _comunes(navegador, socket_timeout) | {"extract_flat": "in_playlist"}
    return _ejecutar(opciones, url, download=False)


def descargar(
    descarga: Descarga,
    directorio_trabajo: Path,
    navegador: str | None,
    al_progresar: Callable[[Progreso], None],
    socket_timeout: float = SOCKET_TIMEOUT,
    vigilancia: Vigilancia | None = None,
) -> dict:
    """Vuelve a extraer y descarga (o continúa) en el directorio de trabajo (R2)."""
    reinicios: list[str] = []  # los anota `YoutubeDLRegistrado` (R23)

    def hook(evento: dict) -> None:
        al_progresar(
            Progreso(
                bytes=evento.get("downloaded_bytes"),
                total=evento.get("total_bytes") or evento.get("total_bytes_estimate"),
                velocidad=evento.get("speed"),
                eta=evento.get("eta"),
                archivo=evento.get("filename"),
                estado=evento["status"],
                total_exacto=evento.get("total_bytes"),
                # El primer evento tras anotarse un reinicio es el del archivo que vuelve a 0.
                motivo_reinicio=reinicios.pop(0) if reinicios else None,
            )
        )

    return _ejecutar(
        opciones(descarga, directorio_trabajo, navegador, hook, socket_timeout, vigilancia)
        | {"downloader_reinicios": reinicios},
        descarga.url,
        download=True,
    )


_ALTURAS = {Calidad.P1080: 1080, Calidad.P720: 720}


def aviso_calidad(calidad: Calidad, info: dict) -> str | None:
    """Aviso si la altura descargada no es la pedida (research R6)."""
    pedida, obtenida = _ALTURAS.get(calidad), info.get("height")
    if pedida is None or obtenida is None or obtenida == pedida:
        return None
    return f"calidad {calidad.value} no disponible; se descargó {obtenida}p"


def archivo_descargado(info: dict) -> Path:
    """Ruta del archivo final (tras unir formatos y postprocesar) dentro del directorio de trabajo."""
    descargas = info.get("requested_downloads") or [info]
    return Path(descargas[-1]["filepath"])
