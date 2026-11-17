"""La aplicación sin ventana real, como proceso, para los tests (specs/002-desktop-app; tasks T086).

`python -m tests.support.ventana_headless` hace lo mismo que `principal.main()` salvo crear la
ventana de pywebview: configura el registro y llama a `principal.arrancar` con una ventana falsa
que se queda abierta hasta que la destruyen o matan el proceso.

Escribe una línea por suceso: `url <url de la página con el secreto>`, y `enfocar` o `destruir`
cuando se llaman. Van a la salida estándar y, si existe `VENTANA_HEADLESS_SALIDA`, también a ese
archivo: con `pythonw.exe` (`uv run --gui-script`) no hay salida estándar. Solo para tests: la
URL lleva el secreto de la sesión.
"""

import os
import sys
import threading

from tests.support.ventana_falsa import VentanaFalsa

SALIDA = "VENTANA_HEADLESS_SALIDA"


def _escribir(linea: str) -> None:
    print(linea, flush=True)  # sin salida estándar (pythonw), no hace nada
    ruta = os.environ.get(SALIDA)
    if ruta:
        with open(ruta, "a", encoding="utf-8") as archivo:
            archivo.write(linea + "\n")


class VentanaHeadless(VentanaFalsa):
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self._cerrada = threading.Event()
        _escribir(f"url {url}")

    def mostrar(self) -> None:
        self._cerrada.wait()

    def enfocar(self) -> None:
        super().enfocar()
        _escribir("enfocar")

    def destruir(self) -> None:
        super().destruir()
        _escribir("destruir")
        self._cerrada.set()


def main() -> int:
    from downloader.app import principal

    return principal.abrir(VentanaHeadless)


if __name__ == "__main__":
    sys.exit(main())
