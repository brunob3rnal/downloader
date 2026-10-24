"""`downloader estado`: la cola sin modificarla (FR-032, contracts/cli.md; plan, decisión 29)."""

import os
import re
import sqlite3
from pathlib import Path

import pytest

from downloader.models import Estado, TipoDescarga
from downloader.store import EjecucionEnCurso, Store


def visible(ruta: Path) -> str:
    """Una ruta como la muestra la salida: relativa a `~` si está dentro de la carpeta personal."""
    try:
        return str(Path("~") / ruta.relative_to(Path.home()))
    except ValueError:
        return str(ruta)


def contenido(app_home) -> list[tuple]:
    """Todas las filas y columnas de `cola.db`, leídas sin `Store`."""
    conexion = sqlite3.connect(app_home / "cola.db")
    try:
        return conexion.execute("SELECT * FROM descargas ORDER BY id").fetchall()
    finally:
        conexion.close()


@pytest.fixture
def cola(app_home, destino):
    """Una fila por estado y caso de la línea de `estado`; devuelve las líneas esperadas."""
    store = Store.abrir(app_home)
    carpeta = str(destino)

    def agregar(url):
        return store.agregar(url, "mejor", carpeta).descarga.id

    try:
        en_cola = agregar("https://v.ejemplo/watch/en-cola")
        store.transicion(en_cola, Estado.DOWNLOADING)
        store.transicion(en_cola, Estado.QUEUED, titulo="Título en cola")

        descargando = agregar("https://v.ejemplo/watch/descargando")
        store.transicion(descargando, Estado.DOWNLOADING, titulo="Título descargando")
        store.guardar_progreso(descargando, 431, 1000, forzar=True)

        esperando = agregar("https://v.ejemplo/watch/esperando")
        store.transicion(esperando, Estado.DOWNLOADING, titulo="Título esperando")
        store.guardar_progreso(esperando, 120, 1000, forzar=True)
        store.transicion(esperando, Estado.WAITING_NETWORK, ultimo_error="conexión rechazada",
                         proximo_intento="2026-10-24T10:00:00.000+00:00")

        completada = agregar("https://v.ejemplo/watch/completada")
        publicado = destino / "Título completada.mp4"
        store.transicion(completada, Estado.DOWNLOADING, titulo="Título completada")
        store.guardar_progreso(completada, 900, 1000, forzar=True)  # el último guardado
        store.transicion(completada, Estado.COMPLETED, archivo_final=str(publicado),
                         ultimo_aviso="calidad 720p no disponible; se descargó 480p")

        lista = agregar("https://v.ejemplo/playlist/xyz")
        store.transicion(lista, Estado.DOWNLOADING)
        hijas = [
            store.agregar(f"https://v.ejemplo/watch/h{i}", "mejor", carpeta, padre_id=lista)
            .descarga.id
            for i in (1, 2, 3)
        ]
        store.transicion(lista, Estado.COMPLETED, tipo=TipoDescarga.PLAYLIST, titulo="Lista xyz")

        fallida = agregar("https://v.ejemplo/watch/abc?token=SECRETO-URL")
        store.transicion(fallida, Estado.DOWNLOADING)
        store.transicion(
            fallida, Estado.FAILED,
            ultimo_error=(
                "HTTP Error 403 en https://cdn.ejemplo/v.mp4?X-Amz-Signature=SECRETO-ERROR"
            ),
        )

        interrumpida = agregar("https://v.ejemplo/watch/interrumpida")
        store.transicion(interrumpida, Estado.DOWNLOADING, titulo="Título interrumpida")
        store.guardar_progreso(interrumpida, int(35.2 * 2**20), None, forzar=True)
        store.transicion(interrumpida, Estado.QUEUED)
    finally:
        store.cerrar()

    return [
        f"#{en_cola} en cola  —  Título en cola",
        f"#{descargando} descargando  43.1%  Título descargando",
        f"#{esperando} esperando red  12.0%  Título esperando  (conexión rechazada)",
        f"#{completada} completada  100%  Título completada  {visible(publicado)}"
        "  aviso: calidad 720p no disponible; se descargó 480p",
        f"#{lista} completada  lista (3 videos)  Lista xyz",
        *(f"#{h} en cola  —  https://v.ejemplo/watch/h{i}" for i, h in zip((1, 2, 3), hijas)),
        f"#{fallida} fallida  —  https://v.ejemplo/watch/abc?token=***"
        "  HTTP Error 403 en https://cdn.ejemplo/v.mp4?X-Amz-Signature=***",
        f"#{interrumpida} en cola  35.2 MiB  Título interrumpida",
    ]


def test_una_linea_por_descarga_en_orden_de_id(cli, cola):
    resultado = cli("estado")
    assert resultado.exit_code == 0, resultado.output  # aunque haya una fallida
    assert resultado.output.splitlines() == cola


def test_oculta_los_secretos(cli, cola):
    resultado = cli("estado")
    assert resultado.exit_code == 0, resultado.output
    assert "SECRETO" not in resultado.output


def test_funciona_con_ejecutar_en_marcha_y_no_toca_la_cola(cli, app_home, cola):
    otro = Store.abrir(app_home)  # otro descriptor, como el de un `ejecutar` en marcha
    bloqueo = otro.bloquear_ejecucion()
    try:
        antes = contenido(app_home)
        resultado = cli("estado")
        assert resultado.exit_code == 0, resultado.output
        assert resultado.output.splitlines() == cola
        assert contenido(app_home) == antes  # sin transiciones ni `recuperar()`
        tercero = Store.abrir(app_home)
        try:
            with pytest.raises(EjecucionEnCurso):  # el bloqueo sigue tomado
                tercero.bloquear_ejecucion()
        finally:
            tercero.cerrar()
    finally:
        bloqueo.liberar()
        otro.cerrar()
    assert contenido(app_home) == antes


def test_cola_vacia(cli, app_home):
    Store.abrir(app_home).cerrar()  # crea el esquema, sin filas
    resultado = cli("estado")
    assert resultado.exit_code == 0, resultado.output
    assert resultado.output == "cola vacía\n"


def test_sin_cola_db_no_la_crea(cli, app_home):
    resultado = cli("estado")
    assert resultado.exit_code == 0, resultado.output
    assert resultado.output == "cola vacía\n"
    assert not (app_home / "cola.db").exists()


def test_cola_db_vacia_sin_esquema(cli, app_home):
    """Un archivo de 0 bytes es una base SQLite sin tablas (`agregar` crearía el esquema)."""
    (app_home / "cola.db").write_bytes(b"")
    resultado = cli("estado")
    assert resultado.exit_code == 0, resultado.output
    assert resultado.output == "cola vacía\n"
    assert (app_home / "cola.db").read_bytes() == b""


def test_cola_corrupta_sale_con_3_como_agregar(cli, app_home, destino):
    def sin_fecha(texto):
        return re.sub(r"corrupta-\d{8}T\d{6}Z", "corrupta-<fecha>", texto)

    basura = os.urandom(4096)
    (app_home / "cola.db").write_bytes(basura)
    agregar = cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    assert agregar.exit_code == 3
    for copia in app_home.glob("cola.db.corrupta-*"):
        copia.unlink()

    resultado = cli("estado")

    assert resultado.exit_code == 3, resultado.output
    assert "corrupt" in resultado.output
    assert sin_fecha(resultado.output) == sin_fecha(agregar.output)
    (copia,) = app_home.glob("cola.db.corrupta-*")
    assert copia.read_bytes() == basura
    assert (app_home / "cola.db").read_bytes() == basura  # no se sustituye por una cola nueva
