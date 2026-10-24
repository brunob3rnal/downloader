"""Conexión que se estanca sin cortarse: sin datos o a goteo (research R21).

El monitor de red indica conexión, así que no es un corte. El vigilante aborta el intento, el
trabajador espera poco y el intento siguiente abre una conexión nueva que reanuda desde el
`.part`. Los umbrales se reducen como el resto de tiempos de los tests; `socket_timeout` es
menor que el umbral de silencio, como en producción (15 s frente a 30 s).
"""

import io
import threading

import pytest
from rich.console import Console

from downloader import progress, worker
from downloader.models import Estado
from downloader.store import Store
from tests.support import fake_extractor

# Dependen de `_real_read` y `read1` de la respuesta de yt-dlp (R21).
pytestmark = pytest.mark.ytdlp_internals

LIMITE = 60  # s; un bucle que no se recupera no debe colgar la suite


def tiempos(socket_timeout):
    return worker.Tiempos(
        backoff_base=0.05, backoff_tope=0.5, socket_timeout=socket_timeout, espera_cola=0.05,
        estancamiento=1, velocidad_minima=1024, espera_estancamiento=0.1,
    )


@pytest.fixture
def entorno(app_home, destino, servidor, medios, monitor_falso, monkeypatch):
    fake_extractor.instalar(monkeypatch)
    cola = Store.abrir(app_home)
    filas = []
    transicion = cola.transicion

    def espiar(id_, hacia, **campos):
        fila = transicion(id_, hacia, **campos)
        filas.append(fila)
        return fila

    monkeypatch.setattr(cola, "transicion", espiar)
    servidor.registrar("v1.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    id_ = cola.agregar(servidor.url("watch/v1"), "mejor", str(destino)).descarga.id
    salida = io.StringIO()

    def ejecutar(socket_timeout):
        hilo = threading.Thread(
            target=worker.ejecutar,
            args=(cola, monitor_falso, progress.Presentador(Console(file=salida)),
                  tiempos(socket_timeout)),
            daemon=True,
        )
        hilo.start()
        hilo.join(timeout=LIMITE)
        assert not hilo.is_alive(), "ejecutar no terminó"
        return salida.getvalue()

    yield id_, cola, filas, ejecutar
    cola.cerrar()


def comprobar(entorno, destino, servidor, medios, salida, causa):
    id_, cola, filas, _ = entorno
    assert cola.obtener(id_).estado is Estado.COMPLETED
    esperas = [f for f in filas if f.estado is Estado.WAITING_NETWORK]
    assert esperas, "no abortó el intento estancado"
    assert all(causa in f.ultimo_error for f in esperas), [f.ultimo_error for f in esperas]
    # Un estancamiento no cuenta como intento sin avance.
    assert all(f.intentos_sin_avance == 0 for f in esperas)
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == medios.datos_mp4
    # Reanuda desde el `.part`: ni reinicio desde cero ni bytes repetidos (SC-002).
    assert "reiniciada" not in salida
    assert servidor.bytes_servidos["v1.mp4"] <= 1.01 * len(medios.datos_mp4)


def test_conexion_abierta_sin_datos(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    # Tras el 40 % el servidor deja de enviar sin cerrar; el reintento interno de yt-dlp
    # reconecta y la nueva conexión tampoco envía nada.
    servidor.programar("v1.mp4", pausar_tras_bytes=int(tamano * 0.4))
    servidor.programar("v1.mp4", pausar_tras_bytes=0)
    salida = entorno[3](socket_timeout=0.5)
    comprobar(entorno, destino, servidor, medios, salida, "sin datos hace")


def test_conexion_a_goteo(entorno, destino, servidor, medios):
    tamano = len(medios.datos_mp4)
    # Tras el 40 %, un byte cada 0,2 s: el timeout de socket (2 s) nunca salta.
    servidor.programar("v1.mp4", gotear_tras_bytes=int(tamano * 0.4), intervalo=0.2)
    salida = entorno[3](socket_timeout=2)
    comprobar(entorno, destino, servidor, medios, salida, "menos de 1 KiB/s")
