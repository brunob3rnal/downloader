"""Pausar: la parada ordenada del procesador de la aplicación, equivalente a Ctrl+C
(specs/002-desktop-app, US2, FR-009, SC-004; research R7; tasks T075).

Una descarga de 50 MB a 2 MB/s con el trabajador real, en un subproceso. Cada caso mide lo que
tarda el proceso en terminar desde `POST api/detener` y lo escribe en la salida, para anotar
en research R7 las cifras del CI de los dos sistemas.
"""

import json
import subprocess
import sys
import time

import psutil
import pytest

from downloader.app import api as api_app
from downloader.store import Store
from tests.integration.test_ctrl_c import SALIDA_TRAS_INTERRUPCION, interrumpir
from tests.integration.test_interrupcion import ejecutar
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta

NOMBRE = "grande.mp4"
TAMANO = 50_000_000
VELOCIDAD = 2_000_000  # bytes/s
ESTANCADA = 3_000_000  # bytes que llegan antes de que la conexión se quede sin datos
PARADA_MAXIMA = 10  # s (SC-004)
PARADA_ESTANCADA_EN_WINDOWS = 20  # s; SC-004 enmendado el 2026-11-17 (research R7)
INTERRUMPIDO = 130
DETENIDA = {"tipo": "detenida", "mensaje": "interrumpido; progreso guardado"}


@pytest.fixture
def cola(app_local, servidor, destino):
    """La aplicación con una descarga de 50 MB a 2 MB/s en cola."""
    (app_local.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    servidor.registrar(NOMBRE, bytes(TAMANO), tipo_contenido="video/mp4", velocidad=VELOCIDAD)
    respuesta = app_local.cliente.post("api/agregar", {
        "texto": servidor.url(NOMBRE), "calidad": "mejor", "carpeta": str(destino),
        "navegador": None})
    assert respuesta.estado == 200, respuesta.cuerpo
    return app_local


def _estado(app):
    return app.cliente.get("api/estado").json()


def _fila(app):
    return _estado(app)["cola"]["filas"][0]


def _parcial(destino):
    """Bytes del `.part` de la descarga (0 si aún no existe)."""
    trabajo = destino / ".downloader-tmp" / "1"
    parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
    return parciales[0].stat().st_size if parciales else 0


def _iniciar(app):
    respuesta = app.cliente.post("api/iniciar")
    assert respuesta.estado == 202, respuesta.cuerpo
    return respuesta.json()["ejecucion"]


def _detener(app, plazo=60):
    """`POST api/detener` y espera a que el trabajador termine.

    Devuelve los segundos que tarda el proceso en terminar y el `api/estado` posterior.
    Mientras el proceso vive, la ventana solo ve `deteniendo` (FR-009).
    """
    proceso = app.servidor.api.lanzamiento.proceso
    inicio = time.monotonic()
    respuesta = app.cliente.post("api/detener")
    assert respuesta.estado == 202, respuesta.cuerpo
    vistos = []
    while proceso.poll() is None:
        assert time.monotonic() - inicio < plazo, f"sigue vivo tras {plazo} s: {vistos[-5:]}"
        vistos.append(_estado(app)["procesador"]["estado"])
        time.sleep(0.02)
    duracion = time.monotonic() - inicio
    assert proceso.returncode == INTERRUMPIDO
    # Una consulta puede cruzarse con el final del proceso; antes, siempre `deteniendo`.
    assert set(vistos) <= {"deteniendo", "ninguno"}, vistos
    if "ninguno" in vistos:
        assert "deteniendo" not in vistos[vistos.index("ninguno"):], vistos
    datos = _estado(app)
    procesador = datos["procesador"]
    assert (procesador["estado"], procesador["puede_iniciar"]) == ("ninguno", True)
    return duracion, datos


def _comprobar_detenida(app, datos, ejecucion):
    """El resultado dice `detenida` con 130 y la descarga vuelve a la cola (FR-009)."""
    ultima = datos["ultima_ejecucion"]
    assert (ultima["ejecucion"], ultima["codigo"], ultima["error"]) == (
        ejecucion, INTERRUMPIDO, DETENIDA)
    assert datos["cola"]["filas"][0]["estado"] == "queued"
    assert not (app.home / "parada.json").exists()
    assert not (app.home / "procesador.json").exists()


def _anotar(capsys, record_property, caso, duracion):
    """La duración, a la salida del test: es la cifra que se anota en research R7."""
    record_property(f"parada_{caso}_s", round(duracion, 2))
    with capsys.disabled():
        print(f"\n[parada] {caso} ({sys.platform}): {duracion:.2f} s")


def test_parada_con_datos_llegando_y_reanudar_desde_la_aplicacion(cola, servidor, destino,
                                                                  capsys, record_property):
    app = cola
    ejecucion = _iniciar(app)
    hasta(lambda: (_fila(app)["porcentaje"] or 0) >= 30, 120, "el 30 % descargado")

    duracion, datos = _detener(app)
    _anotar(capsys, record_property, "con_datos", duracion)
    assert duracion <= PARADA_MAXIMA  # SC-004
    _comprobar_detenida(app, datos, ejecucion)
    guardado = _parcial(destino)
    assert guardado > 0
    assert Store.leer(app.home)[0].bytes_descargados > 0

    # Iniciar otra vez continúa desde el `.part`: no empieza de cero.
    antes = len(servidor.rangos[NOMBRE])
    segunda = _iniciar(app)
    assert segunda != ejecucion
    rangos = hasta(lambda: [r for r in servidor.rangos[NOMBRE][antes:] if r], 60,
                   "la petición que reanuda")
    assert rangos[0] == f"bytes={guardado}-"
    hasta(lambda: _parcial(destino) > guardado, 60, "que siga descargando")
    _, datos = _detener(app)
    _comprobar_detenida(app, datos, segunda)


def test_parada_esperando_la_red(cola, servidor, destino, capsys, record_property):
    """Con el trabajador en `waiting_network` (R7: la interrupción despierta sus esperas)."""
    app = cola
    ejecucion = _iniciar(app)
    hasta(lambda: _parcial(destino) >= 1_000_000, 120, "que empiece a descargar")
    servidor.detener()
    hasta(lambda: _fila(app)["estado"] == "waiting_network", 120, "la espera de la red")
    guardado = _parcial(destino)

    duracion, datos = _detener(app)
    _anotar(capsys, record_property, "esperando_red", duracion)
    assert duracion <= PARADA_MAXIMA  # SC-004
    _comprobar_detenida(app, datos, ejecucion)
    assert _parcial(destino) >= guardado


def test_parada_con_la_conexion_estancada(cola, servidor, destino, capsys, record_property):
    """Sin marcador y con un límite por sistema. En Windows una señal no interrumpe una lectura
    de socket bloqueada, que vuelve con su tiempo de espera (15 s): SC-004, enmendado, admite
    ahí hasta 20 s (research R7)."""
    app = cola
    # Toda petición al archivo, también los sondeos del extractor, recibe 3 MB y se queda sin datos.
    servidor.programar(NOMBRE, pausar_tras_bytes=ESTANCADA, veces=12)
    ejecucion = _iniciar(app)
    hasta(lambda: _parcial(destino) >= 1_000_000, 120, "los primeros bytes")
    time.sleep(2)  # el resto de los 3 MB ya ha llegado: el trabajador espera datos que no llegan

    duracion, datos = _detener(app, plazo=90)
    _anotar(capsys, record_property, "estancada", duracion)
    limite = PARADA_ESTANCADA_EN_WINDOWS if sys.platform == "win32" else PARADA_MAXIMA
    assert duracion <= limite
    _comprobar_detenida(app, datos, ejecucion)
    assert _parcial(destino) == ESTANCADA  # todo lo recibido queda en el `.part`


def test_reanudar_con_la_cli_continua_desde_el_progreso_guardado(cola, servidor, destino):
    app = cola
    _iniciar(app)
    hasta(lambda: _parcial(destino) >= 2_000_000, 120, "2 MB descargados")
    _detener(app)
    guardado = _parcial(destino)
    antes = len(servidor.rangos[NOMBRE])

    proceso = ejecutar(app.home)  # `downloader ejecutar`, como desde una terminal
    try:
        rangos = hasta(lambda: [r for r in servidor.rangos[NOMBRE][antes:] if r], 60,
                       "la petición que reanuda")
        assert rangos[0] == f"bytes={guardado}-"
        hasta(lambda: _parcial(destino) > guardado, 60, "que siga descargando")
        assert proceso.poll() is None
    finally:
        if proceso.poll() is None:
            interrumpir(proceso)
        try:
            _, errores = proceso.communicate(timeout=SALIDA_TRAS_INTERRUPCION + 15)
        except subprocess.TimeoutExpired:
            proceso.kill()
            _, errores = proceso.communicate()
    assert proceso.returncode == INTERRUMPIDO, errores


def test_detener_repetido_y_sin_procesador(app_local):
    """Con un procesador simulado, para que `deteniendo` dure lo que el test necesita."""
    app = app_local
    respuesta = app.cliente.post("api/detener")
    assert (respuesta.estado, respuesta.json()) == (409, {"motivo": api_app.MOTIVO_NINGUNO})

    simulado = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        (app.home / "procesador.json").write_text(json.dumps({
            "pid": simulado.pid, "creado": psutil.Process(simulado.pid).create_time(),
            "origen": "app", "ejecucion": "e1", "inicio": "2026-11-17T10:00:00.000+00:00",
        }), encoding="utf-8")
        assert _estado(app)["procesador"]["puede_detener"] is True

        assert app.cliente.post("api/detener").estado == 202
        pedida = json.loads((app.home / "parada.json").read_text(encoding="utf-8"))
        assert pedida["ejecucion"] == "e1"
        deteniendo = {"estado": "deteniendo", "origen": "app", "puede_iniciar": False,
                      "puede_detener": False, "motivo": api_app.MOTIVO_DETENIENDO, "terminando": None}
        assert _estado(app)["procesador"] == deteniendo

        (app.home / "parada.json").unlink()  # el vigilante ya la recogió
        assert app.cliente.post("api/detener").estado == 202  # repetido: sin efecto
        assert not (app.home / "parada.json").exists()
        assert _estado(app)["procesador"] == deteniendo
    finally:
        simulado.kill()
        simulado.wait()
    hasta(lambda: _estado(app)["procesador"]["estado"] == "ninguno", 5,
          "que deje de verse en marcha")
    assert app.cliente.post("api/detener").estado == 409
