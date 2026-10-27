"""Límite de SC-002 para los tests (spec SC-002; research R19 y R20).

El total servido para una descarga no supera el 1 % del tamaño del archivo, más el coste fijo
de los sondeos del extractor genérico (16 KiB por extracción, dos extracciones por intento:
`extraer_plano` y `descargar`), más, por cada terminación abrupta, max(128 KiB, bloque en
vuelo). Los sondeos solo existen con URLs directas `video/*` resueltas por el extractor
genérico, y los 16 KiB solo están medidos con el servidor limitado hasta 4 MB/s (R19).
"""

SONDEO = 16 * 1024  # bytes por extracción (R19)
EXTRACCIONES_POR_INTENTO = 2  # `extraer_plano` y `descargar`
ABRUPTA = 128 * 1024  # bytes por terminación abrupta con bloque pequeño (R20)


def limite(tamano: int, intentos: int, abruptas: int = 0, en_vuelo: int = 0) -> float:
    """Bytes servidos admitidos por SC-002 para `intentos` intentos con el extractor genérico."""
    return (
        1.01 * tamano
        + SONDEO * EXTRACCIONES_POR_INTENTO * intentos
        + abruptas * max(ABRUPTA, en_vuelo)
    )


def intentos(servidor, ruta: str) -> int:
    """Intentos que llegaron al servidor, contados en la secuencia de sus peticiones.

    Cada intento hace dos sondeos sin `Range` (uno por extracción) y después la petición de la
    descarga, con o sin `Range`; los reintentos internos de yt-dlp que siguen llevan `Range`.
    Vale para URLs directas con el extractor genérico, sin cortes con el `.part` vacío.
    """
    rangos = servidor.rangos[ruta]
    cuenta = posicion = 0
    while posicion < len(rangos):
        assert rangos[posicion : posicion + 2] == [None, None][: len(rangos) - posicion], rangos
        cuenta += 1
        posicion += 1 + EXTRACCIONES_POR_INTENTO  # los sondeos y la descarga
        while posicion < len(rangos) and rangos[posicion] is not None:
            posicion += 1  # reintentos internos de la descarga
    return cuenta
