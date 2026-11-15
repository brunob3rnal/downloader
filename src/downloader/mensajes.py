"""Mensajes de fallo que apuntan a la sesión del usuario (FR-016, FR-026, FR-028, FR-029).

Sin yt-dlp, para la ventana (research R15 de la feature 002). `errors`, `retry` y `worker` los
usan con los mismos nombres. `requiere_sesion` decide si una descarga fallida ofrece
"Reintentar con cookies" en la ventana (research R17).
"""

# Un acceso denegado suele deberse a un video que exige la sesión del usuario (FR-016, FR-026).
CONSEJO_SESION = (
    "Puede que el video exija una sesión iniciada: vuelva a agregarlo con --cookies-de "
    "NAVEGADOR (por ejemplo, --cookies-de firefox)."
)
# Una lista sin entradas suele ser una URL que yt-dlp no sabe leer o que exige sesión (FR-028).
LISTA_VACIA = (
    "no se encontraron videos; la URL puede no estar soportada o requerir sesión "
    "(ver --cookies-de)"
)
# Cookies del navegador que no se pueden leer (FR-029): se puede probar con otro navegador.
COOKIES_ILEGIBLES = "No se pudieron leer las cookies de {navegador}: {causa}"
# Lo que yt-dlp pone en los errores de contenido que exige iniciar sesión, sin 403
# (`InfoExtractor.raise_login_required`); la 001 los guarda tal cual (research R17).
MARCAS_YTDLP_SESION = ("--cookies-from-browser",)


def requiere_sesion(texto: str | None) -> bool:
    """Si el último error de una descarga apunta a que necesita la sesión del usuario."""
    if not texto:
        return False
    prefijo_cookies = COOKIES_ILEGIBLES.split("{", 1)[0]
    return (
        CONSEJO_SESION in texto
        or LISTA_VACIA in texto
        or texto.startswith(prefijo_cookies)
        or any(marca in texto for marca in MARCAS_YTDLP_SESION)
    )
