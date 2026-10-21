"""Cálculo de la espera entre reintentos (research R10)."""

import random

VARIACION = 0.2


def espera(n: int, base: float = 2, tope: float = 300, rng=random) -> float:
    """Segundos a esperar tras `n` intentos fallidos seguidos sin avance.

    `min(tope, base * 2**n)` con una variación aleatoria de ±20 %, sin superar nunca `tope`.
    """
    nominal = min(tope, base * 2 ** min(n, 64))
    return min(tope, nominal * rng.uniform(1 - VARIACION, 1 + VARIACION))


def siguiente_intento(n: int, hubo_avance: bool) -> int:
    """Nuevo valor del contador `n`: cualquier avance de bytes lo reinicia."""
    return 0 if hubo_avance else n + 1
