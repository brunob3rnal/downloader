"""Línea de comandos: `agregar`, `ejecutar` y `actualizar` (contracts/cli.md)."""

import logging
import shutil
import signal
import sys
import threading
from pathlib import Path
from typing import Annotated

import typer

from downloader import actualizacion, config, publish, worker, ytdl
from downloader.errors import ErrorFatal, ocultar_secretos
from downloader.models import Calidad
from downloader.network import MonitorRed
from downloader.progress import Presentador
from downloader.store import BaseCorrupta, EjecucionEnCurso, Store

# Códigos de salida (contracts/cli.md).
CON_FALLIDAS = 1
ACTUALIZACION_FALLIDA = 1  # `actualizar`
USO_INVALIDO = 2
FATAL = 3
INTERRUMPIDO = 130

# `--cookies-de` (FR-016, research R12): solo el nombre del navegador; nunca se guardan cookies.
CookiesDe = Annotated[
    str | None,
    typer.Option(
        "--cookies-de", metavar="NAVEGADOR", show_default=False,
        help="Lee las cookies de ese navegador (p. ej. firefox) para videos que exigen su "
        "sesión. Solo se leen al descargar; nunca se guardan.",
    ),
]

app = typer.Typer(
    help="Gestor de descargas de video resiliente.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def principal() -> None:
    # Los mensajes de yt-dlp ya llegan resumidos en la salida de `ejecutar`.
    logging.getLogger("downloader").addHandler(logging.NullHandler())
    # En Windows, con la salida redirigida a un archivo o una tubería, Python escribe en la
    # codificación ANSI (cp1252): un título fuera de ella no debe detener el programa.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="backslashreplace")


def _error(mensaje: str, codigo: int) -> typer.Exit:
    typer.echo(f"error: {ocultar_secretos(mensaje)}", err=True)
    return typer.Exit(codigo)


def _configuracion() -> config.Configuracion:
    try:
        return config.cargar()
    except config.ConfiguracionInvalida as error:
        raise _error(str(error), USO_INVALIDO) from None


def _abrir_cola() -> Store:
    try:
        return Store.abrir(config.directorio_app())
    except BaseCorrupta as error:
        raise _error(str(error), FATAL) from None


def _calidad(valor: str | None, por_defecto: Calidad) -> Calidad:
    if valor is None:
        return por_defecto
    try:
        return Calidad(valor)
    except ValueError:
        admitidas = ", ".join(c.value for c in Calidad)
        raise _error(f"calidad desconocida «{valor}»; admitidas: {admitidas}", USO_INVALIDO) from None


def _navegador(valor: str | None) -> str | None:
    """El nombre del navegador, si es uno de los que admite yt-dlp."""
    if valor is None:
        return None
    navegador = valor.strip().lower()
    if navegador not in ytdl.NAVEGADORES:
        admitidos = ", ".join(sorted(ytdl.NAVEGADORES))
        raise _error(
            f"navegador no admitido en --cookies-de: «{valor}»; admitidos: {admitidos}",
            USO_INVALIDO,
        )
    return navegador


def _carpeta(valor: Path | None, por_defecto: Path) -> Path:
    carpeta = (valor or por_defecto).expanduser().resolve()
    if not carpeta.is_dir():
        raise _error(f"la carpeta de destino {carpeta} no existe", USO_INVALIDO)
    if not publish.escribible(carpeta):
        raise _error(f"sin permiso de escritura en la carpeta de destino {carpeta}", USO_INVALIDO)
    return carpeta


@app.command()
def agregar(
    urls: Annotated[list[str], typer.Argument(help="Una o varias URLs de videos o listas.")],
    calidad: Annotated[
        str | None, typer.Option(help="mejor, 1080p, 720p o audio.", show_default=False)
    ] = None,
    carpeta: Annotated[
        Path | None, typer.Option(help="Carpeta de destino.", show_default=False)
    ] = None,
    cookies_de: CookiesDe = None,
) -> None:
    """Guarda URLs en la cola. No necesita red."""
    if any(not url.strip() for url in urls):
        raise _error("URL vacía", USO_INVALIDO)
    navegador = _navegador(cookies_de)
    ajustes = _configuracion()
    calidad_elegida = _calidad(calidad, ajustes.calidad)
    carpeta_elegida = _carpeta(carpeta, ajustes.carpeta_destino)

    store = _abrir_cola()
    try:
        for url in urls:
            resultado = store.agregar(
                url.strip(), calidad_elegida, str(carpeta_elegida), navegador_cookies=navegador
            )
            descarga = resultado.descarga
            url_visible = ocultar_secretos(descarga.url)
            if resultado.accion == "agregada":
                typer.echo(f"agregada #{descarga.id}  {url_visible}  ({descarga.calidad.value})")
            elif resultado.accion == "reencolada":
                typer.echo(f"reencolada #{descarga.id}  {url_visible}  (antes: failed)")
            else:
                typer.echo(
                    f"ya en cola #{descarga.id}  {url_visible}  "
                    f"({descarga.calidad.value}, {descarga.estado.value})"
                )
    finally:
        store.cerrar()


def _comprobar_ffmpeg() -> None:
    """ffmpeg une y convierte los formatos y ffprobe verifica antes de publicar (R8)."""
    ausentes = [programa for programa in ("ffmpeg", "ffprobe") if shutil.which(programa) is None]
    if ausentes:
        raise _error(
            f"no se encuentra {' ni '.join(ausentes)} en el PATH. Instale ffmpeg, que incluye "
            "ffprobe, y vuelva a ejecutar:\n"
            "  Windows: winget install Gyan.FFmpeg  (o: choco install ffmpeg)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg  (Debian, Ubuntu; o el gestor de su distribución)\n"
            "La cola no se ha modificado.",
            FATAL,
        )


@app.command()
def ejecutar(cookies_de: CookiesDe = None) -> None:
    """Procesa la cola en primer plano hasta que no queden descargas pendientes.

    `--cookies-de` vale para esta ejecución y prevalece sobre el navegador de cada descarga.
    """
    navegador = _navegador(cookies_de)
    if hasattr(signal, "SIGBREAK") and threading.current_thread() is threading.main_thread():
        # Windows: Ctrl+Break, y `CTRL_BREAK_EVENT`, el único evento que se puede enviar a otro
        # grupo de procesos, interrumpen igual que Ctrl+C (`KeyboardInterrupt`). `signal.signal`
        # solo puede llamarse desde el hilo principal, y las señales solo llegan a él.
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    ajustes = _configuracion()
    _comprobar_ffmpeg()  # antes de abrir la cola: sin ffmpeg no se toca (ni se recupera)
    store = _abrir_cola()
    try:
        try:
            store.bloquear_ejecucion()
        except EjecucionEnCurso as error:
            raise _error(str(error), USO_INVALIDO) from None
        # Lo que un cierre abrupto dejó en `downloading` o `waiting_network` vuelve a la cola;
        # el bloqueo garantiza que no es de otro `ejecutar` en marcha (R11).
        store.recuperar()
        monitor = MonitorRed(ajustes.sondas_red)
        monitor.iniciar()
        try:
            tiempos = worker.Tiempos(
                estancamiento=ajustes.estancamiento_segundos,
                velocidad_minima=ajustes.estancamiento_velocidad_minima,
            )
            resultado = worker.ejecutar(
                store, monitor, Presentador(), tiempos, navegador, ajustes.plantilla_nombre
            )
        except ErrorFatal as error:
            raise _error(f"{error}. El progreso se ha conservado.", FATAL) from None
        except KeyboardInterrupt:
            typer.echo("interrumpido; progreso guardado", err=True)
            raise typer.Exit(INTERRUMPIDO) from None
        finally:
            monitor.detener()
    finally:
        store.cerrar()
    if resultado.fallidas:  # la cola se vació, pero no todo se completó (FR-021)
        raise typer.Exit(CON_FALLIDAS)


@app.command()
def actualizar() -> None:
    """Actualiza yt-dlp a su última versión dentro del proyecto y la verifica.

    Tras `uv lock --upgrade-package yt-dlp` y `uv sync`, ejecuta los tests del comportamiento
    interno de yt-dlp (`pytest -m ytdlp_internals`); si fallan, vuelve a la versión anterior.
    """
    raiz = actualizacion.raiz_proyecto()
    if raiz is None:
        raise _error(
            "downloader no se está ejecutando desde su proyecto (con `uv run` en el directorio "
            "del repositorio), así que no hay un uv.lock que actualizar. Si lo instaló con "
            "`uv tool install`, actualícelo con: uv tool upgrade downloader",
            ACTUALIZACION_FALLIDA,
        )
    try:
        resultado = actualizacion.actualizar(raiz, avisar=typer.echo)
    except actualizacion.FalloActualizacion as error:
        raise _error(f"no se pudo actualizar yt-dlp: {error}", ACTUALIZACION_FALLIDA) from None
    if resultado.nueva == resultado.anterior:
        typer.echo(f"yt-dlp ya está al día ({resultado.anterior})")
    else:
        typer.echo(f"yt-dlp {resultado.anterior} → {resultado.nueva}")
