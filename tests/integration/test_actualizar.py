"""`downloader actualizar` (escenario 12 de quickstart; FR-025 enmendado, research R14).

La ejecución de uv se sustituye por `UvFalso`, que reescribe el `uv.lock` de un proyecto
temporal como lo haría `uv lock --upgrade-package yt-dlp` y responde a `uv sync` y a
`uv run pytest -m ytdlp_internals` con el código que se le indique. Nada sale a internet.
"""

import importlib.metadata
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from downloader import actualizacion
from downloader.cli import app

RAIZ = Path(__file__).resolve().parents[2]
ANTERIOR, NUEVA = "2026.8.19", "2026.10.1"
LOCK = ["lock", "--upgrade-package", "yt-dlp"]
SYNC = ["sync"]
PRUEBAS = ["run", "pytest", "-m", "ytdlp_internals"]

LOCK_INICIAL = f"""version = 1
requires-python = ">=3.12"

[[package]]
name = "typer"
version = "0.27.3"

[[package]]
name = "yt-dlp"
version = "{ANTERIOR}"

[[package]]
name = "yt-dlp-ejs"
version = "0.8.0"
"""

FALLO_PRUEBAS = (
    "..F.........\n"
    "FAILED tests/integration/test_rango_no_satisfacible.py::"
    "test_parcial_completo_con_416_se_publica_sin_pedir_mas_bytes - AssertionError: 2 == 1\n"
    "1 failed, 30 passed in 21.07s\n"
)


def version(proyecto):
    return actualizacion.version_bloqueada((proyecto / "uv.lock").read_text(encoding="utf-8"))


class UvFalso:
    """Sustituye a `actualizacion.ejecutar_uv`: anota cada llamada y simula su resultado."""

    def __init__(self, proyecto):
        self.proyecto = proyecto
        self.llamadas = []
        self.nueva = NUEVA
        self.lock = (0, "")  # (código, stderr) de `uv lock`
        self.syncs = []  # (código, stderr) de cada `uv sync`, en orden; por defecto correcto
        self.pruebas = (0, "31 passed in 20.50s\n")  # (código, stdout) de `uv run pytest`

    def __call__(self, argumentos, raiz):
        assert raiz == self.proyecto
        self.llamadas.append(list(argumentos))
        if argumentos[0] == "lock":
            codigo, error = self.lock
            if codigo == 0:
                lock = raiz / "uv.lock"
                texto = lock.read_text(encoding="utf-8")
                lock.write_text(
                    re.sub(r'(name = "yt-dlp"\nversion = )"[^"]+"', rf'\1"{self.nueva}"', texto),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(argumentos, codigo, "", error)
        if argumentos[0] == "sync":
            codigo, error = self.syncs.pop(0) if self.syncs else (0, "")
            return subprocess.CompletedProcess(argumentos, codigo, "", error)
        codigo, salida = self.pruebas
        return subprocess.CompletedProcess(argumentos, codigo, salida, "")


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    raiz = tmp_path / "proyecto"
    raiz.mkdir()
    (raiz / "pyproject.toml").write_text('[project]\nname = "downloader"\n', encoding="utf-8")
    (raiz / "uv.lock").write_text(LOCK_INICIAL, encoding="utf-8")
    monkeypatch.setattr(actualizacion, "raiz_proyecto", lambda: raiz)
    return raiz


@pytest.fixture
def con_ffmpeg(monkeypatch):
    """ffmpeg y ffprobe "instalados": los tests marcados `ytdlp_internals` los necesitan."""
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda nombre, *a, **k: f"/usr/bin/{nombre}" if nombre in ("ffmpeg", "ffprobe")
        else which(nombre, *a, **k),
    )


@pytest.fixture
def uv(proyecto, con_ffmpeg, monkeypatch):
    falso = UvFalso(proyecto)
    monkeypatch.setattr(actualizacion, "ejecutar_uv", falso)
    return falso


def actualizar():
    return CliRunner().invoke(app, ["actualizar"])


def test_version_nueva_que_supera_los_tests_se_conserva(uv, proyecto):
    resultado = actualizar()

    assert resultado.exit_code == 0, resultado.output
    assert f"yt-dlp {ANTERIOR} → {NUEVA}" in resultado.stdout
    assert version(proyecto) == NUEVA
    lock, sync, pruebas = uv.llamadas
    assert (lock, sync) == (LOCK, SYNC)
    assert pruebas[: len(PRUEBAS)] == PRUEBAS  # después de `uv sync`: con la versión nueva


def test_misma_version_ya_esta_al_dia(uv, proyecto):
    uv.nueva = ANTERIOR
    antes = (proyecto / "uv.lock").read_bytes()

    resultado = actualizar()

    assert resultado.exit_code == 0, resultado.output
    assert f"yt-dlp ya está al día ({ANTERIOR})" in resultado.stdout
    assert uv.llamadas == [LOCK]  # nada que sincronizar ni que verificar
    assert (proyecto / "uv.lock").read_bytes() == antes


def test_si_fallan_los_tests_de_internos_restaura_la_version_anterior(uv, proyecto):
    uv.pruebas = (1, FALLO_PRUEBAS)
    antes = (proyecto / "uv.lock").read_bytes()

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert (proyecto / "uv.lock").read_bytes() == antes  # byte a byte
    assert uv.llamadas[:2] == [LOCK, SYNC]
    assert uv.llamadas[2][: len(PRUEBAS)] == PRUEBAS
    assert uv.llamadas[3:] == [SYNC]  # vuelve a sincronizar con el uv.lock anterior
    error = resultado.stderr
    assert f"yt-dlp {NUEVA}" in error
    assert "test_rango_no_satisfacible.py::test_parcial_completo_con_416" in error  # qué falló
    assert f"se conserva yt-dlp {ANTERIOR}" in error
    assert "→" not in resultado.stdout


def test_si_falla_uv_sync_restaura_la_version_anterior(uv, proyecto):
    uv.syncs = [(1, "error: Failed to install yt-dlp==2026.10.1")]
    antes = (proyecto / "uv.lock").read_bytes()

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert (proyecto / "uv.lock").read_bytes() == antes
    assert uv.llamadas == [LOCK, SYNC, SYNC]  # sin tests: no hay nada nuevo instalado
    assert "Failed to install" in resultado.stderr
    assert f"se conserva yt-dlp {ANTERIOR}" in resultado.stderr


def test_si_no_se_puede_restaurar_el_entorno_lo_dice(uv, proyecto):
    uv.pruebas = (1, FALLO_PRUEBAS)
    uv.syncs = [(0, ""), (1, "error: network unreachable")]
    antes = (proyecto / "uv.lock").read_bytes()

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert (proyecto / "uv.lock").read_bytes() == antes
    assert "network unreachable" in resultado.stderr
    assert "uv sync" in resultado.stderr  # qué hacer para terminar de restaurarlo


def test_si_falla_uv_lock_no_modifica_nada(uv, proyecto):
    uv.lock = (2, "error: Failed to fetch: `https://pypi.org/simple/yt-dlp/`")
    antes = (proyecto / "uv.lock").read_bytes()

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert "Failed to fetch" in resultado.stderr
    assert uv.llamadas == [LOCK]
    assert (proyecto / "uv.lock").read_bytes() == antes


def test_sin_uv_sale_con_1(proyecto, monkeypatch):
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda nombre, *a, **k: None if nombre == "uv"
        else f"/usr/bin/{nombre}" if nombre in ("ffmpeg", "ffprobe") else which(nombre, *a, **k),
    )
    antes = (proyecto / "uv.lock").read_bytes()

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert "uv" in resultado.stderr
    assert (proyecto / "uv.lock").read_bytes() == antes


def test_sin_ffmpeg_no_modifica_nada(proyecto, monkeypatch):
    """Sin ffmpeg, los tests marcados `ytdlp_internals` que descargan se omitirían y la versión
    nueva se aceptaría sin verificar."""
    falso = UvFalso(proyecto)
    monkeypatch.setattr(actualizacion, "ejecutar_uv", falso)
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda nombre, *a, **k: None if nombre == "ffmpeg" else which(nombre, *a, **k),
    )

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert "ffmpeg" in resultado.stderr
    assert falso.llamadas == []
    assert version(proyecto) == ANTERIOR


def test_fuera_del_proyecto_no_modifica_nada(uv, monkeypatch):
    monkeypatch.setattr(actualizacion, "raiz_proyecto", lambda: None)

    resultado = actualizar()

    assert resultado.exit_code == 1, resultado.output
    assert "uv tool upgrade downloader" in resultado.stderr
    assert uv.llamadas == []


# --- dónde está el proyecto y qué versión tiene -----------------------------------------


def test_desde_el_repositorio_la_raiz_es_la_del_proyecto():
    """`uv sync` instala el paquete en modo editable: el código está en `<raíz>/src`."""
    assert actualizacion.raiz_proyecto() == RAIZ


def test_instalado_fuera_del_proyecto_no_hay_raiz(tmp_path):
    instalado = tmp_path / "lib" / "python3.12" / "site-packages" / "downloader"
    instalado.mkdir(parents=True)
    assert actualizacion.raiz_proyecto(instalado / "actualizacion.py") is None


def test_un_pyproject_de_otro_proyecto_no_es_la_raiz(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "otro"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text(LOCK_INICIAL, encoding="utf-8")
    paquete = tmp_path / "src" / "downloader"
    paquete.mkdir(parents=True)
    assert actualizacion.raiz_proyecto(paquete / "actualizacion.py") is None


def test_version_bloqueada_es_la_instalada():
    lock = (RAIZ / "uv.lock").read_text(encoding="utf-8")
    assert actualizacion.version_bloqueada(lock) == importlib.metadata.version("yt-dlp")
