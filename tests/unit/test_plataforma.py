"""Compatibilidad con Windows 10/11 y Linux (constitución, sección Plataformas).

Estos tests corren en Linux y en Windows. En Linux no prueban el comportamiento en Windows,
pero sí que el código no depende sin protección de módulos o atributos de un solo sistema.
"""

import ast
import importlib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
PAQUETE = RAIZ / "src" / "downloader"

# Módulos que solo existen en POSIX o solo en Windows.
SOLO_POSIX = {"fcntl", "termios", "tty", "pty", "resource", "pwd", "grp", "posix", "syslog"}
SOLO_WINDOWS = {"msvcrt", "winreg", "winsound", "_winapi", "nt"}

# Atributos de `os`, `signal` y `subprocess` que no existen en uno de los dos sistemas.
ATRIBUTOS = {
    "os": {
        "fork", "forkpty", "getuid", "geteuid", "getgid", "getegid", "setuid", "setsid",
        "getpgid", "killpg", "chown", "lchown", "mkfifo", "statvfs", "sync", "O_DIRECTORY",
        "O_NOFOLLOW", "O_CLOEXEC", "O_BINARY", "startfile",
    },
    "signal": {
        "SIGKILL", "SIGHUP", "SIGQUIT", "SIGUSR1", "SIGUSR2", "SIGALRM", "SIGCHLD", "SIGPIPE",
        "SIGSTOP", "SIGCONT", "SIGTSTP", "alarm", "setitimer", "pause", "pthread_kill",
        "SIGBREAK", "CTRL_C_EVENT", "CTRL_BREAK_EVENT",
    },
    "subprocess": {"CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW", "STARTUPINFO"},
}


def _modulos_del_paquete() -> list[str]:
    nombres = []
    for ruta in sorted(PAQUETE.rglob("*.py")):
        partes = ruta.relative_to(PAQUETE.parent).with_suffix("").parts
        if partes[-1] == "__init__":
            partes = partes[:-1]
        if partes[-1] != "__main__":  # importarlo ejecutaría la CLI
            nombres.append(".".join(partes))
    return nombres


@pytest.mark.parametrize("modulo", _modulos_del_paquete())
def test_todos_los_modulos_se_importan(modulo):
    importlib.import_module(modulo)


def test_existe_python_m_downloader():
    assert (PAQUETE / "__main__.py").exists()


def _archivos_python():
    return sorted([*PAQUETE.rglob("*.py"), *(RAIZ / "tests").rglob("*.py")])


def _menciona_plataforma(nodo: ast.AST) -> bool:
    """Si una condición depende del sistema: `os.name`, `sys.platform` o `hasattr(...)`."""
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.Attribute) and isinstance(hijo.value, ast.Name):
            if (hijo.value.id, hijo.attr) in {("os", "name"), ("sys", "platform")}:
                return True
        if isinstance(hijo, ast.Call) and getattr(hijo.func, "id", None) == "hasattr":
            return True
    return False


def _captura_importerror(intento: ast.Try) -> bool:
    for manejador in intento.handlers:
        tipos = manejador.type.elts if isinstance(manejador.type, ast.Tuple) else [manejador.type]
        if any(getattr(t, "id", None) in {"ImportError", "ModuleNotFoundError"} for t in tipos):
            return True
    return False


def _protegido(nodo: ast.AST, padres: dict) -> bool:
    hijo, actual = nodo, padres.get(nodo)
    while actual is not None:
        if isinstance(actual, (ast.If, ast.IfExp)) and _menciona_plataforma(actual.test):
            return True
        if isinstance(actual, ast.Try) and hijo in actual.body and _captura_importerror(actual):
            return True
        hijo, actual = actual, padres.get(actual)
    return False


def _usos_sin_proteger(ruta: Path) -> list[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    padres = {hijo: nodo for nodo in ast.walk(arbol) for hijo in ast.iter_child_nodes(nodo)}
    usos = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres = [alias.name.split(".")[0] for alias in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            nombres = [nodo.module.split(".")[0]]
        elif (
            isinstance(nodo, ast.Attribute)
            and isinstance(nodo.value, ast.Name)
            and nodo.attr in ATRIBUTOS.get(nodo.value.id, ())
        ):
            nombres = [f"{nodo.value.id}.{nodo.attr}"]
        else:
            continue
        for nombre in nombres:
            if (nombre in SOLO_POSIX | SOLO_WINDOWS or "." in nombre) and not _protegido(
                nodo, padres
            ):
                usos.append(f"{ruta.relative_to(RAIZ)}:{nodo.lineno}: {nombre}")
    return usos


def test_sin_usos_de_un_solo_sistema_sin_proteger():
    """`import fcntl`, `signal.SIGKILL`, `os.fork`… solo bajo `if os.name …`/`sys.platform`,
    `hasattr(...)` o `try: … except ImportError`."""
    usos = [uso for ruta in _archivos_python() for uso in _usos_sin_proteger(ruta)]
    assert usos == []


@pytest.mark.parametrize(
    "codigo",
    [
        "import fcntl",
        "import os, fcntl",
        "from fcntl import flock",
        "def f():\n    import msvcrt",
        "import signal\nsignal.SIGKILL",
        "import os\nos.fork()",
        "import os\nif True:\n    os.getuid()",
    ],
)
def test_el_detector_encuentra_usos_sin_proteger(tmp_path, codigo, monkeypatch):
    monkeypatch.setattr(f"{__name__}.RAIZ", tmp_path)
    ruta = tmp_path / "m.py"
    ruta.write_text(codigo, encoding="utf-8")
    assert _usos_sin_proteger(ruta)


@pytest.mark.parametrize(
    "codigo",
    [
        "import os\nif os.name == 'nt':\n    import msvcrt\nelse:\n    import fcntl",
        "import sys\nif sys.platform != 'win32':\n    import fcntl",
        "try:\n    import fcntl\nexcept ImportError:\n    fcntl = None",
        "import signal\nif hasattr(signal, 'SIGBREAK'):\n    signal.signal(signal.SIGBREAK, 0)",
        "import os, subprocess\nf = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0",
    ],
)
def test_el_detector_acepta_usos_protegidos(tmp_path, codigo, monkeypatch):
    monkeypatch.setattr(f"{__name__}.RAIZ", tmp_path)
    ruta = tmp_path / "m.py"
    ruta.write_text(codigo, encoding="utf-8")
    assert _usos_sin_proteger(ruta) == []
