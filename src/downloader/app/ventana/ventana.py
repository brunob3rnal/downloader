"""La ventana real, con pywebview y WebView2 (contracts/ventana.md; research R1, R14).

Implementa el protocolo `api.Ventana`: examinar carpeta, enfocar, ejecutar JavaScript y
destruir. `mostrar` bloquea hasta que el usuario cierra la ventana.
"""

import webview

from downloader.app import entorno_webview2

TITULO = "Gestor de descargas"
ANCHO, ALTO = 1100, 760
MINIMO = (720, 520)


class VentanaWebview:
    con_dialogo = True

    def __init__(self, url: str):
        self._ventana = webview.create_window(TITULO, url, width=ANCHO, height=ALTO,
                                              min_size=MINIMO)

    def mostrar(self) -> None:
        # Solo en el entorno de este proceso, que hereda el motor web; nunca persistente (R14).
        entorno_webview2.fijar()
        webview.start(gui="edgechromium", private_mode=True)

    def examinar_carpeta(self) -> str | None:
        elegidas = self._ventana.create_file_dialog(webview.FileDialog.FOLDER)
        if not elegidas:
            return None
        return elegidas[0] if isinstance(elegidas, (list, tuple)) else str(elegidas)

    def enfocar(self) -> None:
        self._ventana.restore()
        self._ventana.show()
        # Traer al frente: subir y bajar "siempre encima" (research R12).
        self._ventana.on_top = True
        self._ventana.on_top = False

    def ejecutar_js(self, codigo: str) -> None:
        self._ventana.evaluate_js(codigo)

    def destruir(self) -> None:
        self._ventana.destroy()


def crear(url: str) -> VentanaWebview:
    return VentanaWebview(url)
