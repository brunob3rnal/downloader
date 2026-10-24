"""Condición de salida de `ejecutar` (FR-021)."""

import os
import signal
import sqlite3
import threading
import time

from downloader.models import Estado
from downloader.store import Store


def registrar(servidor, medios, id_video, **propiedades):
    servidor.registrar(f"{id_video}.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       **propiedades)
    servidor.registrar_video(id_video, f"{id_video}.mp4")


def test_todas_completadas_sale_con_0(cli, destino, servidor, medios):
    registrar(servidor, medios, "v1")
    cli("agregar", servidor.url("watch/v1"), "--carpeta", destino)
    assert cli("ejecutar").exit_code == 0


def test_cola_vacia_desde_el_inicio_sale_con_0(cli):
    resultado = cli("ejecutar")
    assert resultado.exit_code == 0, resultado.output


def test_recoge_urls_agregadas_mientras_corre(cli, app_home, destino, servidor, medios):
    registrar(servidor, medios, "lento", velocidad=800_000)  # ≈ 2,5 s
    registrar(servidor, medios, "v2")
    cli("agregar", servidor.url("watch/lento"), "--carpeta", destino)

    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(r=cli("ejecutar")))
    hilo.start()
    while servidor.bytes_servidos["lento.mp4"] == 0:
        assert hilo.is_alive(), resultado["r"].output
        time.sleep(0.02)

    # Otra conexión a la cola, como haría `downloader agregar` desde otra terminal.
    otro = Store.abrir(app_home)
    otro.agregar(servidor.url("watch/v2"), "mejor", str(destino))
    otro.cerrar()

    hilo.join(timeout=60)
    assert not hilo.is_alive()
    assert resultado["r"].exit_code == 0, resultado["r"].output
    store = Store.abrir(app_home)
    try:
        assert [store.obtener(i).estado for i in (1, 2)] == [Estado.COMPLETED] * 2
    finally:
        store.cerrar()


def test_segundo_ejecutar_simultaneo_sale_con_2(cli, app_home):
    otro = Store.abrir(app_home)
    bloqueo = otro.bloquear_ejecucion()
    try:
        resultado = cli("ejecutar")
    finally:
        bloqueo.liberar()
        otro.cerrar()
    assert resultado.exit_code == 2
    assert "otro" in resultado.output and "ejecutar" in resultado.output


def test_cola_corrupta_sale_con_3_sin_tocar_las_descargas_parciales(
    cli, app_home, destino, servidor, medios
):
    """Edge case "archivo de estado corrupto" (spec; FR-021, T087)."""
    registrar(servidor, medios, "v")
    assert cli("agregar", servidor.url("watch/v"), "--carpeta", destino).exit_code == 0
    trabajo = destino / ".downloader-tmp" / "1"
    trabajo.mkdir(parents=True)
    parciales = {"descarga.mp4.part": medios.datos_mp4[:300_000],
                 "descarga.mp4.ytdl": b'{"downloader": {"current_fragment": {"index": 0}}}'}
    for nombre, datos in parciales.items():
        (trabajo / nombre).write_bytes(datos)
    basura = os.urandom(4096)
    (app_home / "cola.db").write_bytes(basura)

    resultado = cli("ejecutar")

    assert resultado.exit_code == 3, resultado.output
    assert "cola.db" in resultado.stderr and "corrupt" in resultado.stderr
    (copia,) = app_home.glob("cola.db.corrupta-*")
    assert copia.read_bytes() == basura
    assert copia.name in resultado.stderr
    assert (app_home / "cola.db").read_bytes() == basura  # no hay una cola nueva
    assert {p.name: p.read_bytes() for p in trabajo.iterdir()} == parciales
    assert sum(servidor.peticiones.values()) == 0 and servidor.secretos_emitidos == []
    # El bloqueo de `ejecutar` quedó libre: otro proceso lo podría tomar.
    otro = Store(app_home, sqlite3.connect(":memory:"))
    try:
        otro.bloquear_ejecucion()
    finally:
        otro.cerrar()


# `signal.SIGBREAK` solo existe en Windows; en Linux se simula con `SIGTERM`, que existe en ambos.


def test_ejecutar_en_un_hilo_secundario_con_sigbreak(cli, monkeypatch):
    """`signal.signal` solo puede llamarse desde el hilo principal (los tests usan hilos)."""
    monkeypatch.setattr(signal, "SIGBREAK", signal.SIGTERM, raising=False)
    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(r=cli("ejecutar")))
    hilo.start()
    hilo.join(timeout=30)
    assert resultado["r"].exit_code == 0, repr(resultado["r"].exception)


def test_ctrl_break_interrumpe_como_ctrl_c(cli, monkeypatch):
    monkeypatch.setattr(signal, "SIGBREAK", signal.SIGTERM, raising=False)
    anterior = signal.getsignal(signal.SIGTERM)
    try:
        assert cli("ejecutar").exit_code == 0
        assert signal.getsignal(signal.SIGTERM) is signal.default_int_handler
    finally:
        signal.signal(signal.SIGTERM, anterior)
