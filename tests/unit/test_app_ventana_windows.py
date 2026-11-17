"""La capa de la ventana en Windows (specs/002-desktop-app, research R2; tasks T061).

Solo Windows (constitución v3.1.0): importa pywebview y su backend WinForms. En ninguno de los
casos se crea una ventana: `create_window` y `start` están parcheados para fallar.
"""

import builtins
import ctypes
import sys

import pytest

pytestmark = pytest.mark.solo_windows


@pytest.fixture
def sin_ventana(monkeypatch):
    import webview

    def prohibido(*argumentos, **opciones):
        raise AssertionError("no debe crearse ninguna ventana")

    monkeypatch.setattr(webview, "create_window", prohibido)
    monkeypatch.setattr(webview, "start", prohibido)


@pytest.fixture
def cuadros(monkeypatch):
    """`MessageBoxW` parcheado: guarda el texto y el título de cada cuadro."""
    mostrados = []

    def falso(hwnd, texto, titulo, tipo):
        mostrados.append((texto, titulo))
        return 1

    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", falso)
    return mostrados


def _registro(app_home):
    return (app_home / "registro" / "ventana.log").read_text(encoding="utf-8")


def test_sin_edgechromium_mensaje_registro_y_codigo_3(monkeypatch, app_home, sin_ventana,
                                                      cuadros):
    import webview.platforms.winforms as winforms

    from downloader.app import principal
    from downloader.app.ventana import webview2

    monkeypatch.setattr(winforms, "renderer", "mshtml")
    assert webview2.comprobar() is not None
    cuadros.clear()

    assert principal.main() == 3
    assert cuadros == [(webview2.TEXTO, "Gestor de descargas")]
    assert "ERROR" in _registro(app_home) and "mshtml" in _registro(app_home)


def test_si_el_backend_no_se_importa_el_mismo_mensaje_con_la_causa(monkeypatch, app_home,
                                                                   sin_ventana, cuadros):
    from downloader.app import principal
    from downloader.app.ventana import webview2

    importar = builtins.__import__

    def roto(nombre, *argumentos, **opciones):
        if nombre.startswith("webview.platforms.winforms") or (
                nombre == "webview.platforms" and "winforms" in (argumentos[2] or ())):
            raise Exception("sin .NET Framework")
        return importar(nombre, *argumentos, **opciones)

    monkeypatch.delitem(sys.modules, "webview.platforms.winforms", raising=False)
    monkeypatch.setattr(builtins, "__import__", roto)

    assert principal.main() == 3
    [(texto, _)] = cuadros
    assert texto.startswith(webview2.TEXTO) and "sin .NET Framework" in texto
    assert "sin .NET Framework" in _registro(app_home)


def test_sin_motor_no_toca_la_cola(monkeypatch, app_home, sin_ventana, cuadros):
    import webview.platforms.winforms as winforms

    from downloader.app import principal

    monkeypatch.setattr(winforms, "renderer", "mshtml")
    assert principal.main() == 3
    assert not (app_home / "cola.db").exists()


def test_la_ventana_fija_los_argumentos_de_webview2_antes_de_arrancar(monkeypatch):
    import webview

    from downloader.app import entorno_webview2
    from downloader.app.ventana import ventana

    vistos = {}
    monkeypatch.delenv(entorno_webview2.VARIABLE, raising=False)
    monkeypatch.setattr(webview, "create_window", lambda *a, **o: _VentanaPywebview())
    monkeypatch.setattr(webview, "start", lambda *a, **o: vistos.update(
        valor=__import__("os").environ.get(entorno_webview2.VARIABLE), opciones=o))
    ventana.crear("http://127.0.0.1:1/x/").mostrar()
    assert vistos["valor"] == " ".join(entorno_webview2.ARGUMENTOS)
    assert vistos["opciones"] == {"gui": "edgechromium", "private_mode": True}


@pytest.mark.pywebview_internals
def test_el_backend_expone_renderer():
    import webview.platforms.winforms as winforms

    assert winforms.renderer in {"edgechromium", "mshtml", "cef"}


# --- el aviso al cerrar (FR-010, R18; tasks T095) ---------------------------------------


class _Evento:
    def __init__(self):
        self.manejadores = []

    def __iadd__(self, manejador):
        self.manejadores.append(manejador)
        return self


class _VentanaPywebview:
    """Lo que `VentanaWebview` usa del objeto de pywebview, sin ventana."""

    def __init__(self):
        self.events = type("Eventos", (), {})()
        self.events.closing = _Evento()
        self.scripts = []
        self.destruida = False

    def evaluate_js(self, codigo):
        self.scripts.append(codigo)

    def destroy(self):
        self.destruida = True
        for manejador in self.events.closing.manejadores:  # como pywebview: avisa al cerrar
            manejador()


@pytest.fixture
def ventana_simulada(monkeypatch, tmp_path):
    import webview

    from downloader.app import api as api_app
    from downloader.app.ventana import ventana

    falsa = _VentanaPywebview()
    monkeypatch.setattr(webview, "create_window", lambda *a, **o: falsa)
    real = ventana.crear("http://127.0.0.1:1/x/")
    real.al_cerrar = api_app.Api(tmp_path, real).al_cerrar_ventana
    return real, falsa


def test_si_hay_que_preguntar_cancela_el_cierre_y_muestra_el_dialogo(ventana_simulada,
                                                                     monkeypatch):
    from downloader.app import cierre
    from tests.support.esperas import hasta

    real, falsa = ventana_simulada
    monkeypatch.setattr(cierre, "decidir", lambda info: cierre.PREGUNTAR)
    (manejador,) = falsa.events.closing.manejadores
    assert manejador() is False
    scripts = hasta(lambda: falsa.scripts, 5, "el diálogo")
    assert scripts[0].startswith("mostrarDialogoCierre(")


def test_si_no_hay_que_preguntar_cierra_sin_llamar_a_nada(ventana_simulada, monkeypatch):
    from downloader.app import cierre

    real, falsa = ventana_simulada
    monkeypatch.setattr(cierre, "decidir", lambda info: cierre.CERRAR)
    (manejador,) = falsa.events.closing.manejadores
    assert manejador() is True
    assert falsa.scripts == []


def test_destruir_desde_la_aplicacion_no_vuelve_a_preguntar(ventana_simulada, monkeypatch):
    from downloader.app import cierre

    real, falsa = ventana_simulada
    preguntas = []
    monkeypatch.setattr(cierre, "decidir", lambda info: preguntas.append(1) or cierre.PREGUNTAR)
    real.destruir()  # "Seguir en segundo plano" o "Pausar y cerrar" ya decididos
    assert falsa.destruida and preguntas == [] and falsa.scripts == []
