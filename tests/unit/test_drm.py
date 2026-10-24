"""Política de cifrado: DRM, no soportado o aceptado (research R5; plan, decisión 30).

`downloader.drm` es puro: analiza los manifiestos HLS, MPD, ISM y F4M, sin red ni yt-dlp.
"""

import subprocess
import sys
from xml.etree import ElementTree

import pytest

from downloader import drm
from downloader.drm import Veredicto
from downloader.models import Calidad
from tests.support import dash
from tests.support.hls import CLAVE, FAIRPLAY, IV, PLAYREADY, WIDEVINE, caja_pssh, uri_data

NO_SOPORTADO = (
    "no soportado: el cifrado SAMPLE-AES solo se puede descargar a través de ffmpeg, que no "
    "permite reanudar de forma fiable"
)
SISTEMA_DESCONOCIDO = "urn:uuid:00112233-4455-6677-8899-aabbccddeeff"


def test_no_usa_yt_dlp():
    resultado = subprocess.run(
        [sys.executable, "-c", "import sys, downloader.drm; print('yt_dlp' in sys.modules)"],
        capture_output=True, text=True, check=True,
    )
    assert resultado.stdout.strip() == "False"


# --- Analizador de atributos (RFC 8216 §4.2) ---------------------------------------


def test_valores_entre_comillas_con_comas():
    assert drm.atributos('METHOD=AES-128,URI="https://k.ejemplo/c?a=1,b=2",IV=0x0A') == {
        "METHOD": "AES-128", "URI": "https://k.ejemplo/c?a=1,b=2", "IV": "0x0A",
    }


def test_atributos_en_cualquier_orden():
    esperado = {"METHOD": "AES-128", "URI": "clave.key", "KEYFORMAT": "identity"}
    assert drm.atributos('URI="clave.key",KEYFORMAT="identity",METHOD=AES-128') == esperado
    assert drm.atributos('KEYFORMAT="identity",METHOD=AES-128,URI="clave.key"') == esperado


def test_comillas_vacias_espacios_y_atributos_sin_valor():
    assert drm.atributos(' METHOD=AES-128 , KEYFORMAT="",SUELTO') == {
        "METHOD": "AES-128", "KEYFORMAT": "",
    }


@pytest.mark.parametrize("lista", ['URI="sin cerrar,METHOD=AES-128', '=', ',,', '"', ''])
def test_listas_mal_formadas_no_lanzan(lista):
    assert isinstance(drm.atributos(lista), dict)


# --- Decisión de R5 por etiqueta ---------------------------------------------------


def clave(*atributos: str, etiqueta="#EXT-X-KEY") -> str:
    return f"{etiqueta}:{','.join(atributos)}"


@pytest.mark.parametrize(
    ("linea", "veredicto", "sistema"),
    [
        # Aceptados: AES-128 con KEYFORMAT ausente o `identity`, clave por HTTP(S) o `data:`.
        (clave("METHOD=NONE"), Veredicto.ACEPTADO, None),
        (clave("METHOD=AES-128", 'URI="https://k.ejemplo/clave"', f"IV={IV}"),
         Veredicto.ACEPTADO, None),
        (clave("METHOD=AES-128", 'URI="clave.key"', 'KEYFORMAT="identity"'),
         Veredicto.ACEPTADO, None),
        (clave("METHOD=AES-128", f'URI="{uri_data(CLAVE)}"'), Veredicto.ACEPTADO, None),
        (clave("METHOD=AES-128", 'URI="data:,%00%01%02%03%04%05%06%07%08%09%0A%0B%0C%0D%0E%0F"'),
         Veredicto.ACEPTADO, None),
        # DRM: `skd://`, cualquier KEYFORMAT distinto de `identity` o un PSSH.
        (clave("METHOD=AES-128", 'URI="skd://clave-ficticia"'), Veredicto.DRM, "FairPlay"),
        (clave("METHOD=SAMPLE-AES", 'URI="skd://clave-ficticia"', f'KEYFORMAT="{FAIRPLAY}"',
               'KEYFORMATVERSIONS="1"'), Veredicto.DRM, "FairPlay"),
        (clave("METHOD=SAMPLE-AES", 'URI="clave.key"', f'KEYFORMAT="{PLAYREADY}"'),
         Veredicto.DRM, "PlayReady"),
        (clave("METHOD=AES-128", 'URI="clave.key"', f'KEYFORMAT="{PLAYREADY}"'),
         Veredicto.DRM, "PlayReady"),
        (clave("METHOD=AES-128", 'URI="clave.key"', f'KEYFORMAT="{WIDEVINE}"'),
         Veredicto.DRM, "Widevine"),
        (clave("METHOD=SAMPLE-AES", f'URI="{uri_data(caja_pssh(WIDEVINE))}"',
               f'KEYFORMAT="{WIDEVINE}"'), Veredicto.DRM, "Widevine"),
        (clave("METHOD=SAMPLE-AES-CTR", 'URI="clave.key"', f'KEYFORMAT="{WIDEVINE}"'),
         Veredicto.DRM, "Widevine"),
        (clave("METHOD=AES-128", 'URI="clave.key"', 'KEYFORMAT="com.ejemplo.licencias"'),
         Veredicto.DRM, "com.ejemplo.licencias"),
        (clave("METHOD=AES-128", f'URI="{uri_data(caja_pssh(WIDEVINE))}"'),
         Veredicto.DRM, "Widevine"),
        (clave("METHOD=AES-128", f'URI="{uri_data(caja_pssh(SISTEMA_DESCONOCIDO))}"'),
         Veredicto.DRM, "PSSH"),
        # No soportados: otro método con clave `identity`.
        (clave("METHOD=SAMPLE-AES", 'URI="https://k.ejemplo/clave"', 'KEYFORMAT="identity"'),
         Veredicto.NO_SOPORTADO, None),
        (clave("METHOD=SAMPLE-AES", 'URI="clave.key"'), Veredicto.NO_SOPORTADO, None),
        (clave("METHOD=SAMPLE-AES-CTR", 'URI="clave.key"', 'KEYFORMAT="identity"'),
         Veredicto.NO_SOPORTADO, None),
        (clave("METHOD=AES-256", 'URI="clave.key"', 'KEYFORMAT="identity"'),
         Veredicto.NO_SOPORTADO, None),
        (clave('URI="clave.key"'), Veredicto.NO_SOPORTADO, None),  # sin METHOD
        # Con `URI` antes que `METHOD`, el orden que la expresión de yt-dlp no reconoce.
        (clave('URI="clave.key"', f'KEYFORMAT="{WIDEVINE}"', "METHOD=AES-128"),
         Veredicto.DRM, "Widevine"),
        (clave('URI="clave.key"', "METHOD=AES-128", 'KEYFORMAT="identity"'),
         Veredicto.ACEPTADO, None),
        (clave('URI="clave.key"', "METHOD=SAMPLE-AES", 'KEYFORMAT="identity"'),
         Veredicto.NO_SOPORTADO, None),
        # `#EXT-X-SESSION-KEY` del manifiesto maestro, con las mismas reglas.
        (clave("METHOD=AES-128", f'URI="{uri_data(caja_pssh(WIDEVINE))}"',
               f'KEYFORMAT="{WIDEVINE}"', etiqueta="#EXT-X-SESSION-KEY"),
         Veredicto.DRM, "Widevine"),
        (clave("METHOD=SAMPLE-AES", 'URI="clave.key"', etiqueta="#EXT-X-SESSION-KEY"),
         Veredicto.NO_SOPORTADO, None),
        (clave("METHOD=AES-128", 'URI="clave.key"', etiqueta="#EXT-X-SESSION-KEY"),
         Veredicto.ACEPTADO, None),
    ],
)
def test_decision_de_r5(linea, veredicto, sistema):
    decision = drm.analizar(f"#EXTM3U\n{linea}\n#EXTINF:2.0,\nfragmento0.ts\n")
    assert decision.veredicto is veredicto
    if veredicto is Veredicto.DRM:
        assert sistema in decision.mensaje
        assert decision.mensaje == f"contenido con DRM ({decision.sistema}): no se puede descargar"
    elif veredicto is Veredicto.NO_SOPORTADO:
        assert decision.mensaje.startswith(NO_SOPORTADO)
        assert "DRM" not in decision.mensaje
    else:
        assert decision.mensaje is None


@pytest.mark.parametrize(
    "linea",
    [
        clave("method=aes-128", 'uri="clave.key"', 'keyformat="IDENTITY"'),
        clave("METHOD=aes-128", 'URI="clave.key"', 'KEYFORMAT="Identity"'),
    ],
)
def test_method_y_keyformat_sin_distinguir_mayusculas(linea):
    assert drm.analizar(f"#EXTM3U\n{linea}\n").veredicto is Veredicto.ACEPTADO


def test_mayusculas_en_drm_y_no_soportado():
    widevine = clave("METHOD=AES-128", f'KEYFORMAT="{WIDEVINE.upper()}"')
    assert drm.analizar(f"#EXTM3U\n{widevine}\n").sistema == "Widevine"
    muestra = clave("METHOD=sample-aes", 'KEYFORMAT="identity"')
    assert drm.analizar(f"#EXTM3U\n{muestra}\n").veredicto is Veredicto.NO_SOPORTADO
    fairplay = clave("METHOD=SAMPLE-AES", 'URI="SKD://clave"')
    assert drm.analizar(f"#EXTM3U\n{fairplay}\n").sistema == "FairPlay"


def test_no_soportado_nombra_el_metodo_si_no_es_sample_aes():
    muestra = drm.analizar(f'#EXTM3U\n{clave("METHOD=SAMPLE-AES")}\n')
    assert muestra.mensaje == NO_SOPORTADO
    linea = clave("METHOD=SAMPLE-AES-CTR", 'KEYFORMAT="identity"')
    ctr = drm.analizar(f"#EXTM3U\n{linea}\n")
    assert ctr.mensaje == f"{NO_SOPORTADO} (METHOD=SAMPLE-AES-CTR)"


@pytest.mark.parametrize(
    "uri",
    [
        "data:text/plain;base64,@@no-es-base64@@",
        "data:text/plain;base64,AAECAwQFBgcICQoLDA0ODw",  # sin el relleno `==`
        "data:text/plain;base64,",
        "data:;base64",  # sin coma
        "data:",
        "data:,%ZZ%",
    ],
)
def test_uri_data_ilegible_no_lanza_y_no_es_un_pssh(uri):
    """Si su contenido no se puede decodificar, no puede ser una caja PSSH: yt-dlp no podrá
    leer la clave y la descarga fallará por su cuenta, pero el analizador no lanza."""
    linea = clave("METHOD=AES-128", f'URI="{uri}"')
    decision = drm.analizar(f"#EXTM3U\n{linea}\n")
    assert decision.veredicto is Veredicto.ACEPTADO


# --- Manifiesto completo -----------------------------------------------------------


def test_sin_cifrar_se_acepta():
    assert drm.analizar("#EXTM3U\n#EXTINF:2.0,\nfragmento0.ts\n#EXT-X-ENDLIST\n").veredicto \
        is Veredicto.ACEPTADO


def test_adobe_access():
    decision = drm.analizar("#EXTM3U\n#EXT-X-FAXS-CM:MIIBkzCCAT...\n#EXTINF:2.0,\nf.ts\n")
    assert decision.veredicto is Veredicto.DRM
    assert decision.sistema == "Adobe Access"


def test_el_drm_prevalece_sobre_un_metodo_no_soportado():
    manifiesto = "\n".join([
        "#EXTM3U",
        clave("METHOD=SAMPLE-AES", 'URI="clave.key"', 'KEYFORMAT="identity"'),
        clave("METHOD=SAMPLE-AES", 'URI="clave.key"', f'KEYFORMAT="{WIDEVINE}"'),
        "#EXTINF:2.0,", "fragmento0.ts",
    ])
    assert drm.analizar(manifiesto).veredicto is Veredicto.DRM


def test_cualquier_etiqueta_del_manifiesto_cuenta():
    """Una clave que cambia a mitad de la lista (cada fragmento puede tener la suya)."""
    manifiesto = "\n".join([
        "#EXTM3U",
        clave("METHOD=AES-128", 'URI="clave.key"'), "#EXTINF:2.0,", "fragmento0.ts",
        clave("METHOD=NONE"), "#EXTINF:2.0,", "fragmento1.ts",
        clave("METHOD=SAMPLE-AES", 'URI="otra.key"'), "#EXTINF:2.0,", "fragmento2.ts",
    ])
    assert drm.analizar(manifiesto).veredicto is Veredicto.NO_SOPORTADO


def test_lineas_con_espacios_y_fin_de_linea_de_windows():
    linea = clave("METHOD=AES-128", f'KEYFORMAT="{WIDEVINE}"')
    manifiesto = f"#EXTM3U\r\n  {linea}  \r\n"
    assert drm.analizar(manifiesto).veredicto is Veredicto.DRM


def test_otras_etiquetas_parecidas_no_cuentan():
    manifiesto = "#EXTM3U\n#EXT-X-KEYFRAMES:METHOD=SAMPLE-AES\n#EXT-X-MAP:URI=\"skd://x\"\n"
    assert drm.analizar(manifiesto).veredicto is Veredicto.ACEPTADO


# --- La clave que entrega el extractor (`info['hls_aes']`) ----------------------------


@pytest.mark.parametrize(
    ("hls_aes", "veredicto"),
    [
        ({"key": "0x" + CLAVE.hex()}, Veredicto.ACEPTADO),
        ({"uri": "https://k.ejemplo/clave", "iv": IV}, Veredicto.ACEPTADO),
        ({"uri": uri_data(CLAVE)}, Veredicto.ACEPTADO),
        ({"uri": uri_data(caja_pssh(WIDEVINE))}, Veredicto.DRM),
        ({"uri": "skd://clave-ficticia"}, Veredicto.DRM),
        ({"uri": "data:text/plain;base64,@@"}, Veredicto.ACEPTADO),
    ],
)
def test_clave_del_extractor(hls_aes, veredicto):
    assert drm.analizar_hls_aes(hls_aes).veredicto is veredicto


# --- MPD, ISM y F4M: indicadores, alcance y decisión (FR-015, hallazgo G1; R5) --------

CALIDADES_DE_VIDEO = [Calidad.MEJOR, Calidad.P1080, Calidad.P720]
CP = dash.WIDEVINE_CP


def video(id_, altura=720, dentro=""):
    return dash.representacion(id_, f"{id_}.mp4", dash.VIDEO, altura=altura, dentro=dentro)


def audio(id_="audio", dentro=""):
    return dash.representacion(id_, f"{id_}.m4a", dash.AUDIO, dentro=dentro)


def conjunto_video(*representaciones, dentro=""):
    return dash.conjunto(dash.VIDEO, *representaciones, dentro=dentro)


def conjunto_audio(*representaciones, dentro=""):
    return dash.conjunto(dash.AUDIO, *(representaciones or [audio()]), dentro=dentro)


def quedan(cuerpo: bytes) -> list[str]:
    """Los `id` de las `Representation` que quedan en el manifiesto."""
    return [e.get("id") for e in ElementTree.fromstring(cuerpo).iter()
            if e.tag.rpartition("}")[2] == "Representation"]


def rechazado(filtrado, sistema="DASH"):
    return (filtrado is not None and filtrado.decision.veredicto is Veredicto.DRM
            and filtrado.decision.mensaje
            == f"contenido con DRM ({sistema}): no se puede descargar")


def aceptado_sin_cambios(filtrado, cuerpo):
    return (filtrado is not None and filtrado.decision.veredicto is Veredicto.ACEPTADO
            and filtrado.cuerpo == cuerpo and filtrado.omitidas == 0)


@pytest.mark.parametrize(
    "cuerpo",
    [
        b"<!DOCTYPE html>\n<html><head><title>v</title></head><body></body></html>",
        b'<?xml version="1.0"?><rss version="2.0"><channel/></rss>',
        b'<smil xmlns="http://www.w3.org/2001/SMIL20/Language"><body/></smil>',
        b"<MPD><Period>sin cerrar",  # XML mal formado: yt-dlp tampoco lo leería
        b"\x00\x00\x00\x18ftypmp42",
        b"",
    ],
    ids=["html", "rss", "smil", "mal-formado", "binario", "vacio"],
)
def test_lo_que_no_es_un_manifiesto_no_se_analiza(cuerpo):
    assert drm.filtrar_xml(cuerpo, Calidad.MEJOR) is None


@pytest.mark.parametrize("calidad", [*Calidad, None])
def test_mpd_sin_proteccion_se_entrega_igual(calidad):
    cuerpo = dash.mpd(dash.periodo(conjunto_video(video("v1080", 1080), video("v720")),
                                   conjunto_audio()))
    assert aceptado_sin_cambios(drm.filtrar_xml(cuerpo, calidad), cuerpo)


# Indicadores por su nombre local, con cualquier espacio de nombres (o ninguno).
CON_PREFIJO = (
    b'<dash:MPD xmlns:dash="urn:mpeg:dash:schema:mpd:2011"><dash:Period>'
    b'<dash:ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"/>'
    b'<dash:AdaptationSet mimeType="video/mp4"><dash:Representation id="v" height="720">'
    b"<dash:BaseURL>v.mp4</dash:BaseURL></dash:Representation></dash:AdaptationSet>"
    b"</dash:Period></dash:MPD>"
)


@pytest.mark.parametrize(
    "cuerpo",
    [
        dash.mpd(dash.periodo(conjunto_video(video("v")), dentro=CP)),
        CON_PREFIJO,
        dash.mpd(dash.periodo(conjunto_video(video("v")),
                              dentro='<otro:ContentProtection xmlns:otro="urn:ejemplo"/>')),
        dash.mpd(dash.periodo(conjunto_video(video("v")), dentro='<ContentProtection xmlns=""/>')),
        CON_PREFIJO.replace(b"dash:", b"").replace(b' xmlns:dash="urn:mpeg:dash:schema:mpd:2011"',
                                                     b""),
        CON_PREFIJO.replace(b"dash:MPD", b"dash:mpd"),
    ],
    ids=["espacio-por-defecto", "con-prefijo", "otro-espacio", "sin-espacio-en-un-mpd-con-el",
         "mpd-sin-espacio", "raiz-en-minusculas"],
)
def test_content_protection_por_su_nombre_local(cuerpo):
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.AUDIO))


@pytest.mark.parametrize("calidad", [*Calidad, None])
@pytest.mark.parametrize(
    "cuerpo",
    [
        dash.mpd(dash.periodo(conjunto_video(video("v")), conjunto_audio()), dentro=CP),
        dash.mpd(dash.periodo(conjunto_video(video("v")), conjunto_audio(), dentro=CP)),
        dash.mpd(dash.periodo(conjunto_video(video("v")), conjunto_audio(),
                              dentro=f"<EventStream>{CP}</EventStream>")),
    ],
    ids=["mpd", "period", "period-fuera-de-un-adaptationset"],
)
def test_proteccion_en_el_mpd_o_en_un_period_rechaza_todo(cuerpo, calidad):
    assert rechazado(drm.filtrar_xml(cuerpo, calidad))


VIDEO_PROTEGIDO = {
    "adaptationset": conjunto_video(video("v"), dentro=CP),
    "contentcomponent": conjunto_video(
        video("v"), dentro=f'<ContentComponent contentType="video">{CP}</ContentComponent>'),
    "representation": conjunto_video(video("v", dentro=CP)),
    "subrepresentation": conjunto_video(
        video("v", dentro=f'<SubRepresentation level="0">{CP}</SubRepresentation>')),
}


@pytest.mark.parametrize("nivel", VIDEO_PROTEGIDO)
@pytest.mark.parametrize("calidad", CALIDADES_DE_VIDEO)
def test_video_protegido_con_audio_libre_se_rechaza_si_se_pide_video(nivel, calidad):
    """El defecto de R5: yt-dlp descargaba solo el audio, sin aviso."""
    cuerpo = dash.mpd(dash.periodo(VIDEO_PROTEGIDO[nivel], conjunto_audio()))
    assert rechazado(drm.filtrar_xml(cuerpo, calidad))


@pytest.mark.parametrize("nivel", VIDEO_PROTEGIDO)
def test_video_protegido_con_audio_libre_se_descarga_si_se_pide_audio(nivel):
    cuerpo = dash.mpd(dash.periodo(VIDEO_PROTEGIDO[nivel], conjunto_audio()))
    filtrado = drm.filtrar_xml(cuerpo, Calidad.AUDIO)
    assert filtrado.decision.veredicto is Veredicto.ACEPTADO
    assert filtrado.omitidas == 1
    assert quedan(filtrado.cuerpo) == ["audio"]


@pytest.mark.parametrize("calidad", [*Calidad])
def test_audio_protegido_con_video_libre_se_rechaza(calidad):
    """Con video, el contenido tiene audio (protegido): hace falta un audio sin proteger."""
    cuerpo = dash.mpd(dash.periodo(conjunto_video(video("v")), conjunto_audio(dentro=CP)))
    assert rechazado(drm.filtrar_xml(cuerpo, calidad))


@pytest.mark.parametrize("calidad", [*Calidad, None])
def test_mixto_excluye_la_1080p_protegida(calidad):
    cuerpo = dash.mpd(dash.periodo(
        conjunto_video(video("v1080", 1080, dentro=CP), video("v720")), conjunto_audio(),
    ))
    filtrado = drm.filtrar_xml(cuerpo, calidad)
    assert filtrado.decision.veredicto is Veredicto.ACEPTADO
    assert filtrado.omitidas == 1
    assert quedan(filtrado.cuerpo) == ["v720", "audio"]


def test_solo_se_quitan_los_bytes_de_las_representaciones_protegidas():
    protegida = video("v1080", 1080, dentro=CP)
    cuerpo = dash.mpd(dash.periodo(conjunto_video(protegida, video("v720")), conjunto_audio()))
    filtrado = drm.filtrar_xml(cuerpo, Calidad.MEJOR)
    assert filtrado.cuerpo == cuerpo.replace(protegida.encode(), b"")


def test_la_proteccion_del_conjunto_alcanza_a_todas_sus_representaciones():
    cuerpo = dash.mpd(dash.periodo(
        conjunto_video(video("v1440", 1440), video("v1080", 1080), dentro=CP),
        conjunto_video(video("v720")), conjunto_audio(),
    ))
    filtrado = drm.filtrar_xml(cuerpo, Calidad.MEJOR)
    assert filtrado.omitidas == 2
    assert quedan(filtrado.cuerpo) == ["v720", "audio"]


def test_contenido_sin_audio_basta_un_video_libre():
    cuerpo = dash.mpd(dash.periodo(conjunto_video(video("v1080", 1080, dentro=CP), video("v720"))))
    filtrado = drm.filtrar_xml(cuerpo, Calidad.MEJOR)
    assert filtrado.decision.veredicto is Veredicto.ACEPTADO
    assert quedan(filtrado.cuerpo) == ["v720"]
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.AUDIO))


MIXTA = ('<Representation id="{id}" codecs="avc1.64001e,mp4a.40.2" height="720" bandwidth="1">'
         '{dentro}<BaseURL>{id}.mp4</BaseURL></Representation>')


def test_una_representacion_con_video_y_audio_cuenta_como_los_dos():
    # Protegida: el contenido tiene audio, y no queda ninguno sin proteger.
    cuerpo = dash.mpd(dash.periodo(conjunto_video(MIXTA.format(id="m", dentro=CP), video("v"))))
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.MEJOR))
    # Libre: cubre el video y el audio.
    cuerpo = dash.mpd(dash.periodo(conjunto_video(MIXTA.format(id="m", dentro="")),
                                   conjunto_audio(dentro=CP)))
    for calidad in Calidad:
        filtrado = drm.filtrar_xml(cuerpo, calidad)
        assert filtrado.decision.veredicto is Veredicto.ACEPTADO
        assert quedan(filtrado.cuerpo) == ["m"]


def test_los_codecs_prevalecen_sobre_el_mime_type():
    """Como yt-dlp: una representación `video/mp4` con solo `mp4a` es un audio."""
    solo_audio = ('<Representation id="a" codecs="mp4a.40.2" bandwidth="1">'
                  "<BaseURL>a.mp4</BaseURL></Representation>")
    cuerpo = dash.mpd(dash.periodo(conjunto_video(video("v", dentro=CP), solo_audio)))
    assert quedan(drm.filtrar_xml(cuerpo, Calidad.AUDIO).cuerpo) == ["a"]
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.MEJOR))


def test_sin_calidad_basta_cualquier_representacion_libre():
    """La extracción sin descarga (`ytdl.extraer_plano`) no conoce la calidad pedida."""
    cuerpo = dash.mpd(dash.periodo(conjunto_video(video("v"), dentro=CP), conjunto_audio()))
    assert quedan(drm.filtrar_xml(cuerpo, None).cuerpo) == ["audio"]
    todo = dash.mpd(dash.periodo(conjunto_video(video("v"), dentro=CP), conjunto_audio(dentro=CP)))
    assert rechazado(drm.filtrar_xml(todo, None))


def test_varios_period_con_proteccion_parcial_se_rechazan():
    """yt-dlp une los periodos por formato: excluir en uno y no en otro daría un archivo
    incompleto (o solo los anuncios de un MPD con anuncios insertados)."""
    libre = dash.periodo(conjunto_video(video("v1080", 1080), video("v720")), conjunto_audio())
    mixto = dash.periodo(conjunto_video(video("v1080", 1080, dentro=CP), video("v720")),
                         conjunto_audio())
    assert rechazado(drm.filtrar_xml(dash.mpd(libre, mixto), Calidad.MEJOR))
    sin_proteccion = dash.mpd(libre, libre)
    assert aceptado_sin_cambios(drm.filtrar_xml(sin_proteccion, Calidad.MEJOR), sin_proteccion)


@pytest.mark.parametrize("codificacion", ["utf-16", "iso-8859-1"])
def test_otras_codificaciones_declaradas(codificacion):
    texto = dash.mpd(dash.periodo(
        conjunto_video(video("v1080", 1080, dentro=CP), video("v720")),
        conjunto_audio(audio("audio-ñ")),
    )).decode().replace('encoding="UTF-8"', f'encoding="{codificacion}"')
    filtrado = drm.filtrar_xml(texto.encode(codificacion), Calidad.MEJOR)
    assert filtrado.omitidas == 1
    assert quedan(filtrado.cuerpo) == ["v720", "audio-ñ"]


def test_utf8_invalido_sin_declaracion_se_analiza_igual():
    """yt-dlp lo decodifica sustituyendo los bytes inválidos; el analizador también lo lee."""
    cuerpo = dash.mpd(dash.periodo(conjunto_video(video("v")), dentro=CP))
    cuerpo = cuerpo.replace(b'<?xml version="1.0" encoding="UTF-8"?>\n', b"")
    cuerpo = cuerpo.replace(b'type="static"', b'type="static" titulo="\xe9\xff"')
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.MEJOR))


@pytest.mark.parametrize(
    "proteccion",
    [
        dash.PROTECTION, dash.PROTECTION_SOLA, dash.PROTECTION_HEADER,
        '<otro:Protection xmlns:otro="urn:ejemplo"/>',
    ],
    ids=["protection-y-header", "protection", "protection-header", "otro-espacio"],
)
def test_ism_con_cualquier_indicador_se_rechaza(proteccion):
    assert rechazado(drm.filtrar_xml(dash.ism(proteccion), Calidad.AUDIO), "ISM")


def test_protection_header_dentro_de_un_stream_index():
    cuerpo = dash.ism().replace(b"</StreamIndex>",
                                f"{dash.PROTECTION_HEADER}</StreamIndex>".encode())
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.MEJOR), "ISM")


def test_ism_sin_proteccion_se_entrega_igual():
    cuerpo = dash.ism()
    assert aceptado_sin_cambios(drm.filtrar_xml(cuerpo, Calidad.MEJOR), cuerpo)


@pytest.mark.parametrize("junto", [False, True], ids=["solo", "con-media-sin-cifrar"])
@pytest.mark.parametrize(
    "indicador",
    [dash.DRM_HEADER, dash.DRM_HEADER_SET, dash.MEDIA_CON_HEADER_ID,
     dash.MEDIA_CON_HEADER_SET_ID],
    ids=["drm-header", "drm-header-set", "media-header-id", "media-header-set-id"],
)
def test_f4m_con_cualquier_indicador_se_rechaza_entero(indicador, junto):
    elementos = [indicador, dash.MEDIA_SIN_CIFRAR] if junto else [indicador]
    assert rechazado(drm.filtrar_xml(dash.f4m(*elementos), Calidad.MEJOR), "F4M")


def test_f4m_2_0():
    cuerpo = dash.f4m(dash.MEDIA_CON_HEADER_ID).replace(b"f4m/1.0", b"f4m/2.0")
    assert rechazado(drm.filtrar_xml(cuerpo, Calidad.MEJOR), "F4M")


def test_f4m_sin_cifrar_se_entrega_igual():
    cuerpo = dash.f4m(dash.MEDIA_SIN_CIFRAR)
    assert aceptado_sin_cambios(drm.filtrar_xml(cuerpo, Calidad.MEJOR), cuerpo)


# --- Qué cuerpos se retienen hasta tenerlos enteros ---------------------------------


@pytest.mark.parametrize(
    ("prefijo", "final", "esperado"),
    [
        (b"<MPD", False, True),
        (b' \r\n\t<?xml version="1.0"?>', False, True),
        (b"\xef\xbb\xbf<MPD", False, True),
        (b"\xff\xfe<\x00", False, True),
        (b"\xfe\xff\x00<", False, True),
        (b"\x00\x00\x00\x18ftypmp42", False, False),
        (b"#EXTM3U\n", False, False),
        (b"G@\x11", False, False),
        (b"  \n", False, None),
        (b"\xef\xbb", False, None),
        (b"", False, None),
        (b"  \n", True, False),
        (b"", True, False),
    ],
)
def test_posible_xml(prefijo, final, esperado):
    assert drm.posible_xml(prefijo, final) is esperado
