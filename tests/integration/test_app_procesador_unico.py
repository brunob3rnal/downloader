"""Un único procesador y el modo observación (specs/002-desktop-app, US3, FR-008, FR-009,
SC-003; research R6; tasks T081).

`downloader ejecutar` en un subproceso, como desde una terminal, y la aplicación con su
trabajador real. La ventana observa un `ejecutar` de la CLI sin lanzar otro ni detenerlo, deja
de mostrarlo en 2 s cuando termina, y nunca hay dos procesadores de la misma cola.
"""

import os
import subprocess
import sys
import time

import pytest

from downloader import archivos_json
from downloader.app import api as api_app
from downloader.app import resultado
from downloader.store import EjecucionEnCurso, Store
from tests.integration.test_ctrl_c import SALIDA_TRAS_INTERRUPCION, interrumpir
from tests.integration.test_interrupcion import ejecutar
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta

LENTO = "lento.mp4"
MOTIVO_CLI = "Este procesamiento se lanzó desde la terminal; deténgalo allí con Ctrl+C."
OBSERVANDO = {"estado": "en_marcha", "origen": "cli", "puede_iniciar": False,
              "puede_detener": False, "motivo": MOTIVO_CLI, "terminando": None}
NINGUNO = {"estado": "ninguno", "origen": None, "puede_iniciar": True, "puede_detener": False,
           "motivo": None, "terminando": None}
OTRO_EN_MARCHA = b"Ya hay otro `downloader ejecutar` en marcha"
RETIRADA_MAXIMA = 2  # s para dejar de mostrar un procesador que ya no existe (US3, Edge Cases)
INTERRUMPIDO, OCUPADO = 130, 2
RONDAS = 20


def _preparar(app, servidor):
    (app.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")


def _agregar(app, url, destino):
    respuesta = app.cliente.post("api/agregar", {
        "texto": url, "calidad": "mejor", "carpeta": str(destino), "navegador": None})
    assert respuesta.estado == 200, respuesta.cuerpo


@pytest.fixture
def cola(app_local, servidor, destino):
    """La aplicación y una descarga en cola que no termina durante el test: 50 MB a 200 kB/s."""
    _preparar(app_local, servidor)
    servidor.registrar(LENTO, bytes(50_000_000), tipo_contenido="video/mp4", velocidad=200_000)
    _agregar(app_local, servidor.url(LENTO), destino)
    return app_local


@pytest.fixture
def cli(app_local):
    """Lanza `downloader ejecutar` como desde una terminal; ninguno queda vivo tras el test."""
    lanzados = []

    def lanzar():
        proceso = ejecutar(app_local.home)
        lanzados.append(proceso)
        return proceso

    yield lanzar
    for proceso in lanzados:
        if proceso.poll() is None:
            proceso.kill()
        proceso.communicate()


def _estado(app):
    return app.cliente.get("api/estado").json()


def _procesador(app):
    return _estado(app)["procesador"]


def _parcial(destino):
    trabajo = destino / ".downloader-tmp" / "1"
    parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
    return parciales[0].stat().st_size if parciales else 0


def _iniciar(app):
    """`POST api/iniciar` y espera a que el trabajador de la aplicación se anuncie."""
    respuesta = app.cliente.post("api/iniciar")
    assert respuesta.estado == 202, respuesta.cuerpo
    hasta(lambda: _procesador(app)["estado"] == "en_marcha", 60, "que el trabajador arranque")
    return respuesta.json()["ejecucion"]


def _detener(app):
    """`POST api/detener` y espera a que el trabajador de la aplicación termine."""
    proceso = app.servidor.api.lanzamiento.proceso
    assert app.cliente.post("api/detener").estado == 202
    assert proceso.wait(timeout=60) == INTERRUMPIDO


def _interrumpir(proceso):
    """Ctrl+C al `ejecutar` de la CLI; devuelve su salida de errores."""
    interrumpir(proceso)
    _, errores = proceso.communicate(timeout=SALIDA_TRAS_INTERRUPCION + 15)
    assert proceso.returncode == INTERRUMPIDO, errores
    return errores


def _sin_procesador(app):
    datos = _estado(app)
    return datos if datos["procesador"]["estado"] == "ninguno" else None


def test_la_ventana_observa_un_ejecutar_de_la_cli(cola, cli):
    """US3, escenarios 1, 2 y 5: lo muestra en directo con su origen y no lo toca."""
    app = cola
    assert api_app.MOTIVO_CLI == MOTIVO_CLI  # el texto exacto de contracts/api-local.md
    proceso = cli()

    def en_directo():
        datos = _estado(app)
        fila = datos["cola"]["filas"][0]
        return datos if datos["procesador"]["estado"] == "en_marcha" and fila["velocidad"] else None

    datos = hasta(en_directo, 60, "la cola en directo")
    assert datos["procesador"] == OBSERVANDO
    assert datos["en_directo"] is True and datos["en_linea"] is True
    fila = datos["cola"]["filas"][0]
    assert fila["estado"] == "downloading" and "MB/s" in fila["detalle"]

    for orden in ("api/iniciar", "api/detener"):  # FR-008, FR-009
        respuesta = app.cliente.post(orden)
        assert (respuesta.estado, respuesta.json()) == (409, {"motivo": MOTIVO_CLI}), orden
    assert app.servidor.api.lanzamiento is None  # no se ha lanzado ningún trabajador
    assert not (app.home / "parada.json").exists()  # ni se ha pedido ninguna parada
    assert proceso.poll() is None  # el `ejecutar` de la terminal sigue a lo suyo
    _interrumpir(proceso)


def test_con_la_aplicacion_procesando_ejecutar_sale_con_2(cola, cli):
    app = cola
    _iniciar(app)
    anunciado = archivos_json.leer(app.home / "procesador.json")
    assert anunciado["origen"] == "app"

    proceso = cli()
    salida, errores = proceso.communicate(timeout=60)
    assert proceso.returncode == OCUPADO, errores
    assert OTRO_EN_MARCHA in errores
    assert salida == b""  # no procesa nada
    assert archivos_json.leer(app.home / "procesador.json") == anunciado
    procesador = _procesador(app)
    assert (procesador["estado"], procesador["origen"], procesador["puede_detener"]) == (
        "en_marcha", "app", True)
    _detener(app)


def test_cuando_el_ejecutar_de_la_cli_termina_deja_de_verse_en_2_s(app_local, cli, servidor,
                                                                   medios, destino):
    """US3, escenario 4."""
    app = app_local
    _preparar(app, servidor)
    servidor.registrar("corto.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                       velocidad=500_000)  # unos 4 s
    _agregar(app, servidor.url("corto.mp4"), destino)

    proceso = cli()
    hasta(lambda: _procesador(app) == OBSERVANDO, 60, "que la ventana lo vea en marcha")
    assert proceso.wait(timeout=120) == 0
    datos = hasta(lambda: _sin_procesador(app), RETIRADA_MAXIMA, "que deje de verse en marcha",
                  intervalo=0.05)
    assert datos["procesador"] == NINGUNO
    assert datos["cola"]["filas"][0]["estado"] == "completed"
    assert datos["ultima_ejecucion"] is None  # el resultado es solo de la aplicación (FR-012)


def test_tras_matar_el_ejecutar_de_la_cli_deja_de_verse_y_la_cola_se_recupera(cola, cli,
                                                                              servidor, destino):
    """Edge Cases: procesador terminado de forma abrupta."""
    app = cola
    proceso = cli()
    hasta(lambda: _parcial(destino) >= 200_000 and _procesador(app) == OBSERVANDO, 60,
          "que descargue algo")

    proceso.kill()
    proceso.wait(timeout=30)
    datos = hasta(lambda: _sin_procesador(app), RETIRADA_MAXIMA, "que deje de verse en marcha",
                  intervalo=0.05)
    assert datos["procesador"] == NINGUNO
    fila = datos["cola"]["filas"][0]
    assert (fila["estado"], fila["etiqueta"]) == ("queued", "En cola")  # ya no "Descargando"
    assert (app.home / "procesador.json").exists()  # el cierre abrupto lo deja atrás

    # Al iniciar desde la ventana, la cola se recupera y continúa desde el `.part`.
    guardado = _parcial(destino)
    antes = len(servidor.rangos[LENTO])
    _iniciar(app)
    rangos = hasta(lambda: [r for r in servidor.rangos[LENTO][antes:] if r], 60,
                   "la petición que reanuda")
    assert rangos[0] == f"bytes={guardado}-"
    _detener(app)


def test_si_el_trabajador_no_consigue_el_bloqueo_sale_con_2_sin_dejar_resultado(cola):
    """La carrera aceptada de R6, sin azar: el bloqueo ya está tomado por un procesador que
    todavía no se ha anunciado, así que la ventana no lo ve y lanza su trabajador."""
    app = cola
    store = Store.abrir(app.home)
    bloqueo = store.bloquear_ejecucion()
    try:
        respuesta = app.cliente.post("api/iniciar")
        assert respuesta.estado == 202, respuesta.cuerpo
        proceso = app.servidor.api.lanzamiento.proceso
        assert proceso.wait(timeout=60) == OCUPADO
        assert resultado.leer(app.home) is None  # ni resultado...
        assert not (app.home / "procesador.json").exists()  # ...ni anuncio
        assert _procesador(app) == NINGUNO  # no se queda arrancando
        assert _estado(app)["cola"]["filas"][0]["estado"] == "queued"  # ni ha tocado la cola
    finally:
        bloqueo.liberar()
        store.cerrar()


def _segundos_hasta_anunciarse(orden, home):
    """Lo que tarda un procesador, con la cola vacía, en tomar el bloqueo y anunciarse."""
    (home / "config.toml").write_text('sondas_red = ["127.0.0.1:9"]\n', encoding="utf-8")
    anuncio = home / "procesador.json"
    inicio = time.monotonic()
    proceso = subprocess.Popen(orden, env={**os.environ, "DOWNLOADER_HOME": str(home)},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        # Si termina sin que se llegue a ver el anuncio, vale lo que tardó en terminar: solo
        # hace falta una cifra aproximada para desfasar los arranques.
        while not anuncio.exists() and proceso.poll() is None:
            assert time.monotonic() - inicio < 60, orden
            time.sleep(0.002)
        return time.monotonic() - inicio
    finally:
        proceso.wait(timeout=60)


def test_en_una_carrera_entre_ejecutar_e_iniciar_solo_procesa_uno(cola, cli, tmp_path, capsys):
    """R6, SC-003: decide `ejecutar.lock`. En cada ronda arrancan casi a la vez un `ejecutar`
    de la CLI y el trabajador de la aplicación: solo uno toma el bloqueo y se anuncia.

    Los dos no tardan lo mismo en arrancar, así que se mide cuánto tarda cada uno y se desfasan
    para que lleguen al bloqueo con una diferencia que barre de "gana la aplicación" a "gana la
    CLI". Cada ronda acaba de una de tres formas, todas con un solo procesador:
    - gana la aplicación y el `ejecutar` de la CLI sale con 2;
    - gana la CLI y el trabajador de la aplicación sale con 2, sin resultado (la carrera de R6);
    - la CLI ya se había anunciado y la ventana ni siquiera lanza el trabajador (409, FR-008).
    """
    app = cola
    api = app.servidor.api
    calibracion = tmp_path / "calibracion"
    calibracion.mkdir()
    # Dos veces cada uno y vale la segunda: la primera, en frío, tarda mucho más (en el CI de
    # Windows, 1,2 s frente a 0,3 s) y desfasaría todas las rondas hacia el mismo desenlace.
    for _ in range(2):
        de_cli = _segundos_hasta_anunciarse([sys.executable, "-m", "downloader", "ejecutar"],
                                            calibracion)
        de_app = _segundos_hasta_anunciarse(
            [sys.executable, "-m", "downloader.app.procesador", "--ejecucion", "calibracion"],
            calibracion)
    finales = []
    for ronda in range(RONDAS):
        # Ventaja de la CLI al llegar al bloqueo: de -0,5 a +1,2 veces el arranque del trabajador.
        ventaja = de_app * (-0.5 + 1.7 * ronda / (RONDAS - 1))
        espera = ventaja + de_cli - de_app  # cuánto después de la CLI se pulsa Iniciar
        ultima = resultado.leer(app.home)
        if espera >= 0:
            de_la_cli = cli()
            time.sleep(espera)
            respuesta = app.cliente.post("api/iniciar")
        else:
            respuesta = app.cliente.post("api/iniciar")
            time.sleep(-espera)
            de_la_cli = cli()
        contendientes = [de_la_cli]
        if respuesta.estado == 409:  # la ventana ya veía el `ejecutar`: no lanza nada
            assert respuesta.json() == {"motivo": MOTIVO_CLI}, ronda
            ejecucion = None
        else:
            assert respuesta.estado == 202, (ronda, respuesta.cuerpo)
            ejecucion = respuesta.json()["ejecucion"]
            assert api.lanzamiento.ejecucion == ejecucion
            contendientes.append(api.lanzamiento.proceso)

        anunciados = set()  # (pid, origen, ejecución) de cada `procesador.json` visto

        def resuelta():
            datos = archivos_json.leer(app.home / "procesador.json")
            if datos:
                anunciados.add((datos["pid"], datos["origen"], datos["ejecucion"]))
            vivos = [p for p in contendientes if p.poll() is None]
            return len(vivos) == 1 and anunciados

        hasta(resuelta, 90, f"que en la ronda {ronda} quede un solo procesador", intervalo=0.01)
        gana_la_cli = de_la_cli.poll() is None
        for perdedor in contendientes:
            if perdedor.poll() is not None:
                assert perdedor.returncode == OCUPADO, (ronda, perdedor.returncode)
        # Solo se anunció el ganador (el pid es el del intérprete, no el del lanzador del entorno).
        assert len(anunciados) == 1, (ronda, anunciados)
        assert {(origen, de) for _, origen, de in anunciados} == {
            ("cli", None) if gana_la_cli else ("app", ejecucion)}, ronda
        # Y el bloqueo es suyo: nadie más puede tomarlo.
        store = Store.abrir(app.home)
        try:
            with pytest.raises(EjecucionEnCurso):
                store.bloquear_ejecucion()
        finally:
            store.cerrar()

        if gana_la_cli:
            # El intento de la aplicación, si lo hubo, no deja resultado; la ventana observa.
            assert resultado.leer(app.home) == ultima, ronda
            assert _procesador(app) == OBSERVANDO, ronda
            _interrumpir(de_la_cli)
            finales.append("vista" if ejecucion is None else "cli")
        else:
            assert OTRO_EN_MARCHA in de_la_cli.communicate()[1], ronda
            procesador = _procesador(app)
            assert (procesador["estado"], procesador["origen"], procesador["puede_detener"]) == (
                "en_marcha", "app", True), ronda
            _detener(app)
            finales.append("app")
        hasta(lambda: _procesador(app) == NINGUNO, 30, "que no quede ningún procesador")

    with capsys.disabled():
        print(f"\n[carrera] {RONDAS} rondas ({sys.platform}; la CLI se anuncia en {de_cli:.2f} s "
              f"y el trabajador en {de_app:.2f} s): gana la aplicación en "
              f"{finales.count('app')}, gana la CLI con el trabajador ya lanzado en "
              f"{finales.count('cli')} y la ventana ya la veía en {finales.count('vista')}")
