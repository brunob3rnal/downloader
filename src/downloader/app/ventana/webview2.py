"""Comprobación del motor de la ventana antes de crearla (research R2).

pywebview elige el motor al importar su backend WinForms y, si no encuentra WebView2, cae en
silencio al motor MSHTML, con el que la interfaz no funciona. Aquí se comprueba el motor y, si
no es `edgechromium`, se avisa con un cuadro de mensaje nativo, que no necesita la ventana web.
"""

import ctypes

TITULO = "Gestor de descargas"
TEXTO = (
    "Falta Microsoft Edge WebView2 Runtime, que la ventana necesita para mostrarse. "
    "Instálelo con `winget install Microsoft.EdgeWebView2Runtime` o desde "
    "https://developer.microsoft.com/microsoft-edge/webview2/ y vuelva a abrir la aplicación. "
    "La línea de comandos (`uv run downloader`) sigue funcionando."
)
SIN_RUNTIME = "WebView2 Runtime no encontrado"
MB_ICONERROR = 0x10


def comprobar() -> str | None:
    """`None` si el motor es `edgechromium`; si no, la causa.

    La causa empieza por `SIN_RUNTIME` si pywebview eligió otro motor, o es el error de la
    importación del backend (pythonnet sin .NET, por ejemplo).
    """
    try:
        from webview.platforms import winforms
    except Exception as error:  # noqa: BLE001 — cualquier fallo del backend impide la ventana
        return f"{type(error).__name__}: {error}"
    renderer = getattr(winforms, "renderer", None)
    if renderer == "edgechromium":
        return None
    return f"{SIN_RUNTIME} (motor de pywebview: {renderer})"


def mensaje(causa: str) -> str:
    """El texto de R2; con la causa añadida si no es la falta del runtime."""
    if causa.startswith(SIN_RUNTIME):
        return TEXTO
    return f"{TEXTO}\n\nCausa: {causa}"


def mostrar_error(texto: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, texto, TITULO, MB_ICONERROR)
