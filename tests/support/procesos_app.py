"""La aplicación como proceso aparte, para los tests de segundo plano e instancia única
(specs/002-desktop-app; tasks T086, T094).

`abrir_ventana` lanza `tests/support/ventana_headless.py`, lee sus líneas en un hilo y da un
cliente de su API. `trabajadores_de` encuentra los trabajadores de un `DOWNLOADER_HOME`, también
los que ya no son hijos de pytest porque la ventana que los lanzó murió; `terminar_trabajadores_de`
los mata al acabar el test.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from tests.support.cliente_app import ClienteApp

RAIZ = Path(__file__).resolve().parents[2]
MODULO_TRABAJADOR = "downloader.app.procesador"


class VentanaEnProceso:
    def __init__(self, home: Path, plazo: float = 30):
        self.proceso = subprocess.Popen(
            [sys.executable, "-m", "tests.support.ventana_headless"], cwd=RAIZ,
            env={**os.environ, "DOWNLOADER_HOME": str(home)},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8")
        self.lineas: list[str] = []
        self._hay = threading.Condition()
        threading.Thread(target=self._leer, daemon=True).start()
        linea = self.esperar_linea(lambda l: l.startswith("url "), plazo)
        self.url = linea.removeprefix("url ")
        self.cliente = ClienteApp(self.url)

    def _leer(self):
        for linea in self.proceso.stdout:
            with self._hay:
                self.lineas.append(linea.rstrip("\n"))
                self._hay.notify_all()

    def esperar_linea(self, condicion, plazo: float) -> str:
        limite = time.monotonic() + plazo
        terminado = False
        with self._hay:
            while True:
                for linea in self.lineas:
                    if condicion(linea):
                        return linea
                if terminado or time.monotonic() > limite:
                    raise AssertionError(f"la ventana no escribió la línea esperada en {plazo} s "
                                         f"(código {self.proceso.poll()}): {self.lineas}")
                terminado = self.proceso.poll() is not None  # una última vuelta tras terminar
                self._hay.wait(0.2)

    def matar(self) -> None:
        """Termina la ventana de golpe, como un cierre forzado (los trabajadores no son suyos)."""
        if self.proceso.poll() is None:
            self.proceso.kill()
        self.proceso.wait(timeout=30)

    def estado(self) -> dict:
        return self.cliente.get("api/estado").json()


def trabajadores_de(home: Path) -> list[psutil.Process]:
    """Los trabajadores vivos cuyo `DOWNLOADER_HOME` es `home` (el intérprete, sin su lanzador)."""
    encontrados = []
    for proceso in psutil.process_iter(["cmdline"]):
        try:
            if MODULO_TRABAJADOR not in (proceso.info["cmdline"] or ()):
                continue
            if proceso.environ().get("DOWNLOADER_HOME") == str(home):
                encontrados.append(proceso)
        except psutil.Error:
            continue
    return encontrados


def terminar_trabajadores_de(home: Path) -> None:
    vivos = trabajadores_de(home)
    for proceso in vivos:
        try:
            proceso.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(vivos, timeout=10)
