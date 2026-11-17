"""El trabajador de la aplicación con el navegador falso instalado (specs/002-desktop-app, US5;
tasks T101).

`python -m tests.support.trabajador_con_navegador_falso --ejecucion ID`: sustituye la lectura
de cookies del navegador por `NavegadorFalso` (su cookie ficticia) y ejecuta el procesador real,
`downloader.app.procesador`. Los tests lo usan en lugar del módulo del trabajador
(`procesos.MODULO`), porque un `monkeypatch` no llega a un subproceso.
"""

import sys

import yt_dlp.cookies

from tests.support.navegador_falso import NavegadorFalso


def main(argv=None) -> int:
    yt_dlp.cookies.extract_cookies_from_browser = NavegadorFalso()
    from downloader.app import procesador

    return procesador.main(argv)


if __name__ == "__main__":
    sys.exit(main())
