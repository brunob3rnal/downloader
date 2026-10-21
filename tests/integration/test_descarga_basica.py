"""Descarga básica de dos URLs (escenario 1 de quickstart; FR-001, FR-002, FR-005, FR-006,
SC-006, SC-007)."""

import re
import time

from downloader import progress
from downloader.models import Estado
from downloader.store import Store

LINEA_PROGRESO = re.compile(
    r"^#(?P<id>\d+) descargando  \d{1,3}\.\d%  \d+\.\d MiB/s  ETA \d\d:\d\d:\d\d  ", re.M
)


def test_dos_urls_con_progreso(cli, app_home, destino, servidor, medios, monkeypatch):
    datos = medios.datos_mp4
    # La primera se sirve lenta (≈ 2,5 s) para ver varias líneas de progreso.
    servidor.registrar("v1.mp4", datos, tipo_contenido="video/mp4", velocidad=800_000)
    servidor.registrar("v2.mp4", datos, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4", titulo="Video uno")
    servidor.registrar_video("v2", "v2.mp4", titulo="Video dos")

    lineas = []
    escribir = progress.Presentador._linea

    def con_marca_de_tiempo(self, texto):
        lineas.append((time.monotonic(), texto))
        escribir(self, texto)

    monkeypatch.setattr(progress.Presentador, "_linea", con_marca_de_tiempo)

    agregar = cli("agregar", servidor.url("watch/v1"), servidor.url("watch/v2"),
                  "--calidad", "720p", "--carpeta", destino)
    assert agregar.exit_code == 0, agregar.output
    resultado = cli("ejecutar")
    assert resultado.exit_code == 0, resultado.output

    publicados = sorted(p.name for p in destino.iterdir() if p.is_file())
    assert publicados == ["Video dos [v2].mp4", "Video uno [v1].mp4"]
    for nombre in publicados:
        assert (destino / nombre).read_bytes() == datos

    store = Store.abrir(app_home)
    try:
        for id_, nombre in ((1, "Video uno [v1].mp4"), (2, "Video dos [v2].mp4")):
            descarga = store.obtener(id_)
            assert descarga.estado is Estado.COMPLETED
            assert descarga.archivo_final == str(destino / nombre)
            assert not (destino / ".downloader-tmp" / str(id_)).exists()
    finally:
        store.cerrar()

    # Progreso, velocidad y ETA al menos una vez por segundo mientras dura la descarga.
    progreso = [(t, texto) for t, texto in lineas if LINEA_PROGRESO.match(texto)]
    assert LINEA_PROGRESO.search(resultado.output)
    marcas = [t for t, texto in progreso if texto.startswith("#1 ")]
    assert len(marcas) >= 2
    assert max(b - a for a, b in zip(marcas, marcas[1:])) <= 1.25
    assert "#1 completada  " in resultado.output
    assert "#2 completada  " in resultado.output
