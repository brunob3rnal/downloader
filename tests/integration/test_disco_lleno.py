"""Disco lleno durante una descarga (edge case "Disco lleno" de la spec; FR-021, R4, Principio IV).

La escritura del `.part` falla con `OSError(ENOSPC)` a partir de un número de bytes: se
sustituye la apertura de archivos del descargador de yt-dlp por una que envuelve el `.part`.
"""

import errno
import gc
from types import SimpleNamespace

import pytest
from yt_dlp.downloader.common import FileDownloader

from downloader.models import Estado
from downloader.store import Store


class _Lleno:
    """Archivo que acepta escrituras hasta `estado.limite` bytes y luego falla con ENOSPC."""

    def __init__(self, flujo, estado):
        self._flujo = flujo
        self._estado = estado

    def write(self, datos):
        if self._estado.activo and self._estado.escritos + len(datos) > self._estado.limite:
            raise OSError(errno.ENOSPC, "No space left on device")
        self._estado.escritos += len(datos)
        return self._flujo.write(datos)

    def __getattr__(self, nombre):
        return getattr(self._flujo, nombre)


@pytest.fixture
def disco_lleno(monkeypatch):
    estado = SimpleNamespace(activo=True, limite=0, escritos=0)
    abrir = FileDownloader.sanitize_open

    def sanitize_open(self, nombre, modo):
        flujo, nombre = abrir(self, nombre, modo)
        if nombre.endswith(".part"):
            estado.escritos = 0
            flujo = _Lleno(flujo, estado)
        return flujo, nombre

    monkeypatch.setattr(FileDownloader, "sanitize_open", sanitize_open)
    return estado


def fila(app_home):
    store = Store.abrir(app_home)
    try:
        return store.obtener(1)
    finally:
        store.cerrar()


def test_disco_lleno_detiene_ejecutar_y_conserva_el_parcial(
    cli, app_home, destino, servidor, medios, disco_lleno
):
    datos = medios.datos_mp4
    servidor.registrar("v.mp4", datos, tipo_contenido="video/mp4")
    servidor.registrar_video("v", "v.mp4", titulo="Video v")
    disco_lleno.limite = int(len(datos) * 0.4)
    assert cli("agregar", servidor.url("watch/v"), "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 3, resultado.output  # error fatal del entorno (FR-021)
    error = resultado.stderr
    assert "disco lleno" in error.lower()
    assert str(destino) in error  # en qué recurso
    assert "Libere espacio" in error and "downloader ejecutar" in error  # qué hacer
    assert "progreso se ha conservado" in error
    descarga = fila(app_home)
    assert descarga.estado is Estado.QUEUED  # vuelve a la cola, no falla
    assert "disco lleno" in descarga.ultimo_error.lower()
    gc.collect()  # el `.part` que deja abierto el error de yt-dlp (R24)
    (parcial,) = (destino / ".downloader-tmp" / str(descarga.id)).glob("*.part")
    contenido = parcial.read_bytes()
    assert 0 < len(contenido) <= disco_lleno.limite
    assert datos.startswith(contenido)  # intacto: lo escrito antes del fallo
    assert [p.name for p in destino.iterdir()] == [".downloader-tmp"]  # nada publicado
    servidos = servidor.bytes_servidos["v.mp4"]

    disco_lleno.activo = False  # se liberó espacio
    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    assert fila(app_home).estado is Estado.COMPLETED
    (publicado,) = [p for p in destino.iterdir() if p.is_file()]
    assert publicado.read_bytes() == datos
    # Continuó el `.part`: solo se pidió lo que faltaba.
    assert servidor.bytes_servidos["v.mp4"] - servidos == len(datos) - len(contenido)
