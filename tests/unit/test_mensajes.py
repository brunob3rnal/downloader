"""Mensajes y secretos sin yt-dlp (specs/002-desktop-app, research R15 y R17).

`mensajes.requiere_sesion` decide si una descarga fallida ofrece "Reintentar con cookies"
(FR-016) comparando con las constantes de la feature 001, no con texto suelto.
"""

import pytest
import yt_dlp.cookies
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError

from downloader import errors, mensajes, models, secretos, worker
from tests.support.fake_extractor import FalsoIE
from tests.unit import test_errors


def _casos_de(prueba):
    return next(m for m in prueba.pytestmark if m.name == "parametrize").args[1]


# --- requiere_sesion ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        f"HTTP Error 403: Forbidden. {mensajes.CONSEJO_SESION}",
        mensajes.LISTA_VACIA,
        "No se pudieron leer las cookies de chrome: no se pudieron descifrar con la protección "
        "de datos de Windows (DPAPI). Chrome 127 y posteriores las cifran con una clave ligada "
        "a la aplicación que yt-dlp no sabe leer: use otro navegador, por ejemplo "
        "--cookies-de firefox",
        "No se pudieron leer las cookies de edge: su base de datos de cookies está bloqueada",
    ],
    ids=["403", "lista-vacia", "cookies-cifradas", "cookies-bloqueadas"],
)
def test_requiere_sesion(texto):
    assert mensajes.requiere_sesion(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        "Contenido protegido con DRM (Widevine): no se descarga",
        "Video privado: el propietario restringió el acceso",
        "Unsupported URL: https://x.example/v",
        "HTTP Error 404: Not Found",
        "",
        None,
    ],
)
def test_no_requiere_sesion(texto):
    assert mensajes.requiere_sesion(texto) is False


def test_los_mensajes_son_los_de_001():
    assert errors.CONSEJO_SESION is mensajes.CONSEJO_SESION
    assert worker.LISTA_VACIA is mensajes.LISTA_VACIA


def test_cookies_ilegibles_lleva_el_prefijo_que_reconoce_requiere_sesion():
    error = errors.CookiesIlegibles(
        mensajes.COOKIES_ILEGIBLES.format(navegador="edge", causa="bloqueada")
    )
    assert mensajes.requiere_sesion(str(error))


# --- R17: contenido que exige iniciar sesión sin 403 --------------------------------------


def test_login_requerido_de_yt_dlp_ofrece_reintentar_con_cookies():
    """[a verificar R17] yt-dlp lo lanza como `ExtractorError(expected=True)`; la 001 lo
    clasifica PERMANENTE sin `CONSEJO_SESION`, y el mensaje guardado es el de yt-dlp, que
    sugiere `--cookies-from-browser`. `requiere_sesion` lo reconoce sin tocar el mensaje."""
    extractor = FalsoIE(YoutubeDL({"quiet": True}))
    with pytest.raises(ExtractorError) as error:
        extractor.raise_login_required()

    clasificacion = errors.clasificar_detalle(error.value, en_linea=True)
    assert clasificacion.tipo is errors.Tipo.PERMANENTE
    assert clasificacion.consejo is None  # 001 no añade CONSEJO_SESION en este caso

    mensaje = worker._mensaje(error.value)  # lo que se guarda como `ultimo_error`
    assert "--cookies-from-browser" in mensaje
    assert mensajes.requiere_sesion(mensaje) is True


# --- NAVEGADORES y secretos sin yt-dlp ---------------------------------------------------


def test_navegadores_coinciden_con_yt_dlp():
    assert models.NAVEGADORES == frozenset(yt_dlp.cookies.SUPPORTED_BROWSERS)


@pytest.mark.parametrize("texto, secreto", _casos_de(test_errors.test_ocultar_secretos))
def test_secretos_oculta_lo_mismo_que_errors(texto, secreto):
    assert secretos.ocultar_secretos(texto) == errors.ocultar_secretos(texto)
    assert secreto not in secretos.ocultar_secretos(texto)


def test_secretos_es_la_misma_funcion_que_errors():
    assert errors.ocultar_secretos is secretos.ocultar_secretos
