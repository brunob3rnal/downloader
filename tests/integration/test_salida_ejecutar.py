"""Condición de salida de `ejecutar` (FR-021)."""

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
