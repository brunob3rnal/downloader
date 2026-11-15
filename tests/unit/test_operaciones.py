"""Código compartido entre la CLI y la aplicación (specs/002-desktop-app, T024; research R5).

Los mensajes de error son los mismos que imprime hoy la CLI: la CLI los convierte en
`error: …` con el código 2, y la ventana los mostrará tal cual (contracts/api-local.md).
"""

import subprocess
import sys

import pytest

from downloader import config, operaciones, publish
from downloader.models import Calidad, Estado
from downloader.store import EjecucionEnCurso, Store
from tests.support import fake_extractor


# --- validaciones ----------------------------------------------------------------------------


def test_validar_calidad():
    assert operaciones.validar_calidad(None, Calidad.P720) is Calidad.P720
    assert operaciones.validar_calidad("audio", Calidad.MEJOR) is Calidad.AUDIO
    with pytest.raises(ValueError) as error:
        operaciones.validar_calidad("480p", Calidad.MEJOR)
    assert str(error.value) == "calidad desconocida «480p»; admitidas: mejor, 1080p, 720p, audio"


def test_validar_navegador():
    assert operaciones.validar_navegador(None) is None
    assert operaciones.validar_navegador(" Firefox ") == "firefox"
    with pytest.raises(ValueError) as error:
        operaciones.validar_navegador("netscape")
    assert str(error.value) == (
        "navegador no admitido en --cookies-de: «netscape»; admitidos: brave, chrome, chromium, "
        "edge, firefox, opera, safari, vivaldi, whale"
    )


def test_validar_carpeta(tmp_path):
    assert operaciones.validar_carpeta(None, tmp_path) == tmp_path.resolve()
    assert operaciones.validar_carpeta(tmp_path, tmp_path / "otra") == tmp_path.resolve()
    inexistente = tmp_path / "no-existe"
    with pytest.raises(ValueError) as error:
        operaciones.validar_carpeta(inexistente, tmp_path)
    assert str(error.value) == f"la carpeta de destino {inexistente.resolve()} no existe"


def test_validar_carpeta_sin_permiso_usa_publish_escribible(tmp_path, monkeypatch):
    # A través del módulo: así lo sustituye `tests/integration/test_cli_agregar.py` (001).
    monkeypatch.setattr(publish, "escribible", lambda carpeta: False)
    with pytest.raises(ValueError) as error:
        operaciones.validar_carpeta(tmp_path, tmp_path)
    assert str(error.value) == (
        f"sin permiso de escritura en la carpeta de destino {tmp_path.resolve()}"
    )


# --- agregar ---------------------------------------------------------------------------------


def test_agregar_urls(app_home, tmp_path):
    store = Store.abrir(app_home)
    try:
        primeros = operaciones.agregar_urls(store, ["https://a.ejemplo/1", "https://a.ejemplo/2"],
                                            Calidad.P720, tmp_path, "firefox")
        assert [r.accion for r in primeros] == ["agregada", "agregada"]
        assert primeros[0].descarga.navegador_cookies == "firefox"
        assert primeros[0].descarga.carpeta_destino == str(tmp_path)

        store.transicion(2, Estado.DOWNLOADING)
        store.transicion(2, Estado.FAILED, ultimo_error="HTTP 403")
        segundos = operaciones.agregar_urls(store, ["https://a.ejemplo/1", "https://a.ejemplo/2"],
                                            Calidad.P720, tmp_path, None)
        assert [r.accion for r in segundos] == ["ya_en_cola", "reencolada"]
    finally:
        store.cerrar()


# --- ejecutar_cola ---------------------------------------------------------------------------


def test_ejecutar_cola_con_la_cola_vacia(app_home):
    store = Store.abrir(app_home)
    try:
        resultado = operaciones.ejecutar_cola(store, config.Configuracion(), origen="cli")
    finally:
        store.cerrar()
    assert resultado.completadas == [] and resultado.fallidas == []


def test_ejecutar_cola_con_otro_procesador_lanza_ejecucion_en_curso(app_home):
    otro = Store.abrir(app_home)
    bloqueo = otro.bloquear_ejecucion()
    store = Store.abrir(app_home)
    try:
        with pytest.raises(EjecucionEnCurso):
            operaciones.ejecutar_cola(store, config.Configuracion(), origen="cli")
    finally:
        bloqueo.liberar()
        otro.cerrar()
        store.cerrar()


@pytest.mark.ffmpeg
def test_ejecutar_cola_descarga_y_devuelve_el_resultado(app_home, destino, servidor, medios,
                                                        monkeypatch, esperas_cortas):
    fake_extractor.instalar(monkeypatch)
    servidor.registrar("v1.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v1", "v1.mp4")
    ajustes = config.Configuracion(sondas_red=[f"127.0.0.1:{servidor.puerto}"])
    store = Store.abrir(app_home)
    try:
        store.agregar(servidor.url("watch/v1"), Calidad.MEJOR, str(destino))
        store.agregar(servidor.url("watch/privado"), Calidad.MEJOR, str(destino))
        resultado = operaciones.ejecutar_cola(store, ajustes, origen="cli")
        assert resultado.completadas == [1] and resultado.fallidas == [2]
        # Al terminar, el bloqueo queda libre.
        otro = Store.abrir(app_home)
        try:
            otro.bloquear_ejecucion().liberar()
        finally:
            otro.cerrar()
    finally:
        store.cerrar()


def test_importar_operaciones_no_carga_yt_dlp():
    codigo = ("import sys, downloader.operaciones\n"
              "sys.exit(1 if any(m == 'yt_dlp' or m.startswith('yt_dlp.') for m in sys.modules)"
              " else 0)")
    assert subprocess.run([sys.executable, "-c", codigo], timeout=60).returncode == 0
