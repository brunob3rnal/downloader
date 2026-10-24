"""Sustituto de la lectura de cookies del navegador de yt-dlp (research R12, R25).

`NavegadorFalso` reemplaza `yt_dlp.cookies.extract_cookies_from_browser`: devuelve una cookie
ficticia conocida (Principio VI: ningún valor real) o reproduce un fallo de lectura. Los fallos
de Windows lanzan la misma cadena de excepciones que el código de `yt_dlp/cookies.py`: allí,
`logger.error` (`YoutubeDL.report_error`) ya lanza el `DownloadError`.
"""

import http.cookiejar

import yt_dlp.cookies
from yt_dlp.utils import DownloadError

NOMBRE = "SID"
VALOR = "galleta-ficticia-3f9c2a71"

# Mensajes literales de `yt_dlp/cookies.py` (yt-dlp 2026.08.19).
MENSAJE_BLOQUEADA = (
    "Could not copy Chrome cookie database. See  https://github.com/yt-dlp/yt-dlp/issues/7271  "
    "for more info"
)
MENSAJE_DPAPI = (
    "Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  "
    "for more info"
)


def bloqueada(navegador, logger):
    """Un navegador basado en Chromium abierto en Windows: copiar su base de cookies falla."""
    try:
        raise PermissionError(
            13, "The process cannot access the file because it is being used by another process"
        )
    except PermissionError:
        logger.error(MENSAJE_BLOQUEADA)
        raise DownloadError(MENSAJE_BLOQUEADA)


def sin_descifrar(navegador, logger):
    """Una cookie que DPAPI no descifra (Chrome 127+ en Windows: cifrado ligado a la app)."""
    logger.error(MENSAJE_DPAPI)
    raise DownloadError(MENSAJE_DPAPI)


def no_encontrada(navegador, logger):
    raise FileNotFoundError(f'could not find {navegador} cookies database in "/perfiles"')


def plataforma(navegador, logger):
    raise ValueError("unsupported platform: linux")


def otro(navegador, logger):
    logger.error("no se pudo leer el perfil")
    raise DownloadError("no se pudo leer el perfil")


def cookie(dominio="127.0.0.1"):
    return http.cookiejar.Cookie(
        version=0, name=NOMBRE, value=VALOR, port=None, port_specified=False, domain=dominio,
        domain_specified=False, domain_initial_dot=False, path="/", path_specified=True,
        secure=False, expires=None, discard=False, comment=None, comment_url=None, rest={},
    )


class NavegadorFalso:
    """Anota cada navegador leído; `fallo`, si se asigna, se llama en lugar de devolver cookies."""

    def __init__(self):
        self.leidos: list[str] = []
        self.fallo = None

    def __call__(self, browser_name, profile=None, logger=None, *, keyring=None, container=None):
        self.leidos.append(browser_name)
        if self.fallo is not None:
            self.fallo(browser_name, logger)
        jar = yt_dlp.cookies.YoutubeDLCookieJar()
        jar.set_cookie(cookie())
        return jar

    def instalar(self, monkeypatch):
        monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", self)
        return self
