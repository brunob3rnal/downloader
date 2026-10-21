"""Publicación sin sobrescribir en la carpeta de destino (research R7, FR-022)."""

import errno
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yt_dlp.utils import sanitize_filename

# Errores con los que `os.link` indica que el sistema de archivos no admite enlaces duros.
# En Windows (FAT32, exFAT, algunas unidades de red) llegan como `winerror`, cuya traducción a
# `errno` es genérica (EINVAL): ERROR_INVALID_FUNCTION, ERROR_ACCESS_DENIED,
# ERROR_NOT_SAME_DEVICE, ERROR_NOT_SUPPORTED y ERROR_TOO_MANY_LINKS.
_SIN_ENLACES = {errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK}
_SIN_ENLACES_WINDOWS = {1, 5, 17, 50, 1142}
_MAX_SUFIJO = 10_000
_RESERVA_SUFIJO = len(f" ({_MAX_SUFIJO - 1})")

# Límites de longitud: 255 bytes por nombre (ext4; en NTFS son 255 unidades UTF-16, que nunca
# superan a los bytes UTF-8) y, en Windows, 259 caracteres por ruta (MAX_PATH sin el NUL).
LIMITE_NOMBRE = 255
LIMITE_RUTA = 259 if os.name == "nt" else None

# Nombres de dispositivo que Windows no admite como nombre de archivo, con cualquier extensión.
_RESERVADOS = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefijo}{n}" for prefijo in ("COM", "LPT") for n in [*"123456789", "¹", "²", "³"]
}


@dataclass
class Publicacion:
    ruta: Path
    aviso: str | None = None  # si se publicó con otro nombre porque el pedido ya existía


def _unidades_windows(texto: str) -> int:
    return len(texto.encode("utf-16-le")) // 2


def nombre_publicado(
    titulo: str,
    id_video: str,
    extension: str,
    carpeta: Path | str | None = None,
    limite_ruta: int | None = LIMITE_RUTA,
) -> str:
    """`<titulo> [<id_video>].<ext>`, válido como nombre de archivo en Windows y en Linux.

    `sanitize_filename` sustituye los caracteres no válidos en Windows (`: ? | " < > * / \\`).
    Si el nombre, con hueco para el sufijo ` (N)` de `publicar`, no cabe en `LIMITE_NOMBRE`
    bytes o la ruta en `carpeta` supera `limite_ruta`, se acorta el título. Si lo que queda antes
    del primer punto es un nombre reservado de Windows (`CON`, `NUL`, `COM1`…), se antepone `_`.
    """
    cola = sanitize_filename(f"[{id_video}].{extension}")
    titulo = sanitize_filename(titulo) if titulo else ""
    prefijo = _unidades_windows(str(Path(carpeta))) + 1 if carpeta is not None else 0

    def cabe(nombre: str) -> bool:
        if len(nombre.encode()) + _RESERVA_SUFIJO > LIMITE_NOMBRE:
            return False
        if limite_ruta is None or carpeta is None:
            return True
        return prefijo + _unidades_windows(nombre) + _RESERVA_SUFIJO <= limite_ruta

    def unir(titulo: str) -> str:
        nombre = f"{titulo} {cola}" if titulo else cola
        if nombre.split(".")[0].rstrip(" ").upper() in _RESERVADOS:
            nombre = f"_{nombre}"
        return nombre

    while titulo and not cabe(unir(titulo)):
        titulo = titulo[:-1].rstrip(" .")
    return unir(titulo)


def escribible(carpeta: Path | str) -> bool:
    """Si se puede crear un archivo en `carpeta`.

    Se prueba creando uno temporal: `os.access` no sirve en Windows, donde ignora los permisos
    (ACL) de las carpetas y siempre responde que se puede escribir.
    """
    try:
        with tempfile.TemporaryFile(dir=carpeta):
            return True
    except OSError:
        return False


def _candidatos(nombre: str):
    base, extension = os.path.splitext(nombre)
    yield nombre
    for n in range(1, _MAX_SUFIJO):
        yield f"{base} ({n}){extension}"


def publicar(origen: Path, carpeta: Path, nombre: str) -> Publicacion:
    """Mueve `origen` a `carpeta/nombre` sin sobrescribir nunca un archivo existente.

    `os.link` falla si el destino existe, y entonces se prueba `nombre (1).ext`,
    `nombre (2).ext`…; el nombre final aparece de golpe y completo. Si el sistema de archivos no
    admite enlaces duros (FAT32, exFAT), se crea el destino con `O_CREAT | O_EXCL` y se copia.
    Funciona igual en NTFS y en Linux. Nunca se usa `os.rename` ni `os.replace`: sobrescriben
    en POSIX (y `os.replace` también en Windows).
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
                    if not _sin_enlaces(error):
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


def _sin_enlaces(error: OSError) -> bool:
    return error.errno in _SIN_ENLACES or getattr(error, "winerror", None) in _SIN_ENLACES_WINDOWS


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
