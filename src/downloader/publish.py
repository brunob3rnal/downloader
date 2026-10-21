"""Publicación sin sobrescribir en la carpeta de destino (research R7, FR-022)."""

import errno
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from yt_dlp.utils import sanitize_filename

# Errores con los que `os.link` indica que el sistema de archivos no admite enlaces duros.
_SIN_ENLACES = {errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK}
_MAX_SUFIJO = 10_000


@dataclass
class Publicacion:
    ruta: Path
    aviso: str | None = None  # si se publicó con otro nombre porque el pedido ya existía


def nombre_publicado(titulo: str, id_video: str, extension: str) -> str:
    """`<titulo> [<id_video>].<ext>`, válido como nombre de archivo."""
    return sanitize_filename(f"{titulo} [{id_video}].{extension}")


def _candidatos(nombre: str):
    base, extension = os.path.splitext(nombre)
    yield nombre
    for n in range(1, _MAX_SUFIJO):
        yield f"{base} ({n}){extension}"


def publicar(origen: Path, carpeta: Path, nombre: str) -> Publicacion:
    """Mueve `origen` a `carpeta/nombre` sin sobrescribir nunca un archivo existente.

    `os.link` falla si el destino existe, y entonces se prueba `nombre (1).ext`,
    `nombre (2).ext`…; el nombre final aparece de golpe y completo. Si el sistema de archivos no
    admite enlaces duros, se crea el destino con `O_CREAT | O_EXCL` y se copia. Nunca se usa
    `os.rename` ni `os.replace`, que sobrescriben en POSIX.
    """
    origen, carpeta = Path(origen), Path(carpeta)
    enlazar = True
    for candidato in _candidatos(nombre):
        destino = carpeta / candidato
        try:
            if enlazar:
                try:
                    os.link(origen, destino)
                except OSError as error:
                    if error.errno not in _SIN_ENLACES:
                        raise
                    enlazar = False
            if not enlazar:
                _copiar_exclusivo(origen, destino)
        except FileExistsError:
            continue
        os.unlink(origen)
        aviso = None
        if candidato != nombre:
            aviso = f"ya existía «{nombre}»; se publicó como «{candidato}» sin tocar el anterior"
        return Publicacion(destino, aviso)
    raise FileExistsError(f"no hay ningún nombre libre para «{nombre}» en {carpeta}")


def _copiar_exclusivo(origen: Path, destino: Path) -> None:
    descriptor = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as salida, open(origen, "rb") as entrada:
            shutil.copyfileobj(entrada, salida)
            salida.flush()
            os.fsync(salida.fileno())
    except BaseException:
        destino.unlink(missing_ok=True)  # lo creamos nosotros: no queda una copia a medias
        raise
