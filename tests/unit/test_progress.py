"""Línea de una descarga en `waiting_network` (contracts/cli.md, T040)."""

import time

from downloader.models import Calidad, Descarga, Estado
from downloader.progress import _Activa, linea_activa


def descarga():
    return Descarga(id=13, url="https://videos.ejemplo/v?token=SECRETO", calidad=Calidad.MEJOR,
                    carpeta_destino="/tmp", estado=Estado.WAITING_NETWORK, titulo="Título")


def test_cuenta_atras_hasta_el_reintento():
    activa = _Activa(descarga(), causa_espera="conexión rechazada",
                     reintento=time.monotonic() + 39.2)
    assert linea_activa(activa) == "#13 esperando red  reintento en 40 s  (conexión rechazada)"
    activa.reintento = time.monotonic() - 1
    assert linea_activa(activa) == "#13 esperando red  reintento en 0 s  (conexión rechazada)"


def test_sin_conexion_espera_a_la_red():
    activa = _Activa(descarga(), causa_espera="conexión rechazada", reintento=None)
    assert linea_activa(activa) == (
        "#13 esperando red  sin conexión, reintento al volver la red  (conexión rechazada)"
    )


def descargando(titulo="Título"):
    return Descarga(id=12, url="https://videos.ejemplo/v", calidad=Calidad.MEJOR,
                    carpeta_destino="/tmp", estado=Estado.DOWNLOADING, titulo=titulo)


def test_sin_datos_hace_n_s_durante_un_estancamiento():
    from downloader.ytdl import Progreso

    activa = _Activa(descargando(), progreso=Progreso(1000, 2000, None, None, "v.mp4", "downloading"),
                     ultimo_avance=time.monotonic() - 12.2)
    assert linea_activa(activa) == "#12 descargando  sin datos hace 12 s, reintentando  Título"
    activa.ultimo_avance = time.monotonic()
    assert linea_activa(activa).startswith("#12 descargando  50.0%")


def test_el_presentador_anota_el_ultimo_avance():
    import io

    from rich.console import Console

    from downloader.progress import Presentador
    from downloader.ytdl import Progreso

    presentador = Presentador(Console(file=io.StringIO()))
    descarga = descargando()
    presentador.progreso(descarga, Progreso(1000, 2000, None, None, "v.mp4", "downloading"))
    activa = presentador._activas[12]
    activa.ultimo_avance -= 20
    presentador.progreso(descarga, Progreso(1000, 2000, None, None, "v.mp4", "downloading"))
    assert "sin datos hace 20 s" in linea_activa(activa)  # mismos bytes: no es avance
    presentador.progreso(descarga, Progreso(1500, 2000, None, None, "v.mp4", "downloading"))
    assert "sin datos" not in linea_activa(activa)


def test_la_linea_activa_se_recorta_al_ancho_de_la_terminal():
    import io

    from rich.console import Console

    from downloader.progress import Presentador
    from downloader.ytdl import Progreso

    salida = io.StringIO()
    consola = Console(file=salida, width=40, force_terminal=True, color_system=None)
    presentador = Presentador(consola)
    descarga = descargando(titulo="Un título larguísimo " * 5)
    presentador.progreso(descarga, Progreso(1000, 2000, 1e6, 1, "v.mp4", "downloading"))
    consola.print(presentador._activas_renderizables())
    lineas = salida.getvalue().splitlines()
    assert len(lineas) == 1
    assert len(lineas[0]) <= 40
    assert lineas[0].startswith("#12 descargando  50.0%")
