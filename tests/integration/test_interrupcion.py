"""Ctrl+C durante `ejecutar` y relanzamiento (FR-020, FR-021, edge case Ctrl+C de la spec).

Como en `test_ctrl_c.py`, en Windows se lanza `ejecutar` en su propio grupo de procesos y se le
envía `CTRL_BREAK_EVENT`, que `cli.ejecutar` trata igual que Ctrl+C.
"""

import os
import sqlite3
import subprocess
import sys
import time

import pytest

from downloader import ytdl
from downloader.models import Calidad, Estado
from downloader.store import Store
from tests.integration.test_ctrl_c import SALIDA_TRAS_INTERRUPCION, interrumpir
from tests.support import sc002

LIMITE = 60
INTERRUMPIDO = 130


def ejecutar(app_home, **kwargs):
    return subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        **kwargs,
    )


def estado(app_home, id_):
    """Estado de la fila leído sin bloquear al `ejecutar` en marcha."""
    conexion = sqlite3.connect(f"{(app_home / 'cola.db').as_uri()}?mode=ro", uri=True)
    try:
        fila = conexion.execute("SELECT estado FROM descargas WHERE id = ?", (id_,)).fetchone()
    finally:
        conexion.close()
    return fila[0] if fila else None


@pytest.mark.slow
@pytest.mark.ytdlp_internals  # R19: dos sondeos del extractor genérico por intento (`sc002`)
def test_ctrl_c_guarda_el_progreso_y_relanzar_termina(app_home, destino, servidor, medios):
    datos = medios.datos_mp4
    servidor.registrar("lento.mp4", datos, tipo_contenido="video/mp4", velocidad=200_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    id_ = store.agregar(servidor.url("lento.mp4"), Calidad.MEJOR, str(destino)).descarga.id
    store.cerrar()

    proceso = ejecutar(app_home)
    try:
        trabajo = destino / ".downloader-tmp" / str(id_)
        fin = time.monotonic() + LIMITE
        # Se interrumpe solo con la fila en `downloading` y su `.part` con datos; además, más
        # allá de los 2 s entre escrituras de progreso, para que solo el guardado al
        # interrumpir deje en la cola lo que ya hay en el `.part`.
        while True:
            parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
            if (
                parciales
                and parciales[0].stat().st_size > 0.3 * len(datos)
                and estado(app_home, id_) == Estado.DOWNLOADING
            ):
                break
            assert proceso.poll() is None, "ejecutar terminó antes de poder interrumpirlo"
            assert time.monotonic() < fin, "la descarga lenta no avanzó"
            time.sleep(0.05)  # intervalo de sondeo
        en_disco = parciales[0].stat().st_size
        interrumpir(proceso)
        _, errores = proceso.communicate(timeout=SALIDA_TRAS_INTERRUPCION)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()

    assert proceso.returncode == INTERRUMPIDO, errores
    assert b"Traceback" not in errores
    assert b"interrumpido; progreso guardado" in errores
    assert parciales[0].exists()
    assert [p.name for p in destino.iterdir()] == [".downloader-tmp"]
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(id_)
        assert descarga.estado is Estado.QUEUED
        assert descarga.bytes_descargados >= en_disco
    finally:
        store.cerrar()

    relanzado = ejecutar(app_home)
    salida, errores = relanzado.communicate(timeout=LIMITE)
    assert relanzado.returncode == 0, errores
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == datos
    # Ctrl+C no es una terminación abrupta: el 1 % de SC-002 y los sondeos del extractor
    # genérico, sin el término de un cierre abrupto (R19, R20).
    intentos = sc002.intentos(servidor, "lento.mp4")
    assert servidor.bytes_servidos["lento.mp4"] <= sc002.limite(len(datos), intentos)


# Bytes del bloque en curso ya recibidos y aún no escritos en el `.part` cuando se interrumpe:
# muy por encima del 1 % del archivo (~20 KB).
EN_VUELO = 64 * 1024


@pytest.mark.slow
@pytest.mark.ytdlp_internals  # R18: yt-dlp escribe lo que devuelve `read` antes de volver a leer
def test_ctrl_c_a_mitad_de_bloque_no_pierde_lo_recibido(app_home, destino, servidor, medios):
    """Ctrl+C mientras el envoltorio de lectura (R18) acumula el bloque que pidió yt-dlp.

    El servidor envía el archivo en trozos de 1 KiB con una pausa entre ellos (200 KB/s), así
    que yt-dlp pide bloques de unos 200 KB que tardan cerca de 1 s en llegar. Se interrumpe
    cuando ya se han recibido `EN_VUELO` bytes del bloque en curso sin escribirlos: lo recibido
    tiene que acabar en el `.part` y no volver a pedirse. Se comprueba el 1 %, más estricto que
    SC-002: con los sondeos de dos intentos (64 KiB, R19), perder `EN_VUELO` cabría en el límite.
    """
    datos = medios.datos_mp4
    servidor.registrar("bloque.mp4", datos, tipo_contenido="video/mp4", velocidad=200_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    id_ = store.agregar(servidor.url("bloque.mp4"), Calidad.MEJOR, str(destino)).descarga.id
    store.cerrar()

    proceso = ejecutar(app_home)
    try:
        trabajo = destino / ".downloader-tmp" / str(id_)
        fin = time.monotonic() + LIMITE
        while True:
            parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
            en_disco = parciales[0].stat().st_size if parciales else 0
            if (
                en_disco > 0.3 * len(datos)
                and servidor.bytes_servidos["bloque.mp4"] - en_disco >= EN_VUELO
                and estado(app_home, id_) == Estado.DOWNLOADING
            ):
                break
            assert proceso.poll() is None, "ejecutar terminó antes de poder interrumpirlo"
            assert time.monotonic() < fin, "no llegó a haber un bloque a medias"
            time.sleep(0.01)  # intervalo de sondeo
        interrumpir(proceso)
        _, errores = proceso.communicate(timeout=SALIDA_TRAS_INTERRUPCION)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()

    assert proceso.returncode == INTERRUMPIDO, errores
    assert b"Traceback" not in errores
    assert b"interrumpido; progreso guardado" in errores

    relanzado = ejecutar(app_home)
    _, errores = relanzado.communicate(timeout=LIMITE)
    assert relanzado.returncode == 0, errores
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == datos
    assert servidor.bytes_servidos["bloque.mp4"] <= 1.01 * len(datos)


@pytest.mark.ytdlp_internals  # R18: `HttpFD` no vuelve a leer al completar el archivo
def test_ctrl_c_con_los_ultimos_bytes_y_relanzar_publica_sin_volver_a_descargar(
    cli, app_home, destino, servidor, medios, monkeypatch
):
    """Ctrl+C justo después de recibir los últimos bytes del archivo, antes de que el envoltorio
    de lectura (R18) los entregue: se escriben, yt-dlp termina el archivo sin volver a leer, y la
    interrupción se relanza al final de `dl`. Al relanzar, el archivo se verifica y se publica
    sin pedirlo otra vez (SC-002: con el extractor de prueba no hay sondeos, así que el 1 %).

    Una señal real no puede caer ahí (tras el último byte, la librería devuelve `b""` sin
    esperar): la lectura de la librería lanza `KeyboardInterrupt`, como lo haría Ctrl+C en ese
    instante, en lugar de devolver el final del cuerpo.
    """
    from yt_dlp import YoutubeDL

    datos = medios.datos_mp4
    servidor.registrar("final.mp4", datos, tipo_contenido="video/mp4")
    servidor.registrar_video("final", "final.mp4")
    inyeccion = {"interrumpida": False, "lecturas_despues": 0}
    urlopen = YoutubeDL.urlopen

    def urlopen_interrumpido(self, req):
        respuesta = urlopen(self, req)
        if not getattr(req, "url", req).split("?")[0].endswith("/final.mp4"):
            return respuesta
        leer, entregados = respuesta.read, 0

        def read(amt=None):
            nonlocal entregados
            if inyeccion["interrumpida"]:
                inyeccion["lecturas_despues"] += 1
            elif entregados == len(datos):
                inyeccion["interrumpida"] = True
                raise KeyboardInterrupt
            trozo = leer(amt)
            entregados += len(trozo)
            return trozo

        respuesta.read = read
        return respuesta

    monkeypatch.setattr(YoutubeDL, "urlopen", urlopen_interrumpido)
    salidas_de_dl = []  # cada `KeyboardInterrupt` que sale de `dl`
    dl = ytdl.YoutubeDLRegistrado.dl

    def dl_espiado(self, *args, **kwargs):
        try:
            return dl(self, *args, **kwargs)
        except KeyboardInterrupt:
            salidas_de_dl.append(True)
            raise

    monkeypatch.setattr(ytdl.YoutubeDLRegistrado, "dl", dl_espiado)
    assert cli("agregar", servidor.url("watch/final"), "--carpeta", destino).exit_code == 0

    interrumpido = cli("ejecutar")

    assert interrumpido.exit_code == INTERRUMPIDO, interrumpido.output
    assert "interrumpido; progreso guardado" in interrumpido.stderr
    assert inyeccion == {"interrumpida": True, "lecturas_despues": 0}  # no volvió a leer
    assert salidas_de_dl == [True]  # relanzada al final de `dl`
    trabajo = destino / ".downloader-tmp" / "1"
    assert [p.name for p in trabajo.iterdir()] == ["descarga.mp4"]  # terminado, sin `.part`
    assert (trabajo / "descarga.mp4").read_bytes() == datos
    assert [p.name for p in destino.iterdir()] == [".downloader-tmp"]
    store = Store.abrir(app_home)
    try:
        assert store.obtener(1).estado is Estado.QUEUED
    finally:
        store.cerrar()

    relanzado = cli("ejecutar")

    assert relanzado.exit_code == 0, relanzado.output
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == datos
    assert servidor.bytes_servidos["final.mp4"] <= 1.01 * len(datos)
