"""Interfaz pública del bloqueo (specs/002-desktop-app, research R12): la usará `ventana.lock`."""

import os

from downloader import bloqueo, store


def test_es_el_mismo_bloqueo_que_ejecutar_lock():
    assert bloqueo.bloquear is store._bloquear
    assert bloqueo.desbloquear is store._desbloquear


def test_un_segundo_descriptor_no_consigue_el_bloqueo_hasta_liberarlo(tmp_path):
    ruta = tmp_path / "ventana.lock"
    primero = os.open(ruta, os.O_RDWR | os.O_CREAT, 0o600)
    segundo = os.open(ruta, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert bloqueo.bloquear(primero)
        assert not bloqueo.bloquear(segundo)
        bloqueo.desbloquear(primero)
        assert bloqueo.bloquear(segundo)
        bloqueo.desbloquear(segundo)
    finally:
        os.close(primero)
        os.close(segundo)
