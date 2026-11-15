"""Argumentos de WebView2 para la ventana (research R14, "Decisión de la puerta").

Se fijan solo en el entorno del proceso de la aplicación, antes de `webview.start`: WebView2 los
lee al crear su entorno y los hereda el motor web, que es hijo de este proceso. Nunca se
escriben como variable persistente del usuario o del sistema (ni `setx` ni el registro de
Windows). Es lógica pura, sin pywebview, para probarla también en Linux.
"""

import os

VARIABLE = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
ARGUMENTOS = ("--renderer-process-limit=1", "--disable-gpu", "--disable-extensions")


def fijar(entorno=None) -> str:
    """Suma `ARGUMENTOS` al valor que ya tuviera `VARIABLE`, sin repetirlos; devuelve el valor.

    `entorno` es `os.environ` por defecto: el entorno de este proceso y de sus hijos.
    """
    entorno = os.environ if entorno is None else entorno
    previos = entorno.get(VARIABLE, "").split()
    valor = " ".join(previos + [a for a in ARGUMENTOS if a not in previos])
    entorno[VARIABLE] = valor
    return valor
