"""Arranque de la aplicación de escritorio (research R3, R4, R12, R19).

`arrancar` es el único camino de arranque, con este orden: instancia única (Fase 12),
comprobación del motor de la ventana (WebView2 en Windows), servidor y ventana. `main` solo
importa la capa de Windows (`ventana/`) en Windows (constitución v3.1.0).
"""

import secrets
import sys
from pathlib import Path

from downloader import config
from downloader.app import registro
from downloader.app.api import Api
from downloader.app.servidor import Servidor

SOLO_WINDOWS = "La ventana solo está disponible en Windows; use la CLI: uv run downloader"
SIN_MOTOR = 3


def arrancar_servidor(ventana, directorio=None):
    """El servidor local, ya escuchando, y la URL de la página con el secreto de la sesión.

    El secreto (256 bits) solo vive en memoria: ni en disco ni en el registro (research R4).
    """
    directorio = Path(directorio) if directorio is not None else config.directorio_app()
    servidor = Servidor(Api(directorio, ventana), secrets.token_urlsafe(32))
    servidor.iniciar()
    return servidor, servidor.url


def arrancar(crear_ventana, comprobar_motor=None, directorio=None) -> int:
    """Arranca y bloquea hasta que la ventana se cierra; devuelve el código de salida."""
    if comprobar_motor is not None and not comprobar_motor():
        return SIN_MOTOR
    servidor, url = arrancar_servidor(None, directorio)
    try:
        ventana = crear_ventana(url)
        servidor.api.ventana = ventana
        ventana.mostrar()  # hasta que se cierra
    finally:
        servidor.cerrar()
    return 0


def abrir(crear_ventana, comprobar_motor=None, al_fallar=None) -> int:
    """El camino de `main()` con cualquier ventana: el registro de errores y `arrancar`.

    Lo usan `main()` con la ventana de pywebview y los tests con una ventana falsa en un
    proceso propio (`tests/support/ventana_headless.py`). Con `al_fallar`, una excepción al
    crear o mostrar la ventana se le entrega, con el registro aún abierto, y se sale con 3.
    """
    manejo = registro.configurar("ventana", config.directorio_app())
    try:
        try:
            return arrancar(crear_ventana, comprobar_motor)
        except Exception as error:  # noqa: BLE001 — sin consola, nadie vería la traza (R2)
            if al_fallar is None:
                raise
            al_fallar(error)
            return SIN_MOTOR
    finally:
        manejo.cerrar()


def main() -> int:
    if sys.platform == "win32":
        # Solo aquí y solo en Windows: la capa de la ventana (constitución v3.1.0).
        from downloader.app import ventana

        return abrir(ventana.crear, ventana.comprobar_motor, ventana.fallo_al_abrir)
    print(SOLO_WINDOWS, file=sys.stderr)
    return 2
