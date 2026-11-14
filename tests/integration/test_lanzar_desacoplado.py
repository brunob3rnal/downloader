"""Comprobación temprana de R8 (specs/002-desktop-app, tarea T006, hallazgo O1).

El trabajador de la aplicación debe sobrevivir al proceso que lo lanza. En Windows se lanza
fuera del objeto de trabajo (job object) del padre con `CREATE_BREAKAWAY_FROM_JOB`; si el
objeto de trabajo de `uv run` o del runner del CI no lo permite, `CreateProcess` falla con
`ERROR_ACCESS_DENIED` y este test lo pone de manifiesto antes de construir nada encima. En
Linux, `start_new_session=True`. Corre en los dos sistemas, bajo `uv run pytest` como en el CI.
"""

import json
import os
import subprocess
import sys
import textwrap
import time

import psutil
import pytest

# Proceso intermedio: lanza el hijo con los indicadores de R8, informa y termina enseguida.
INTERMEDIO = textwrap.dedent(
    """
    import json, subprocess, sys

    hijo = [sys.executable, "-c", "import time; time.sleep(60)"]
    if sys.platform == "win32":
        indicadores = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                       | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        try:
            proceso = subprocess.Popen(hijo, creationflags=indicadores,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            print(json.dumps({"error": getattr(error, "winerror", None), "mensaje": str(error)}))
            sys.exit(0)
    else:
        proceso = subprocess.Popen(hijo, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({"pid": proceso.pid}))
    """
)

ERROR_ACCESS_DENIED = 5


def _en_algun_objeto_de_trabajo(pid: int) -> bool:
    """Windows: si el proceso pertenece a algún objeto de trabajo (`IsProcessInJob`)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process_query_limited_information = 0x1000
    manejador = kernel32.OpenProcess(process_query_limited_information, False, pid)
    assert manejador, f"OpenProcess falló: {ctypes.get_last_error()}"
    try:
        resultado = wintypes.BOOL()
        assert kernel32.IsProcessInJob(manejador, None, ctypes.byref(resultado)), (
            f"IsProcessInJob falló: {ctypes.get_last_error()}"
        )
        return bool(resultado.value)
    finally:
        kernel32.CloseHandle(manejador)


def test_el_hijo_sobrevive_al_proceso_que_lo_lanza(record_property):
    salida = subprocess.run(
        [sys.executable, "-c", INTERMEDIO], capture_output=True, text=True, timeout=30,
        check=True,
    ).stdout
    datos = json.loads(salida)
    if "error" in datos:
        motivo = (
            "el objeto de trabajo no permite CREATE_BREAKAWAY_FROM_JOB"
            if datos["error"] == ERROR_ACCESS_DENIED else "CreateProcess falló"
        )
        pytest.fail(f"{motivo}: {datos['mensaje']} (research R8, consultar al usuario)")

    hijo = psutil.Process(datos["pid"])
    try:
        time.sleep(3)  # el intermedio ya ha terminado: `subprocess.run` esperó a que saliera
        assert hijo.is_running() and hijo.status() != psutil.STATUS_ZOMBIE
        if sys.platform == "win32":
            en_trabajo = _en_algun_objeto_de_trabajo(hijo.pid)
            record_property("hijo_en_objeto_de_trabajo", en_trabajo)
            print(f"\nhijo en algún objeto de trabajo: {en_trabajo}")
            assert not en_trabajo, "el hijo sigue dentro de un objeto de trabajo (research R8)"
        else:
            assert os.getsid(hijo.pid) == hijo.pid  # su propia sesión: no recibe las señales
    finally:
        hijo.kill()
