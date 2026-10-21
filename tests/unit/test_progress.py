"""Línea de una descarga en `waiting_network` (contracts/cli.md, T040)."""

import time

from downloader.models import Calidad, Descarga, Estado
from downloader.progress import _Activa, linea_activa


def descarga():
    return Descarga(id=13, url="https://videos.ejemplo/v?token=SECRETO", calidad=Calidad.MEJOR,
                    carpeta_destino="/tmp", estado=Estado.WAITING_NETWORK, titulo="Título")


def test_cuenta_atras_hasta_el_reintento():
    activa = _Activa(descarga(), causa_espera="conexión rechazada",
                     reintento=time.monotonic() + 39.2)
    assert linea_activa(activa) == "#13 esperando red  reintento en 40 s  (conexión rechazada)"
    activa.reintento = time.monotonic() - 1
    assert linea_activa(activa) == "#13 esperando red  reintento en 0 s  (conexión rechazada)"


def test_sin_conexion_espera_a_la_red():
    activa = _Activa(descarga(), causa_espera="conexión rechazada", reintento=None)
    assert linea_activa(activa) == (
        "#13 esperando red  sin conexión, reintento al volver la red  (conexión rechazada)"
    )
