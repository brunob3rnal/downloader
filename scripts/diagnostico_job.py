"""Diagnóstico de R8: ¿sobrevive el trabajador a la cadena que lo lanza? (specs/002-desktop-app).

Opción A de la consulta sobre la Fase 1: en el CI de Windows, un hijo lanzado con
`CREATE_BREAKAWAY_FROM_JOB` sobrevivía, pero seguía dentro de un objeto de trabajo. Este script
lo comprueba en el equipo del usuario, sin runner de por medio, por los tres caminos que puede
seguir la aplicación:

- `python directo`: el `python.exe` del entorno virtual;
- `uv run`: `uv run python …`;
- `uv run --gui-script`: el camino del lanzador (`Gestor de descargas.vbs`, research R11).

Por cada camino, un proceso intermedio lanza un hijo con los indicadores de R8, como hará la
aplicación con el trabajador, en dos escenarios: la cadena termina con normalidad, o se mata a
la fuerza su proceso superior (el que lanza este script). Se informa de si el hijo queda en un
objeto de trabajo y, lo que decide, de si sobrevive. Al terminar no quedan procesos.

    .venv\\Scripts\\python.exe scripts\\diagnostico_job.py

Se ejecuta con el Python del entorno y no con `uv run`, para que el propio diagnóstico no
quede dentro del objeto de trabajo de uv. Sale con 0 si el hijo sobrevive en todos los casos,
con 1 si no, y con 2 si el diagnóstico no se pudo hacer.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

RAIZ = Path(__file__).resolve().parents[1]
CAMINOS = ("python directo", "uv run", "uv run --gui-script")
ESCENARIOS = ("fin normal", "matar el superior")
ESPERA_SUPERVIVENCIA = 3  # s tras el fin de la cadena antes de comprobar el hijo

# Proceso intermedio: lanza el hijo con los indicadores de R8, escribe el informe y, según el
# modo, termina enseguida o espera a que lo maten. Con `--gui-script` corre en `pythonw.exe`,
# sin salida estándar: por eso informa en un archivo.
INTERMEDIO = r'''
import json, os, subprocess, sys, time
from pathlib import Path

informe, modo = Path(sys.argv[1]), sys.argv[2]
interprete = Path(sys.executable)
if interprete.name.lower() == "pythonw.exe":
    interprete = interprete.with_name("python.exe")
hijo = [str(interprete), "-c", "import time; time.sleep(300)"]
datos = {"intermedio": os.getpid()}
nulos = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    if sys.platform == "win32":
        indicadores = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                       | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        proceso = subprocess.Popen(hijo, creationflags=indicadores, **nulos)
    else:
        proceso = subprocess.Popen(hijo, start_new_session=True, **nulos)
    datos["hijo"] = proceso.pid
except OSError as error:
    datos["error"] = f"{error} (winerror {getattr(error, 'winerror', None)})"
temporal = informe.with_suffix(".tmp")
temporal.write_text(json.dumps(datos), encoding="utf-8")
os.replace(temporal, informe)
if modo == "esperar":
    time.sleep(300)
'''


@dataclass
class Resultado:
    camino: str
    escenario: str
    error: str | None  # no se pudo crear el hijo o hacer el caso
    hijo_en_trabajo: bool | None  # None: no aplica (fuera de Windows) o no se pudo saber
    intermedio_en_trabajo: bool | None
    hijo_sobrevive: bool | None
    intermedio_sobrevive: bool | None  # solo al matar el superior


def _si_no(valor) -> str:
    return {True: "sí", False: "no"}.get(valor, "—")


def conclusion(resultados) -> dict:
    """Por camino, si el hijo sobrevive en todos sus escenarios. Lo que decide es esto."""
    caminos: dict = {}
    for r in resultados:
        caminos[r.camino] = caminos.get(r.camino, True) and r.error is None and bool(
            r.hijo_sobrevive
        )
    return caminos


def codigo_de_salida(resultados) -> int:
    if not resultados:
        return 2
    return 0 if all(conclusion(resultados).values()) else 1


def tabla(resultados) -> list[str]:
    lineas = ["camino | escenario | hijo en objeto de trabajo | intermedio en objeto de trabajo"
              " | HIJO SOBREVIVE | intermedio sobrevive | error"]
    for r in resultados:
        lineas.append(" | ".join([
            r.camino, r.escenario, _si_no(r.hijo_en_trabajo), _si_no(r.intermedio_en_trabajo),
            _si_no(r.hijo_sobrevive), _si_no(r.intermedio_sobrevive), r.error or "—",
        ]))
    return lineas


# --- ejecución -------------------------------------------------------------------------


def en_objeto_de_trabajo(pid: int) -> bool | None:
    """Windows: si el proceso pertenece a algún objeto de trabajo (`IsProcessInJob`)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    manejador = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not manejador:
        return None
    try:
        resultado = wintypes.BOOL()
        if not kernel32.IsProcessInJob(manejador, None, ctypes.byref(resultado)):
            return None
        return bool(resultado.value)
    finally:
        kernel32.CloseHandle(manejador)


def _vivo(pid) -> bool | None:
    if pid is None:
        return None
    try:
        proceso = psutil.Process(pid)
        return proceso.is_running() and proceso.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _matar(pid) -> None:
    if pid is None:
        return
    try:
        proceso = psutil.Process(pid)
        for p in [*proceso.children(recursive=True), proceso]:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


def _orden(camino: str, intermedio: Path, informe: Path, modo: str, uv: str | None) -> list:
    argumentos = [str(intermedio), str(informe), modo]
    if camino == "python directo":
        return [sys.executable, *argumentos]
    if camino == "uv run":
        return [uv, "run", "python", *argumentos]
    return [uv, "run", "--gui-script", *argumentos]


def _esperar_informe(informe: Path, superior, segundos=90) -> dict:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if informe.exists():
            return json.loads(informe.read_text(encoding="utf-8"))
        if superior.poll() is not None and not informe.exists():
            time.sleep(0.5)  # por si el informe llega justo después
            if not informe.exists():
                return {"error": f"la cadena terminó con el código {superior.returncode} "
                                 "sin lanzar el hijo"}
        time.sleep(0.2)
    return {"error": f"sin informe del intermedio en {segundos} s"}


def caso(camino: str, escenario: str, intermedio: Path, directorio: Path, uv) -> Resultado:
    modo = "terminar" if escenario == "fin normal" else "esperar"
    informe = directorio / f"{CAMINOS.index(camino)}-{ESCENARIOS.index(escenario)}.json"
    superior = subprocess.Popen(
        _orden(camino, intermedio, informe, modo, uv), cwd=RAIZ,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    datos: dict = {}
    try:
        datos = _esperar_informe(informe, superior)
        if "error" in datos:
            return Resultado(camino, escenario, datos["error"], None, None, None, None)
        hijo, intermedio_pid = datos["hijo"], datos["intermedio"]
        hijo_en_trabajo = en_objeto_de_trabajo(hijo)
        intermedio_en_trabajo = en_objeto_de_trabajo(intermedio_pid)
        intermedio_sobrevive = None
        if escenario == "fin normal":
            superior.wait(timeout=60)
        else:
            time.sleep(1)
            superior.kill()  # solo el superior (uv.exe o el lanzador de python), no su árbol
            superior.wait(timeout=30)
        time.sleep(ESPERA_SUPERVIVENCIA)
        if escenario == "matar el superior":
            intermedio_sobrevive = _vivo(intermedio_pid)
        return Resultado(camino, escenario, None, hijo_en_trabajo, intermedio_en_trabajo,
                         _vivo(hijo), intermedio_sobrevive)
    except subprocess.TimeoutExpired:
        return Resultado(camino, escenario, "la cadena no terminó a tiempo", None, None, None,
                         None)
    finally:
        _matar(datos.get("hijo"))
        _matar(datos.get("intermedio"))
        _matar(superior.pid)


def _version_uv(uv) -> str:
    if uv is None:
        return "no encontrado"
    try:
        return subprocess.run([uv, "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return f"error: {error}"


def main() -> int:
    uv = shutil.which("uv")
    caminos = [c for c in CAMINOS if c == "python directo" or uv is not None]
    if sys.platform != "win32":
        caminos = [c for c in caminos if c != "uv run --gui-script"]  # solo existe en Windows
    directorio = Path(tempfile.mkdtemp(prefix="diagnostico-r8-"))
    intermedio = directorio / "intermedio.py"
    intermedio.write_text(INTERMEDIO, encoding="utf-8")
    resultados = []
    try:
        for camino in caminos:
            for escenario in ESCENARIOS:
                print(f"comprobando: {camino}, {escenario}…", file=sys.stderr, flush=True)
                resultados.append(caso(camino, escenario, intermedio, directorio, uv))
    except KeyboardInterrupt:
        print("interrumpido", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(directorio, ignore_errors=True)

    propio = en_objeto_de_trabajo(os.getpid())
    lineas = [
        "=== COPIAR DESDE AQUÍ (diagnóstico R8) ===",
        f"fecha: {time.strftime('%Y-%m-%d %H:%M')}",
        f"equipo: {platform.platform()}; Python {platform.python_version()}; {_version_uv(uv)}",
        f"intérprete del diagnóstico: {sys.executable}",
        f"el propio diagnóstico está en un objeto de trabajo: {_si_no(propio)}",
        *tabla(resultados),
    ]
    for camino, sobrevive in conclusion(resultados).items():
        lineas.append(f"conclusión, {camino}: el hijo sobrevive en todos los casos: "
                      f"{_si_no(sobrevive)}")
    for camino in CAMINOS:
        if camino not in caminos:
            lineas.append(f"conclusión, {camino}: no comprobado en este sistema")
    lineas.append("=== HASTA AQUÍ ===")
    print("\n".join(lineas))
    return codigo_de_salida(resultados)


if __name__ == "__main__":
    sys.exit(main())
