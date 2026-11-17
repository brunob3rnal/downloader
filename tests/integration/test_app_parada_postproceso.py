"""Pausar mientras ffmpeg une audio y vídeo o ffprobe verifica el archivo (specs/002-desktop-app,
SC-004 enmendado; research R7).

En POSIX la interrupción llega al hilo principal mientras espera al programa y lo termina; en
Windows, Python no puede interrumpir esa espera y la parada llega cuando el programa acaba. El
test mide las dos cosas en los dos sistemas: cuánto tarda Pausar y cuánto le quedaba al
programa. Para que la unión y la verificación duren lo bastante como para pulsar Pausar en
medio, las pistas son las de prueba repetidas hasta unos 300 MB (sin recodificar).

Después de la parada, Iniciar termina la descarga sin volver a pedir las pistas: no se pierde
progreso.
"""

import subprocess
import sys
import time

import psutil
import pytest

from tests.integration.test_directo_drm import registrar_manifiesto
from tests.support import dash
from tests.support.app_local import app_local  # noqa: F401  (fixture)
from tests.support.esperas import hasta

REPETICIONES = 150  # 10 s de prueba x 150: unos 285 MB de vídeo y 24 MB de audio
PARADA_MAXIMA = 10  # s (SC-004)
PARADA_MAXIMA_EN_WINDOWS = 20  # s; por encima, el usuario decide (research R7)
INTERRUMPIDO = 130
TIPO_MPD = "application/dash+xml"


def _repetir(origen, destino, veces):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", str(veces - 1), "-i",
                    str(origen), "-c", "copy", "-movflags", "+faststart", str(destino)],
                   check=True)
    return destino


def _cronometrar(orden):
    inicio = time.monotonic()
    subprocess.run(orden, check=True, capture_output=True)
    return time.monotonic() - inicio


@pytest.fixture(scope="module")
def pistas_grandes(pistas, tmp_path_factory):
    """Pistas de unos 300 MB y lo que tardan en unirse y verificarse sin interrupción."""
    directorio = tmp_path_factory.mktemp("pistas_grandes")
    (directorio / "v.mp4").write_bytes(pistas.video)
    (directorio / "a.m4a").write_bytes(pistas.audio)
    video = _repetir(directorio / "v.mp4", directorio / "video.mp4", REPETICIONES)
    audio = _repetir(directorio / "a.m4a", directorio / "audio.m4a", REPETICIONES)
    unido = directorio / "unido.mp4"
    union = _cronometrar(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
                          "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", str(unido)])
    verificacion = _cronometrar(["ffprobe", "-v", "error", "-count_packets", "-show_entries",
                                 "format=duration:stream=nb_read_packets", "-of", "json",
                                 str(unido)])
    megas = unido.stat().st_size / 1e6
    medidas = (f"{megas:.0f} MB; unión sin interrumpir {union:.2f} s ({megas / union:.0f} MB/s); "
               f"verificación {verificacion:.2f} s ({megas / verificacion:.0f} MB/s)")
    return {"video": video.read_bytes(), "audio": audio.read_bytes(), "medidas": medidas}


def _es(caso, proceso):
    """Si `proceso` es la unión de yt-dlp o la verificación de `publish` (no un `-version`)."""
    try:
        nombre, orden = proceso.name().lower(), proceso.cmdline()
    except psutil.Error:
        return False
    if caso == "union":
        return nombre.startswith("ffmpeg") and orden.count("-i") >= 2 and "-map" in orden
    return nombre.startswith("ffprobe") and "-count_packets" in orden


def _buscar(trabajador, caso, plazo=180):
    """El proceso de ffmpeg o ffprobe del caso, en cuanto aparece entre los del trabajador."""
    raiz = psutil.Process(trabajador.pid)
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        assert trabajador.poll() is None, f"el trabajador terminó sin llegar a la {caso}"
        try:
            hijos = raiz.children(recursive=True)
        except psutil.Error:
            hijos = []
        for hijo in hijos:
            if _es(caso, hijo):
                return hijo
        time.sleep(0.005)
    raise AssertionError(f"no se llegó a la {caso} en {plazo} s")


@pytest.mark.parametrize("caso", ["union", "verificacion"])
def test_pausar_durante_la_union_o_la_verificacion(app_local, servidor, destino, pistas_grandes,
                                                   caso, capsys, record_property):
    app = app_local
    (app.home / "config.toml").write_text(
        f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n', encoding="utf-8")
    manifiesto = registrar_manifiesto(
        servidor, "grande", "manifiesto.mpd",
        dash.mpd(dash.periodo(
            dash.conjunto(dash.VIDEO, dash.representacion("video", "video.mp4", dash.VIDEO)),
            dash.conjunto(dash.AUDIO, dash.representacion("audio", "audio.m4a", dash.AUDIO)))),
        TIPO_MPD, {"video.mp4": pistas_grandes["video"], "audio.m4a": pistas_grandes["audio"]},
        pagina=False)
    respuesta = app.cliente.post("api/agregar", {
        "texto": servidor.url(manifiesto), "calidad": "mejor", "carpeta": str(destino),
        "navegador": None})
    assert respuesta.estado == 200, respuesta.cuerpo
    assert app.cliente.post("api/iniciar").estado == 202
    trabajador = app.servidor.api.lanzamiento.proceso

    programa = _buscar(trabajador, caso)
    inicio = time.monotonic()
    respuesta = app.cliente.post("api/detener")
    assert respuesta.estado == 202, respuesta.cuerpo
    estados, fin_programa = set(), None
    while trabajador.poll() is None:
        if fin_programa is None and not programa.is_running():
            fin_programa = time.monotonic() - inicio
        estados.add(app.cliente.get("api/estado").json()["procesador"]["estado"])
        assert time.monotonic() - inicio < 120, "Pausar no terminó en 120 s"
        time.sleep(0.02)
    duracion = time.monotonic() - inicio
    # Si el programa acaba antes de la siguiente consulta del vigilante (0,25 s), la parada
    # puede llegar cuando el procesamiento ya ha terminado: sale con 0 y no hay nada que parar
    # (spec, Edge Cases: "Pausar cuando el procesador ya está terminando"). Pasa con la
    # verificación de estas pistas, que dura unos 0,2 s.
    assert trabajador.returncode in (INTERRUMPIDO, 0), trabajador.returncode
    detenida = trabajador.returncode == INTERRUMPIDO
    assert estados <= {"deteniendo", "ninguno"} and "deteniendo" in estados, estados
    ultima = app.cliente.get("api/estado").json()["ultima_ejecucion"]
    assert ultima["error"] == ({"tipo": "detenida", "mensaje": "interrumpido; progreso guardado"}
                               if detenida else None)

    record_property(f"parada_{caso}_s", round(duracion, 2))
    with capsys.disabled():
        print(f"\n[postproceso] {sys.platform}: {pistas_grandes['medidas']}")
        terminado = (f"a los {fin_programa:.2f} s" if fin_programa is not None
                     else "con el trabajador")
        final = "" if detenida else "; llegó cuando ya había terminado todo (código 0)"
        print(f"\n[parada] durante la {caso} ({sys.platform}): {duracion:.2f} s; "
              f"{'ffmpeg' if caso == 'union' else 'ffprobe'} terminó {terminado}{final}")
    limite = PARADA_MAXIMA_EN_WINDOWS if sys.platform == "win32" else PARADA_MAXIMA
    assert duracion <= limite

    # Sin progreso perdido: Iniciar termina sin volver a pedir las pistas.
    servidas = dict(servidor.bytes_servidos)
    assert app.cliente.post("api/iniciar").estado == 202

    def terminada():
        datos = app.cliente.get("api/estado").json()
        return datos if datos["procesador"]["estado"] == "ninguno" and (
            datos["ultima_ejecucion"]["error"] is None) else None

    datos = hasta(terminada, 300, "que la descarga se complete")
    # La verificación de estas pistas dura menos que el intervalo del vigilante (0,25 s): a
    # veces la parada llega cuando ya se ha publicado y la segunda ejecución no tiene nada que
    # hacer. En los dos casos la descarga queda completa y publicada una sola vez.
    assert datos["ultima_ejecucion"]["completadas"] in (0, 1)
    assert datos["cola"]["filas"][0]["estado"] == "completed"
    assert len([p for p in destino.iterdir() if p.is_file()]) == 1
    for pista in ("video.mp4", "audio.m4a"):
        ruta = f"grande/{pista}"
        assert servidor.bytes_servidos[ruta] == servidas[ruta], pista
        assert servidas[ruta] <= 1.01 * len(pistas_grandes[pista.split(".")[0]]), pista
