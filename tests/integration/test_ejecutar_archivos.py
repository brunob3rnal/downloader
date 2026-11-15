"""`ejecutar` publica `procesador.json` y `progreso-vivo.json` (specs/002-desktop-app, T030;
research R6, R9; contracts/archivos.md).

Se lanza `python -m downloader ejecutar` de verdad. Que la salida de la CLI no cambia lo
prueba `test_cli_caracterizacion.py` contra sus referencias.
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime

import psutil
import pytest

from downloader.models import Calidad
from downloader.store import Store
from tests.integration.test_ctrl_c import interrumpir

# Sin `slow`: `tests/unit/test_marcadores.py` (001) fija la lista exacta de tests lentos.
LIMITE = 60


def _lanzar(app_home):
    return subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def _pids(proceso) -> set[int]:
    """El proceso y sus descendientes: en Windows, el `python.exe` del entorno virtual es un
    lanzador que arranca el intérprete real como hijo."""
    try:
        raiz = psutil.Process(proceso.pid)
        return {raiz.pid, *(p.pid for p in raiz.children(recursive=True))}
    except psutil.NoSuchProcess:
        return {proceso.pid}


def _leer(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _esperar(condicion, que):
    limite = time.monotonic() + LIMITE
    while time.monotonic() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(0.05)
    raise AssertionError(f"no se ha cumplido en {LIMITE} s: {que}")


def _terminar(proceso):
    """Mata el proceso y sus descendientes y espera a que salgan: en Windows, `kill` vuelve
    antes de que el proceso haya terminado."""
    if proceso.poll() is None:
        try:
            hijos = psutil.Process(proceso.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            hijos = []
        for p in hijos:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        proceso.kill()
        proceso.wait()
        psutil.wait_procs(hijos, timeout=10)


@pytest.fixture
def cola(app_home, destino, servidor, medios):
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')

    def agregar(*nombres):
        store = Store.abrir(app_home)
        try:
            for nombre in nombres:
                store.agregar(servidor.url(nombre), Calidad.MEJOR, str(destino))
        finally:
            store.cerrar()

    servidor.registrar("lento.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       velocidad=400_000)
    servidor.registrar("rapido.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    return agregar


def test_durante_la_descarga_y_al_terminar_con_0(app_home, cola):
    cola("lento.mp4")
    proceso = _lanzar(app_home)
    try:
        procesador = _esperar(lambda: _leer(app_home / "procesador.json"), "procesador.json")
        assert procesador["origen"] == "cli" and procesador["ejecucion"] is None
        assert procesador["pid"] in _pids(proceso)
        assert procesador["creado"] == psutil.Process(procesador["pid"]).create_time()
        datetime.fromisoformat(procesador["inicio"])

        def vivo_con_cifras():
            vivo = _leer(app_home / "progreso-vivo.json")
            activa = vivo and vivo["activa"]
            return vivo if activa and activa["velocidad"] and activa["eta"] is not None else None

        vivo = _esperar(vivo_con_cifras, "progreso-vivo.json con velocidad y tiempo estimado")
        assert (vivo["pid"], vivo["creado"]) == (procesador["pid"], procesador["creado"])
        assert vivo["activa"]["id"] == 1 and vivo["en_linea"] is True
        # Se actualiza al menos cada 2 s.
        primero = datetime.fromisoformat(vivo["actualizado"])
        time.sleep(2.2)
        segundo = _leer(app_home / "progreso-vivo.json")
        if segundo is not None:  # la descarga puede haber terminado ya
            assert datetime.fromisoformat(segundo["actualizado"]) > primero
            assert (datetime.now(UTC) - datetime.fromisoformat(segundo["actualizado"])
                    ).total_seconds() <= 2
        assert proceso.wait(timeout=LIMITE) == 0
    finally:
        _terminar(proceso)
    assert not (app_home / "procesador.json").exists()
    assert not (app_home / "progreso-vivo.json").exists()


def test_al_terminar_con_fallidas_los_borra(app_home, cola):
    cola("rapido.mp4", "no-existe.mp4")
    proceso = _lanzar(app_home)
    try:
        assert proceso.wait(timeout=LIMITE) == 1
    finally:
        _terminar(proceso)
    assert not (app_home / "procesador.json").exists()
    assert not (app_home / "progreso-vivo.json").exists()


def test_con_ctrl_c_los_borra(app_home, cola):
    cola("lento.mp4")
    proceso = _lanzar(app_home)
    try:
        _esperar(lambda: _leer(app_home / "progreso-vivo.json"), "progreso-vivo.json")
        interrumpir(proceso)
        proceso.communicate(timeout=30)
        assert proceso.returncode == 130
    finally:
        _terminar(proceso)
    assert not (app_home / "procesador.json").exists()
    assert not (app_home / "progreso-vivo.json").exists()


def test_tras_un_cierre_abrupto_queda_y_el_siguiente_lo_sobrescribe(app_home, cola):
    cola("lento.mp4")
    proceso = _lanzar(app_home)
    try:
        anterior = _esperar(lambda: _leer(app_home / "procesador.json"), "procesador.json")
    finally:
        _terminar(proceso)  # cierre abrupto
    quedado = _leer(app_home / "procesador.json")
    assert quedado == anterior
    assert not psutil.pid_exists(quedado["pid"]) or (
        psutil.Process(quedado["pid"]).create_time() != quedado["creado"]
    )

    siguiente = _lanzar(app_home)
    try:
        nuevo = _esperar(
            lambda: (d := _leer(app_home / "procesador.json")) and d["pid"] != anterior["pid"]
            and d, "procesador.json del siguiente ejecutar",
        )
        assert nuevo["pid"] in _pids(siguiente)
    finally:
        _terminar(siguiente)
