"""Actualización de yt-dlp con uv, verificada con los tests de sus internos (FR-025, R14).

`uv lock --upgrade-package yt-dlp` y `uv sync` instalan la última versión; después se ejecutan
los tests marcados `ytdlp_internals`, que prueban el comportamiento interno de yt-dlp del que
depende el programa. Si fallan, se restaura el `uv.lock` anterior y se vuelve a sincronizar.
"""

import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from downloader.errors import ocultar_secretos

PAQUETE = "yt-dlp"
LOCK = ["lock", "--upgrade-package", PAQUETE]
SYNC = ["sync"]
PRUEBAS = ["run", "pytest", "-m", "ytdlp_internals", "-q", "-rfE", "-p", "no:cacheprovider"]
LINEAS_DE_ERROR = 15  # de la salida de uv o de pytest, como mucho, en el mensaje


class FalloActualizacion(Exception):
    pass


@dataclass(frozen=True)
class Resultado:
    anterior: str
    nueva: str  # igual que `anterior` si ya estaba al día


def raiz_proyecto(modulo: Path = Path(__file__)) -> Path | None:
    """La raíz del proyecto si `downloader` se ejecuta desde él.

    `uv sync` instala el paquete en modo editable, así que el código está en
    `<raíz>/src/downloader`, junto al `pyproject.toml` del paquete y su `uv.lock`. Instalado con
    `uv tool install` o `pip`, está en `site-packages` y no hay proyecto que actualizar.
    """
    raiz = Path(modulo).resolve().parents[2]
    pyproject, lock = raiz / "pyproject.toml", raiz / "uv.lock"
    if not (pyproject.is_file() and lock.is_file()):
        return None
    try:
        nombre = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
    except (tomllib.TOMLDecodeError, KeyError):
        return None
    return raiz if nombre == "downloader" else None


def version_bloqueada(lock: str) -> str:
    """La versión de yt-dlp en el contenido de un `uv.lock`."""
    for paquete in tomllib.loads(lock).get("package", []):
        if paquete.get("name") == PAQUETE:
            return paquete["version"]
    raise FalloActualizacion(f"uv.lock no incluye {PAQUETE}")


def ejecutar_uv(argumentos: list[str], raiz: Path) -> subprocess.CompletedProcess:
    uv = shutil.which("uv")
    if uv is None:
        raise FalloActualizacion(
            "no se encuentra uv en el PATH; instálelo (https://docs.astral.sh/uv/) y vuelva a "
            "intentarlo"
        )
    return subprocess.run(
        [uv, *argumentos], cwd=raiz, capture_output=True, text=True, errors="replace"
    )


def _final(texto: str) -> str:
    lineas = [linea for linea in texto.strip().splitlines() if linea.strip()]
    return ocultar_secretos("\n".join(lineas[-LINEAS_DE_ERROR:]))


def _uv(argumentos: list[str], raiz: Path) -> None:
    resultado = ejecutar_uv(argumentos, raiz)
    if resultado.returncode != 0:
        detalle = _final(resultado.stderr or resultado.stdout) or f"código {resultado.returncode}"
        raise FalloActualizacion(f"`uv {' '.join(argumentos)}` falló:\n{detalle}")


def _comprobar_ffmpeg() -> None:
    ausentes = [programa for programa in ("ffmpeg", "ffprobe") if shutil.which(programa) is None]
    if ausentes:
        raise FalloActualizacion(
            f"no se encuentra {' ni '.join(ausentes)} en el PATH. `actualizar` verifica la "
            "versión nueva de yt-dlp con tests que descargan videos de prueba y los necesitan; "
            "sin ellos se omitirían y la verificación no valdría. Instale ffmpeg y vuelva a "
            "intentarlo. No se ha modificado nada."
        )


def actualizar(raiz: Path, avisar=lambda texto: None) -> Resultado:
    """Actualiza yt-dlp en el proyecto de `raiz` y lo verifica; `avisar` recibe el progreso.

    Lanza `FalloActualizacion` con el motivo si algo falla; en ese caso el `uv.lock` y el
    entorno quedan como estaban.
    """
    _comprobar_ffmpeg()
    lock = raiz / "uv.lock"
    contenido = lock.read_bytes()
    anterior = version_bloqueada(contenido.decode("utf-8"))
    try:
        _uv(LOCK, raiz)
    except FalloActualizacion:
        lock.write_bytes(contenido)  # uv solo lo escribe si termina bien; por si acaso
        raise
    nueva = version_bloqueada(lock.read_text(encoding="utf-8"))
    if nueva == anterior:
        return Resultado(anterior, nueva)
    try:
        _uv(SYNC, raiz)
        avisar(f"comprobando yt-dlp {nueva} con los tests de su comportamiento interno…")
        pruebas = ejecutar_uv(PRUEBAS, raiz)
        if pruebas.returncode != 0:
            raise FalloActualizacion(
                f"yt-dlp {nueva} no supera los tests de su comportamiento interno "
                f"(pytest -m ytdlp_internals):\n{_final(pruebas.stdout + pruebas.stderr)}"
            )
    except FalloActualizacion as error:
        raise FalloActualizacion(f"{error}\n{_restaurar(raiz, contenido, anterior)}") from None
    return Resultado(anterior, nueva)


def _restaurar(raiz: Path, contenido: bytes, anterior: str) -> str:
    """Vuelve al `uv.lock` anterior y sincroniza; devuelve qué se hizo, para el mensaje."""
    (raiz / "uv.lock").write_bytes(contenido)
    try:
        _uv(SYNC, raiz)
    except FalloActualizacion as error:
        return (
            f"Se ha restaurado uv.lock con yt-dlp {anterior}, pero no el entorno: {error}\n"
            f"Ejecute `uv sync` en {raiz} para terminar de restaurarlo."
        )
    return f"Se ha restaurado uv.lock y se conserva yt-dlp {anterior}."
