"""Calidad `audio` de extremo a extremo (FR-002, US1 escenario 4; research R6, R8)."""

import json
import subprocess

import pytest

from downloader import publish
from downloader.models import Estado
from downloader.store import Store

pytestmark = pytest.mark.ffmpeg


def tipos_de_flujo(archivo):
    salida = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json",
         str(archivo)],
        capture_output=True, text=True, check=True,
    ).stdout
    return [flujo["codec_type"] for flujo in json.loads(salida)["streams"]]


def test_solo_audio_publica_un_archivo_sin_video(cli, app_home, destino, servidor, medios,
                                                 esperas_cortas):
    assert tipos_de_flujo(medios.mp4) == ["video", "audio"]  # el origen tiene los dos
    servidor.registrar("v.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v", "v.mp4", titulo="Video v")
    assert cli("agregar", servidor.url("watch/v"), "--calidad", "audio",
               "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.COMPLETED
    archivo = destino / descarga.archivo_final
    assert archivo.parent == destino and archivo.is_file()
    assert [p for p in destino.iterdir() if p.is_file()] == [archivo]
    publish.verificar([], archivo)  # se lee entero, con paquetes en todos los flujos (R8)
    tipos = tipos_de_flujo(archivo)
    assert "audio" in tipos and "video" not in tipos
    assert not (destino / ".downloader-tmp" / "1").exists()
