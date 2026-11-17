"""Qué pasa al cerrar la ventana (specs/002-desktop-app, FR-010; research R18;
contracts/ventana.md, "Aviso al cerrar"; tasks T092)."""

import pytest

from downloader.app import cierre


def info(estado, origen):
    return {"estado": estado, "origen": origen}


@pytest.mark.parametrize("estado", ["en_marcha", "arrancando", "deteniendo"])
def test_con_un_procesamiento_de_la_aplicacion_pregunta(estado):
    assert cierre.decidir(info(estado, "app")) == cierre.PREGUNTAR == "preguntar"


def test_con_un_procesamiento_de_la_terminal_cierra_sin_preguntar():
    """US4, escenario 6; Edge Cases: el `ejecutar` de la CLI no se ve afectado."""
    assert cierre.decidir(info("en_marcha", "cli")) == cierre.CERRAR == "cerrar"


def test_sin_procesamiento_cierra_sin_preguntar():
    assert cierre.decidir(info("ninguno", None)) == cierre.CERRAR


COLA = "Puede dejarlas avanzando en segundo plano o pausarlas. En ambos casos se conserva lo ya descargado."


@pytest.mark.parametrize("pendientes, principio", [
    (3, "Una descarga está activa y otras 2 esperan en la cola."),
    (2, "Una descarga está activa y otra espera en la cola."),
    (1, "Una descarga está activa y no hay más en la cola."),
    (0, "Una descarga está activa y no hay más en la cola."),
])
def test_el_texto_del_dialogo_cuenta_las_que_esperan(pendientes, principio):
    assert cierre.texto(pendientes) == f"{principio} {COLA}"


def test_titulo_y_botones_de_la_maqueta():
    assert cierre.TITULO == "Hay descargas en curso"
    assert cierre.PLAZO == 30  # s hasta ofrecer "Cerrar igualmente" (R18)
