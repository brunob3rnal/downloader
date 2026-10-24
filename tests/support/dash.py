"""Manifiestos MPD, ISM y F4M para los tests de DRM (research R5).

Todos los valores son ficticios (Principio VI): las cajas PSSH no contienen datos de ninguna
licencia real, solo el identificador público del sistema, y los KID son ceros.
"""

import base64
import subprocess
from pathlib import Path

from tests.support.hls import WIDEVINE, caja_pssh

KID = "00000000-0000-0000-0000-000000000000"
PSSH = base64.b64encode(caja_pssh(WIDEVINE)).decode()

# `ContentProtection` de un sistema de licencias, y los de `mp4protection`, que no nombran
# ninguno (con y sin `cenc:default_KID`).
WIDEVINE_CP = (
    f'<ContentProtection schemeIdUri="{WIDEVINE}"><cenc:pssh>{PSSH}</cenc:pssh>'
    "</ContentProtection>"
)
MP4PROTECTION = '<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/>'
MP4PROTECTION_KID = (
    '<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc" '
    f'cenc:default_KID="{KID}"/>'
)

VIDEO, AUDIO = "video", "audio"
_CODECS = {VIDEO: "avc1.64001e", AUDIO: "mp4a.40.2"}
_MIME = {VIDEO: "video/mp4", AUDIO: "audio/mp4"}


def separar(mp4: Path, directorio: Path) -> tuple[bytes, bytes]:
    """El video y el audio de `mp4`, cada uno en un MP4 válido, sin recodificar (ffmpeg)."""
    directorio.mkdir(parents=True)
    video, audio = directorio / "video.mp4", directorio / "audio.m4a"
    for salida, opcion in ((video, "-an"), (audio, "-vn")):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), opcion, "-c", "copy",
             "-movflags", "+faststart", str(salida)],
            check=True,
        )
    return video.read_bytes(), audio.read_bytes()


def representacion(id_: str, archivo: str, tipo: str = VIDEO, *, altura: int = 720,
                   dentro: str = "") -> str:
    """Una `Representation` de un solo archivo (`BaseURL`), con `dentro` como primeros hijos."""
    if tipo == VIDEO:
        atributos = (f'bandwidth="{altura * 2000}" width="{altura * 16 // 9}" '
                     f'height="{altura}"')
    else:
        atributos = 'bandwidth="128000" audioSamplingRate="44100"'
    return (f'<Representation id="{id_}" codecs="{_CODECS[tipo]}" {atributos}>{dentro}'
            f"<BaseURL>{archivo}</BaseURL></Representation>")


def conjunto(tipo: str, *representaciones: str, dentro: str = "") -> str:
    """Un `AdaptationSet` de `tipo` (`video` o `audio`) con `dentro` como primeros hijos."""
    return (f'<AdaptationSet contentType="{tipo}" mimeType="{_MIME[tipo]}">{dentro}'
            f'{"".join(representaciones)}</AdaptationSet>')


def periodo(*conjuntos: str, dentro: str = "") -> str:
    return f'<Period>{dentro}{"".join(conjuntos)}</Period>'


def mpd(*periodos: str, dentro: str = "") -> bytes:
    """Un MPD estático de 10 s con el espacio de nombres de DASH por defecto."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013" '
        'type="static" mediaPresentationDuration="PT10S" minBufferTime="PT2S" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">\n'
        f'{dentro}{"".join(periodos)}\n</MPD>\n'
    ).encode()


# --- ISM (Smooth Streaming) ---------------------------------------------------------

PLAYREADY_ID = "9a04f079-9840-4286-ab92-e65be0885f95"
PROTECTION_HEADER = (
    f'<ProtectionHeader SystemID="{PLAYREADY_ID}">'
    f'{base64.b64encode(b"cabecera ficticia").decode()}</ProtectionHeader>'
)
PROTECTION = f"<Protection>{PROTECTION_HEADER}</Protection>"
PROTECTION_SOLA = "<Protection/>"
FRAGMENTO_ISM = "QualityLevels(1500000)/Fragments(video=0)"


def ism(*elementos: str) -> bytes:
    """Un ISM con un video H264 de un fragmento (`FRAGMENTO_ISM`) y `elementos` al final."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<SmoothStreamingMedia MajorVersion="2" MinorVersion="0" Duration="100000000">'
        '<StreamIndex Type="video" Name="video" Chunks="1" QualityLevels="1" '
        'Url="QualityLevels({bitrate})/Fragments(video={start time})">'
        '<QualityLevel Index="0" Bitrate="1500000" FourCC="H264" MaxWidth="640" MaxHeight="360" '
        'CodecPrivateData="00000001674D401F"/><c t="0" d="100000000"/></StreamIndex>'
        f'{"".join(elementos)}</SmoothStreamingMedia>\n'
    ).encode()


# --- F4M (HDS) ----------------------------------------------------------------------

DRM_HEADER = f'<drmAdditionalHeader id="cabecera">{PSSH}</drmAdditionalHeader>'
DRM_HEADER_SET = (
    f'<drmAdditionalHeaderSet id="conjunto"><drmAdditionalHeader>{PSSH}</drmAdditionalHeader>'
    "</drmAdditionalHeaderSet>"
)
MEDIA_SIN_CIFRAR = '<media url="libre" bitrate="800" width="640" height="360" bootstrapInfoId="b"/>'
MEDIA_CON_HEADER_ID = (
    '<media url="cifrado" bitrate="1500" width="1280" height="720" bootstrapInfoId="b" '
    'drmAdditionalHeaderId="cabecera"/>'
)
MEDIA_CON_HEADER_SET_ID = (
    '<media url="cifrado" bitrate="1500" width="1280" height="720" bootstrapInfoId="b" '
    'drmAdditionalHeaderSetId="conjunto"/>'
)


def f4m(*elementos: str) -> bytes:
    """Un F4M 1.0 con un `bootstrapInfo` ficticio y `elementos` (`media`, cabeceras de DRM)."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns="http://ns.adobe.com/f4m/1.0"><id>v</id><duration>10</duration>'
        '<mimeType>video/mp4</mimeType>'
        f'<bootstrapInfo profile="named" id="b">{base64.b64encode(b"arranque").decode()}'
        f'</bootstrapInfo>{"".join(elementos)}</manifest>\n'
    ).encode()
