"""HLS cifrado con AES-128: la clave se pide durante la descarga (research R4, R19).

Cerrar las respuestas de la extracción al empezar la descarga no debe afectar al manifiesto,
la clave ni los fragmentos, que la descarga pide con sus propias peticiones.
"""

import subprocess

import pytest

from downloader import ytdl
from downloader.models import Calidad, Descarga, Estado
from tests.support import fake_extractor

# HLS con el descargador nativo y AES-128 con pycryptodomex, no con ffmpeg (R1).
pytestmark = pytest.mark.ytdlp_internals

CLAVE = bytes(range(16))  # valor ficticio


def test_hls_aes_128_se_descarga_y_descifra(tmp_path, destino, servidor, medios, monkeypatch,
                                             tiempos_rapidos):
    hls = tmp_path / "aes"
    hls.mkdir()
    (tmp_path / "clave.key").write_bytes(CLAVE)
    # Formato de -hls_key_info_file: URI de la clave en el manifiesto y archivo local.
    (tmp_path / "clave.info").write_text(f"clave.key\n{tmp_path / 'clave.key'}\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(medios.mp4), "-c", "copy",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
         "-hls_key_info_file", str(tmp_path / "clave.info"),
         "-hls_segment_filename", str(hls / "fragmento%d.ts"), str(hls / "index.m3u8")],
        check=True,
    )
    assert "METHOD=AES-128" in (hls / "index.m3u8").read_text()
    manifiesto = servidor.registrar_hls("aes", hls)
    servidor.registrar("aes/clave.key", CLAVE)
    servidor.registrar_video("cifrado", manifiesto)
    fake_extractor.instalar(monkeypatch)

    descarga = Descarga(id=1, url=servidor.url("watch/cifrado"), calidad=Calidad.MEJOR,
                        carpeta_destino=str(destino), estado=Estado.DOWNLOADING)
    info = ytdl.descargar(descarga, ytdl.directorio_trabajo(descarga), None, lambda _: None,
                          tiempos_rapidos.socket_timeout)

    assert servidor.peticiones["aes/clave.key"] >= 1
    archivo = ytdl.archivo_descargado(info)
    # Descifrado correcto: ffmpeg lo decodifica entero sin errores y dura lo que el original.
    decodificar = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(archivo), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    assert decodificar.returncode == 0 and not decodificar.stderr
    duracion = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(archivo)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert abs(float(duracion) - 10) < 0.5
