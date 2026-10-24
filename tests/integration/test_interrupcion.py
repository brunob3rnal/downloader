"""Ctrl+C durante `ejecutar` y relanzamiento (FR-020, FR-021, edge case Ctrl+C de la spec).

Como en `test_ctrl_c.py`, en Windows se lanza `ejecutar` en su propio grupo de procesos y se le
envía `CTRL_BREAK_EVENT`, que `cli.ejecutar` trata igual que Ctrl+C.
"""

import os
import sqlite3
import subprocess
import sys
import time

import pytest

from downloader.models import Calidad, Estado
from downloader.store import Store
from tests.integration.test_ctrl_c import SALIDA_TRAS_INTERRUPCION, interrumpir

LIMITE = 60
INTERRUMPIDO = 130


def ejecutar(app_home, **kwargs):
    return subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        **kwargs,
    )


def estado(app_home, id_):
    """Estado de la fila leído sin bloquear al `ejecutar` en marcha."""
    conexion = sqlite3.connect(f"{(app_home / 'cola.db').as_uri()}?mode=ro", uri=True)
    try:
        fila = conexion.execute("SELECT estado FROM descargas WHERE id = ?", (id_,)).fetchone()
    finally:
        conexion.close()
    return fila[0] if fila else None


@pytest.mark.slow
def test_ctrl_c_guarda_el_progreso_y_relanzar_termina(app_home, destino, servidor, medios):
    datos = medios.datos_mp4
    servidor.registrar("lento.mp4", datos, tipo_contenido="video/mp4", velocidad=200_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    id_ = store.agregar(servidor.url("lento.mp4"), Calidad.MEJOR, str(destino)).descarga.id
    store.cerrar()

    proceso = ejecutar(app_home)
    try:
        trabajo = destino / ".downloader-tmp" / str(id_)
        fin = time.monotonic() + LIMITE
        # Se interrumpe solo con la fila en `downloading` y su `.part` con datos; además, más
        # allá de los 2 s entre escrituras de progreso, para que solo el guardado al
        # interrumpir deje en la cola lo que ya hay en el `.part`.
        while True:
            parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
            if (
                parciales
                and parciales[0].stat().st_size > 0.3 * len(datos)
                and estado(app_home, id_) == Estado.DOWNLOADING
            ):
                break
            assert proceso.poll() is None, "ejecutar terminó antes de poder interrumpirlo"
            assert time.monotonic() < fin, "la descarga lenta no avanzó"
            time.sleep(0.05)  # intervalo de sondeo
        en_disco = parciales[0].stat().st_size
        interrumpir(proceso)
        _, errores = proceso.communicate(timeout=SALIDA_TRAS_INTERRUPCION)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()

    assert proceso.returncode == INTERRUMPIDO, errores
    assert b"Traceback" not in errores
    assert b"interrumpido; progreso guardado" in errores
    assert parciales[0].exists()
    assert [p.name for p in destino.iterdir()] == [".downloader-tmp"]
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(id_)
        assert descarga.estado is Estado.QUEUED
        assert descarga.bytes_descargados >= en_disco
    finally:
        store.cerrar()

    relanzado = ejecutar(app_home)
    salida, errores = relanzado.communicate(timeout=LIMITE)
    assert relanzado.returncode == 0, errores
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == datos
