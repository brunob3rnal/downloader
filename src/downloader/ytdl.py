"""Adaptador de yt-dlp: opciones, formatos, extracción y progreso (research R1, R2, R6, R12, R13)."""

import logging
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from yt_dlp import YoutubeDL
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.external import ExternalFD
from yt_dlp.networking.exceptions import HTTPError, IncompleteRead, TransportError
from yt_dlp.utils import DownloadError

from downloader.errors import Fase, RegistroRed, UltimaPeticion, ocultar_secretos
from downloader.models import Calidad, Descarga

DIRECTORIO_TEMPORAL = ".downloader-tmp"
# Tamaño de cada lectura del cuerpo de una respuesta: lo máximo que se pierde en un corte (R18).
TROZO_LECTURA = 4096
# Nombre fijo y corto en el directorio de trabajo (que ya es uno por descarga): el id del video
# podría ser largo o un nombre reservado de Windows (`CON`, `NUL`…), que `windowsfilenames` no
# sanea. Cada intento usa el mismo nombre, así que yt-dlp continúa el mismo `.part` (R2, R7).
PLANTILLA = "descarga.%(ext)s"

FORMATOS = {
    Calidad.MEJOR: "bv*+ba/b",
    Calidad.P1080: "bv*[height<=1080]+ba/b[height<=1080]/wv*+ba/w",
    Calidad.P720: "bv*[height<=720]+ba/b[height<=720]/wv*+ba/w",
    Calidad.AUDIO: "ba/b",
}


class Progreso(NamedTuple):
    bytes: int | None
    total: int | None
    velocidad: float | None
    eta: float | None
    archivo: str | None
    estado: str  # "downloading" | "finished" | "error"


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
    """

    def __init__(self, params=None, *args, **kwargs):
        self.registro = RegistroRed()
        self._cerrojo_registro = threading.Lock()
        # Respuestas abiertas al extraer, que se cierran al empezar la descarga (research R19).
        self._respuestas_extraccion: list[weakref.ref] = []
        super().__init__({"hls_prefer_native": True, **(params or {})}, *args, **kwargs)

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

    def dl(self, name, info, subtitle=False, test=False):
        if not test:
            self._cerrar_respuestas_extraccion()
            descargador = get_suitable_downloader(info, self.params, to_stdout=(name == "-"))
            self.registro.fase = Fase.DESCARGA
            self.registro.externo = issubclass(descargador, ExternalFD)
        return super().dl(name, info, subtitle, test)

    def urlopen(self, req):
        try:
            respuesta = super().urlopen(req)
        except HTTPError as error:
            self._registrar(UltimaPeticion(estado=error.status))
            raise
        except Exception as error:
            self._registrar(UltimaPeticion(error=error))
            raise
        self._registrar(UltimaPeticion(estado=respuesta.status))
        if self.registro.fase is Fase.EXTRACCION:
            self._respuestas_extraccion.append(weakref.ref(respuesta))
        self._vigilar_cuerpo(respuesta)
        return respuesta

    def _vigilar_cuerpo(self, respuesta) -> None:
        """Envuelve `respuesta.read` para registrar sus errores y no perder bytes en un corte.

        yt-dlp pide bloques de hasta 4 MiB, y urllib3 descarta lo ya recibido de un bloque si
        la conexión se corta antes de completarlo (research R18). Por eso se lee en trozos de
        `TROZO_LECTURA` bytes: si un trozo falla con un error de transporte tras haber
        acumulado bytes, se entregan esos bytes y el error se relanza en la lectura siguiente.
        """
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
                    if not acumulado:
                        raise
                    aplazado = error
                    break
                if not trozo:
                    break
                acumulado += trozo
            return bytes(acumulado)

        respuesta.read = read


def crear_ydl(opciones: dict) -> YoutubeDLRegistrado:
    """Único punto donde se instancia `YoutubeDL` (los tests lo sustituyen)."""
    return YoutubeDLRegistrado(opciones)


def _ejecutar(opciones: dict, url: str, download: bool) -> dict:
    """`extract_info` que, si falla, adjunta al error el registro del intento."""
    with crear_ydl(opciones) as ydl:
        try:
            return ydl.sanitize_info(ydl.extract_info(url, download=download))
        except DownloadError as error:
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
    socket_timeout: float = 20,
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
        # Pocos reintentos internos: los reintentos indefinidos son nuestros (FR-010).
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [hook],
    }
    if descarga.calidad is Calidad.AUDIO:
        opciones["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}]
    return opciones


def extraer_plano(url: str, navegador: str | None = None, socket_timeout: float = 20) -> dict:
    """Información de la URL sin descargar; las listas traen sus entradas sin resolver (R13)."""
    opciones = _comunes(navegador, socket_timeout) | {"extract_flat": "in_playlist"}
    return _ejecutar(opciones, url, download=False)


def descargar(
    descarga: Descarga,
    directorio_trabajo: Path,
    navegador: str | None,
    al_progresar: Callable[[Progreso], None],
    socket_timeout: float = 20,
) -> dict:
    """Vuelve a extraer y descarga (o continúa) en el directorio de trabajo (R2)."""

    def hook(evento: dict) -> None:
        al_progresar(
            Progreso(
                bytes=evento.get("downloaded_bytes"),
                total=evento.get("total_bytes") or evento.get("total_bytes_estimate"),
                velocidad=evento.get("speed"),
                eta=evento.get("eta"),
                archivo=evento.get("filename"),
                estado=evento["status"],
            )
        )

    return _ejecutar(
        opciones(descarga, directorio_trabajo, navegador, hook, socket_timeout),
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
