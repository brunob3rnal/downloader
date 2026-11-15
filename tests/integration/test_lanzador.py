"""El lanzador `Gestor de descargas.vbs` no muestra ninguna consola (research R11; tasks T062).

Solo Windows (constitución v3.1.0). Sin el marcador `slow`: `tests/unit/test_marcadores.py`
(001) fija la lista exacta de tests lentos.
"""

import ctypes
import ctypes.wintypes
import subprocess
import time
from pathlib import Path

import psutil
import pytest

pytestmark = pytest.mark.solo_windows

RAIZ = Path(__file__).resolve().parents[2]
LANZADOR = RAIZ / "Gestor de descargas.vbs"
PLAZO = 30  # s


def _gestor():
    for proceso in psutil.process_iter(["name", "cmdline"]):
        try:
            if (proceso.info["name"] or "").lower() == "pythonw.exe" and any(
                    "gestor.pyw" in parte for parte in proceso.info["cmdline"] or ()):
                return proceso
        except psutil.Error:
            continue
    return None


def _ventanas_de_consola_visibles(pids):
    user32 = ctypes.windll.user32
    encontradas = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def visitar(hwnd, _):
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            clase = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, clase, 256)
            if clase.value == "ConsoleWindowClass":
                encontradas.append((pid.value, clase.value))
        return True

    user32.EnumWindows(visitar, 0)
    return encontradas


def _cadena(proceso):
    """El proceso, todos sus descendientes y sus antecesores de la cadena del lanzador (uv,
    wscript), sin llegar nunca a este pytest ni a sus antecesores."""
    propios = psutil.Process()
    excluidos = {propios.pid, *(p.pid for p in propios.parents())}
    procesos = [proceso, *proceso.children(recursive=True)]
    for padre in proceso.parents():
        if padre.pid in excluidos or padre.name().lower() not in {
                "uv.exe", "uvw.exe", "wscript.exe", "python.exe", "pythonw.exe"}:
            break
        procesos.append(padre)
    return procesos


def test_doble_clic_sin_consola(app_home):
    assert LANZADOR.exists()
    script = subprocess.Popen(["wscript.exe", "//B", str(LANZADOR)], cwd=RAIZ)
    gestor = None
    try:
        limite = time.monotonic() + PLAZO
        while gestor is None and time.monotonic() < limite:
            gestor = _gestor()
            time.sleep(0.25)
        assert gestor is not None, f"no apareció pythonw.exe con gestor.pyw en {PLAZO} s"
        time.sleep(1)  # que la cadena termine de abrir lo que fuera a abrir
        cadena = _cadena(gestor)
        pids = {p.pid for p in cadena} | {script.pid}
        assert _ventanas_de_consola_visibles(pids) == []
    finally:
        procesos = []
        if gestor is not None:
            try:
                procesos = _cadena(gestor)
            except psutil.Error:
                procesos = [gestor]
        for proceso in procesos:
            try:
                proceso.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(procesos, timeout=10)
        if script.poll() is None:
            script.kill()
        script.wait(10)
