"""Política de cifrado: DRM, no soportado o aceptado (research R5; plan, decisión 30).

Funciones puras sobre los manifiestos, sin red ni yt-dlp. El criterio de rechazo es el sistema
de licencias (constitución v3.0.0, Principio V), no el transporte de la clave. En HLS no se usa
la expresión de `HlsFD`, que depende del orden de los atributos, ni
`yt_dlp.utils.parse_m3u8_attributes`, que es interno de yt-dlp. En MPD, ISM y F4M se buscan los
indicadores de protección con expat, el analizador de `xml.etree` con el que yt-dlp lee esos
manifiestos, sin depender de lo que yt-dlp marque como `has_drm`.
"""

import base64
import binascii
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field
from enum import Enum
from xml.parsers import expat

from downloader.models import Calidad

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


# --- MPD, ISM y F4M -------------------------------------------------------------------

DASH, ISM, F4M = "DASH", "ISM", "F4M"
# Elemento raíz de cada manifiesto, por su nombre local y sin distinguir mayúsculas (el
# extractor genérico acepta `mpd`).
RAICES = {"mpd": DASH, "smoothstreamingmedia": ISM, "manifest": F4M}
# Indicadores de protección, por su nombre local en cualquier espacio de nombres.
PROTECCION_DASH = "ContentProtection"
PROTECCION_ISM = {"Protection", "ProtectionHeader"}
CABECERAS_F4M = {"drmAdditionalHeader", "drmAdditionalHeaderSet"}
REFERENCIAS_F4M = {"drmAdditionalHeaderId", "drmAdditionalHeaderSetId"}  # atributos de `media`
# Prefijos de los códecs (atributo `codecs`) que llevan video o audio.
CODECS_VIDEO = ("avc", "hev", "hvc", "vp8", "vp08", "vp9", "vp09", "av01", "dvh", "dva", "mp4v",
                "vvc", "vvi")
CODECS_AUDIO = ("mp4a", "ac-3", "ec-3", "ac-4", "opus", "vorbis", "flac", "alac", "dts", "mp3",
                "mha", "mhm")

_BOM_UTF8 = b"\xef\xbb\xbf"
_BOMS_UTF16 = (b"\xff\xfe", b"\xfe\xff")
_ESPACIOS = re.compile(rb"[ \t\r\n]*")


@dataclass(frozen=True)
class Filtrado:
    """El resultado para un manifiesto MPD, ISM o F4M (R5).

    `cuerpo` es lo que se entrega a yt-dlp: el manifiesto sin los bytes de las representaciones
    protegidas (`omitidas`), o el original si no hay ninguna. Con un rechazo no se entrega nada.
    """

    decision: Decision
    cuerpo: bytes
    omitidas: int = 0


@dataclass(eq=False)
class _Nodo:
    """Un elemento del manifiesto y su posición en bytes: `[inicio, fin)` en el cuerpo leído."""

    nombre: str  # nombre local, sin el espacio de nombres
    atributos: dict[str, str]  # también por su nombre local
    padre: "_Nodo | None"
    inicio: int
    fin: int | None = None
    hijos: list["_Nodo"] = field(default_factory=list)

    def recorrer(self):
        yield self
        for hijo in self.hijos:
            yield from hijo.recorrer()


def posible_xml(prefijo: bytes, final: bool) -> bool | None:
    """Si un cuerpo que empieza por `prefijo` puede ser XML: `<` tras un BOM y espacios.

    `None` si aún no se sabe (solo espacios o parte del BOM) y faltan bytes por llegar.
    """
    if prefijo.startswith(_BOMS_UTF16):
        return True
    if not final and any(bom.startswith(prefijo) for bom in (_BOM_UTF8, *_BOMS_UTF16)):
        return None  # vacío o parte de un BOM
    resto = prefijo.removeprefix(_BOM_UTF8)
    resto = resto[_ESPACIOS.match(resto).end():]
    if resto:
        return resto.startswith(b"<")
    return False if final else None


def _local(nombre: str) -> str:
    return nombre.rpartition(" ")[2]  # expat con `namespace_separator=" "`: "uri local"


def _arbol(cuerpo: bytes) -> _Nodo | None:
    """El árbol de elementos de `cuerpo`, con la posición de cada uno; `None` si no es XML.

    El fin de un elemento es la posición del primer evento de expat que lo sigue: para
    `</X>` y para `<X/>` es el byte siguiente a la etiqueta, en cualquier codificación.
    """
    analizador = expat.ParserCreate(namespace_separator=" ")
    pila: list[_Nodo] = []
    raiz: list[_Nodo] = []
    sin_fin: list[_Nodo] = []

    def evento(*_):
        for nodo in sin_fin:
            nodo.fin = analizador.CurrentByteIndex
        sin_fin.clear()

    def abrir(nombre, atributos):
        evento()
        padre = pila[-1] if pila else None
        nodo = _Nodo(_local(nombre), {_local(k): v for k, v in atributos.items()}, padre,
                     analizador.CurrentByteIndex)
        (padre.hijos if padre else raiz).append(nodo)
        pila.append(nodo)

    def cerrar(_nombre):
        evento()
        sin_fin.append(pila.pop())

    analizador.StartElementHandler = abrir
    analizador.EndElementHandler = cerrar
    for manejador in ("CharacterDataHandler", "CommentHandler", "ProcessingInstructionHandler",
                      "StartCdataSectionHandler", "EndCdataSectionHandler",
                      "DefaultHandlerExpand"):
        setattr(analizador, manejador, evento)
    try:
        analizador.Parse(cuerpo, True)
    except expat.ExpatError:
        return None
    for nodo in sin_fin:
        nodo.fin = len(cuerpo)
    return raiz[0] if raiz else None


def filtrar_xml(cuerpo: bytes, calidad: Calidad | None) -> Filtrado | None:
    """La decisión de R5 para un manifiesto MPD, ISM o F4M; `None` si `cuerpo` no lo es.

    ISM y F4M con cualquier indicador se rechazan enteros. En MPD, las representaciones
    protegidas se excluyen si lo que queda cubre `calidad` (con `None`, la extracción sin
    descarga, basta cualquier video o audio sin proteger); si no, se rechaza.

    Un cuerpo que expat no puede leer tal cual (p. ej. UTF-8 inválido sin declaración) se
    analiza como lo decodifica yt-dlp, sustituyendo los bytes inválidos; ese es entonces el
    cuerpo que se entrega si hay que quitar algo.
    """
    raiz = _arbol(cuerpo)
    if raiz is None:
        reparado = cuerpo.decode("utf-8", "replace").encode()
        raiz = _arbol(reparado) if reparado != cuerpo else None
        cuerpo = reparado
    formato = RAICES.get(raiz.nombre.lower()) if raiz is not None else None
    if formato is None:
        return None
    rechazo = Filtrado(Decision(Veredicto.DRM, formato), cuerpo)
    nodos = list(raiz.recorrer())
    if formato == ISM:
        protegido = any(n.nombre in PROTECCION_ISM for n in nodos)
        return rechazo if protegido else Filtrado(ACEPTADO, cuerpo)
    if formato == F4M:
        protegido = any(
            n.nombre in CABECERAS_F4M
            or (n.nombre == "media" and REFERENCIAS_F4M & n.atributos.keys())
            for n in nodos
        )
        return rechazo if protegido else Filtrado(ACEPTADO, cuerpo)
    return _filtrar_mpd(raiz, nodos, cuerpo, calidad, rechazo)


def _alcance(proteccion: _Nodo) -> _Nodo:
    """La `Representation`, el `AdaptationSet`, el `Period` o el `MPD` que protege."""
    nodo = proteccion.padre
    while nodo.padre is not None and nodo.nombre not in ("Representation", "AdaptationSet",
                                                         "Period"):
        nodo = nodo.padre
    return nodo


def _lleva(representacion: _Nodo) -> tuple[bool, bool]:
    """(video, audio): por los códecs, como yt-dlp, o si no los hay, por el tipo de contenido."""
    atributos = {**representacion.padre.atributos, **representacion.atributos}
    codecs = [c.strip().lower() for c in atributos.get("codecs", "").split(",")]
    video = any(c.startswith(CODECS_VIDEO) for c in codecs)
    audio = any(c.startswith(CODECS_AUDIO) for c in codecs)
    if video or audio:
        return video, audio
    tipo = (atributos.get("contentType") or atributos.get("mimeType", "").partition("/")[0])
    return tipo.lower() == "video", tipo.lower() == "audio"


def _filtrar_mpd(raiz, nodos, cuerpo, calidad, rechazo) -> Filtrado:
    alcances = {_alcance(n) for n in nodos if n.nombre == PROTECCION_DASH}
    if not alcances:
        return Filtrado(ACEPTADO, cuerpo)
    if any(a is raiz or a.nombre == "Period" for a in alcances):
        return rechazo  # la protección alcanza a todo el contenido
    periodos = [n for n in raiz.hijos if n.nombre == "Period"]
    if len(periodos) > 1:
        # yt-dlp une los periodos por formato: excluir una representación en un periodo y no
        # en otro daría un archivo incompleto (Principio III).
        return rechazo
    representaciones = [n for n in nodos
                        if n.nombre == "Representation" and n.padre.nombre == "AdaptationSet"]
    protegidas = [r for r in representaciones if r in alcances or r.padre in alcances]
    libres = [_lleva(r) for r in representaciones if r not in protegidas]
    hay_video = any(v for v, _ in libres)
    hay_audio = any(a for _, a in libres)
    if calidad is Calidad.AUDIO:
        cubre = hay_audio
    elif calidad is None:
        cubre = hay_video or hay_audio
    else:  # con video, también el audio si el contenido lo tiene, protegido o no
        cubre = hay_video and (hay_audio or not any(a for _, a in map(_lleva, representaciones)))
    if not cubre:
        return rechazo
    partes, desde = [], 0
    for representacion in protegidas:
        partes.append(cuerpo[desde:representacion.inicio])
        desde = representacion.fin
    partes.append(cuerpo[desde:])
    return Filtrado(ACEPTADO, b"".join(partes), omitidas=len(protegidas))
