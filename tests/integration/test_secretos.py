"""Ningún secreto en mensajes, salida ni `cola.db` (T078; FR-017, Principio VI).

Dos descargas en la misma ejecución:

- Un video que exige la sesión del usuario (cookie ficticia de `NavegadorFalso`) y cuya URL de
  medios, obtenida al extraer, lleva un token y parámetros de firma, como una URL prefirmada. Su
  primer intento encuentra el servidor caído al pedir los medios y se reintenta.
- Una URL prefirmada que da el propio usuario y que yt-dlp no sabe descargar: el error
  ("Unsupported URL: …") la repite entera, igual que la salida de `agregar`, la línea de
  progreso (aún sin título) y el resumen.

Los tokens y firmas obtenidos al extraer y la cookie no pueden aparecer en ninguna parte. La URL
que da el usuario se guarda tal cual en la columna `url` de `cola.db`, porque hace falta para
descargarla; sus secretos no pueden aparecer en ninguna otra columna ni en ningún mensaje.
"""

import logging
import secrets

import pytest

from downloader.models import Estado
from downloader.store import Store
from tests.support import navegador_falso
from tests.support.navegador_falso import NOMBRE, VALOR

PAGINA_SIN_VIDEO = b"<!DOCTYPE html><html><head><title>Nada</title></head><body></body></html>"


@pytest.fixture
def navegador(monkeypatch):
    return navegador_falso.NavegadorFalso().instalar(monkeypatch)


def test_ningun_secreto_en_mensajes_salida_ni_cola(
    cli, app_home, destino, servidor, medios, navegador, esperas_cortas, caplog, monkeypatch
):
    from downloader import ytdl

    servidor.exigir_sesion(NOMBRE, VALOR)
    servidor.registrar("v.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video("v", "v.mp4", titulo="Video v", firma=True)
    servidor.registrar("pagina.html", PAGINA_SIN_VIDEO, tipo_contenido="text/html")
    de_la_url = {nombre: secrets.token_hex(12) for nombre in
                 ("token", "X-Amz-Security-Token", "X-Amz-Signature", "lsig")}
    prefirmada = servidor.url("pagina.html") + "?" + "&".join(
        f"{nombre}={valor}" for nombre, valor in de_la_url.items()
    )
    dl = ytdl.YoutubeDLRegistrado.dl
    caidas = []

    def dl_con_el_servidor_caido(self, *args, **kwargs):
        if caidas:
            return dl(self, *args, **kwargs)
        caidas.append(True)
        servidor.detener()  # la extracción ya terminó: falla la petición de medios
        try:
            return dl(self, *args, **kwargs)
        finally:
            servidor.reanudar()

    monkeypatch.setattr(ytdl.YoutubeDLRegistrado, "dl", dl_con_el_servidor_caido)

    with caplog.at_level(logging.DEBUG, logger="downloader"):
        agregar = cli("agregar", servidor.url("watch/v"), prefirmada, "--carpeta", destino,
                      "--cookies-de", "firefox")
        resultado = cli("ejecutar")

    assert agregar.exit_code == 0, agregar.output
    assert resultado.exit_code == 1, resultado.output  # la URL sin video falla
    store = Store.abrir(app_home)
    try:
        video, pagina = store.obtener(1), store.obtener(2)
    finally:
        store.cerrar()
    assert video.estado is Estado.COMPLETED
    assert caidas  # hubo un intento fallido y un reintento
    assert pagina.estado is Estado.FAILED and "Unsupported URL" in pagina.ultimo_error
    # Los mensajes llegaron a mostrar la URL prefirmada, con sus secretos ocultos.
    assert "pagina.html?token=***" in resultado.stdout
    assert "X-Amz-Signature=***" in resultado.stdout

    de_la_extraccion = [VALOR, *servidor.secretos_emitidos]
    assert len(de_la_extraccion) >= 1 + 2 * 4  # cookie; token y 3 firmas por extracción
    todos = [*de_la_extraccion, *de_la_url.values()]
    textos = {
        "salida de agregar": agregar.stdout + agregar.stderr,
        "stdout de ejecutar": resultado.stdout,
        "stderr de ejecutar": resultado.stderr,
        "registro": caplog.text,
    }
    for fila in (video, pagina):  # todas las columnas salvo la URL que dio el usuario
        textos |= {f"#{fila.id}.{campo}": str(valor) for campo, valor in vars(fila).items()
                   if campo != "url"}
    for donde, texto in textos.items():
        for secreto in todos:
            assert secreto not in texto, f"{secreto} en {donde}"
    archivos = list(app_home.glob("cola.db*"))
    assert archivos
    for archivo in archivos:  # con el WAL y la memoria compartida de SQLite
        datos = archivo.read_bytes()
        for secreto in de_la_extraccion:
            assert secreto.encode() not in datos, f"{secreto} en {archivo.name}"
