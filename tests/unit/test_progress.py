"""Líneas de `ejecutar` y su presentación en una terminal (contracts/cli.md; T040, T041d, T092)."""

import io
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

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


# --- Modo terminal: `rich.live` (SC-007, FR-005; T092) ---------------------------------
#
# Una consola de rich que se cree una terminal y escribe en memoria; `pantalla` reproduce lo que
# vería el usuario con las secuencias de control que usa `rich.live`. El reloj de `progress` se
# controla a mano: así se sabe qué tiene que decir cada línea al redibujarse, sin eventos.

_TOKEN = re.compile(r"\x1b\[(\??)(\d*)([A-Za-z])|\r|\n|[^\x1b\r\n]+")


def pantalla(salida: str) -> list[str]:
    """Las líneas visibles de una terminal tras escribir `salida`."""
    lineas, fila, columna = [""], 0, 0
    for token in _TOKEN.finditer(salida):
        texto = token[0]
        if texto == "\n":
            fila, columna = fila + 1, 0
            lineas += [""] * (fila + 1 - len(lineas))
        elif texto == "\r":
            columna = 0
        elif token[3]:
            numero = token[2]
            if token[3] == "A":
                fila = max(0, fila - int(numero or 1))
            elif token[3] == "K":
                lineas[fila] = "" if numero == "2" else lineas[fila][:columna]
            elif not (token[1] == "?" and token[3] in "hl"):  # mostrar u ocultar el cursor
                raise AssertionError(f"secuencia no prevista: {texto!r}")
        else:
            linea = lineas[fila].ljust(columna)
            lineas[fila] = linea[:columna] + texto + linea[columna + len(texto):]
            columna += len(texto)
    while lineas and not lineas[-1]:
        lineas.pop()
    return lineas


class Terminal(io.StringIO):
    """Archivo de la consola que anota el instante (reloj real) de cada escritura."""

    def __init__(self):
        super().__init__()
        self.escrituras: list[tuple[float, str]] = []
        self._cerrojo = threading.Lock()

    def write(self, texto):
        with self._cerrojo:
            self.escrituras.append((time.monotonic(), texto))
            return super().write(texto)

    def getvalue(self):
        with self._cerrojo:
            return super().getvalue()


@pytest.fixture
def reloj(monkeypatch):
    """`time.monotonic` de `downloader.progress`, que solo avanza con `reloj.avanzar(s)`."""
    from downloader import progress

    estado = SimpleNamespace(ahora=time.monotonic())
    estado.avanzar = lambda segundos: setattr(estado, "ahora", estado.ahora + segundos)
    monkeypatch.setattr(progress, "time", SimpleNamespace(monotonic=lambda: estado.ahora))
    return estado


@pytest.fixture
def terminal(reloj):
    from rich.console import Console

    from downloader.progress import Presentador

    archivo = Terminal()
    consola = Console(file=archivo, width=60, force_terminal=True, color_system=None,
                      legacy_windows=False)
    presentador = Presentador(consola)
    presentador.iniciar()
    try:
        yield SimpleNamespace(archivo=archivo, presentador=presentador, consola=consola)
    finally:
        presentador.detener()


def hasta(condicion, segundos=1.0):
    """Espera (reloj real) a que se cumpla `condicion`; la devuelve evaluada."""
    limite = time.monotonic() + segundos
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.02)
    return condicion()


def ultima_linea(terminal):
    lineas = pantalla(terminal.archivo.getvalue())
    return lineas[-1] if lineas else None


def test_en_una_terminal_usa_live(terminal):
    from rich.live import Live

    assert isinstance(terminal.presentador._live, Live)
    assert terminal.presentador._hilo is None  # el latido de la salida sin terminal, no


def test_redibuja_al_menos_una_vez_por_segundo_sin_eventos(terminal):
    from downloader.ytdl import Progreso

    descarga = descargando()
    terminal.presentador.inicio(descarga)
    terminal.presentador.progreso(descarga, Progreso(1000, 2000, 2**20, 61, "v.mp4", "downloading"))
    activa = "#12 descargando  50.0%  1.0 MiB/s  ETA 00:01:01  Título"
    assert hasta(lambda: ultima_linea(terminal) == activa), pantalla(terminal.archivo.getvalue())

    desde = time.monotonic()
    time.sleep(2.2)  # sin ningún evento del hook
    hasta_ = time.monotonic()
    redibujos = [t for t, texto in terminal.archivo.escrituras
                 if desde <= t <= hasta_ and activa in texto]
    instantes = [desde, *redibujos, hasta_]
    assert max(b - a for a, b in zip(instantes, instantes[1:])) <= 1.0, redibujos


def test_sin_datos_tras_5_s_sin_bytes_nuevos(terminal, reloj):
    from downloader.ytdl import Progreso

    descarga = descargando()
    terminal.presentador.inicio(descarga)
    terminal.presentador.progreso(descarga, Progreso(1000, 2000, 2**20, 1, "v.mp4", "downloading"))
    reloj.avanzar(6.4)
    esperada = "#12 descargando  sin datos hace 6 s, reintentando  Título"
    assert hasta(lambda: ultima_linea(terminal) == esperada), pantalla(terminal.archivo.getvalue())


def test_cuenta_atras_de_la_espera_sin_eventos(terminal, reloj):
    descarga = descargando()
    terminal.presentador.inicio(descarga)
    terminal.presentador.esperando(descarga, reloj.ahora + 40, "conexión rechazada")
    assert hasta(lambda: ultima_linea(terminal)
                 == "#12 esperando red  reintento en 40 s  (conexión rechazada)")
    reloj.avanzar(10)
    assert hasta(lambda: ultima_linea(terminal)
                 == "#12 esperando red  reintento en 30 s  (conexión rechazada)")


def test_los_eventos_quedan_por_encima_de_la_zona_viva(terminal):
    from downloader.ytdl import Progreso

    presentador = terminal.presentador
    primera = descargando()
    segunda = Descarga(id=13, url="https://videos.ejemplo/otro", calidad=Calidad.MEJOR,
                       carpeta_destino="/tmp", estado=Estado.DOWNLOADING, titulo="Otro")
    for descarga in (primera, segunda):
        presentador.inicio(descarga)
        presentador.progreso(descarga, Progreso(500, 2000, 2**20, 2, "v.mp4", "downloading"))
    zona = ["#12 descargando  25.0%  1.0 MiB/s  ETA 00:00:02  Título",
            "#13 descargando  25.0%  1.0 MiB/s  ETA 00:00:02  Otro"]
    assert hasta(lambda: pantalla(terminal.archivo.getvalue()) == zona)

    presentador.aviso(primera, "reiniciada desde cero: el servidor no permite reanudar")
    presentador.fallida(segunda, "Video privado")
    esperada = ["#12 aviso  reiniciada desde cero: el servidor no permite reanudar",
                "#13 fallida  Video privado", zona[0]]
    assert hasta(lambda: pantalla(terminal.archivo.getvalue()) == esperada), \
        pantalla(terminal.archivo.getvalue())

    presentador.completada(primera, "/tmp/Título.mp4")
    final = [*esperada[:2], f"#12 completada  {Path('/tmp/Título.mp4')}"]  # y sin zona viva
    assert hasta(lambda: pantalla(terminal.archivo.getvalue()) == final), \
        pantalla(terminal.archivo.getvalue())


def test_detener_quita_la_zona_viva(terminal):
    from downloader.ytdl import Progreso

    descarga = descargando()
    terminal.presentador.inicio(descarga)
    progreso = Progreso(1000, 2000, None, None, "v.mp4", "downloading")
    terminal.presentador.progreso(descarga, progreso)
    terminal.presentador.aviso(descarga, "un aviso")
    assert hasta(lambda: "descargando" in (ultima_linea(terminal) or ""))

    terminal.presentador.detener()

    salida = terminal.archivo.getvalue()
    assert pantalla(salida) == ["#12 aviso  un aviso"]
    assert salida.rfind("\x1b[?25h") > salida.rfind("\x1b[?25l")  # el cursor vuelve a verse


def test_la_zona_viva_recorta_cada_linea_al_ancho(terminal):
    from downloader.ytdl import Progreso

    descarga = descargando(titulo="Un título larguísimo " * 5)
    terminal.presentador.inicio(descarga)
    terminal.presentador.progreso(descarga, Progreso(1000, 2000, 1e6, 1, "v.mp4", "downloading"))
    assert hasta(lambda: (ultima_linea(terminal) or "").startswith("#12 descargando  50.0%"))
    lineas = pantalla(terminal.archivo.getvalue())
    assert len(lineas) == 1 and len(lineas[0]) <= terminal.consola.width
