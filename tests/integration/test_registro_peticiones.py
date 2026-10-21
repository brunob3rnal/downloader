"""Clasificación de errores reales de yt-dlp contra el servidor local (research R4).

Los casos de fallo repetido fallan más veces que los 3 reintentos internos de yt-dlp, que
entonces lanza un `DownloadError` sin la excepción original y la clasificación depende del
registro de la última petición.

`intentar` reproduce lo que hará el trabajador en cada intento (volver a extraer y descargar,
clasificar, aplicar el tope de errores ambiguos) sin las esperas ni la cola.
"""

import pytest
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import DownloadError

from downloader import ytdl
from downloader.errors import Fase, RegistroRed, Tipo, clasificar, clasificar_detalle
from downloader.models import Calidad, Descarga, Estado
from downloader.retry import TopeSinAvance
from tests.support import fake_extractor


@pytest.fixture
def descarga(destino, servidor):
    return Descarga(
        id=1, url=servidor.url("watch/v"), calidad=Calidad.MEJOR,
        carpeta_destino=str(destino), estado=Estado.DOWNLOADING,
    )


@pytest.fixture
def una_vez(descarga, tiempos_rapidos, monkeypatch):
    """Un intento de descarga; `al_progresar` recibe cada `Progreso`."""
    fake_extractor.instalar(monkeypatch)

    def una_vez(al_progresar=lambda _: None):
        return ytdl.descargar(
            descarga, ytdl.directorio_trabajo(descarga), None, al_progresar,
            socket_timeout=tiempos_rapidos.socket_timeout,
        )

    return una_vez


@pytest.fixture
def fallo(una_vez):
    """El error del intento, que debe fallar."""

    def fallo():
        with pytest.raises(DownloadError) as error:
            una_vez()
        return error.value

    return fallo


def sin_causa(error):
    return not error.exc_info or error.exc_info[0] is None


def intentar(una_vez, maximo=10):
    """Intentos sucesivos hasta completar o hasta una decisión no temporal.

    Devuelve (número de intentos, clasificaciones de los fallos, decisión final o `None`).
    """
    tope = TopeSinAvance()
    clasificaciones = []
    for numero in range(1, maximo + 1):
        try:
            una_vez(lambda p: tope.observar(p.archivo, p.bytes))
            return numero, clasificaciones, None
        except DownloadError as error:
            clasificacion = clasificar_detalle(error, en_linea=True)
            clasificaciones.append(clasificacion)
            decision = tope.cerrar_intento(clasificacion)
            if decision.tipo is not Tipo.TEMPORAL:
                return numero, clasificaciones, decision
    pytest.fail(f"no terminó en {maximo} intentos")


# --- clasificación de un intento ---------------------------------------------------


def test_corte_repetido_es_temporal(fallo, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, cortar_tras_bytes=1000, veces=4)
    servidor.registrar_video("v", "v.mp4")
    error = fallo()
    assert sin_causa(error)
    assert error.registro.fase is Fase.DESCARGA
    assert error.registro.ultima.error is not None
    assert clasificar_detalle(error, en_linea=True).tipo is Tipo.TEMPORAL
    assert not clasificar_detalle(error, en_linea=True).ambiguo


def test_503_repetido_es_temporal(fallo, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, errores=[503] * 4)
    servidor.registrar_video("v", "v.mp4")
    error = fallo()
    assert sin_causa(error)
    assert error.registro.ultima.estado == 503
    assert clasificar(error, en_linea=True) is Tipo.TEMPORAL


def test_fragmento_hls_ausente_es_permanente(fallo, servidor, medios):
    manifiesto = servidor.registrar_hls("hls", medios.hls, fragmento_ausente=2)
    servidor.registrar_video("v", manifiesto)
    error = fallo()
    assert sin_causa(error)
    assert error.registro == RegistroRed(error.registro.ultima, Fase.DESCARGA, externo=False)
    assert error.registro.ultima.estado == 404
    assert clasificar(error, en_linea=True) is Tipo.PERMANENTE


def test_429_con_causa_es_temporal(fallo, servidor, medios):
    # La descarga HTTP no reintenta un 4xx: yt-dlp lo entrega con la causa encadenada.
    servidor.registrar("v.mp4", medios.datos_mp4, errores=[429])
    servidor.registrar_video("v", "v.mp4")
    error = fallo()
    assert isinstance(error.exc_info[1], HTTPError) and error.exc_info[1].status == 429
    assert clasificar(error, en_linea=True) is Tipo.TEMPORAL


def test_429_sin_causa_es_temporal(fallo, servidor, medios):
    # Los fragmentos sí reintentan cualquier error HTTP y lo entregan sin causa.
    manifiesto = servidor.registrar_hls("hls", medios.hls)
    servidor.registrar_video("v", manifiesto)
    servidor.programar("hls/fragmento1.ts", errores=[429] * 4)
    error = fallo()
    assert sin_causa(error)
    assert error.registro.ultima.estado == 429
    assert clasificar(error, en_linea=True) is Tipo.TEMPORAL


# --- varios intentos -----------------------------------------------------------------


def test_token_hls_caducado_se_recupera_al_volver_a_extraer(una_vez, servidor, medios, descarga):
    manifiesto = servidor.registrar_hls("hls", medios.hls, tokens=True)
    servidor.registrar_video("v", manifiesto)
    servidor.programar("hls/fragmento2.ts", caducar_tokens=True)

    intentos, clasificaciones, decision = intentar(una_vez)

    assert decision is None and intentos == 2
    (primer_fallo,) = clasificaciones
    assert primer_fallo.tipo is Tipo.TEMPORAL
    assert primer_fallo.ambiguo and primer_fallo.motivo == "HTTP 403"
    # El segundo intento continúa: no vuelve a pedir los fragmentos ya descargados.
    for i in range(3):
        assert servidor.peticiones[f"hls/fragmento{i}.ts"] == 1
    assert servidor.peticiones["hls/index.m3u8"] == 2
    assert (ytdl.directorio_trabajo(descarga) / "descarga.mp4").exists()


def test_403_constante_falla_tras_3_intentos_sin_avance(una_vez, servidor, medios):
    servidor.registrar("v.mp4", medios.datos_mp4, errores=[403] * 50)
    servidor.registrar_video("v", "v.mp4")

    intentos, clasificaciones, decision = intentar(una_vez)

    assert intentos == 3
    assert all(c.ambiguo and c.motivo == "HTTP 403" for c in clasificaciones)
    assert decision.tipo is Tipo.PERMANENTE
    assert "--cookies-de" in decision.mensaje
    assert servidor.peticiones["v.mp4"] == 3  # uno por intento: yt-dlp no reintenta un 403


def test_403_con_avance_no_agota_el_tope(una_vez, servidor, medios, descarga):
    # Cada intento descarga algo y luego el token caduca: hay avance, así que nunca se rinde.
    datos = medios.datos_mp4
    servidor.registrar("v.mp4", datos, tokens=True)
    servidor.registrar_video("v", "v.mp4")
    tercio = len(datos) // 3
    for _ in range(3):
        servidor.programar("v.mp4", cortar_tras_bytes=tercio // 4, caducar_tokens=True)

    intentos, clasificaciones, decision = intentar(una_vez)

    assert decision is None
    assert any(c.ambiguo for c in clasificaciones)
    assert (ytdl.directorio_trabajo(descarga) / "descarga.mp4").read_bytes() == datos
