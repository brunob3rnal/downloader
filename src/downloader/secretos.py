"""Filtro de secretos en mensajes y registros (Principio VI, FR-017; research R12).

Sin yt-dlp, para la ventana (research R15 de la feature 002). `errors` reexporta
`ocultar_secretos` y `OCULTO` con los mismos nombres.
"""

import re

_CABECERA = re.compile(
    r"""(?P<clave>\b(?:cookie|authorization)\b['"]?\s*[:=]\s*)(?P<comilla>['"])?"""
    r"""(?P<valor>(?(comilla)[^'"\n]*|[^\n]*))""",
    re.IGNORECASE,
)
_PARAMETRO = re.compile(r"(?P<clave>[?&;](?P<nombre>[\w.~-]+)=)(?P<valor>[^&\s#'\"]*)")
# Palabras que delatan un secreto en el nombre de un parámetro, separadas por `-`, `_` o `.`
# (`X-Amz-Signature`, `access_token`, `Key-Pair-Id`); `monkey` o `itag` no lo son. Los nombres
# que terminan en `token`, `sig` o `signature` (`lsig`) también.
_PALABRAS_SECRETAS = {
    "token", "sig", "signature", "key", "auth", "credential", "policy", "hmac", "hdnts",
    "password", "secret",
}
_SUFIJOS_SECRETOS = ("token", "sig", "signature")
OCULTO = "***"


def _parametro_secreto(nombre: str) -> bool:
    nombre = nombre.lower()
    return (
        not _PALABRAS_SECRETAS.isdisjoint(re.split(r"[-_.]", nombre))
        or nombre.endswith(_SUFIJOS_SECRETOS)
    )


def ocultar_secretos(texto: str) -> str:
    """Elimina valores de cabeceras Cookie/Authorization y de parámetros de URL de token o
    firma (`token`, `sig`, `X-Amz-Signature`, `Key-Pair-Id`, `access_token`…)."""
    texto = _CABECERA.sub(lambda m: f"{m['clave']}{m['comilla'] or ''}{OCULTO}", texto)
    return _PARAMETRO.sub(
        lambda m: f"{m['clave']}{OCULTO}" if _parametro_secreto(m["nombre"]) else m[0], texto
    )
