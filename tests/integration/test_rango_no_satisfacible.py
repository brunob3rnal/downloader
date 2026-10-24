"""HTTP 416 al reanudar (research R23; FR-008, FR-023, FR-024, FR-026).

El `.part` se deja en el directorio de trabajo antes de `ejecutar`, como lo dejaría un cierre
abrupto justo al recibir el último byte: yt-dlp lo reanuda con `Range: bytes=<tamaño>-`.

- `Content-Range: bytes */TOTAL` con TOTAL igual al `.part`: está completo; se verifica y se
  publica sin pedir ni un byte más.
- `.part` mayor que TOTAL: se reinicia desde cero con aviso, aunque la diferencia sea menor
  que la tolerancia de 100 bytes de yt-dlp.
- 416 sin `Content-Range`: error ambiguo (tope de 3 intentos sin avance), nunca permanente.
"""

import io
import threading

import pytest

from tests.support import fake_extractor

# Dependen de cómo repite yt-dlp la petición tras un 416 (R23).
pytestmark = pytest.mark.ytdlp_internals

LIMITE = 60  # s


@pytest.fixture
def entorno(app_home, destino, servidor, medios, monitor_falso, tiempos_rapidos, monkeypatch):
    from rich.console import Console

    from downloader import progress, store

    fake_extractor.instalar(monkeypatch)
    cola = store.Store.abrir(app_home)
    servidor.registrar("v1.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    descarga = cola.agregar(servidor.url("watch/v1"), "mejor", str(destino)).descarga
    entorno = _Entorno(cola, descarga.id, destino, monitor_falso, tiempos_rapidos)
    transicion = cola.transicion

    def espiar(id_, hacia, **campos):
        entorno.estados.append(hacia)
        return transicion(id_, hacia, **campos)

    monkeypatch.setattr(cola, "transicion", espiar)
    entorno.salida = io.StringIO()
    entorno.presentador = progress.Presentador(Console(file=entorno.salida, width=200))
    yield entorno
    cola.cerrar()


class _Entorno:
    def __init__(self, cola, id_, destino, monitor, tiempos):
        self.cola = cola
        self.id = id_
        self.destino = destino
        self.monitor = monitor
        self.tiempos = tiempos
        self.estados = []
        self.trabajo = destino / ".downloader-tmp" / str(id_)

    def dejar_parcial(self, datos):
        self.trabajo.mkdir(parents=True)
        (self.trabajo / "descarga.mp4.part").write_bytes(datos)

    def ejecutar(self):
        from downloader import worker

        hilo = threading.Thread(
            target=worker.ejecutar,
            args=(self.cola, self.monitor, self.presentador, self.tiempos),
            daemon=True,
        )
        hilo.start()
        hilo.join(timeout=LIMITE)
        assert not hilo.is_alive(), "ejecutar no terminó"
        return self.salida.getvalue()

    def descarga(self):
        return self.cola.obtener(self.id)

    def publicado(self):
        return self.destino / "Video de prueba.mp4"


def test_parcial_completo_con_416_se_publica_sin_pedir_mas_bytes(entorno, servidor, medios):
    from downloader.models import Estado

    datos = medios.datos_mp4
    entorno.dejar_parcial(datos)
    salida = entorno.ejecutar()

    descarga = entorno.descarga()
    assert descarga.estado is Estado.COMPLETED, descarga.ultimo_error
    assert entorno.publicado().read_bytes() == datos
    assert servidor.bytes_servidos["v1.mp4"] <= 0.01 * len(datos)
    assert servidor.peticiones["v1.mp4"] == 1  # solo la petición que respondió 416
    assert Estado.WAITING_NETWORK not in entorno.estados
    assert not descarga.ultimo_aviso
    assert "aviso" not in salida


def test_parcial_completo_pero_danado_no_pasa_la_verificacion(entorno, servidor, medios):
    """El 416 dice que el tamaño es el correcto, no que el contenido lo sea: ffprobe decide."""
    from downloader import publish
    from downloader.models import Estado

    datos = medios.datos_mp4
    mitad = len(datos) // 2
    danado = datos[:mitad] + bytes(len(datos) - mitad)  # mismo tamaño; la mitad final a cero
    entorno.dejar_parcial(danado)
    entorno.ejecutar()

    descarga = entorno.descarga()
    assert descarga.estado is Estado.FAILED
    assert "verificación" in descarga.ultimo_error.lower()
    assert not entorno.publicado().exists()
    assert sorted(p.name for p in entorno.trabajo.iterdir()) == [publish.INVALIDOS]
    (intento,) = (entorno.trabajo / publish.INVALIDOS).iterdir()
    assert (intento / "descarga.mp4").read_bytes() == danado
    assert servidor.bytes_servidos["v1.mp4"] <= 0.01 * len(datos)


@pytest.mark.parametrize("sobrante", [50, 5000], ids=["dentro-de-la-tolerancia", "fuera"])
def test_parcial_mayor_que_el_archivo_se_reinicia_con_aviso(
    entorno, servidor, medios, sobrante
):
    from downloader.models import Estado

    datos = medios.datos_mp4
    entorno.dejar_parcial(datos + b"\0" * sobrante)
    salida = entorno.ejecutar()

    descarga = entorno.descarga()
    assert descarga.estado is Estado.COMPLETED, descarga.ultimo_error
    assert entorno.publicado().read_bytes() == datos
    assert "reiniciada desde cero" in descarga.ultimo_aviso
    assert "mayor que el archivo del servidor" in descarga.ultimo_aviso
    assert f"#{entorno.id} aviso  reiniciada desde cero: " in salida
    # Se descargó una vez entero: el reinicio que exige un parcial que no encaja (FR-024).
    assert len(datos) <= servidor.bytes_servidos["v1.mp4"] <= 1.01 * len(datos)


def test_416_sin_content_range_se_reintenta_como_ambiguo(entorno, servidor, medios):
    from downloader.models import Estado

    datos = medios.datos_mp4
    servidor.programar("v1.mp4", cortar_tras_bytes=int(len(datos) * 0.4))
    servidor.programar("v1.mp4", cortar_tras_bytes=0)
    servidor.programar("v1.mp4", errores=[416, 416])  # dos intentos ambiguos: bajo el tope
    entorno.ejecutar()

    descarga = entorno.descarga()
    assert descarga.estado is Estado.COMPLETED, descarga.ultimo_error
    assert Estado.FAILED not in entorno.estados
    assert entorno.estados.count(Estado.WAITING_NETWORK) == 3
    assert entorno.publicado().read_bytes() == datos
    assert servidor.bytes_servidos["v1.mp4"] <= 1.01 * len(datos)  # SC-002


def test_416_sin_content_range_persistente_falla_tras_3_intentos_sin_avance(
    entorno, servidor, medios
):
    from downloader.models import Estado

    datos = medios.datos_mp4
    servidor.programar("v1.mp4", cortar_tras_bytes=int(len(datos) * 0.4))
    servidor.programar("v1.mp4", cortar_tras_bytes=0)
    servidor.programar("v1.mp4", errores=[416] * 20)
    entorno.ejecutar()

    descarga = entorno.descarga()
    assert descarga.estado is Estado.FAILED
    assert "3 veces seguidas sin avanzar" in descarga.ultimo_error
    assert "HTTP 416" in descarga.ultimo_error
    # El corte y los dos primeros 416 esperan; el tercer 416 sin avance agota el tope.
    assert entorno.estados.count(Estado.WAITING_NETWORK) == 3
    assert not entorno.publicado().exists()
    assert list(entorno.trabajo.glob("*.part"))  # el parcial se conserva (Principio IV)
