"""Estados, calidades y la entidad Descarga (ver data-model.md)."""

from dataclasses import dataclass
from enum import StrEnum


class Estado(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    WAITING_NETWORK = "waiting_network"
    COMPLETED = "completed"
    FAILED = "failed"


ETIQUETAS = {
    Estado.QUEUED: "en cola",
    Estado.DOWNLOADING: "descargando",
    Estado.WAITING_NETWORK: "esperando red",
    Estado.COMPLETED: "completada",
    Estado.FAILED: "fallida",
}

PENDIENTES = frozenset({Estado.QUEUED, Estado.DOWNLOADING, Estado.WAITING_NETWORK})

# (desde, hacia); `None` es la creación de la fila con `agregar`.
TRANSICIONES = frozenset({
    (None, Estado.QUEUED),
    (Estado.FAILED, Estado.QUEUED),
    (Estado.QUEUED, Estado.DOWNLOADING),
    (Estado.DOWNLOADING, Estado.WAITING_NETWORK),
    (Estado.WAITING_NETWORK, Estado.DOWNLOADING),
    (Estado.DOWNLOADING, Estado.COMPLETED),
    (Estado.DOWNLOADING, Estado.FAILED),
    (Estado.DOWNLOADING, Estado.QUEUED),
    (Estado.WAITING_NETWORK, Estado.QUEUED),
})


class TransicionInvalida(ValueError):
    """Transición no listada en la máquina de estados: es un error de programación."""


def validar_transicion(desde: Estado | None, hacia: Estado) -> None:
    if (desde, hacia) not in TRANSICIONES:
        raise TransicionInvalida(f"transición no permitida: {desde} → {hacia}")


class Calidad(StrEnum):
    MEJOR = "mejor"
    P1080 = "1080p"
    P720 = "720p"
    AUDIO = "audio"


class TipoDescarga(StrEnum):
    VIDEO = "video"
    PLAYLIST = "playlist"  # lista ya expandida en descargas hijas


@dataclass
class Descarga:
    id: int
    url: str
    calidad: Calidad
    carpeta_destino: str
    estado: Estado
    navegador_cookies: str | None = None
    tipo: TipoDescarga = TipoDescarga.VIDEO
    padre_id: int | None = None
    titulo: str | None = None
    bytes_descargados: int = 0
    bytes_totales: int | None = None
    intentos_sin_avance: int = 0
    proximo_intento: str | None = None
    ultimo_error: str | None = None
    ultimo_aviso: str | None = None
    archivo_final: str | None = None
    creada: str | None = None
    actualizada: str | None = None
