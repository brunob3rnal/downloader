"""Espera entre reintentos del trabajador (FR-007, FR-010, FR-026, SC-001; research R2, R10).

`ytdl.extraer_plano` y `ytdl.descargar` se sustituyen por un guion de intentos: cada intento
puede informar progreso (y escribir el `.part` correspondiente, como yt-dlp) y después fallar
con un error dado o terminar con un archivo en el directorio de trabajo.
"""

import io
import threading
import time
from datetime import datetime

import pytest
from rich.console import Console
from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import DownloadError

from downloader import progress, publish, worker, ytdl
from downloader.errors import Fase, RegistroRed, UltimaPeticion
from downloader.models import Estado
from downloader.store import Store
from downloader.worker import Tiempos

LIMITE = 20  # s


def corte():
    return TransportError("Connection refused: http://videos.ejemplo/v.mp4?token=SECRETO")


def acceso_denegado():
    """403 al descargar después de agotar yt-dlp sus reintentos: error ambiguo (R4)."""
    error = DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    error.registro = RegistroRed(UltimaPeticion(estado=403), Fase.DESCARGA)
    return error


class Intento:
    def __init__(self, bytes_=None, error=None, terminado=False):
        self.bytes = bytes_
        self.error = error
        self.terminado = terminado  # informa el formato como terminado (hook `finished`)


class Guion:
    """Sustituye a yt-dlp: cada llamada a `descargar` consume un `Intento`.

    Su archivo final (`b"video"`) no es un video: la fixture `entorno` sustituye también la
    verificación con ffprobe (`publish.verificar`) por `Entorno.verificar`, que lo acepta.
    """

    def __init__(self, intentos):
        self.intentos = list(intentos)
        self.extracciones = 0
        self.descargas = 0
        self.inicios: list[float] = []

    def extraer_plano(self, url, navegador=None, socket_timeout=20):
        self.extracciones += 1
        return {"id": "v", "title": "Video de prueba"}

    def descargar(self, descarga, trabajo, navegador, al_progresar, socket_timeout=20,
                  vigilancia=None):
        self.descargas += 1
        self.inicios.append(time.monotonic())
        intento = self.intentos.pop(0) if self.intentos else Intento()
        final = trabajo / "descarga.mp4"
        if intento.bytes is not None:
            # Como yt-dlp: lo informado ya está en el `.part`.
            parcial = trabajo / "descarga.mp4.part"
            parcial.write_bytes(b"x" * intento.bytes)
            al_progresar(ytdl.Progreso(intento.bytes, 1000, None, None, str(final), "downloading"))
        if intento.error is not None:
            raise intento.error
        final.write_bytes(b"video")
        if intento.terminado:
            al_progresar(ytdl.Progreso(5, 5, None, None, str(final), "finished", 5))
        return {"id": "v", "title": "Video de prueba",
                "requested_downloads": [{"filepath": str(final)}]}


@pytest.fixture
def cola(app_home):
    cola = Store.abrir(app_home)
    yield cola
    cola.cerrar()


@pytest.fixture
def entorno(cola, destino, monitor_falso, tiempos_rapidos, monkeypatch):
    """Lanza `worker.ejecutar` en un hilo con el guion dado; registra cada fila tras cambiar."""

    filas = []
    transicion = cola.transicion

    def espiar(id_, hacia, **campos):
        fila = transicion(id_, hacia, **campos)
        filas.append(fila)
        return fila

    monkeypatch.setattr(cola, "transicion", espiar)
    descarga = cola.agregar("http://videos.ejemplo/watch/v", "mejor", str(destino)).descarga

    class Entorno:
        id = descarga.id
        estados = filas
        tiempos = tiempos_rapidos
        hilo = None

        @staticmethod
        def verificar(formatos, archivo_final):
            pass

        def lanzar(self, intentos):
            self.guion = Guion(intentos)
            monkeypatch.setattr(ytdl, "extraer_plano", self.guion.extraer_plano)
            monkeypatch.setattr(ytdl, "descargar", self.guion.descargar)
            monkeypatch.setattr(publish, "verificar",
                                lambda formatos, archivo: self.verificar(formatos, archivo),
                                raising=False)
            presentador = progress.Presentador(Console(file=io.StringIO()))
            self.hilo = threading.Thread(
                target=worker.ejecutar, args=(cola, monitor_falso, presentador, self.tiempos),
                daemon=True,
            )
            self.hilo.start()
            return self.guion

        def esperar_fin(self):
            self.hilo.join(timeout=LIMITE)
            assert not self.hilo.is_alive(), "ejecutar no terminó"

        def esperando(self):
            return [f for f in filas if f.estado is Estado.WAITING_NETWORK]

    return Entorno()


def test_espera_en_waiting_network_con_proximo_intento(entorno, cola):
    entorno.lanzar([Intento(error=corte()), Intento(error=corte())])
    entorno.esperar_fin()

    esperas = entorno.esperando()
    assert len(esperas) == 2
    for fila in esperas:
        assert fila.proximo_intento is not None
        assert datetime.fromisoformat(fila.proximo_intento)
        assert "Connection refused" in fila.ultimo_error
        assert "SECRETO" not in fila.ultimo_error
    final = cola.obtener(entorno.id)
    assert final.estado is Estado.COMPLETED
    assert final.proximo_intento is None
    # Cada intento vuelve a extraer (R2).
    assert entorno.guion.extracciones == entorno.guion.descargas == 3


def test_respeta_la_espera_de_backoff(entorno):
    entorno.tiempos = Tiempos(backoff_base=0.4, backoff_tope=0.4, socket_timeout=1,
                              espera_cola=0.05)
    guion = entorno.lanzar([Intento(error=corte())])
    entorno.esperar_fin()
    assert guion.inicios[1] - guion.inicios[0] >= 0.4 * 0.8 - 0.01


def test_reintento_inmediato_al_volver_la_red(entorno, monitor_falso):
    # Espera de backoff muy larga: solo la vuelta de la red puede adelantar el reintento.
    entorno.tiempos = Tiempos(backoff_base=30, backoff_tope=60, socket_timeout=1,
                              espera_cola=0.05)
    monitor_falso.poner_sin_conexion()
    entorno.lanzar([Intento(error=corte())])

    fin = time.monotonic() + LIMITE
    while not entorno.esperando():
        assert time.monotonic() < fin, "no pasó a waiting_network"
        time.sleep(0.02)
    proximo = datetime.fromisoformat(entorno.esperando()[0].proximo_intento)
    assert (proximo - datetime.now(proximo.tzinfo)).total_seconds() > 20

    time.sleep(0.3)
    assert entorno.hilo.is_alive()
    vuelta = time.monotonic()
    monitor_falso.poner_en_linea()
    entorno.esperar_fin()
    assert entorno.guion.inicios[1] - vuelta < 1


def test_reintento_inmediato_si_la_red_cae_y_vuelve_durante_la_espera(entorno, monitor_falso):
    entorno.tiempos = Tiempos(backoff_base=30, backoff_tope=60, socket_timeout=1,
                              espera_cola=0.05)
    entorno.lanzar([Intento(error=corte())])
    fin = time.monotonic() + LIMITE
    while not entorno.esperando():
        assert time.monotonic() < fin, "no pasó a waiting_network"
        time.sleep(0.02)

    monitor_falso.poner_sin_conexion()
    time.sleep(0.5)
    vuelta = time.monotonic()
    monitor_falso.poner_en_linea()
    entorno.esperar_fin()
    assert entorno.guion.inicios[1] - vuelta < 1


def test_sin_conexion_no_consume_intentos(entorno, monitor_falso):
    monitor_falso.poner_sin_conexion()
    entorno.lanzar([Intento(error=corte())] * 3)
    fin = time.monotonic() + LIMITE
    while not entorno.esperando():
        assert time.monotonic() < fin, "no pasó a waiting_network"
        time.sleep(0.02)
    # Varias veces el tope de backoff (0,5 s): sigue esperando a la red, sin reintentar.
    time.sleep(1.5)
    assert entorno.guion.descargas == 1
    assert len(entorno.esperando()) == 1
    assert entorno.esperando()[0].intentos_sin_avance == 0
    monitor_falso.poner_en_linea()
    entorno.esperar_fin()


def test_intentos_sin_avance_vuelve_a_0_tras_progreso(entorno, cola):
    entorno.lanzar([
        Intento(bytes_=100, error=corte()),  # avanza: 0
        Intento(error=corte()),  # sin progreso: 1
        Intento(bytes_=100, error=corte()),  # 100 ya estaba en el `.part`: 2
        Intento(bytes_=300, error=corte()),  # avanza: 0
        Intento(error=corte()),  # 1
    ])
    entorno.esperar_fin()
    assert [f.intentos_sin_avance for f in entorno.esperando()] == [0, 1, 2, 0, 1]
    assert cola.obtener(entorno.id).estado is Estado.COMPLETED


def test_nunca_falla_por_errores_temporales_no_ambiguos(entorno, cola):
    error_5xx = DownloadError("ERROR: Giving up after 3 retries")
    error_5xx.registro = RegistroRed(UltimaPeticion(estado=503), Fase.DESCARGA)
    entorno.lanzar([Intento(error=corte()), Intento(error=error_5xx)] * 6)
    entorno.esperar_fin()
    assert Estado.FAILED not in [f.estado for f in entorno.estados]
    assert len(entorno.esperando()) == 12
    assert cola.obtener(entorno.id).estado is Estado.COMPLETED


def test_tres_ambiguos_seguidos_sin_avance_fallan(entorno, cola):
    entorno.lanzar([Intento(error=acceso_denegado()) for _ in range(10)])
    entorno.esperar_fin()
    final = cola.obtener(entorno.id)
    assert final.estado is Estado.FAILED
    assert "3 intentos seguidos" in final.ultimo_error
    assert "--cookies-de" in final.ultimo_error
    assert entorno.guion.descargas == 3
    assert len(entorno.esperando()) == 2


def test_un_corte_entre_ambiguos_rompe_la_racha(entorno, cola):
    entorno.lanzar([
        Intento(error=acceso_denegado()), Intento(error=acceso_denegado()),
        Intento(error=corte()),
        Intento(error=acceso_denegado()), Intento(error=acceso_denegado()),
    ])
    entorno.esperar_fin()
    assert cola.obtener(entorno.id).estado is Estado.COMPLETED


# --- Estancamiento (research R21) ---


def estancado():
    from downloader.errors import SinDatos

    return SinDatos("sin datos hace 30 s")


def test_primera_espera_tras_un_estancamiento_es_corta(entorno, cola):
    # El backoff normal sería de 30 s o más: solo la espera de estancamiento (~0,3 s) cabe.
    entorno.tiempos = Tiempos(backoff_base=30, backoff_tope=60, socket_timeout=1,
                              espera_cola=0.05, espera_estancamiento=0.3)
    guion = entorno.lanzar([Intento(bytes_=100, error=estancado())])
    entorno.esperar_fin()
    assert 0.3 * 0.8 - 0.01 <= guion.inicios[1] - guion.inicios[0] < 2
    (espera,) = entorno.esperando()
    assert espera.ultimo_error == "sin datos hace 30 s"
    assert cola.obtener(entorno.id).estado is Estado.COMPLETED


def test_un_estancamiento_no_cuenta_para_el_tope_de_ambiguos(entorno, cola):
    # Tres 403 sin avance con estancamientos entre medias: los estancamientos ni suman ni
    # rompen la racha, así que el tercer 403 agota el tope.
    entorno.tiempos = Tiempos(backoff_base=0.05, backoff_tope=0.5, socket_timeout=1,
                              espera_cola=0.05, espera_estancamiento=0.05)
    entorno.lanzar([
        Intento(error=acceso_denegado()), Intento(error=estancado()),
        Intento(error=acceso_denegado()), Intento(error=estancado()),
        Intento(error=acceso_denegado()),
    ])
    entorno.esperar_fin()
    final = cola.obtener(entorno.id)
    assert final.estado is Estado.FAILED
    assert "3 intentos seguidos" in final.ultimo_error
    assert entorno.guion.descargas == 5


def test_un_estancamiento_no_cuenta_como_intento_sin_avance(entorno, cola):
    entorno.tiempos = Tiempos(backoff_base=0.05, backoff_tope=0.5, socket_timeout=1,
                              espera_cola=0.05, espera_estancamiento=0.05)
    entorno.lanzar([
        Intento(error=corte()),  # sin progreso: 1
        Intento(error=estancado()),  # sigue en 1
        Intento(bytes_=100, error=estancado()),  # avanza, pero el estancamiento no cierra: 1
        Intento(error=corte()),  # el avance del intento estancado se suma a este: 0
    ])
    entorno.esperar_fin()
    assert [f.intentos_sin_avance for f in entorno.esperando()] == [1, 1, 1, 0]
    assert cola.obtener(entorno.id).estado is Estado.COMPLETED


# --- Verificación antes de publicar (FR-023, research R8) ---


def test_verifica_antes_de_publicar(entorno, cola, destino):
    llamadas = []

    def verificar(formatos, archivo_final):
        llamadas.append((list(formatos), archivo_final, sorted(p.name for p in destino.iterdir())))

    entorno.verificar = verificar
    entorno.lanzar([Intento(terminado=True)])
    entorno.esperar_fin()

    trabajo = destino / ".downloader-tmp" / str(entorno.id)
    ((formatos, archivo_final, contenido),) = llamadas
    assert archivo_final == trabajo / "descarga.mp4"
    assert [(f.tamano, f.total) for f in formatos] == [(5, 5)]
    assert contenido == [".downloader-tmp"]  # aún no se había publicado nada
    final = cola.obtener(entorno.id)
    assert final.estado is Estado.COMPLETED
    assert (destino / "Video de prueba [v].mp4").read_bytes() == b"video"


def test_verificacion_fallida_no_publica_y_aparta_los_archivos(entorno, cola, destino):
    def verificar(formatos, archivo_final):
        raise publish.VerificacionFallida("ffprobe no puede leer el archivo hasta el final")

    entorno.verificar = verificar
    entorno.lanzar([Intento(terminado=True)])
    entorno.esperar_fin()

    final = cola.obtener(entorno.id)
    assert final.estado is Estado.FAILED
    assert "ffprobe no puede leer el archivo hasta el final" in final.ultimo_error
    assert entorno.guion.descargas == 1  # sin reintentos
    assert sorted(p.name for p in destino.iterdir()) == [".downloader-tmp"]
    trabajo = destino / ".downloader-tmp" / str(entorno.id)
    assert sorted(p.name for p in trabajo.iterdir()) == [publish.INVALIDOS]
    (intento,) = (trabajo / publish.INVALIDOS).iterdir()
    assert (intento / "descarga.mp4").read_bytes() == b"video"
