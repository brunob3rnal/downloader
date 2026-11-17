"""El procesador de la aplicación (specs/002-desktop-app, research R5, R10; FR-012, FR-020)."""

import pytest

from downloader import operaciones
from downloader.app import procesador, resultado
from downloader.store import EjecucionEnCurso


@pytest.fixture
def sin_ffmpeg_que_comprobar(monkeypatch):
    monkeypatch.setattr(operaciones, "ffmpeg_ausente", lambda: None)


def test_excepcion_inesperada(app_home, monkeypatch, sin_ffmpeg_que_comprobar):
    def falla(*argumentos, **opciones):
        raise RuntimeError("boom")

    monkeypatch.setattr(operaciones, "ejecutar_cola", falla)
    codigo = procesador.main(["--ejecucion", "x1"])
    assert codigo != 0
    datos = resultado.leer(app_home)
    assert datos["ejecucion"] == "x1" and datos["error"]["tipo"] == "inesperado"
    assert "RuntimeError: boom" in datos["error"]["mensaje"]
    texto = (app_home / "registro" / "procesador.log").read_text(encoding="utf-8")
    assert "Traceback" in texto and "RuntimeError: boom" in texto


def test_otro_procesador_en_marcha_sale_con_2_sin_resultado(app_home, monkeypatch,
                                                            sin_ffmpeg_que_comprobar):
    def ocupado(*argumentos, **opciones):
        raise EjecucionEnCurso("otro")

    monkeypatch.setattr(operaciones, "ejecutar_cola", ocupado)
    assert procesador.main(["--ejecucion", "x2"]) == 2
    assert resultado.leer(app_home) is None


def test_ctrl_c_es_detenida(app_home, monkeypatch, sin_ffmpeg_que_comprobar):
    def interrumpido(*argumentos, **opciones):
        raise KeyboardInterrupt

    monkeypatch.setattr(operaciones, "ejecutar_cola", interrumpido)
    assert procesador.main(["--ejecucion", "x3"]) == 130
    datos = resultado.leer(app_home)
    assert datos["error"] == {"tipo": "detenida", "mensaje": "interrumpido; progreso guardado"}
    assert datos["codigo"] == 130


def test_sin_ffmpeg_es_fatal_con_el_mensaje_de_la_cli(app_home, monkeypatch):
    monkeypatch.setattr(operaciones, "ffmpeg_ausente", lambda: "no se encuentra ffmpeg")
    assert procesador.main(["--ejecucion", "x4"]) == 3
    datos = resultado.leer(app_home)
    assert datos["error"] == {"tipo": "fatal", "mensaje": "no se encuentra ffmpeg"}


def test_configuracion_invalida_es_fatal_con_codigo_2(app_home, sin_ffmpeg_que_comprobar):
    (app_home / "config.toml").write_text("calidad = '480p'\n", encoding="utf-8")
    assert procesador.main(["--ejecucion", "x5"]) == 2
    datos = resultado.leer(app_home)
    assert datos["error"]["tipo"] == "fatal" and "calidad desconocida" in datos["error"]["mensaje"]


def test_cola_vacia_termina_con_0(app_home, sin_ffmpeg_que_comprobar):
    (app_home / "config.toml").write_text('sondas_red = ["127.0.0.1:9"]\n', encoding="utf-8")
    assert procesador.main(["--ejecucion", "x6"]) == 0
    datos = resultado.leer(app_home)
    assert (datos["codigo"], datos["completadas"], datos["fallidas"], datos["error"]) == (
        0, 0, [], None)


def test_una_parada_entregada_al_terminar_tambien_es_detenida(app_home, monkeypatch,
                                                              sin_ffmpeg_que_comprobar):
    """La interrupción del vigilante puede llegar cuando `ejecutar_cola` ya ha vuelto: también
    entonces queda un resultado `detenida` con 130, no una excepción sin resultado (R7)."""
    from types import SimpleNamespace

    from downloader.store import Store

    def interrumpida(self, id_):
        raise KeyboardInterrupt

    (app_home / "config.toml").write_text('sondas_red = ["127.0.0.1:9"]\n', encoding="utf-8")
    monkeypatch.setattr(operaciones, "ejecutar_cola",
                        lambda *a, **k: SimpleNamespace(completadas=[], fallidas=[7]))
    monkeypatch.setattr(Store, "obtener", interrumpida)
    assert procesador.main(["--ejecucion", "x7"]) == 130
    datos = resultado.leer(app_home)
    assert datos["error"] == {"tipo": "detenida", "mensaje": "interrumpido; progreso guardado"}
    assert datos["codigo"] == 130
