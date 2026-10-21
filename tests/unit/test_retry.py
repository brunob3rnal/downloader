import random

import pytest

from downloader.errors import Clasificacion, Tipo
from downloader.retry import TOPE_SIN_AVANCE, TopeSinAvance, espera, siguiente_intento


@pytest.mark.parametrize("n, nominal", [(0, 2), (1, 4), (2, 8), (5, 64), (7, 256)])
def test_espera_exponencial_con_variacion(n, nominal):
    rng = random.Random(1234)
    valores = [espera(n, rng=rng) for _ in range(500)]
    assert all(nominal * 0.8 <= v <= nominal * 1.2 for v in valores)
    # La variación se usa de verdad, por ambos lados.
    assert min(valores) < nominal * 0.85
    assert max(valores) > nominal * 1.15


@pytest.mark.parametrize("n", [8, 9, 20, 100, 10_000])
def test_nunca_supera_cinco_minutos(n):
    rng = random.Random(99)
    valores = [espera(n, rng=rng) for _ in range(500)]
    assert all(300 * 0.8 <= v <= 300 for v in valores)


def test_base_y_tope_inyectables():
    rng = random.Random(7)
    assert all(0.04 <= espera(0, base=0.05, tope=0.5, rng=rng) <= 0.06 for _ in range(100))
    assert all(espera(30, base=0.05, tope=0.5, rng=rng) <= 0.5 for _ in range(100))


def test_n_vuelve_a_cero_tras_avance():
    assert siguiente_intento(0, hubo_avance=False) == 1
    assert siguiente_intento(4, hubo_avance=False) == 5
    assert siguiente_intento(4, hubo_avance=True) == 0


# --- Tope de errores ambiguos sin avance (R4, FR-026) ---

AMBIGUO_403 = Clasificacion(Tipo.TEMPORAL, ambiguo=True, motivo="HTTP 403")
AMBIGUO_FFMPEG = Clasificacion(Tipo.TEMPORAL, ambiguo=True, motivo="descargador externo (ffmpeg)")
NO_AMBIGUO = Clasificacion(Tipo.TEMPORAL)  # corte, timeout, 5xx, 429


def test_el_tope_es_3():
    assert TOPE_SIN_AVANCE == 3


@pytest.mark.parametrize("ambiguo", [AMBIGUO_403, AMBIGUO_FFMPEG])
def test_falla_al_tercer_ambiguo_seguido_sin_avance(ambiguo):
    tope = TopeSinAvance()
    assert tope.cerrar_intento(ambiguo).tipo is Tipo.TEMPORAL
    assert tope.cerrar_intento(ambiguo).tipo is Tipo.TEMPORAL
    decision = tope.cerrar_intento(ambiguo)
    assert decision.tipo is Tipo.PERMANENTE
    assert decision.mensaje


def test_el_mensaje_de_403_y_410_menciona_cookies_de():
    for motivo in ("HTTP 403", "HTTP 410"):
        tope = TopeSinAvance()
        for _ in range(3):
            decision = tope.cerrar_intento(Clasificacion(Tipo.TEMPORAL, True, motivo))
        assert "--cookies-de" in decision.mensaje
        assert motivo in decision.mensaje


def test_bytes_nuevos_sobre_el_maximo_reinician_la_racha():
    tope = TopeSinAvance()
    tope.observar("v.mp4", 1000)
    tope.cerrar_intento(AMBIGUO_403)  # hubo avance: no cuenta
    tope.cerrar_intento(AMBIGUO_403)  # 1
    tope.cerrar_intento(AMBIGUO_403)  # 2
    tope.observar("v.mp4", 1500)
    assert tope.cerrar_intento(AMBIGUO_403).tipo is Tipo.TEMPORAL  # avance: vuelve a 0
    tope.cerrar_intento(AMBIGUO_403)
    tope.cerrar_intento(AMBIGUO_403)
    assert tope.cerrar_intento(AMBIGUO_403).tipo is Tipo.PERMANENTE


def test_volver_a_bytes_ya_vistos_no_es_avance():
    tope = TopeSinAvance()
    tope.observar("v.mp4", 5000)
    tope.cerrar_intento(AMBIGUO_403)
    decisiones = []
    for _ in range(3):
        tope.observar("v.mp4", 3000)  # p. ej. reinicio desde cero que no supera lo ya visto
        decisiones.append(tope.cerrar_intento(AMBIGUO_403).tipo)
    assert decisiones == [Tipo.TEMPORAL, Tipo.TEMPORAL, Tipo.PERMANENTE]


def test_el_maximo_es_por_archivo():
    tope = TopeSinAvance()
    tope.observar("video.mp4", 5000)
    tope.cerrar_intento(AMBIGUO_403)
    tope.observar("audio.m4a", 100)  # archivo nuevo: es avance
    tope.cerrar_intento(AMBIGUO_403)
    assert tope.consecutivos == 0


def test_cortes_y_5xx_no_cuentan_y_rompen_la_racha():
    tope = TopeSinAvance()
    for _ in range(10):
        assert tope.cerrar_intento(NO_AMBIGUO).tipo is Tipo.TEMPORAL
    tope.cerrar_intento(AMBIGUO_403)
    tope.cerrar_intento(AMBIGUO_403)
    tope.cerrar_intento(NO_AMBIGUO)
    assert tope.cerrar_intento(AMBIGUO_403).tipo is Tipo.TEMPORAL


def test_no_cambia_permanentes_ni_fatales():
    tope = TopeSinAvance()
    assert tope.cerrar_intento(Clasificacion(Tipo.PERMANENTE)).tipo is Tipo.PERMANENTE
    assert tope.cerrar_intento(Clasificacion(Tipo.FATAL)).tipo is Tipo.FATAL
