"""Qué hacer al cerrar la ventana (FR-010; research R18; contracts/ventana.md, "Aviso al cerrar").

Solo se pregunta con un procesamiento de la propia aplicación en marcha, arrancando o
deteniéndose. Con uno de la terminal, o sin ninguno, la ventana se cierra sin más: un
`ejecutar` de la CLI no se ve afectado.
"""

PREGUNTAR, CERRAR = "preguntar", "cerrar"
TITULO = "Hay descargas en curso"
PLAZO = 30  # s tras "Pausar y cerrar" hasta ofrecer "Cerrar igualmente" (R18)
_EN_CURSO = {"en_marcha", "arrancando", "deteniendo"}
_COMO_SIGUE = ("Puede dejarlas avanzando en segundo plano o pausarlas. En ambos casos se conserva "
               "lo ya descargado.")


def decidir(procesador: dict) -> str:
    """`preguntar` o `cerrar`, según el procesador de `api/estado` (`estado` y `origen`)."""
    if procesador.get("origen") == "app" and procesador.get("estado") in _EN_CURSO:
        return PREGUNTAR
    return CERRAR


def texto(pendientes: int) -> str:
    """El texto del diálogo: una activa y las demás pendientes que esperan."""
    esperan = max(0, pendientes - 1)
    if esperan == 0:
        cola = "no hay más en la cola"
    elif esperan == 1:
        cola = "otra espera en la cola"
    else:
        cola = f"otras {esperan} esperan en la cola"
    return f"Una descarga está activa y {cola}. {_COMO_SIGUE}"
