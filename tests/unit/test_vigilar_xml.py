"""Qué cuerpos retiene `_vigilar_xml` y cuánto (research R5 de la 001; defecto D2, 002 R20).

Un cuerpo que empieza por `<` se retiene entero para analizarlo como manifiesto. No se retienen
las respuestas 206 (reanudaciones), las de un archivo que descarga `HttpFD` ni las de tipo
vídeo o audio, y nunca más de `TOPE_XML` bytes: por encima, se entrega completo y en orden,
con un aviso si podía ser un manifiesto.
"""

import pytest
from yt_dlp import YoutubeDL

from downloader import ytdl
from downloader.errors import ErrorPermanente
from tests.support import dash

TOPE = 64 * 1024  # el de los tests; en el código, 16 MB


class Cuerpo:
    """Una respuesta falsa que cuenta lo que se le ha leído."""

    def __init__(self, datos, status=200, tipo=None):
        self.datos, self.status, self.leidos = datos, status, 0
        self.headers = {"Content-Length": str(len(datos))}
        if tipo:
            self.headers["Content-Type"] = tipo
        self.url = "http://127.0.0.1/recurso"

    def read(self, amt=None):
        fin = len(self.datos) if amt is None or amt < 0 else self.leidos + amt
        trozo = self.datos[self.leidos:fin]
        self.leidos += len(trozo)
        return trozo

    def close(self):
        pass


@pytest.fixture
def ydl(monkeypatch):
    respuestas = []
    monkeypatch.setattr(YoutubeDL, "urlopen", lambda self, req: respuestas.pop(0))
    monkeypatch.setattr(YoutubeDL, "dl", lambda self, *a, **k: (True, True))
    monkeypatch.setattr(ytdl, "TOPE_XML", TOPE)
    sin_analizar = []
    instancia = ytdl.crear_ydl({"quiet": True, ytdl.SIN_ANALIZAR: sin_analizar})
    instancia.respuestas, instancia.sin_analizar = respuestas, sin_analizar
    return instancia


def abrir(ydl, cuerpo, url="http://127.0.0.1/recurso"):
    ydl.respuestas.append(cuerpo)
    return ydl.urlopen(url)


def leer_todo(respuesta, bloque=1024):
    partes = []
    while datos := respuesta.read(bloque):
        partes.append(datos)
    return b"".join(partes)


def xml_grande(raiz, tamano):
    cabeza = f"<{raiz}>".encode()
    return cabeza + b"x" * (tamano - len(cabeza))


# --- el tope ---------------------------------------------------------------------------


@pytest.mark.parametrize("raiz", ["html", "MPD"])
def test_un_cuerpo_mayor_que_el_tope_se_entrega_completo_y_en_orden(ydl, raiz):
    datos = xml_grande(raiz, 5 * TOPE + 123)
    cuerpo = Cuerpo(datos)
    respuesta = abrir(ydl, cuerpo)
    primero = respuesta.read(1024)
    assert cuerpo.leidos <= TOPE + ytdl.TROZO_LECTURA  # nunca se retiene más que el tope
    assert primero == datos[:1024]
    assert primero + leer_todo(respuesta) == datos


def test_un_manifiesto_mayor_que_el_tope_deja_un_aviso(ydl):
    respuesta = abrir(ydl, Cuerpo(xml_grande("MPD", 2 * TOPE)), "http://127.0.0.1/otro.mpd")
    leer_todo(respuesta)
    assert ydl.sin_analizar == ["http://127.0.0.1/otro.mpd"]


def test_un_html_mayor_que_el_tope_no_avisa(ydl):
    leer_todo(abrir(ydl, Cuerpo(b"<!DOCTYPE html>\n" + xml_grande("html", 2 * TOPE))))
    assert ydl.sin_analizar == []


def test_por_debajo_del_tope_el_manifiesto_se_sigue_analizando(ydl):
    respuesta = abrir(ydl, Cuerpo(dash.f4m(dash.DRM_HEADER, dash.MEDIA_CON_HEADER_ID)))
    with pytest.raises(ErrorPermanente):
        leer_todo(respuesta)


# --- qué no se retiene -----------------------------------------------------------------


def _no_retenido(ydl, cuerpo):
    respuesta = abrir(ydl, cuerpo)
    primero = respuesta.read(1024)
    assert cuerpo.leidos <= 1024 + ytdl.TROZO_LECTURA
    assert primero + leer_todo(respuesta) == cuerpo.datos


def test_una_respuesta_206_no_se_retiene(ydl):
    _no_retenido(ydl, Cuerpo(xml_grande("MPD", 4 * TOPE // 2), status=206))


@pytest.mark.parametrize("tipo", ["video/mp4", "audio/mp4", "Video/MP2T; charset=binary"])
def test_un_cuerpo_de_video_o_audio_no_se_retiene(ydl, tipo):
    _no_retenido(ydl, Cuerpo(xml_grande("MPD", TOPE // 2), tipo=tipo))


# Depende de `get_suitable_downloader` de yt-dlp (R4). Sin el marcador `ytdlp_internals`:
# `tests/unit/test_marcadores.py` (001) fija la lista exacta de esos tests.
def test_el_archivo_que_descarga_httpfd_no_se_retiene(ydl):
    ydl.dl("destino.mp4", {"url": "http://127.0.0.1/v.mp4", "protocol": "https",
                           "to_stdout": False})
    _no_retenido(ydl, Cuerpo(xml_grande("MPD", TOPE // 2)))


def test_al_descargar_un_f4m_el_manifiesto_se_sigue_analizando(ydl):
    """`F4mFD` pide el manifiesto en la fase de descarga: la detección no se debilita."""
    ydl.dl("destino.flv", {"url": "http://127.0.0.1/v.f4m", "protocol": "f4m",
                           "to_stdout": False})
    respuesta = abrir(ydl, Cuerpo(dash.f4m(dash.DRM_HEADER, dash.MEDIA_CON_HEADER_ID)))
    with pytest.raises(ErrorPermanente):
        leer_todo(respuesta)


@pytest.mark.parametrize("raiz", ["html", "MPD"])
def test_por_encima_del_tope_un_read_sin_tamano_devuelve_todo_lo_que_queda(ydl, raiz):
    """Como el extractor genérico: 512 bytes y después `read()`."""
    datos = xml_grande(raiz, 3 * TOPE + 7)
    respuesta = abrir(ydl, Cuerpo(datos))
    assert respuesta.read(512) + respuesta.read() == datos
