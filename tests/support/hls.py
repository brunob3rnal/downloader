"""Manifiestos HLS cifrados para los tests de DRM y de `AES-128` (research R5).

Todos los valores son ficticios (Principio VI): la clave es `CLAVE`, y las cajas PSSH no
contienen datos de ninguna licencia real, solo el identificador público del sistema.
"""

import base64
import shutil
import subprocess
from pathlib import Path

CLAVE = bytes(range(16))
IV = "0x00000000000000000000000000000000"

# Identificadores públicos de los sistemas de licencias (KEYFORMAT y SystemID de la caja PSSH).
WIDEVINE = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
PLAYREADY = "com.microsoft.playready"
FAIRPLAY = "com.apple.streamingkeydelivery"


def cifrar(mp4: Path, directorio: Path) -> Path:
    """Genera con ffmpeg la versión HLS de `mp4` cifrada con `AES-128` y la clave `CLAVE`.

    El manifiesto, `index.m3u8`, declara `#EXT-X-KEY:METHOD=AES-128,URI="clave.key",IV=…`;
    la clave no se copia al directorio del HLS.
    """
    directorio.mkdir(parents=True)
    clave = directorio.parent / f"{directorio.name}.key"
    clave.write_bytes(CLAVE)
    info = directorio.parent / f"{directorio.name}.keyinfo"
    # Formato de -hls_key_info_file: URI de la clave en el manifiesto, archivo local e IV.
    # Con un IV fijo, el manifiesto tiene una sola etiqueta para todos los fragmentos.
    info.write_text(f"clave.key\n{clave}\n{IV.removeprefix('0x')}\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-c", "copy",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", "-hls_key_info_file", str(info),
         "-hls_segment_filename", str(directorio / "fragmento%d.ts"),
         str(directorio / "index.m3u8")],
        check=True,
    )
    return directorio


def con_claves(origen: Path, destino: Path, *lineas: str) -> Path:
    """Copia el HLS de `origen` a `destino` con `lineas` en lugar de su `#EXT-X-KEY`."""
    shutil.copytree(origen, destino)
    manifiesto = destino / "index.m3u8"
    texto = manifiesto.read_text()
    (clave,) = [linea for linea in texto.splitlines() if linea.startswith("#EXT-X-KEY")]
    nuevo = texto.replace(clave + "\n", "".join(f"{linea}\n" for linea in lineas))
    manifiesto.write_text(nuevo, newline="\n")  # también en Windows, como lo escribe ffmpeg
    return destino


def maestro(*lineas: str, variante: str = "index.m3u8") -> bytes:
    """Manifiesto maestro con `lineas` (p. ej. `#EXT-X-SESSION-KEY`) y una sola variante."""
    return "".join(
        f"{linea}\n" for linea in
        ("#EXTM3U", *lineas, "#EXT-X-STREAM-INF:BANDWIDTH=1700000,RESOLUTION=640x360", variante)
    ).encode()


def caja_pssh(sistema: str) -> bytes:
    """Caja PSSH (versión 0) del sistema con ese `urn:uuid:…`, sin datos de licencia."""
    sistema_id = bytes.fromhex(sistema.removeprefix("urn:uuid:").replace("-", ""))
    cuerpo = b"\x00\x00\x00\x00" + sistema_id + (0).to_bytes(4, "big")
    return (8 + len(cuerpo)).to_bytes(4, "big") + b"pssh" + cuerpo


def uri_data(datos: bytes) -> str:
    """URI `data:` en base64, como las de las claves y los PSSH de los manifiestos."""
    return "data:text/plain;base64," + base64.b64encode(datos).decode()
