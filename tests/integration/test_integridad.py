"""Integridad de los archivos entregados (escenario 7 de quickstart; US5, FR-013, FR-023,
SC-004; research R8 y R22).

Un servidor que anuncia más bytes de los que sirve no llega a la verificación: yt-dlp lo ve
como un corte en el mismo byte en cada intento y se reintenta como cualquier corte (FR-010).
Lo que la verificación debe atrapar es un archivo que yt-dlp da por terminado y está truncado:
un servidor que anuncia menos bytes de los que tiene el archivo y sirve justo esos.
"""

import io
import subprocess
import threading
import time

import pytest

from tests.support import fake_extractor

LIMITE = 60  # s; un bucle que no termina no debe colgar la suite


@pytest.fixture
def entorno(app_home, destino, servidor, monitor_falso, tiempos_rapidos, monkeypatch):
    from rich.console import Console

    from downloader import progress, store

    fake_extractor.instalar(monkeypatch)
    cola = store.Store.abrir(app_home)
    entorno = _Entorno(cola, destino, monitor_falso, tiempos_rapidos,
                       progress.Presentador(Console(file=io.StringIO())))
    transicion = cola.transicion

    def espiar(id_, hacia, **campos):
        fila = transicion(id_, hacia, **campos)
        entorno.al_cambiar(hacia)
        return fila

    monkeypatch.setattr(cola, "transicion", espiar)
    yield entorno
    cola.cerrar()


class _Entorno:
    def __init__(self, cola, destino, monitor, tiempos, presentador):
        self.cola = cola
        self.destino = destino
        self.monitor = monitor
        self.tiempos = tiempos
        self.presentador = presentador
        self.estados = []
        # Contenido de la carpeta de destino y del directorio de trabajo en cada transición.
        self.vistas = []
        self.hilo = None

    def al_cambiar(self, hacia):
        self.estados.append(hacia)
        temporal = self.destino / ".downloader-tmp"
        partes = sorted(p.name for p in temporal.glob("*/*.part")) if temporal.exists() else []
        self.vistas.append((hacia, sorted(p.name for p in self.destino.iterdir()), partes))

    def agregar(self, url):
        return self.cola.agregar(url, "mejor", str(self.destino))

    def lanzar(self):
        from downloader import worker

        self.estados.clear()
        self.vistas.clear()
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

    def publicados(self):
        return sorted(p.name for p in self.destino.iterdir() if p.is_file())


def paquetes(archivo):
    salida = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries", "stream=nb_read_packets",
         "-of", "csv=p=0", str(archivo)],
        check=True, capture_output=True, text=True,
    ).stdout
    return [int(linea) for linea in salida.split()]


def test_tamano_anunciado_mayor_que_el_servido_se_reintenta_sin_publicar(
    entorno, destino, servidor, medios
):
    """Indistinguible de un corte repetido en el mismo byte: sigue pendiente (FR-010)."""
    from downloader.models import Estado

    datos = medios.datos_mp4
    servidor.registrar("v1.mp4", datos, tipo_contenido="video/mp4",
                       anunciar_tamano=len(datos) + 5000)
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    id_ = entorno.agregar(servidor.url("watch/v1")).descarga.id
    entorno.lanzar()

    fin = time.monotonic() + LIMITE
    while entorno.estados.count(Estado.WAITING_NETWORK) < 4:
        assert entorno.hilo.is_alive(), "ejecutar terminó en lugar de seguir reintentando"
        assert time.monotonic() < fin, "no reintentó"
        time.sleep(0.02)
    descarga = entorno.cola.obtener(id_)
    assert descarga.estado in (Estado.WAITING_NETWORK, Estado.DOWNLOADING)
    assert Estado.FAILED not in entorno.estados
    assert entorno.publicados() == []  # SC-004
    assert list((destino / ".downloader-tmp" / str(id_)).glob("*.part"))

    # El servidor se corrige: el `.part` ya tiene todo el archivo (responde 416 con
    # `Content-Range: bytes */TOTAL`, research R23) y se publica tras verificarlo.
    servidor.configurar("v1.mp4", anunciar_tamano=None)
    entorno.esperar_fin()
    assert entorno.cola.obtener(id_).estado is Estado.COMPLETED
    assert entorno.publicados() == ["Video de prueba [v1].mp4"]
    assert (destino / "Video de prueba [v1].mp4").read_bytes() == datos
    assert servidor.bytes_servidos["v1.mp4"] <= 1.01 * len(datos)  # SC-002


def test_archivo_servido_truncado_no_se_publica(entorno, destino, servidor, medios):
    """Content-Length menor que el archivo: yt-dlp termina y ffprobe detecta el truncamiento."""
    from downloader import publish
    from downloader.models import Estado

    datos = medios.datos_mp4
    servido = int(len(datos) * 0.97)
    servidor.registrar("v1.mp4", datos, tipo_contenido="video/mp4", anunciar_tamano=servido)
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    id_ = entorno.agregar(servidor.url("watch/v1")).descarga.id
    entorno.ejecutar()

    descarga = entorno.cola.obtener(id_)
    assert descarga.estado is Estado.FAILED
    assert "verificación" in descarga.ultimo_error.lower()
    assert Estado.WAITING_NETWORK not in entorno.estados  # sin reintentos: es permanente
    assert entorno.publicados() == []  # SC-004
    assert servidor.peticiones["v1.mp4"] == 1

    trabajo = destino / ".downloader-tmp" / str(id_)
    assert sorted(p.name for p in trabajo.iterdir()) == [publish.INVALIDOS]
    (intento,) = (trabajo / publish.INVALIDOS).iterdir()
    assert (intento / "descarga.mp4").read_bytes() == datos[:servido]


def test_reencolar_tras_verificacion_fallida_descarga_de_nuevo(
    entorno, destino, servidor, medios
):
    from downloader import publish
    from downloader.models import Estado

    datos = medios.datos_mp4
    servido = int(len(datos) * 0.97)
    servidor.registrar("v1.mp4", datos, tipo_contenido="video/mp4", anunciar_tamano=servido)
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    url = servidor.url("watch/v1")
    id_ = entorno.agregar(url).descarga.id
    entorno.ejecutar()
    assert entorno.cola.obtener(id_).estado is Estado.FAILED
    servidos_antes = servidor.bytes_servidos["v1.mp4"]

    servidor.configurar("v1.mp4", anunciar_tamano=None)
    assert entorno.agregar(url).accion == "reencolada"
    entorno.ejecutar()

    assert entorno.cola.obtener(id_).estado is Estado.COMPLETED
    assert (destino / "Video de prueba [v1].mp4").read_bytes() == datos
    # Se descargó entero otra vez: los archivos de `invalidos/` no se dieron por buenos.
    assert servidor.bytes_servidos["v1.mp4"] - servidos_antes >= len(datos)
    # Y siguen ahí: nunca se borran automáticamente (data-model.md).
    invalidos = destino / ".downloader-tmp" / str(id_) / publish.INVALIDOS
    (intento,) = invalidos.iterdir()
    assert (intento / "descarga.mp4").read_bytes() == datos[:servido]


def test_durante_una_descarga_interrumpida_solo_existe_el_directorio_temporal(
    entorno, destino, servidor, medios
):
    from downloader.models import Estado

    datos = medios.datos_mp4
    servidor.registrar("v1.mp4", datos, tipo_contenido="video/mp4")
    servidor.programar("v1.mp4", cortar_tras_bytes=int(len(datos) * 0.4))
    servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)
    servidor.registrar_video("v1", "v1.mp4", titulo="Video de prueba")
    entorno.agregar(servidor.url("watch/v1"))
    entorno.ejecutar()

    interrumpida = [v for v in entorno.vistas if v[0] is Estado.WAITING_NETWORK]
    assert interrumpida, "la descarga no llegó a interrumpirse"
    for _, contenido, partes in interrumpida:
        assert contenido == [".downloader-tmp"]  # ningún nombre final (US5, escenario 1)
        assert partes == ["descarga.mp4.part"]  # el parcial, claramente identificado
    assert entorno.publicados() == ["Video de prueba [v1].mp4"]
    assert (destino / "Video de prueba [v1].mp4").read_bytes() == datos


def test_fragmento_hls_cortado_en_transito_se_reintenta_y_termina_integro(
    entorno, destino, servidor, medios
):
    """yt-dlp no acepta un fragmento más corto que su Content-Length: nunca se une truncado."""
    from downloader.models import Estado

    manifiesto = servidor.registrar_hls("hls", medios.hls)
    # Cada petición trae 1000 bytes más del fragmento: agota los reintentos internos de yt-dlp.
    servidor.programar("hls/fragmento2.ts", cortar_tras_bytes=1000, veces=4)
    servidor.registrar_video("h", manifiesto, titulo="Video HLS")
    id_ = entorno.agregar(servidor.url("watch/h")).descarga.id
    entorno.ejecutar()

    assert entorno.cola.obtener(id_).estado is Estado.COMPLETED
    assert Estado.WAITING_NETWORK in entorno.estados
    assert entorno.publicados() == ["Video HLS [h].mp4"]
    assert paquetes(destino / "Video HLS [h].mp4") == paquetes(medios.mp4)


def test_fragmento_hls_ausente_no_se_salta(entorno, destino, servidor, medios):
    from downloader.models import Estado

    manifiesto = servidor.registrar_hls("hls", medios.hls, fragmento_ausente=2)
    servidor.registrar_video("h", manifiesto, titulo="Video HLS")
    id_ = entorno.agregar(servidor.url("watch/h")).descarga.id
    entorno.ejecutar()

    descarga = entorno.cola.obtener(id_)
    assert descarga.estado is Estado.FAILED
    assert "404" in descarga.ultimo_error
    assert entorno.publicados() == []  # SC-004
    # `skip_unavailable_fragments=False`: no siguió con los fragmentos posteriores.
    assert servidor.peticiones["hls/fragmento3.ts"] == 0
    assert servidor.peticiones["hls/fragmento4.ts"] == 0
