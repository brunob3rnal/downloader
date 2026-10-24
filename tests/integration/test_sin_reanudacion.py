"""Servidor que no permite reanudar (escenario 11 de quickstart; FR-024, research R3).

Si el servidor ignora `Range`, yt-dlp borra el `.part` y descarga desde cero. La descarga debe
terminar íntegra y el usuario debe recibir el aviso, tanto si el reinicio ocurre dentro de una
misma llamada a yt-dlp (sus reintentos internos) como en un intento nuestro posterior.
"""

import io
import threading

import pytest
from rich.console import Console

from downloader import progress, worker
from downloader.models import Estado
from downloader.store import Store
from tests.support import fake_extractor

# Dependen de que yt-dlp borre el `.part` si el servidor ignora `Range` (R3).
pytestmark = pytest.mark.ytdlp_internals

AVISO = "reiniciada desde cero: el servidor no permite reanudar"
LIMITE = 60  # s; un bucle que no se recupera no debe colgar la suite


@pytest.fixture
def entorno(app_home, destino, servidor, medios, monitor_falso, tiempos_rapidos, monkeypatch):
    fake_extractor.instalar(monkeypatch)
    cola = Store.abrir(app_home)
    estados = []
    al_cambiar = []
    transicion = cola.transicion

    def espiar(id_, hacia, **campos):
        estados.append(hacia)
        for funcion in al_cambiar:
            funcion(hacia)
        return transicion(id_, hacia, **campos)

    monkeypatch.setattr(cola, "transicion", espiar)
    servidor.registrar("v1.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    descarga = cola.agregar(servidor.url("watch/v1"), "mejor", str(destino)).descarga
    salida = io.StringIO()
    presentador = progress.Presentador(Console(file=salida))

    def ejecutar():
        hilo = threading.Thread(
            target=worker.ejecutar, args=(cola, monitor_falso, presentador, tiempos_rapidos),
            daemon=True,
        )
        hilo.start()
        hilo.join(timeout=LIMITE)
        assert not hilo.is_alive(), "ejecutar no terminó"
        return salida.getvalue()

    yield _Entorno(cola, descarga.id, estados, al_cambiar, ejecutar)
    cola.cerrar()


class _Entorno:
    def __init__(self, cola, id_, estados, al_cambiar, ejecutar):
        self.cola = cola
        self.id = id_
        self.estados = estados
        self.al_cambiar = al_cambiar
        self.ejecutar = ejecutar


def comprobar_integra(entorno, destino, medios):
    descarga = entorno.cola.obtener(entorno.id)
    assert descarga.estado is Estado.COMPLETED
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == medios.datos_mp4
    return descarga


def test_reinicio_dentro_de_la_misma_llamada(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    servidor.configurar("v1.mp4", sin_range=True)
    servidor.programar("v1.mp4", cortar_tras_bytes=int(tamano * 0.4))
    salida = entorno.ejecutar()

    descarga = comprobar_integra(entorno, destino, medios)
    assert AVISO in descarga.ultimo_aviso
    assert f"#{entorno.id} aviso  {AVISO}" in salida
    # yt-dlp reinició dentro de sus reintentos internos: ningún intento nuestro falló.
    assert Estado.WAITING_NETWORK not in entorno.estados
    # El reinicio se descargó entero otra vez, después del 40 % perdido.
    assert servidor.bytes_servidos["v1.mp4"] >= 1.4 * tamano - 1


def test_reinicio_en_un_intento_posterior(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    servidor.programar("v1.mp4", cortar_tras_bytes=int(tamano * 0.4))
    servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)

    def sin_range_al_esperar(hacia):
        # El `.part` del 40 % llega intacto a nuestro siguiente intento, y entonces el
        # servidor deja de admitir `Range`.
        if hacia is Estado.WAITING_NETWORK:
            servidor.configurar("v1.mp4", sin_range=True)

    entorno.al_cambiar.append(sin_range_al_esperar)
    salida = entorno.ejecutar()

    descarga = comprobar_integra(entorno, destino, medios)
    assert Estado.WAITING_NETWORK in entorno.estados
    assert AVISO in descarga.ultimo_aviso
    assert salida.count(f"#{entorno.id} aviso  {AVISO}") == 1


def test_sin_aviso_si_el_servidor_admite_range(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    servidor.programar("v1.mp4", cortar_tras_bytes=int(tamano * 0.4))
    servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)
    salida = entorno.ejecutar()

    descarga = comprobar_integra(entorno, destino, medios)
    assert Estado.WAITING_NETWORK in entorno.estados
    assert not descarga.ultimo_aviso
    assert "reiniciada" not in salida
