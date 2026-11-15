"""Caracterización de la salida de la CLI (specs/002-desktop-app, T022; hallazgo P1).

La feature 002 mueve la validación y el cuerpo de `ejecutar` a `operaciones.py`, y `ejecutar`
pasa a escribir archivos informativos. La CLI no debe cambiar ni de salida ni de códigos
(FR-017). Este test recorre cada camino de `agregar`, `ejecutar` y `estado` de
`specs/001-resilient-video-downloader/contracts/cli.md` y compara la salida estándar, la de
error y el código de salida, normalizados, con referencias generadas **sobre el código anterior
a la refactorización** (`tests/integration/referencias_cli/`).

Con `DOWNLOADER_GENERAR_REFERENCIAS=1` escribe las referencias en lugar de compararlas. Nunca se
editan a mano: si un sistema difiere solo en algo que varía por entorno, se ajusta `normalizar`.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from downloader.models import Calidad, Estado
from downloader.store import Store
from tests.integration.test_ctrl_c import interrumpir

REFERENCIAS = Path(__file__).with_name("referencias_cli")
GENERAR = os.environ.get("DOWNLOADER_GENERAR_REFERENCIAS") == "1"
# Líneas de progreso: su número y sus cifras dependen del tiempo.
PROGRESO = re.compile(r"^#\d+ (descargando|esperando red)\b")


def _variantes(ruta: Path) -> list[str]:
    """Las formas en que una ruta temporal puede aparecer en la salida."""
    variantes = {str(ruta), str(ruta.resolve()), os.path.realpath(ruta)}
    for forma in list(variantes):
        try:
            variantes.add(str(Path("~") / Path(forma).relative_to(Path.home())))
        except ValueError:
            pass
    return sorted(variantes, key=len, reverse=True)


def normalizar(texto: str, tmp_path: Path, quitar_progreso: bool = True) -> str:
    texto = texto.replace("\r\n", "\n")
    for forma in _variantes(tmp_path):
        texto = texto.replace(forma, "<TMP>")
    texto = texto.replace("\\", "/")
    texto = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<PUERTO>", texto)
    texto = re.sub(r"\d{8}T\d{6}Z", "<FECHA>", texto)
    lineas = [linea.rstrip() for linea in texto.split("\n")
              if not (quitar_progreso and PROGRESO.match(linea))]
    return "\n".join(lineas).strip("\n")


def comprobar(caso: str, codigo: int, salida: str, errores: str, tmp_path: Path) -> None:
    # En `estado`, "#1 descargando  43.1%" es la línea de la cola, no progreso en directo.
    quitar_progreso = not caso.startswith("estado_")
    obtenido = (
        f"código: {codigo}\n"
        f"--- salida estándar ---\n{normalizar(salida, tmp_path, quitar_progreso)}\n"
        f"--- salida de error ---\n{normalizar(errores, tmp_path)}\n"
    )
    referencia = REFERENCIAS / f"{caso}.txt"
    if GENERAR:
        REFERENCIAS.mkdir(exist_ok=True)
        referencia.write_text(obtenido, encoding="utf-8", newline="\n")
        return
    assert referencia.exists(), f"falta la referencia {referencia.name}"
    assert obtenido == referencia.read_text(encoding="utf-8")


def _con_cli(caso, resultado, tmp_path):
    comprobar(caso, resultado.exit_code, resultado.stdout, resultado.stderr, tmp_path)


def _video(servidor, medios, id_video="v1", **propiedades):
    servidor.registrar(f"{id_video}.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       **propiedades)
    servidor.registrar_video(id_video, f"{id_video}.mp4")
    return servidor.url(f"watch/{id_video}")


# --- agregar -------------------------------------------------------------------------------


def test_agregar_agregada(cli, destino, servidor, tmp_path):
    _con_cli("agregar_agregada", cli("agregar", servidor.url("watch/a"), "--calidad", "720p",
                                     "--carpeta", destino), tmp_path)


def test_agregar_ya_en_cola(cli, destino, servidor, tmp_path):
    cli("agregar", servidor.url("watch/a"), "--carpeta", destino)
    _con_cli("agregar_ya_en_cola", cli("agregar", servidor.url("watch/a"), "--carpeta", destino),
             tmp_path)


def test_agregar_reencolada(cli, app_home, destino, servidor, tmp_path):
    cli("agregar", servidor.url("watch/a"), "--carpeta", destino)
    store = Store.abrir(app_home)
    try:
        store.transicion(1, Estado.DOWNLOADING)
        store.transicion(1, Estado.FAILED, ultimo_error="HTTP 403")
    finally:
        store.cerrar()
    _con_cli("agregar_reencolada", cli("agregar", servidor.url("watch/a"), "--carpeta", destino,
                                       "--cookies-de", "firefox"), tmp_path)


def test_agregar_url_vacia(cli, destino, tmp_path):
    _con_cli("agregar_url_vacia", cli("agregar", "  ", "--carpeta", destino), tmp_path)


def test_agregar_calidad_desconocida(cli, destino, tmp_path):
    _con_cli("agregar_calidad_desconocida",
             cli("agregar", "https://v.ejemplo/a", "--calidad", "480p", "--carpeta", destino),
             tmp_path)


def test_agregar_navegador_no_admitido(cli, destino, tmp_path):
    _con_cli("agregar_navegador_no_admitido",
             cli("agregar", "https://v.ejemplo/a", "--cookies-de", "netscape", "--carpeta", destino),
             tmp_path)


def test_agregar_carpeta_inexistente(cli, tmp_path):
    _con_cli("agregar_carpeta_inexistente",
             cli("agregar", "https://v.ejemplo/a", "--carpeta", tmp_path / "no-existe"), tmp_path)


def test_agregar_carpeta_sin_permiso(cli, destino, monkeypatch, tmp_path):
    from downloader import publish

    escribible = publish.escribible
    monkeypatch.setattr(
        publish, "escribible",
        lambda carpeta: str(carpeta) != str(destino.resolve()) and escribible(carpeta),
    )
    _con_cli("agregar_carpeta_sin_permiso",
             cli("agregar", "https://v.ejemplo/a", "--carpeta", destino), tmp_path)


def test_agregar_config_invalida(cli, app_home, destino, tmp_path):
    (app_home / "config.toml").write_text('calidad = "480p"\n', encoding="utf-8")
    _con_cli("agregar_config_invalida",
             cli("agregar", "https://v.ejemplo/a", "--carpeta", destino), tmp_path)


# --- ejecutar ------------------------------------------------------------------------------


def test_ejecutar_todas_completadas(cli, destino, servidor, medios, tmp_path):
    cli("agregar", _video(servidor, medios), "--carpeta", destino)
    _con_cli("ejecutar_todas_completadas", cli("ejecutar"), tmp_path)


def test_ejecutar_con_fallidas(cli, destino, servidor, medios, tmp_path):
    cli("agregar", _video(servidor, medios), "--carpeta", destino)
    cli("agregar", servidor.url("watch/privado"), "--carpeta", destino)
    _con_cli("ejecutar_con_fallidas", cli("ejecutar"), tmp_path)


def test_ejecutar_otro_en_marcha(cli, app_home, tmp_path):
    otro = Store.abrir(app_home)
    bloqueo = otro.bloquear_ejecucion()
    try:
        resultado = cli("ejecutar")
    finally:
        bloqueo.liberar()
        otro.cerrar()
    _con_cli("ejecutar_otro_en_marcha", resultado, tmp_path)


def test_ejecutar_navegador_no_admitido(cli, tmp_path):
    _con_cli("ejecutar_navegador_no_admitido", cli("ejecutar", "--cookies-de", "netscape"),
             tmp_path)


def test_ejecutar_sin_ffmpeg(cli, app_home, destino, servidor, tmp_path, monkeypatch):
    cli("agregar", servidor.url("watch/a"), "--carpeta", destino)
    vacio = tmp_path / "bin-vacio"
    vacio.mkdir()
    monkeypatch.setenv("PATH", str(vacio))
    _con_cli("ejecutar_sin_ffmpeg", cli("ejecutar"), tmp_path)


def test_ejecutar_cola_corrupta(cli, app_home, tmp_path):
    (app_home / "cola.db").write_bytes(b"esto no es una base de datos SQLite" * 100)
    _con_cli("ejecutar_cola_corrupta", cli("ejecutar"), tmp_path)


def test_ejecutar_ctrl_c(app_home, destino, servidor, medios, tmp_path):
    servidor.registrar("lento.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       velocidad=200_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    store.agregar(servidor.url("lento.mp4"), Calidad.MEJOR, str(destino))
    store.cerrar()
    proceso = subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        trabajo = destino / ".downloader-tmp"
        limite = time.monotonic() + 60
        while not (trabajo.exists() and any(p.stat().st_size > 50_000
                                            for p in trabajo.glob("*/*.part"))):
            assert proceso.poll() is None, "ejecutar terminó antes de interrumpirlo"
            assert time.monotonic() < limite, "la descarga lenta no avanzó"
            time.sleep(0.05)
        interrumpir(proceso)
        salida, errores = proceso.communicate(timeout=30)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
    comprobar("ejecutar_ctrl_c", proceso.returncode, salida, errores, tmp_path)


# --- estado --------------------------------------------------------------------------------


def test_estado_cola_vacia(cli, tmp_path):
    _con_cli("estado_cola_vacia", cli("estado"), tmp_path)


def test_estado_con_filas(cli, app_home, destino, servidor, tmp_path):
    cli("agregar", servidor.url("watch/a"), "--carpeta", destino)
    cli("agregar", servidor.url("watch/b?token=secreto123"), "--calidad", "audio",
        "--carpeta", destino)
    store = Store.abrir(app_home)
    try:
        store.transicion(1, Estado.DOWNLOADING)
        store.guardar_progreso(1, 431, 1000, forzar=True)
        store.transicion(2, Estado.DOWNLOADING)
        store.transicion(2, Estado.FAILED, ultimo_error="Video privado: el propietario restringió")
    finally:
        store.cerrar()
    _con_cli("estado_con_filas", cli("estado"), tmp_path)


def test_estado_cola_corrupta(cli, app_home, tmp_path):
    (app_home / "cola.db").write_bytes(b"esto no es una base de datos SQLite" * 100)
    _con_cli("estado_cola_corrupta", cli("estado"), tmp_path)


def test_hay_una_referencia_por_caso():
    if GENERAR:
        pytest.skip("generando referencias")
    casos = {nombre.removeprefix("test_") for nombre in globals()
             if nombre.startswith(("test_agregar_", "test_ejecutar_", "test_estado_"))}
    assert {r.stem for r in REFERENCIAS.glob("*.txt")} == casos

