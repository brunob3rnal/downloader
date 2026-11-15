"""`POST api/iniciar`: el trabajador en un subproceso con el mismo código que `ejecutar`
(specs/002-desktop-app, US1, FR-006, FR-007, FR-012; research R5)."""

import pytest

from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta


@pytest.fixture
def preparado(app_local, servidor, medios, destino):
    (app_local.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    for nombre in ("a.mp4", "b.mp4", "c.mp4"):
        servidor.registrar(nombre, medios.datos_mp4, tipo_contenido="video/mp4",
                           velocidad=500_000)

    def agregar(*nombres):
        texto = "\n".join(servidor.url(n) for n in nombres)
        respuesta = app_local.cliente.post("api/agregar", {
            "texto": texto, "calidad": "mejor", "carpeta": str(destino), "navegador": None})
        assert respuesta.estado == 200, respuesta.cuerpo

    app_local.agregar = agregar
    return app_local


def _estado(app):
    return app.cliente.get("api/estado").json()


def _termino(app, ejecucion):
    def comprobar():
        datos = _estado(app)
        ultima = datos["ultima_ejecucion"]
        if datos["procesador"]["estado"] == "ninguno" and ultima and (
                ultima["ejecucion"] == ejecucion):
            return ultima
        return None

    return hasta(comprobar, 120, "el final de la ejecución")


def test_iniciar_procesa_la_cola_y_recoge_lo_agregado_despues(preparado, destino):
    app = preparado
    app.agregar("a.mp4", "b.mp4")
    respuesta = app.cliente.post("api/iniciar")
    assert respuesta.estado == 202
    ejecucion = respuesta.json()["ejecucion"]

    def con_cifras():
        datos = _estado(app)
        procesador = datos["procesador"]
        filas = [f for f in datos["cola"]["filas"] if f["velocidad"] and f["eta"] is not None]
        if procesador["estado"] == "en_marcha" and filas:
            return datos
        return None

    datos = hasta(con_cifras, 60, "velocidad y tiempo estimado en api/estado")
    assert datos["procesador"]["origen"] == "app"
    assert datos["procesador"]["puede_iniciar"] is False
    assert datos["en_directo"] is True

    app.agregar("c.mp4")  # durante el procesamiento: entra en la misma ejecución (US1, 4)
    ultima = _termino(app, ejecucion)
    assert (ultima["completadas"], ultima["codigo"], ultima["error"]) == (3, 0, None)
    assert len([p for p in destino.iterdir() if p.is_file()]) == 3
    assert _estado(app)["procesador"]["puede_iniciar"] is True


def test_con_una_fallida_el_codigo_es_1(preparado, servidor):
    app = preparado
    app.agregar("a.mp4", "no-existe.mp4")
    ejecucion = app.cliente.post("api/iniciar").json()["ejecucion"]
    ultima = _termino(app, ejecucion)
    assert ultima["codigo"] == 1 and ultima["completadas"] == 1
    assert [f["id"] for f in ultima["fallidas"]] == [2]
    assert ultima["fallidas"][0]["causa"]


def test_sin_ffmpeg_es_fatal_con_el_mensaje_de_ejecutar(preparado, monkeypatch, tmp_path):
    from downloader import operaciones

    app = preparado
    app.agregar("a.mp4")
    vacio = tmp_path / "bin-vacio"
    vacio.mkdir()
    monkeypatch.setenv("PATH", str(vacio))  # lo hereda el subproceso
    mensaje = operaciones.ffmpeg_ausente()
    ejecucion = app.cliente.post("api/iniciar").json()["ejecucion"]
    ultima = _termino(app, ejecucion)
    assert ultima["codigo"] == 3
    assert ultima["error"] == {"tipo": "fatal", "mensaje": mensaje}


def test_iniciar_con_un_procesador_en_marcha_da_409(preparado):
    app = preparado
    app.agregar("a.mp4", "b.mp4")
    assert app.cliente.post("api/iniciar").estado == 202
    respuesta = app.cliente.post("api/iniciar")
    assert respuesta.estado == 409 and respuesta.json()["motivo"]
    hasta(lambda: _estado(app)["procesador"]["estado"] == "ninguno", 120, "el final")
