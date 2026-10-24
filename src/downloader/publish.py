"""Verificación y publicación sin sobrescribir en la carpeta de destino (research R7, R8,
FR-022, FR-023)."""

import errno
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from yt_dlp.utils import sanitize_filename

from downloader.errors import ErrorFatal, ErrorPermanente

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


# --- verificación antes de publicar (research R8, R22) ------------------------------------

INVALIDOS = "invalidos"  # subdirectorio del directorio de trabajo con los intentos inválidos


class VerificacionFallida(ErrorPermanente):
    """El archivo descargado no supera la verificación: no se publica (FR-023)."""


@dataclass(frozen=True)
class Formato:
    """Un formato que yt-dlp dio por terminado, con su tamaño en disco en ese momento."""

    archivo: Path
    tamano: int
    total: int | None  # `total_bytes` exacto; `None` si solo hubo una estimación


def formato_terminado(progreso) -> Formato | None:
    """Anota un evento `finished` del hook de progreso (un `ytdl.Progreso`).

    El tamaño se mide ahora: después yt-dlp une los formatos, remuxa el HLS o extrae el audio,
    y el archivo cambia de tamaño o desaparece (R8).
    """
    if not progreso.archivo:
        return None
    archivo = Path(progreso.archivo)
    return Formato(archivo, archivo.stat().st_size, progreso.total_exacto)


def verificar(formatos, archivo_final: Path) -> None:
    """Lanza `VerificacionFallida` si el resultado no se puede publicar como completo.

    1. Cada formato con `total_bytes` exacto mide en disco exactamente eso.
    2. `ffprobe` lee el archivo final hasta el final sin errores, con paquetes en todos sus
       flujos y una duración positiva.
    """
    for formato in formatos:
        if formato.total is not None and formato.tamano != formato.total:
            raise VerificacionFallida(
                f"{formato.archivo.name} mide {formato.tamano} bytes y el servidor anunció "
                f"{formato.total}"
            )
    _comprobar_legible(Path(archivo_final))


def _comprobar_legible(archivo: Path) -> None:
    try:
        resultado = subprocess.run(
            ["ffprobe", "-v", "error", "-count_packets",
             "-show_entries", "format=duration:stream=nb_read_packets", "-of", "json",
             str(archivo)],
            capture_output=True, text=True, errors="replace",
        )
    except FileNotFoundError:
        raise ErrorFatal("ffprobe no está en el PATH; instale ffmpeg") from None
    errores = resultado.stderr.strip()
    if resultado.returncode != 0 or errores:
        detalle = errores.splitlines()[-1] if errores else f"código {resultado.returncode}"
        raise VerificacionFallida(f"ffprobe no puede leer el archivo hasta el final ({detalle})")
    try:
        datos = json.loads(resultado.stdout or "{}")
        paquetes = [int(f.get("nb_read_packets") or 0) for f in datos.get("streams") or []]
        duracion = float((datos.get("format") or {}).get("duration") or 0)
    except ValueError:
        raise VerificacionFallida("ffprobe devolvió una respuesta ilegible") from None
    if not paquetes or 0 in paquetes:
        raise VerificacionFallida("el archivo tiene flujos sin ningún paquete")
    if duracion <= 0:
        raise VerificacionFallida("el archivo no tiene duración")


def apartar_invalidos(trabajo: Path) -> Path:
    """Mueve los archivos del intento a `invalidos/<fecha>/` dentro de `trabajo`; no borra nada.

    Así, si se vuelve a encolar, yt-dlp no da por buenos esos archivos y descarga de nuevo.
    La fecha no lleva `:`, que no es válido en Windows.
    """
    trabajo = Path(trabajo)
    fecha = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    destino = trabajo / INVALIDOS / fecha
    destino.mkdir(parents=True)  # nuevo: nunca mezcla dos intentos
    for entrada in list(trabajo.iterdir()):
        if entrada.name != INVALIDOS:
            entrada.rename(destino / entrada.name)  # el destino está vacío: no sobrescribe
    return destino
