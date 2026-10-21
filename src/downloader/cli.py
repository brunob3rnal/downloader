"""Línea de comandos: `agregar` y `ejecutar` (contracts/cli.md)."""

import logging
import signal
import sys
from pathlib import Path
from typing import Annotated

import typer

from downloader import config, publish, worker
from downloader.errors import ErrorFatal, ocultar_secretos
from downloader.models import Calidad
from downloader.network import MonitorRed
from downloader.progress import Presentador
from downloader.store import BaseCorrupta, EjecucionEnCurso, Store

# Códigos de salida (contracts/cli.md).
USO_INVALIDO = 2
FATAL = 3

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
) -> None:
    """Guarda URLs en la cola. No necesita red."""
    if any(not url.strip() for url in urls):
        raise _error("URL vacía", USO_INVALIDO)
    ajustes = _configuracion()
    calidad_elegida = _calidad(calidad, ajustes.calidad)
    carpeta_elegida = _carpeta(carpeta, ajustes.carpeta_destino)

    store = _abrir_cola()
    try:
        for url in urls:
            resultado = store.agregar(url.strip(), calidad_elegida, str(carpeta_elegida))
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


@app.command()
def ejecutar() -> None:
    """Procesa la cola en primer plano hasta que no queden descargas pendientes."""
    if hasattr(signal, "SIGBREAK"):
        # Windows: Ctrl+Break, y `CTRL_BREAK_EVENT`, el único evento que se puede enviar a otro
        # grupo de procesos, interrumpen igual que Ctrl+C (`KeyboardInterrupt`).
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    ajustes = _configuracion()
    store = _abrir_cola()
    try:
        try:
            store.bloquear_ejecucion()
        except EjecucionEnCurso as error:
            raise _error(str(error), USO_INVALIDO) from None
        monitor = MonitorRed(ajustes.sondas_red)
        monitor.iniciar()
        try:
            worker.ejecutar(store, monitor, Presentador())
        except ErrorFatal as error:
            raise _error(f"{error}. El progreso se ha conservado.", FATAL) from None
        finally:
            monitor.detener()
    finally:
        store.cerrar()
