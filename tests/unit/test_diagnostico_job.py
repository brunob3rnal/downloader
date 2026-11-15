"""Funciones puras de `scripts/diagnostico_job.py` (specs/002-desktop-app, research R8, opción A).

El diagnóstico se ejecuta a mano en Windows; aquí se prueba cómo resume los resultados. Lo que
decide es la supervivencia del hijo, no si queda en un objeto de trabajo.
"""

import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _cargar():
    especificacion = importlib.util.spec_from_file_location(
        "diagnostico_job", RAIZ / "scripts" / "diagnostico_job.py"
    )
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


diag = _cargar()


def _caso(camino, escenario, sobrevive, en_trabajo=False, error=None):
    return diag.Resultado(camino=camino, escenario=escenario, error=error,
                          hijo_en_trabajo=en_trabajo, intermedio_en_trabajo=True,
                          hijo_sobrevive=sobrevive, intermedio_sobrevive=None)


def test_los_tres_caminos_y_los_dos_escenarios():
    assert diag.CAMINOS == ("python directo", "uv run", "uv run --gui-script")
    assert diag.ESCENARIOS == ("fin normal", "matar el superior")


def test_conclusion_depende_solo_de_la_supervivencia():
    resultados = [_caso("uv run", "fin normal", True, en_trabajo=True),
                  _caso("uv run", "matar el superior", True, en_trabajo=True)]
    assert diag.conclusion(resultados) == {"uv run": True}


def test_un_escenario_en_que_muere_hace_fallar_el_camino():
    resultados = [_caso("uv run", "fin normal", True), _caso("uv run", "matar el superior", False)]
    assert diag.conclusion(resultados) == {"uv run": False}


def test_un_error_al_crear_el_hijo_cuenta_como_no_sobrevive():
    resultados = [_caso("python directo", "fin normal", None, error="acceso denegado (5)")]
    assert diag.conclusion(resultados) == {"python directo": False}


def test_codigo_de_salida():
    bien = [_caso("python directo", "fin normal", True)]
    mal = [_caso("python directo", "fin normal", False)]
    assert diag.codigo_de_salida(bien) == 0
    assert diag.codigo_de_salida(mal) == 1
    assert diag.codigo_de_salida([]) == 2


def test_la_tabla_muestra_cada_caso_y_el_error():
    lineas = diag.tabla([
        _caso("uv run", "fin normal", True, en_trabajo=True),
        _caso("uv run --gui-script", "matar el superior", None, error="acceso denegado (5)"),
    ])
    texto = "\n".join(lineas)
    assert "uv run | fin normal | sí | sí" in texto
    assert "acceso denegado (5)" in texto
