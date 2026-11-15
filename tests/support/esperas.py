"""Esperas con plazo para los tests de la aplicación."""

import time


def hasta(condicion, segundos=60, que="la condición", intervalo=0.1):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(intervalo)
    raise AssertionError(f"no se ha cumplido en {segundos} s: {que}")
