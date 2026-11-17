"""El trabajador sobrevive a la ventana y una ventana nueva lo recupera (specs/002-desktop-app,
US4, FR-011, SC-008; research R8; tasks T086).

La ventana es un proceso aparte (`tests/support/ventana_headless.py`), con el mismo arranque
que `main()` salvo la ventana real. Se mata como un cierre forzado y se abre otra.
"""

import time

import psutil
import pytest

from downloader import archivos_json
from tests.support.esperas import hasta
from tests.support.procesos_app import (
    VentanaEnProceso,
    terminar_trabajadores_de,
    trabajadores_de,
)

NOMBRE = "grande.mp4"
RECONEXION_MAXIMA = 5  # s (SC-008)
INTERRUMPIDO = 130


@pytest.fixture
def home(app_home, servidor, destino):
    (app_home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    servidor.registrar(NOMBRE, bytes(50_000_000), tipo_contenido="video/mp4", velocidad=2_000_000)
    yield app_home
    terminar_trabajadores_de(app_home)  # si el test falla a medias


@pytest.fixture
def ventanas(home):
    abiertas = []

    def abrir():
        abiertas.append(VentanaEnProceso(home))
        return abiertas[-1]

    yield abrir
    for ventana in abiertas:
        ventana.matar()


def _parcial(destino):
    trabajo = destino / ".downloader-tmp" / "1"
    parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
    return parciales[0].stat().st_size if parciales else 0


def test_el_trabajador_sigue_sin_la_ventana_y_otra_ventana_lo_recupera(home, ventanas, servidor,
                                                                        destino):
    primera = ventanas()
    respuesta = primera.cliente.post("api/agregar", {
        "texto": servidor.url(NOMBRE), "calidad": "mejor", "carpeta": str(destino),
        "navegador": None})
    assert respuesta.estado == 200, respuesta.cuerpo
    respuesta = primera.cliente.post("api/iniciar")
    assert respuesta.estado == 202, respuesta.cuerpo
    ejecucion = respuesta.json()["ejecucion"]
    hasta(lambda: _parcial(destino) > 500_000, 60, "que empiece a descargar")
    anunciado = archivos_json.leer(home / "procesador.json")
    trabajador = psutil.Process(anunciado["pid"])

    primera.matar()  # cierre forzado de la ventana
    antes = _parcial(destino)
    time.sleep(5)
    assert trabajador.is_running() and trabajador.status() != psutil.STATUS_ZOMBIE
    assert _parcial(destino) > antes  # y sigue descargando

    inicio = time.monotonic()
    segunda = ventanas()

    def reconectada():
        procesador = segunda.estado()["procesador"]
        return procesador if procesador["estado"] == "en_marcha" else None

    procesador = hasta(reconectada, RECONEXION_MAXIMA, "que la ventana nueva lo muestre",
                       intervalo=0.05)
    reconexion = time.monotonic() - inicio
    print(f"\n[reconexión] la ventana nueva lo muestra en {reconexion:.2f} s")
    assert reconexion <= RECONEXION_MAXIMA  # SC-008, contando el arranque de la ventana
    assert procesador == {"estado": "en_marcha", "origen": "app", "puede_iniciar": False,
                          "puede_detener": True, "motivo": None}
    fila = segunda.estado()["cola"]["filas"][0]
    assert fila["estado"] == "downloading"

    # Pausar desde la segunda sesión (FR-011).
    assert segunda.cliente.post("api/detener").estado == 202
    psutil.wait_procs([trabajador], timeout=30)
    assert not trabajador.is_running() or trabajador.status() == psutil.STATUS_ZOMBIE
    datos = hasta(lambda: (d := segunda.estado())["procesador"]["estado"] == "ninguno" and d,
                  10, "que deje de verse en marcha")
    ultima = datos["ultima_ejecucion"]
    assert (ultima["ejecucion"], ultima["codigo"], ultima["error"]["tipo"]) == (
        ejecucion, INTERRUMPIDO, "detenida")
    assert datos["cola"]["filas"][0]["estado"] == "queued"
    assert trabajadores_de(home) == []
