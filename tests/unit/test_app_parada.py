"""La parada ordenada del procesador de la aplicación (specs/002-desktop-app, research R7;
contracts/archivos.md; tasks T074).

`parada.json` y el vigilante que lo consulta en el subproceso. En casi todos los casos la
función que interrumpe está sustituida; la real (`interrumpir_principal`) se prueba en un
proceso aparte, lanzado como el trabajador, para no interrumpir a pytest.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime

import pytest

from downloader.app import parada, procesos
from tests.support.esperas import hasta

RAPIDO = 0.02  # s entre consultas en estos tests


@pytest.fixture
def vigilar(tmp_path):
    """Crea y arranca vigilantes cuya interrupción solo se anota (`llamadas`)."""
    creados = []

    def crear(ejecucion, **opciones):
        llamadas = []
        vigilante = parada.Vigilante(tmp_path, ejecucion,
                                     interrumpir=lambda: llamadas.append(time.monotonic()),
                                     **opciones)
        vigilante.llamadas = llamadas
        creados.append(vigilante)
        vigilante.start()
        return vigilante

    yield crear
    for vigilante in creados:
        vigilante.detener()


def test_pedir_escribe_la_ejecucion_y_la_fecha(tmp_path):
    assert parada.pedir(tmp_path, "9f2c4b1a7d3e5f60") is True
    datos = json.loads((tmp_path / parada.ARCHIVO).read_text(encoding="utf-8"))
    assert parada.ARCHIVO == "parada.json"
    assert set(datos) == {"ejecucion", "pedida"} and datos["ejecucion"] == "9f2c4b1a7d3e5f60"
    assert datetime.fromisoformat(datos["pedida"]).utcoffset().total_seconds() == 0
    assert [p.name for p in tmp_path.iterdir()] == [parada.ARCHIVO]  # sin temporales


def test_con_una_parada_de_su_ejecucion_interrumpe_una_vez_y_la_borra(tmp_path, vigilar):
    parada.pedir(tmp_path, "e1")
    vigilante = vigilar("e1", intervalo=RAPIDO)
    hasta(lambda: vigilante.llamadas, 5, "la interrupción")
    time.sleep(10 * RAPIDO)  # varias consultas más
    assert len(vigilante.llamadas) == 1
    assert not (tmp_path / parada.ARCHIVO).exists()


def test_una_parada_de_otra_ejecucion_no_interrumpe_y_se_borra(tmp_path, vigilar):
    parada.pedir(tmp_path, "antigua")
    vigilante = vigilar("e1", intervalo=RAPIDO)
    hasta(lambda: not (tmp_path / parada.ARCHIVO).exists(), 5, "que se borre la parada ajena")
    time.sleep(5 * RAPIDO)
    assert vigilante.llamadas == []
    parada.pedir(tmp_path, "e1")  # sigue vigilando: la suya sí la obedece
    hasta(lambda: vigilante.llamadas, 5, "la interrupción")


def test_sin_archivo_no_hace_nada(tmp_path, vigilar):
    vigilante = vigilar("e1", intervalo=RAPIDO)
    time.sleep(10 * RAPIDO)
    assert vigilante.llamadas == [] and vigilante.is_alive()
    assert list(tmp_path.iterdir()) == []


def test_consulta_cada_0_25_s(tmp_path, monkeypatch):
    """Entre consulta y consulta espera `INTERVALO`, también antes de la primera. Sin reloj:
    el evento de parada se sustituye por uno que deja pasar tres intervalos."""
    assert parada.INTERVALO == 0.25
    esperas, consultas = [], []

    class TresIntervalos:
        def wait(self, segundos):
            esperas.append((segundos, len(consultas)))  # cuánto espera y con cuántas consultas
            return len(esperas) > 3

        def is_set(self):
            return False

    leer = parada.archivos_json.leer
    monkeypatch.setattr(parada.archivos_json, "leer",
                        lambda ruta: consultas.append(ruta) or leer(ruta))
    vigilante = parada.Vigilante(tmp_path, "e1", interrumpir=lambda: None)
    assert vigilante.intervalo == parada.INTERVALO
    vigilante._parar = TresIntervalos()
    vigilante.run()  # en este hilo
    assert esperas == [(0.25, 0), (0.25, 1), (0.25, 2), (0.25, 3)]
    assert consultas == [tmp_path / parada.ARCHIVO] * 3


def test_tras_interrumpir_deja_de_vigilar(tmp_path, vigilar):
    """Una sola interrupción por ejecución: otra a media limpieza cortaría el guardado."""
    parada.pedir(tmp_path, "e1")
    vigilante = vigilar("e1", intervalo=RAPIDO)
    hasta(lambda: vigilante.llamadas, 5, "la interrupción")
    hasta(lambda: not vigilante.is_alive(), 5, "que el hilo termine")
    parada.pedir(tmp_path, "e1")
    time.sleep(10 * RAPIDO)
    assert len(vigilante.llamadas) == 1


def test_una_parada_a_medio_escribir_se_ignora_hasta_que_esta_entera(tmp_path, vigilar):
    (tmp_path / parada.ARCHIVO).write_text('{"ejecucion": "e', encoding="utf-8")
    vigilante = vigilar("e1", intervalo=RAPIDO)
    time.sleep(5 * RAPIDO)
    assert vigilante.llamadas == [] and (tmp_path / parada.ARCHIVO).exists()
    parada.pedir(tmp_path, "e1")
    hasta(lambda: vigilante.llamadas, 5, "la interrupción")


def test_si_no_puede_borrarla_interrumpe_igual_y_una_sola_vez(tmp_path, vigilar, monkeypatch,
                                                             caplog):
    def sin_permiso(ruta):
        raise PermissionError(13, "en uso por otro proceso")

    monkeypatch.setattr(parada.archivos_json, "borrar", sin_permiso)
    parada.pedir(tmp_path, "e1")
    with caplog.at_level(logging.WARNING, logger="downloader.app"):
        vigilante = vigilar("e1", intervalo=RAPIDO)
        hasta(lambda: vigilante.llamadas, 5, "la interrupción")
        hasta(lambda: not vigilante.is_alive(), 5, "que el hilo termine")
    assert len(vigilante.llamadas) == 1
    assert "parada.json" in caplog.text


def test_detener_termina_el_hilo(tmp_path, vigilar):
    vigilante = vigilar("e1", intervalo=RAPIDO)
    vigilante.detener()
    assert not vigilante.is_alive()
    parada.pedir(tmp_path, "e1")
    time.sleep(5 * RAPIDO)
    assert vigilante.llamadas == []
    vigilante.detener()  # dos veces no falla


def test_el_vigilante_es_un_hilo_demonio(tmp_path):
    assert parada.Vigilante(tmp_path, "e1").daemon is True


# --- la interrupción real (research R7) --------------------------------------------------


def test_asegurar_interrupcion_instala_el_manejador_aunque_estuviera_ignorada():
    anterior = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # heredada así de quien lanza el proceso
        parada.asegurar_interrupcion()
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        signal.signal(signal.SIGINT, anterior)


def test_asegurar_interrupcion_fuera_del_hilo_principal_no_falla():
    errores = []

    def probar():
        try:
            parada.asegurar_interrupcion()
        except Exception as error:  # noqa: BLE001
            errores.append(error)

    hilo = threading.Thread(target=probar)
    hilo.start()
    hilo.join()
    assert errores == []


def _como_el_trabajador(orden, **opciones):
    """Ejecuta `orden` con los indicadores de `procesos.lanzar_trabajador` (research R8)."""
    if sys.platform == "win32":
        opciones["creationflags"] = procesos.CREATE_NO_WINDOW | procesos.CREATE_NEW_PROCESS_GROUP
    else:
        opciones["start_new_session"] = True
    return subprocess.run(orden, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=120, **opciones)


INTERRUMPIDO = textwrap.dedent('''
    import signal
    import sys
    import threading
    import time

    from downloader.app import parada

    if sys.argv[1] == "ignorada":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    parada.asegurar_interrupcion()
    threading.Timer(0.3, parada.interrumpir_principal).start()  # desde otro hilo, como el vigilante
    inicio = time.monotonic()
    try:
        if sys.argv[2] == "sleep":
            time.sleep(30)
        else:  # la espera del trabajador sin red: pasos de 0,25 s (worker.PASO_ESPERA)
            evento = threading.Event()
            while time.monotonic() - inicio < 30:
                evento.wait(0.25)
    except KeyboardInterrupt:
        print(f"{time.monotonic() - inicio:.3f}")
        sys.exit(130)
    sys.exit(1)
''')


@pytest.mark.parametrize("espera", ["sleep", "evento"])
@pytest.mark.parametrize("heredada", ["normal", "ignorada"])
def test_interrumpir_principal_es_un_ctrl_c_en_el_hilo_principal(tmp_path, heredada, espera):
    """Un `KeyboardInterrupt` en el hilo principal que despierta sus esperas, en un proceso sin
    consola y en su propio grupo, como el trabajador (R7, [a verificar en Windows])."""
    guion = tmp_path / "interrumpido.py"
    guion.write_text(INTERRUMPIDO, encoding="utf-8")
    resultado = _como_el_trabajador([sys.executable, str(guion), heredada, espera])
    assert resultado.returncode == 130, resultado.stdout + resultado.stderr
    assert float(resultado.stdout) < 5, resultado.stdout  # no espera los 30 s


# --- el vigilante en el procesador (tasks T077) ------------------------------------------

PROCESADOR = textwrap.dedent('''
    import sys
    import time
    import types

    from downloader import operaciones
    from downloader.app import parada, procesador

    ejecucion, pedida = sys.argv[1], sys.argv[2]


    def cola_larga(store, ajustes, origen, ejecucion=None, **opciones):
        parada.pedir(store.directorio, pedida)  # como `POST api/detener`
        limite = time.monotonic() + 30
        while (store.directorio / parada.ARCHIVO).exists() and time.monotonic() < limite:
            time.sleep(0.05)
        if pedida == ejecucion:
            time.sleep(30)  # la descarga en curso: la parada la interrumpe
        return types.SimpleNamespace(completadas=[], fallidas=[])


    operaciones.ejecutar_cola = cola_larga
    operaciones.ffmpeg_ausente = lambda: None
    sys.exit(procesador.main(["--ejecucion", ejecucion]))
''')


def _procesador(tmp_path, ejecucion, pedida):
    home = tmp_path / "app"
    home.mkdir()
    (home / "config.toml").write_text('sondas_red = ["127.0.0.1:9"]\n', encoding="utf-8")
    guion = tmp_path / "procesador_de_prueba.py"
    guion.write_text(PROCESADOR, encoding="utf-8")
    inicio = time.monotonic()
    resultado = _como_el_trabajador([sys.executable, str(guion), ejecucion, pedida],
                                    env={**os.environ, "DOWNLOADER_HOME": str(home)})
    ultima = json.loads((home / "ultima-ejecucion.json").read_text(encoding="utf-8"))
    return resultado, ultima, home, time.monotonic() - inicio


def test_el_procesador_obedece_la_parada_de_su_ejecucion(tmp_path):
    resultado, ultima, home, duracion = _procesador(tmp_path, "e1", "e1")
    assert resultado.returncode == 130, resultado.stdout + resultado.stderr
    assert duracion < 20  # no espera los 30 s de la descarga
    assert (ultima["ejecucion"], ultima["codigo"]) == ("e1", 130)
    assert ultima["error"] == {"tipo": "detenida", "mensaje": "interrumpido; progreso guardado"}
    assert not (home / "parada.json").exists()


def test_el_procesador_ignora_y_borra_la_parada_de_otra_ejecucion(tmp_path):
    resultado, ultima, home, duracion = _procesador(tmp_path, "e1", "otra")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert duracion < 20
    assert (ultima["codigo"], ultima["error"]) == (0, None)
    assert not (home / "parada.json").exists()
