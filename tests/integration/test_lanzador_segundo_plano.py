"""El trabajador sobrevive al cierre de la cadena del lanzador (specs/002-desktop-app, US4;
research R8 [a verificar]; tasks T087).

Solo Windows (constitución v3.1.0). La ventana se arranca como la arranca `Gestor de
descargas.vbs`: `uv run --gui-script`, con uv, el lanzador del entorno y `pythonw.exe`, que
pueden meter a sus hijos en un objeto de trabajo que se cierra con ellos. Se usa
`tests/support/ventana_headless.py` copiado como `.pyw`. Se mata toda esa cadena, menos el
trabajador, y el trabajador tiene que seguir descargando. Sin el marcador `slow`:
`tests/unit/test_marcadores.py` (001) fija la lista exacta de tests lentos.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import psutil
import pytest

from downloader import archivos_json
from downloader.app import parada
from tests.support.cliente_app import ClienteApp
from tests.support.esperas import hasta
from tests.support.procesos_app import MODULO_TRABAJADOR, RAIZ, terminar_trabajadores_de

pytestmark = pytest.mark.solo_windows

NOMBRE = "grande.mp4"
PYW = f"""import sys
sys.path.insert(0, {str(RAIZ)!r})
from tests.support.ventana_headless import main
sys.exit(main())
"""


def _parcial(destino):
    trabajo = destino / ".downloader-tmp" / "1"
    parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
    return parciales[0].stat().st_size if parciales else 0


def _es_trabajador(proceso):
    try:
        return MODULO_TRABAJADOR in proceso.cmdline()
    except psutil.Error:
        return False


def test_el_trabajador_sobrevive_a_la_cadena_de_uv_run(app_home, servidor, destino, tmp_path):
    (app_home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    servidor.registrar(NOMBRE, bytes(50_000_000), tipo_contenido="video/mp4", velocidad=2_000_000)
    script = tmp_path / "ventana_headless.pyw"
    script.write_text(PYW, encoding="utf-8")
    salida = tmp_path / "salida.txt"
    uv = shutil.which("uv")
    assert uv, "uv no está en el PATH"
    cadena = subprocess.Popen(
        [uv, "run", "--gui-script", str(script)], cwd=RAIZ,
        env={**os.environ, "DOWNLOADER_HOME": str(app_home),
             "VENTANA_HEADLESS_SALIDA": str(salida)},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    arbol = []
    try:
        linea = hasta(lambda: salida.exists() and next(
            (l for l in salida.read_text(encoding="utf-8").splitlines() if l.startswith("url ")),
            None), 60, "la URL de la ventana")
        cliente = ClienteApp(linea.removeprefix("url "))
        respuesta = cliente.post("api/agregar", {
            "texto": servidor.url(NOMBRE), "calidad": "mejor", "carpeta": str(destino),
            "navegador": None})
        assert respuesta.estado == 200, respuesta.cuerpo
        assert cliente.post("api/iniciar").estado == 202
        hasta(lambda: _parcial(destino) > 500_000, 60, "que empiece a descargar")
        anunciado = archivos_json.leer(app_home / "procesador.json")
        trabajador = psutil.Process(anunciado["pid"])

        # Toda la cadena (uv, el lanzador del entorno, pythonw y sus hijos), menos el
        # trabajador y lo que cuelga de él.
        raiz = psutil.Process(cadena.pid)
        del_trabajador = {trabajador.pid, *(p.pid for p in trabajador.children(recursive=True))}
        arbol = [raiz, *raiz.children(recursive=True)]
        arbol = [p for p in arbol if p.pid not in del_trabajador and not _es_trabajador(p)]
        print("\n[lanzador] cadena:", [(p.pid, p.name()) for p in arbol],
              "trabajador:", trabajador.pid, trabajador.name())
        for proceso in arbol:
            try:
                proceso.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(arbol, timeout=30)

        antes = _parcial(destino)
        time.sleep(5)
        assert trabajador.is_running(), "el trabajador murió con la cadena del lanzador (R8)"
        assert _parcial(destino) > antes, "el trabajador no sigue descargando"

        # Se detiene como lo haría una ventana nueva: con la petición de parada.
        assert parada.pedir(app_home, anunciado["ejecucion"])
        psutil.wait_procs([trabajador], timeout=30)
        assert not trabajador.is_running()
    finally:
        for proceso in arbol:
            try:
                proceso.kill()
            except psutil.Error:
                pass
        if cadena.poll() is None:
            cadena.kill()
        terminar_trabajadores_de(app_home)
