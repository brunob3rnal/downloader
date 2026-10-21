import random

import pytest

from downloader.retry import espera, siguiente_intento


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
