"""Enlaces pegados en el formulario de la ventana (FR-002, research R16).

Más estricto que la CLI, que acepta cualquier texto no vacío: solo `http` y `https` con host.
"""

from urllib.parse import urlsplit


def separar(texto: str) -> tuple[list[str], list[tuple[int, str]]]:
    """Las URLs válidas y las líneas que no lo son, con su número (desde 1, contando las
    vacías). Las líneas vacías o de solo espacios se ignoran."""
    validas, invalidas = [], []
    for numero, linea in enumerate(texto.splitlines(), start=1):
        linea = linea.strip()
        if not linea:
            continue
        try:
            partes = urlsplit(linea)
            valida = partes.scheme in ("http", "https") and bool(partes.hostname)
        except ValueError:
            valida = False
        (validas.append(linea) if valida else invalidas.append((numero, linea)))
    return validas, invalidas
