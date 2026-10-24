"""Directos y contenido con DRM: rechazados sin pedir los medios (escenario 10 de quickstart;
FR-015, Principio V, research R5)."""

import pytest

from downloader.models import Estado
from downloader.store import Store


@pytest.mark.parametrize(
    ("id_video", "causa"),
    [("directo", "transmisión en directo"), ("drm", "DRM")],
)
def test_rechazado_sin_peticion_de_medios(cli, app_home, destino, servidor, medios, id_video,
                                          causa):
    # Los medios existen y se servirían: si no se piden es porque se rechazó antes.
    medios_video = f"medios/{id_video}.mp4"
    servidor.registrar(medios_video, medios.datos_mp4, tipo_contenido="video/mp4")
    assert cli("agregar", servidor.url(f"watch/{id_video}"), "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.FAILED
    assert causa in descarga.ultimo_error
    assert f"#1 fallida  {descarga.ultimo_error}" in resultado.output
    assert servidor.peticiones[medios_video] == 0
    assert not any(p.is_file() for p in destino.iterdir())
