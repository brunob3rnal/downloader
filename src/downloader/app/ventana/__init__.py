"""Capa de la ventana, exclusiva de Windows (constitución v3.1.0; contracts/ventana.md).

Solo la importa `principal.main()` en Windows. Todo lo demás de la aplicación (servidor, API,
procesador) es multiplataforma y se prueba en Linux con una ventana falsa.
"""

import logging

from downloader.app.ventana import webview2

_registro = logging.getLogger("downloader.app")


def comprobar_motor() -> bool:
    """`True` si WebView2 está disponible; si no, avisa con un cuadro nativo y lo registra."""
    causa = webview2.comprobar()
    if causa is None:
        return True
    _registro.error("la ventana no puede abrirse: %s", causa)
    webview2.mostrar_error(webview2.mensaje(causa))
    return False


def fallo_al_abrir(error: BaseException) -> None:
    """Cualquier otra excepción al crear o arrancar la ventana: el mismo camino (R2)."""
    _registro.error("la ventana no puede abrirse", exc_info=error)
    webview2.mostrar_error(webview2.mensaje(f"{type(error).__name__}: {error}"))


def crear(url: str):
    from downloader.app.ventana import ventana

    return ventana.crear(url)
