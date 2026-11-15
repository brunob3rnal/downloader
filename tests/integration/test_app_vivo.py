"""Velocidad, tiempo estimado y retraso del estado en la ventana (specs/002-desktop-app,
FR-005, SC-006; research R9). También con un `ejecutar` de la CLI."""

import os
import subprocess
import sys
import threading
import time

import pytest

from downloader.models import Calidad
from downloader.store import Store
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta

VELOCIDAD = 400_000  # bytes/s
RETRASO_MAXIMO = 2.0  # s (SC-006)
TOLERANCIA = 256 * 1024  # bytes en vuelo entre el servidor y el `.part`


class Muestreo:
    """Bytes servidos por el servidor de prueba a lo largo del tiempo."""

    def __init__(self, servidor, nombre):
        self.puntos = []
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._muestrear, args=(servidor, nombre), daemon=True)
        self._hilo.start()

    def _muestrear(self, servidor, nombre):
        while not self._parar.wait(0.05):
            self.puntos.append((time.monotonic(), servidor.bytes_servidos[nombre]))

    def en(self, instante):
        anteriores = [b for t, b in self.puntos if t <= instante]
        return anteriores[-1] if anteriores else 0

    def detener(self):
        self._parar.set()
        self._hilo.join()


@pytest.fixture
def lento(app_local, servidor, medios, destino):
    (app_local.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    servidor.registrar("lento.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       velocidad=VELOCIDAD)
    store = Store.abrir(app_local.home)
    try:
        store.agregar(servidor.url("lento.mp4"), Calidad.MEJOR, str(destino))
    finally:
        store.cerrar()
    return len(medios.datos_mp4)


def _comprobar_en_directo(app_local, servidor, total):
    muestreo = Muestreo(servidor, "lento.mp4")
    vistas = []
    try:
        def fila_activa():
            datos = app_local.cliente.get("api/estado").json()
            filas = [f for f in datos["cola"]["filas"] if f["id"] == 1]
            return filas[0] if filas and filas[0]["velocidad"] else None

        hasta(fila_activa, 60, "la fila activa con velocidad")
        for _ in range(15):
            instante = time.monotonic()
            fila = fila_activa()
            if fila is None or fila["estado"] != "downloading":
                break
            assert fila["velocidad"] > 0 and fila["eta"] is not None
            vistas.append((instante, fila["porcentaje"] * total / 100))
            time.sleep(0.3)
    finally:
        muestreo.detener()
    assert len(vistas) >= 5
    for instante, mostrados in vistas:
        # Lo mostrado no va más de 2 s por detrás de lo que el servidor ya había enviado.
        assert mostrados + TOLERANCIA >= muestreo.en(instante - RETRASO_MAXIMO)


def test_con_un_procesamiento_de_la_aplicacion(app_local, servidor, lento):
    assert app_local.cliente.post("api/iniciar").estado == 202
    try:
        _comprobar_en_directo(app_local, servidor, lento)
    finally:
        hasta(lambda: app_local.cliente.get("api/estado").json()["procesador"]["estado"]
              == "ninguno", 120, "el final")


def test_con_un_ejecutar_de_la_cli(app_local, servidor, lento):
    proceso = subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_local.home)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        hasta(lambda: app_local.cliente.get("api/estado").json()["procesador"]["origen"]
              == "cli", 30, "el procesador de la CLI en api/estado")
        _comprobar_en_directo(app_local, servidor, lento)
        assert proceso.wait(timeout=120) == 0
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
