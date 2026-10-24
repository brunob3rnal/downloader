"""Política de cifrado de HLS: DRM, no soportado o aceptado (research R5; plan, decisión 30).

Funciones puras sobre el texto de los manifiestos, sin red ni yt-dlp. El criterio de rechazo es
el sistema de licencias (constitución v3.0.0, Principio V), no el transporte de la clave. No se
usa la expresión de `HlsFD`, que depende del orden de los atributos, ni
`yt_dlp.utils.parse_m3u8_attributes`, que es interno de yt-dlp.
"""

import base64
import binascii
import urllib.parse
import uuid
from dataclasses import dataclass
from enum import Enum

ETIQUETAS = ("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")
ADOBE_ACCESS = "#EXT-X-FAXS-CM:"
# RFC 8216 §4.3.2.4: un `KEYFORMAT` ausente equivale a `identity`, la clave sin licencias.
SIN_LICENCIAS = "identity"
METODO_NATIVO = "AES-128"  # el único que yt-dlp descifra sin ffmpeg

# `KEYFORMAT` y SystemID de la caja PSSH de los sistemas conocidos, para nombrarlos.
SISTEMAS = {
    "com.apple.streamingkeydelivery": "FairPlay",
    "com.microsoft.playready": "PlayReady",
    "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed": "Widevine",
    "urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95": "PlayReady",
    "urn:uuid:94ce86fb-07ff-4f43-adb8-93d2fa968ca2": "FairPlay",
}

DRM = "contenido con DRM ({sistema}): no se puede descargar"
NO_SOPORTADO = (
    "no soportado: el cifrado SAMPLE-AES solo se puede descargar a través de ffmpeg, que no "
    "permite reanudar de forma fiable"
)


class Veredicto(Enum):
    ACEPTADO = "aceptado"
    NO_SOPORTADO = "no soportado"  # FR-008: solo ffmpeg sabría descargarlo, sin reanudar
    DRM = "drm"  # Principio V: depende de un sistema de licencias


@dataclass(frozen=True)
class Decision:
    veredicto: Veredicto
    sistema: str | None = None  # DRM: el sistema de licencias
    metodo: str | None = None  # no soportado: el `METHOD`, en mayúsculas

    @property
    def mensaje(self) -> str | None:
        if self.veredicto is Veredicto.DRM:
            return DRM.format(sistema=self.sistema)
        if self.veredicto is Veredicto.NO_SOPORTADO:
            if self.metodo == "SAMPLE-AES":
                return NO_SOPORTADO
            return f"{NO_SOPORTADO} (METHOD={self.metodo or 'ausente'})"
        return None


ACEPTADO = Decision(Veredicto.ACEPTADO)
_GRAVEDAD = {Veredicto.ACEPTADO: 0, Veredicto.NO_SOPORTADO: 1, Veredicto.DRM: 2}


def atributos(lista: str) -> dict[str, str]:
    """Los atributos de una etiqueta (RFC 8216 §4.2), con los nombres en mayúsculas.

    `NOMBRE=valor` separados por comas fuera de comillas, en cualquier orden; los valores entre
    comillas pueden contener comas y se devuelven sin ellas. Nunca lanza: lo que no tiene la
    forma `NOMBRE=valor` se ignora.
    """
    partes, actual, entre_comillas = [], [], False
    for caracter in lista:
        if caracter == '"':
            entre_comillas = not entre_comillas
        if caracter == "," and not entre_comillas:
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(caracter)
    partes.append("".join(actual))
    resultado = {}
    for parte in partes:
        nombre, igual, valor = parte.partition("=")
        nombre = nombre.strip().upper()
        if igual and nombre:
            resultado[nombre] = valor.strip().strip('"')
    return resultado


def _sistema_pssh(uri: str) -> str | None:
    """El sistema de la caja PSSH de una URI `data:`; `None` si no contiene una.

    Un contenido que no se puede decodificar no es una caja PSSH: no lanza.
    """
    if not uri.lower().startswith("data:"):
        return None
    cabecera, coma, datos = uri[len("data:"):].partition(",")
    if not coma:
        return None
    try:
        if cabecera.lower().endswith(";base64"):
            contenido = base64.b64decode(datos)
        else:
            contenido = urllib.parse.unquote_to_bytes(datos)
    except (binascii.Error, ValueError):
        return None
    # Caja ISO BMFF: tamaño (4 bytes), tipo `pssh`, versión y flags (4) y SystemID (16).
    if contenido[4:8] != b"pssh":
        return None
    sistema_id = contenido[12:28]
    if len(sistema_id) != 16:
        return "PSSH"
    return SISTEMAS.get(f"urn:uuid:{uuid.UUID(bytes=sistema_id)}", "PSSH")


def decidir(atributos_clave: dict[str, str]) -> Decision:
    """La decisión de R5 para los atributos de una `#EXT-X-KEY` o `#EXT-X-SESSION-KEY`.

    1. DRM si la URI es `skd://`, si hay un `KEYFORMAT` distinto de `identity` o si la URI es
       `data:` con una caja PSSH.
    2. No soportado si el método no es `AES-128` (`SAMPLE-AES`, `SAMPLE-AES-CTR`, otro o ninguno).
    3. Aceptado en otro caso; también `METHOD=NONE`.
    """
    metodo = atributos_clave.get("METHOD", "").upper()
    if metodo == "NONE":
        return ACEPTADO
    uri = atributos_clave.get("URI", "")
    formato = atributos_clave.get("KEYFORMAT")
    if uri.lower().startswith("skd://"):
        return Decision(Veredicto.DRM, "FairPlay")
    if formato is not None and formato.lower() != SIN_LICENCIAS:
        return Decision(Veredicto.DRM, SISTEMAS.get(formato.lower(), formato or "KEYFORMAT vacío"))
    if (sistema := _sistema_pssh(uri)) is not None:
        return Decision(Veredicto.DRM, sistema)
    if metodo != METODO_NATIVO:
        return Decision(Veredicto.NO_SOPORTADO, metodo=metodo or None)
    return ACEPTADO


def _la_mas_grave(decisiones) -> Decision:
    return max(decisiones, key=lambda d: _GRAVEDAD[d.veredicto], default=ACEPTADO)


def analizar(manifiesto: str) -> Decision:
    """La decisión para un manifiesto HLS: la más grave de sus etiquetas de clave.

    Cuentan todas las `#EXT-X-KEY` (una lista puede cambiar de clave a mitad) y
    `#EXT-X-SESSION-KEY` (las del maestro), y `#EXT-X-FAXS-CM` (Adobe Access).
    """
    decisiones = []
    for linea in manifiesto.splitlines():
        linea = linea.strip()
        if linea.startswith(ADOBE_ACCESS):
            decisiones.append(Decision(Veredicto.DRM, "Adobe Access"))
        for etiqueta in ETIQUETAS:
            if linea.startswith(etiqueta):
                decisiones.append(decidir(atributos(linea[len(etiqueta):])))
    return _la_mas_grave(decisiones)


def analizar_hls_aes(hls_aes: dict) -> Decision:
    """La decisión para la clave que entrega el extractor (`info['hls_aes']`).

    Sustituye a la URI del manifiesto, que sigue analizándose aparte: aquí solo cuenta su
    propia URI, si la trae.
    """
    uri = hls_aes.get("uri")
    return decidir({"METHOD": METODO_NATIVO, "URI": uri}) if uri else ACEPTADO
