"""Funciones puras de `scripts/medir_memoria.py` (specs/002-desktop-app, T008; research R14).

La medición real se hace a mano en Windows; aquí se prueban, con procesos falsos, qué procesos
cuentan como de la aplicación, la suma de memoria privada (USS) y la elección del máximo.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

RAIZ = Path(__file__).resolve().parents[2]


def _cargar():
    especificacion = importlib.util.spec_from_file_location(
        "medir_memoria", RAIZ / "scripts" / "medir_memoria.py"
    )
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


medir = _cargar()


class ProcesoFalso:
    def __init__(self, pid, nombre, uss=0, padre=None, muere=False, sin_acceso=False):
        self.pid = pid
        self._nombre = nombre
        self._uss = uss
        self._padre = padre
        self._hijos = []
        self.muere = muere
        self.sin_acceso = sin_acceso
        if padre is not None:
            padre._hijos.append(self)

    def name(self):
        if self.muere:
            raise psutil.NoSuchProcess(self.pid)
        return self._nombre

    def parent(self):
        return self._padre

    def children(self, recursive=False):
        hijos = []
        for hijo in self._hijos:
            hijos.append(hijo)
            if recursive:
                hijos.extend(hijo.children(recursive=True))
        return hijos

    def memory_full_info(self):
        if self.muere:
            raise psutil.NoSuchProcess(self.pid)
        if self.sin_acceso:
            raise psutil.AccessDenied(self.pid)
        return SimpleNamespace(uss=self._uss)


@pytest.fixture
def arbol():
    """explorer → medir (python) → uv → lanzador del venv (pythonw) → ventana (pythonw) → WebView2;
    medir → trabajador (python) → ffmpeg; además, un proceso ajeno."""
    explorer = ProcesoFalso(1, "explorer.exe")
    medir_ = ProcesoFalso(2, "python.exe", padre=explorer)
    uv = ProcesoFalso(3, "uv.exe", uss=8, padre=medir_)
    lanzador = ProcesoFalso(4, "pythonw.exe", uss=3, padre=uv)
    ventana = ProcesoFalso(5, "pythonw.exe", uss=60, padre=lanzador)
    navegador = ProcesoFalso(6, "msedgewebview2.exe", uss=40, padre=ventana)
    renderizador = ProcesoFalso(7, "msedgewebview2.exe", uss=30, padre=navegador)
    trabajador = ProcesoFalso(8, "python.exe", uss=70, padre=medir_)
    ffmpeg = ProcesoFalso(9, "ffmpeg.exe", uss=20, padre=trabajador)
    ajeno = ProcesoFalso(10, "notepad.exe", uss=500, padre=explorer)
    return SimpleNamespace(**locals())


def _pids(procesos):
    return [p.pid for p in procesos]


def test_incluye_ventana_descendientes_cadena_del_lanzador_y_trabajador(arbol):
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador, tope=arbol.medir_.pid)
    assert sorted(_pids(procesos)) == [3, 4, 5, 6, 7, 8, 9]


def test_la_cadena_se_detiene_en_el_primer_ascendiente_que_no_es_del_lanzador(arbol):
    # Sin tope, el ascenso sigue por los procesos de Python y uv, pero no pasa de explorer.
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador)
    assert 2 in _pids(procesos)  # el propio medir es python.exe: por eso se pasa `tope`
    assert 1 not in _pids(procesos) and 10 not in _pids(procesos)


def test_sin_duplicados(arbol):
    # Si el trabajador colgara de la ventana, no se cuenta dos veces.
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.navegador, tope=2)
    assert len(_pids(procesos)) == len(set(_pids(procesos)))


def test_sin_trabajador(arbol):
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, None, tope=2)
    assert sorted(_pids(procesos)) == [3, 4, 5, 6, 7]


def test_suma_uss_y_desglose(arbol):
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador, tope=2)
    muestra = medir.suma_uss(procesos, instante=1.5)
    assert muestra.total == 8 + 3 + 60 + 40 + 30 + 70 + 20
    assert muestra.instante == 1.5
    assert {(pid, nombre, uss) for pid, nombre, uss in muestra.desglose} >= {
        (5, "pythonw.exe", 60), (9, "ffmpeg.exe", 20)
    }
    assert muestra.sin_acceso == []


def test_suma_uss_ignora_los_procesos_que_terminan_durante_la_muestra(arbol):
    arbol.renderizador.muere = True
    procesos = [arbol.ventana, arbol.renderizador, arbol.trabajador]
    muestra = medir.suma_uss(procesos, instante=0)
    assert muestra.total == 60 + 70
    assert 7 not in [pid for pid, _, _ in muestra.desglose]


def test_suma_uss_informa_de_los_procesos_sin_acceso(arbol):
    arbol.navegador.sin_acceso = True
    muestra = medir.suma_uss([arbol.ventana, arbol.navegador], instante=0)
    assert muestra.total == 60
    assert muestra.sin_acceso == [(6, "msedgewebview2.exe")]


def test_maximo_devuelve_la_muestra_mayor_con_su_desglose():
    muestras = [
        medir.Muestra(0.0, 100, [(1, "a", 100)], []),
        medir.Muestra(0.5, 250, [(1, "a", 150), (2, "b", 100)], []),
        medir.Muestra(1.0, 200, [(1, "a", 200)], []),
    ]
    mayor = medir.maximo(muestras)
    assert mayor.total == 250 and mayor.instante == 0.5
    assert mayor.desglose == [(1, "a", 150), (2, "b", 100)]
    assert medir.maximo([]) is None


def test_megabytes_decimales():
    assert medir.mb(300_000_000) == pytest.approx(300.0)
