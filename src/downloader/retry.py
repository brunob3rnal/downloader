"""Espera entre reintentos (research R10) y tope de errores ambiguos sin avance (R4)."""

import random
from dataclasses import dataclass

from downloader.errors import CONSEJO_SESION, Clasificacion, Tipo

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


TOPE_SIN_AVANCE = 3


@dataclass(frozen=True)
class Decision:
    tipo: Tipo
    mensaje: str | None = None  # explicación para el usuario cuando el tope convierte en fallo


class TopeSinAvance:
    """Tope para los errores ambiguos de una descarga (research R4, FR-026).

    Un error ambiguo (403/410 al descargar, fallo de ffmpeg) se reintenta como temporal, salvo
    que `tope` intentos seguidos terminen en un error ambiguo sin ningún byte nuevo sobre el
    máximo ya visto de cada archivo: entonces falla como PERMANENTE. Un intento que termina en
    otro error (un corte, un 5xx) rompe la racha. Hay una instancia por descarga y el trabajador
    la alimenta con el progreso de cada intento.
    """

    def __init__(self, tope: int = TOPE_SIN_AVANCE):
        self.tope = tope
        self.consecutivos = 0
        self._maximos: dict[str | None, int] = {}
        self._avance = False

    def observar(self, archivo: str | None, bytes_descargados: int | None) -> None:
        if bytes_descargados is not None and bytes_descargados > self._maximos.get(archivo, 0):
            self._maximos[archivo] = bytes_descargados
            self._avance = True

    def cerrar_intento(self, clasificacion: Clasificacion) -> Decision:
        """Decide qué hacer con un intento fallido y prepara el siguiente."""
        sin_avance, self._avance = not self._avance, False
        if not (clasificacion.ambiguo and sin_avance):
            self.consecutivos = 0
            return Decision(clasificacion.tipo)
        self.consecutivos += 1
        if self.consecutivos < self.tope:
            return Decision(clasificacion.tipo)
        return Decision(Tipo.PERMANENTE, _mensaje(clasificacion.motivo, self.consecutivos))


def _mensaje(motivo: str | None, intentos: int) -> str:
    if motivo in ("HTTP 403", "HTTP 410"):
        return (
            f"Acceso denegado ({motivo}) en {intentos} intentos seguidos sin avanzar, aun "
            f"volviendo a extraer la URL. {CONSEJO_SESION}"
        )
    return f"La descarga falló {intentos} veces seguidas sin avanzar ({motivo})."
