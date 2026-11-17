"""La interfaz web de la ventana (specs/002-desktop-app, contracts/ventana.md; research R4).

La disposición se valida a mano frente a la maqueta (quickstart V2); aquí, que se sirve y que
no carga nada de otros orígenes, que la CSP bloquearía.
"""

import re

import pytest

from tests.support.app_local import app_local  # noqa: F401  (fixture)


@pytest.mark.parametrize("archivo, tipo", [("", "text/html"), ("app.js", "text/javascript"),
                                           ("app.css", "text/css")])
def test_se_sirven(app_local, archivo, tipo):
    respuesta = app_local.cliente.get(archivo)
    assert respuesta.estado == 200
    assert respuesta.cabeceras["Content-Type"].startswith(tipo)
    assert respuesta.cuerpo


def test_sin_recursos_de_otros_origenes_ni_codigo_en_linea(app_local):
    html = app_local.cliente.get().cuerpo.decode("utf-8")
    assert '<script src="app.js"' in html and 'href="app.css"' in html
    assert "<style" not in html and not re.search(r"<script(?![^>]*src=)", html)
    assert not re.search(r"\son\w+=", html)  # sin manejadores en línea
    for archivo in ("", "app.js", "app.css"):
        texto = app_local.cliente.get(archivo).cuerpo.decode("utf-8")
        assert not re.search(r"https?://", texto), archivo


def test_la_pagina_tiene_las_partes_de_la_maqueta(app_local):
    html = app_local.cliente.get().cuerpo.decode("utf-8")
    for texto in ("Pegar enlaces, uno por línea", "Calidad", "Guardar en", "Examinar",
                  "Agregar a la cola", "Cola", "Iniciar", "Pausar",
                  "La cola se guarda en este equipo. Si se cierra el programa, continúa al abrirlo."):
        assert texto in html, texto


def test_app_js_envia_la_cabecera_y_usa_etag(app_local):
    js = app_local.cliente.get("app.js").cuerpo.decode("utf-8")
    assert '"X-Gestor": "1"' in js and "If-None-Match" in js and "location.pathname" in js
    assert "en_directo" in js


def test_app_js_pausa_con_api_detener_y_dice_que_se_esta_deteniendo(app_local):
    """Pausar llama a `api/detener` y muestra "Deteniendo…" mientras dura (FR-009; T078)."""
    js = app_local.cliente.get("app.js").cuerpo.decode("utf-8")
    assert '"detener"' in js and "puede_detener" in js
    assert "Deteniendo…" in js and '"deteniendo"' in js
    assert "PAUSAR_DISPONIBLE" not in js  # Pausar ya no está desactivado


def test_app_js_dice_el_origen_y_el_motivo_de_los_botones_inactivos(app_local):
    """Con un `ejecutar` de la terminal, la página lo dice y explica por qué no deja iniciar
    ni pausar, al pasar el cursor y al pulsar (US3; T083)."""
    js = app_local.cliente.get("app.js").cuerpo.decode("utf-8")
    assert "En marcha desde la terminal" in js and 'p.origen === "cli"' in js
    assert "boton.title" in js and "dataset.motivo" in js  # el motivo, al pasar el cursor
    assert 'mostrar($("motivo"), boton.dataset.motivo)' in js  # y al pulsar


def test_el_dialogo_de_cierre_de_la_maqueta(app_local):
    """Título, tres botones, "Deteniendo…" y "Cerrar igualmente" (FR-010, R18; T098)."""
    html = app_local.cliente.get().cuerpo.decode("utf-8")
    for texto in ("Hay descargas en curso", "Seguir en segundo plano", "Pausar y cerrar",
                  "Cancelar", "Cerrar igualmente"):
        assert texto in html, texto
    js = app_local.cliente.get("app.js").cuerpo.decode("utf-8")
    assert "window.mostrarDialogoCierre" in js and '"cerrar"' in js
    for decision in ("segundo_plano", "pausar", "cancelar", "cerrar_igualmente"):
        assert f'"{decision}"' in js, decision
    assert "cerrar_igualmente" in js and "Deteniendo…" in js
