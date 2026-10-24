"""`ejecutar` sin ffmpeg o sin ffprobe en el PATH (FR-021, contracts/cli.md: código 3)."""

import os

import pytest

from downloader.models import Calidad, Estado
from downloader.store import Store


def path_con(tmp_path, *programas):
    """Un directorio de PATH con solo estos programas (ficticios: no se llegan a ejecutar)."""
    directorio = tmp_path / "bin"
    directorio.mkdir()
    for programa in programas:
        if os.name == "nt":
            (directorio / f"{programa}.bat").write_text("@exit /b 0\n")
        else:
            ruta = directorio / programa
            ruta.write_text("#!/bin/sh\nexit 0\n")
            ruta.chmod(0o755)
    return str(directorio)


@pytest.mark.parametrize(
    "presentes, ausente",
    [((), "ffmpeg"), (("ffprobe",), "ffmpeg"), (("ffmpeg",), "ffprobe")],
)
def test_sin_ffmpeg_o_ffprobe_sale_con_3_sin_tocar_la_cola(
    cli, app_home, destino, servidor, tmp_path, monkeypatch, presentes, ausente
):
    servidor.registrar("v1.mp4", b"x" * 1000, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4")
    store = Store.abrir(app_home)
    try:
        en_cola = store.agregar(servidor.url("watch/v1"), Calidad.MEJOR, str(destino)).descarga
        otra = store.agregar(servidor.url("watch/v2"), Calidad.MEJOR, str(destino)).descarga
        # Como la dejaría un cierre abrupto: sin ffmpeg, ni siquiera se recupera.
        a_medias = store.transicion(otra.id, Estado.DOWNLOADING)
    finally:
        store.cerrar()

    monkeypatch.setenv("PATH", path_con(tmp_path, *presentes))
    resultado = cli("ejecutar")

    assert resultado.exit_code == 3, resultado.output
    assert ausente in resultado.output
    assert "PATH" in resultado.output
    # Cómo instalarlo en cada sistema.
    assert "winget install Gyan.FFmpeg" in resultado.output  # Windows
    assert "brew install ffmpeg" in resultado.output  # macOS
    assert "apt install ffmpeg" in resultado.output  # Linux (Debian, Ubuntu)
    assert "Traceback" not in resultado.output

    store = Store.abrir(app_home)
    try:
        assert store.obtener(en_cola.id) == en_cola
        assert store.obtener(a_medias.id) == a_medias
    finally:
        store.cerrar()
    assert not servidor.peticiones  # no se pidió nada
    assert list(destino.iterdir()) == []
