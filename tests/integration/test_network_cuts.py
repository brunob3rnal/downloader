"""Cortes de red durante la descarga (escenarios 2, 2b y 3 de quickstart; US2).

Cada caso falla más veces seguidas que los 3 reintentos internos de yt-dlp, de modo que solo
termina si actúan nuestros reintentos; por eso se comprueba que la fila pasó por
`waiting_network`.
"""

import io
import threading
import time

import pytest

from tests.support import fake_extractor

LIMITE = 60  # s; un bucle que no se recupera no debe colgar la suite


@pytest.fixture
def entorno(app_home, destino, servidor, medios, monitor_falso, tiempos_rapidos, monkeypatch):
    from rich.console import Console

    from downloader import progress, store

    fake_extractor.instalar(monkeypatch)
    cola = store.Store.abrir(app_home)
    estados = []
    transicion = cola.transicion

    def espiar(id_, hacia, **campos):
        estados.append(hacia)
        return transicion(id_, hacia, **campos)

    monkeypatch.setattr(cola, "transicion", espiar)
    servidor.registrar("v1.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    descarga = cola.agregar(servidor.url("watch/v1"), "mejor", str(destino)).descarga
    presentador = progress.Presentador(Console(file=io.StringIO()))
    yield _Entorno(cola, descarga.id, estados, presentador, monitor_falso, tiempos_rapidos)
    cola.cerrar()


class _Entorno:
    def __init__(self, cola, id_, estados, presentador, monitor, tiempos):
        self.cola = cola
        self.id = id_
        self.estados = estados
        self.presentador = presentador
        self.monitor = monitor
        self.tiempos = tiempos
        self.hilo = None

    def lanzar(self):
        from downloader import worker

        self.hilo = threading.Thread(
            target=worker.ejecutar,
            args=(self.cola, self.monitor, self.presentador, self.tiempos),
            daemon=True,
        )
        self.hilo.start()

    def esperar_fin(self):
        self.hilo.join(timeout=LIMITE)
        assert not self.hilo.is_alive(), "ejecutar no terminó"

    def ejecutar(self):
        self.lanzar()
        self.esperar_fin()


def comprobar_integra(entorno, destino, servidor, medios):
    from downloader.models import Estado

    descarga = entorno.cola.obtener(entorno.id)
    assert descarga.estado is Estado.COMPLETED
    assert Estado.WAITING_NETWORK in entorno.estados
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == medios.datos_mp4
    assert servidor.bytes_servidos["v1.mp4"] <= 1.01 * len(medios.datos_mp4)  # SC-002


def test_corte_al_40_por_ciento_cuatro_veces(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    servidor.programar("v1.mp4", cortar_tras_bytes=int(tamano * 0.4))
    servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)
    entorno.ejecutar()
    comprobar_integra(entorno, destino, servidor, medios)


def test_servidor_colgado_es_temporal(entorno, destino, servidor, medios):
    servidor.programar("v1.mp4", colgarse=True, veces=4)
    entorno.ejecutar()
    comprobar_integra(entorno, destino, servidor, medios)


def test_errores_5xx_seguidos_son_temporales(entorno, destino, servidor, medios):
    servidor.programar("v1.mp4", errores=[503, 503, 500, 503, 500])
    entorno.ejecutar()
    comprobar_integra(entorno, destino, servidor, medios)


def test_servidor_caido_espera_sin_consumir_intentos(entorno, destino, servidor, medios):
    from downloader.models import Estado

    servidor.detener()
    entorno.monitor.poner_sin_conexion()
    entorno.lanzar()

    fin = time.monotonic() + LIMITE
    while entorno.cola.obtener(entorno.id).estado is not Estado.WAITING_NETWORK:
        assert entorno.hilo.is_alive(), "ejecutar terminó sin pasar a waiting_network"
        assert time.monotonic() < fin, "no pasó a waiting_network"
        time.sleep(0.02)
    intentos = entorno.cola.obtener(entorno.id).intentos_sin_avance

    # Varias veces el tope de backoff de los tests (0,5 s): sigue esperando sin gastar intentos.
    for _ in range(15):
        time.sleep(0.1)
        descarga = entorno.cola.obtener(entorno.id)
        assert descarga.estado is Estado.WAITING_NETWORK
        assert descarga.intentos_sin_avance == intentos

    servidor.reanudar()
    entorno.monitor.poner_en_linea()
    entorno.esperar_fin()
    comprobar_integra(entorno, destino, servidor, medios)


def test_cortes_en_tres_momentos_distintos(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    for _ in range(3):
        servidor.programar("v1.mp4", cortar_tras_bytes=int(tamano * 0.2))
        servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)
    entorno.ejecutar()
    comprobar_integra(entorno, destino, servidor, medios)
