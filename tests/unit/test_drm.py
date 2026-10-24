"""Política de cifrado de HLS: DRM, no soportado o aceptado (research R5; plan, decisión 30).

`downloader.drm` es puro: analiza el texto de los manifiestos, sin red ni yt-dlp.
"""

import subprocess
import sys

import pytest

from downloader import drm
from downloader.drm import Veredicto
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
