"""`progress.Publicador` y `progress.Observadores` (specs/002-desktop-app, T029; research R9).

El publicador escribe `progreso-vivo.json` para la ventana; nunca cambia la salida de la CLI
y, si no puede escribir, lo anota en el logger `downloader.vivo` sin lanzar (P2).
"""

import logging
import threading
from datetime import datetime

import pytest

from downloader import progress
from downloader.models import Calidad, Descarga, Estado
from downloader.ytdl import Progreso


class Reloj:
    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora

    def avanzar(self, segundos):
        self.ahora += segundos


class Monitor:
    def __init__(self, en_linea=True):
        self.en_linea = threading.Event()
        if en_linea:
            self.en_linea.set()


def _descarga(id_=7, url="https://cdn.ejemplo/v.mp4?token=secreto123", titulo="Título secreto"):
    return Descarga(id=id_, url=url, calidad=Calidad.MEJOR, carpeta_destino="/tmp",
                    estado=Estado.DOWNLOADING, titulo=titulo, creada="", actualizada="")


def _progreso(bytes_, total=1000, velocidad=250.0, eta=3.0):
    return Progreso(bytes_, total, velocidad, eta, "v.mp4", "downloading")


@pytest.fixture
def entorno(tmp_path):
    escritos = []

    def escribir(ruta, datos, reintentar=False):
        escritos.append(datos)
        return True

    reloj = Reloj()
    publicador = progress.Publicador(tmp_path, pid=4321, creado=99.5, monitor=Monitor(),
                                     reloj=reloj, escribir=escribir)
    return publicador, reloj, escritos


def test_como_mucho_una_escritura_por_segundo(entorno):
    publicador, reloj, escritos = entorno
    descarga = _descarga()
    publicador.inicio(descarga)
    for n in range(300):  # 100 eventos por segundo durante 3 s
        publicador.progreso(descarga, _progreso(n))
        reloj.avanzar(0.01)
    assert 3 <= len(escritos) <= 4


def test_contenido(entorno):
    publicador, reloj, escritos = entorno
    descarga = _descarga()
    publicador.inicio(descarga)
    reloj.avanzar(1)
    publicador.progreso(descarga, _progreso(400))
    datos = escritos[-1]
    assert datos["pid"] == 4321 and datos["creado"] == 99.5 and datos["en_linea"] is True
    datetime.fromisoformat(datos["actualizado"])
    assert datos["activa"] == {"id": 7, "bytes": 400, "total": 1000, "velocidad": 250.0,
                               "eta": 3.0, "sin_datos": None, "espera": None}


def test_sin_datos_a_partir_de_5_s(entorno):
    publicador, reloj, escritos = entorno
    descarga = _descarga()
    publicador.inicio(descarga)
    publicador.progreso(descarga, _progreso(400))
    reloj.avanzar(4)
    publicador.publicar()
    assert escritos[-1]["activa"]["sin_datos"] is None
    reloj.avanzar(2)
    publicador.publicar()
    assert escritos[-1]["activa"]["sin_datos"] == 6


def test_espera_con_causa_sin_secretos_y_cuenta_atras(entorno):
    publicador, reloj, escritos = entorno
    descarga = _descarga()
    publicador.inicio(descarga)
    publicador.esperando(descarga, reloj() + 40, "conexión rechazada https://x/?sig=abc123")
    publicador.publicar()
    espera = escritos[-1]["activa"]["espera"]
    assert espera["reintento_en"] == 40
    assert "abc123" not in espera["causa"] and "conexión rechazada" in espera["causa"]
    publicador.esperando(descarga, None, "sin conexión")
    reloj.avanzar(1)
    publicador.publicar()
    assert escritos[-1]["activa"]["espera"]["reintento_en"] is None


def test_activa_es_null_entre_descargas(entorno):
    publicador, reloj, escritos = entorno
    descarga = _descarga()
    publicador.inicio(descarga)
    publicador.completada(descarga, "/tmp/v.mp4")
    reloj.avanzar(1)
    publicador.publicar()
    assert escritos[-1]["activa"] is None
    publicador.inicio(descarga)
    publicador.fallida(descarga, "HTTP 404")
    reloj.avanzar(1)
    publicador.publicar()
    assert escritos[-1]["activa"] is None


def test_no_contiene_urls_ni_titulos(entorno):
    publicador, reloj, escritos = entorno
    descarga = _descarga()
    publicador.inicio(descarga)
    publicador.titulo(descarga)
    publicador.progreso(descarga, _progreso(10))
    publicador.aviso(descarga, "reiniciada")
    publicador.mensaje("https://cdn.ejemplo/v.mp4?token=secreto123")
    reloj.avanzar(1)
    publicador.publicar()
    texto = repr(escritos)
    assert "secreto123" not in texto and "Título secreto" not in texto and "cdn.ejemplo" not in texto


def test_sin_monitor_en_linea_es_null(tmp_path):
    escritos = []
    publicador = progress.Publicador(tmp_path, pid=1, creado=1.0, reloj=Reloj(),
                                     escribir=lambda r, d, reintentar=False: escritos.append(d))
    publicador.publicar()
    assert escritos[-1]["en_linea"] is None and escritos[-1]["activa"] is None


def test_escribe_en_el_archivo_y_detener_lo_borra(tmp_path):
    publicador = progress.Publicador(tmp_path, pid=1, creado=1.0, monitor=Monitor())
    publicador.iniciar()
    try:
        publicador.inicio(_descarga())
        publicador.publicar()
        assert (tmp_path / progress.ARCHIVO_VIVO).exists()
    finally:
        publicador.detener()
    assert not (tmp_path / progress.ARCHIVO_VIVO).exists()


def test_los_fallos_se_registran_una_vez_y_nunca_se_lanzan(tmp_path, caplog):
    reloj = Reloj()
    falla = {"si": True}

    def escribir(ruta, datos, reintentar=False):
        if falla["si"]:
            raise IsADirectoryError(f"[Errno 21] Is a directory: '{ruta}'")
        return True

    publicador = progress.Publicador(tmp_path, pid=1, creado=1.0, reloj=reloj, escribir=escribir)
    with caplog.at_level(logging.INFO, logger="downloader.vivo"):
        for _ in range(10):  # diez segundos fallando
            reloj.avanzar(1)
            publicador.publicar()
        falla["si"] = False
        for _ in range(3):
            reloj.avanzar(1)
            publicador.publicar()
    mensajes = [r for r in caplog.records if r.name == "downloader.vivo"]
    assert len(mensajes) == 2
    assert mensajes[0].levelno == logging.WARNING and str(tmp_path) in mensajes[0].getMessage()
    assert mensajes[1].levelno == logging.INFO


def test_un_reemplazo_negado_ocasional_no_se_registra(tmp_path, caplog):
    # Windows: `os.replace` falla si la ventana tiene el archivo abierto en ese instante.
    reloj = Reloj()
    resultados = iter([True, False, True, False, True])
    publicador = progress.Publicador(tmp_path, pid=1, creado=1.0, reloj=reloj,
                                     escribir=lambda r, d, reintentar=False: next(resultados))
    with caplog.at_level(logging.INFO, logger="downloader.vivo"):
        for _ in range(5):
            reloj.avanzar(1)
            publicador.publicar()
    assert [r for r in caplog.records if r.name == "downloader.vivo"] == []


def test_detener_no_lanza_si_no_puede_borrar(tmp_path, caplog):
    (tmp_path / progress.ARCHIVO_VIVO).mkdir()  # un directorio en su lugar
    publicador = progress.Publicador(tmp_path, pid=1, creado=1.0)
    with caplog.at_level(logging.WARNING, logger="downloader.vivo"):
        publicador.detener()
    assert any(r.name == "downloader.vivo" for r in caplog.records)


def test_observadores_reparten_cada_evento():
    class Registro:
        def __init__(self):
            self.llamadas = []

        def __getattr__(self, nombre):
            return lambda *args: self.llamadas.append((nombre, args))

    a, b = Registro(), Registro()
    observadores = progress.Observadores([a, b])
    descarga = _descarga()
    observadores.iniciar()
    observadores.inicio(descarga)
    observadores.progreso(descarga, _progreso(1))
    observadores.resumen()
    observadores.detener()
    esperado = ["iniciar", "inicio", "progreso", "resumen", "detener"]
    assert [n for n, _ in a.llamadas] == esperado == [n for n, _ in b.llamadas]
