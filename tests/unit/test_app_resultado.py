"""Resultado de la última ejecución (specs/002-desktop-app, FR-012, contracts/archivos.md)."""

import pytest

from downloader.app import resultado


def test_escribir_y_leer(tmp_path):
    resultado.escribir(
        tmp_path, ejecucion="9f2c4b1a7d3e5f60", inicio="2026-11-15T10:00:00.000+00:00",
        completadas=4,
        fallidas=[{"id": 15, "nombre": "https://x.ejemplo/v?token=abc123",
                   "causa": "HTTP 403 en https://x.ejemplo/v?sig=def456"}],
        codigo=1, error=None,
    )
    datos = resultado.leer(tmp_path)
    assert datos["ejecucion"] == "9f2c4b1a7d3e5f60" and datos["codigo"] == 1
    assert datos["completadas"] == 4 and datos["error"] is None
    assert datos["inicio"] == "2026-11-15T10:00:00.000+00:00" and datos["fin"]
    texto = (tmp_path / resultado.ARCHIVO).read_text(encoding="utf-8")
    assert "abc123" not in texto and "def456" not in texto
    assert datos["fallidas"][0]["id"] == 15


@pytest.mark.parametrize("tipo, codigo", [("fatal", 3), ("detenida", 130), ("inesperado", 1)])
def test_errores(tmp_path, tipo, codigo):
    resultado.escribir(tmp_path, ejecucion="e", inicio="i", completadas=0, fallidas=[],
                       codigo=codigo, error={"tipo": tipo, "mensaje": "pasó ?token=x9"})
    datos = resultado.leer(tmp_path)
    assert datos["error"]["tipo"] == tipo and datos["codigo"] == codigo
    assert "x9" not in datos["error"]["mensaje"]


def test_tipo_de_error_desconocido_se_rechaza(tmp_path):
    with pytest.raises(ValueError):
        resultado.escribir(tmp_path, ejecucion="e", inicio="i", completadas=0, fallidas=[],
                           codigo=1, error={"tipo": "otro", "mensaje": "?"})


def test_abrupto(tmp_path):
    resultado.escribir_abrupto(tmp_path, ejecucion="e1")
    datos = resultado.leer(tmp_path)
    assert datos["ejecucion"] == "e1" and datos["codigo"] is None
    assert datos["error"]["tipo"] == "abrupto"
    assert "terminó de forma inesperada" in datos["error"]["mensaje"]


def test_leer_sin_archivo(tmp_path):
    assert resultado.leer(tmp_path) is None
