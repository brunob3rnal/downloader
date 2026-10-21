"""`downloader agregar` (contracts/cli.md)."""

import os
import re

from downloader.models import Estado
from downloader.store import Store


def filas(app_home):
    store = Store.abrir(app_home)
    try:
        return [store.obtener(i) for i in range(1, store.contar() + 1)]
    finally:
        store.cerrar()


def test_agregar_varias_urls(cli, app_home, destino):
    resultado = cli("agregar", "https://v.ejemplo/a", "https://v.ejemplo/b",
                    "--calidad", "720p", "--carpeta", destino)
    assert resultado.exit_code == 0, resultado.output
    assert re.search(r"^agregada #1  https://v\.ejemplo/a  \(720p\)$", resultado.output, re.M)
    assert re.search(r"^agregada #2  https://v\.ejemplo/b  \(720p\)$", resultado.output, re.M)
    a, b = filas(app_home)
    assert a.estado is Estado.QUEUED and a.calidad.value == "720p"
    assert a.carpeta_destino == str(destino.resolve())


def test_repetir_una_pendiente(cli, destino):
    cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    resultado = cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    assert resultado.exit_code == 0
    assert re.search(r"^ya en cola #1  https://v\.ejemplo/a  \(mejor, queued\)$",
                     resultado.output, re.M)


def test_reencolar_una_fallida(cli, app_home, destino):
    cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    store = Store.abrir(app_home)
    store.transicion(1, Estado.DOWNLOADING)
    store.transicion(1, Estado.FAILED, ultimo_error="Video privado")
    store.cerrar()
    resultado = cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    assert resultado.exit_code == 0
    assert re.search(r"^reencolada #1  https://v\.ejemplo/a  \(antes: failed\)$",
                     resultado.output, re.M)
    assert filas(app_home)[0].estado is Estado.QUEUED


def test_calidad_desconocida(cli, app_home, destino):
    resultado = cli("agregar", "https://v.ejemplo/a", "--calidad", "4k", "--carpeta", destino)
    assert resultado.exit_code == 2
    assert "4k" in resultado.output
    assert not (app_home / "cola.db").exists() or filas(app_home) == []


def test_carpeta_inexistente(cli, tmp_path):
    resultado = cli("agregar", "https://v.ejemplo/a", "--carpeta", tmp_path / "no-existe")
    assert resultado.exit_code == 2
    assert "no-existe" in resultado.output


def test_carpeta_sin_permisos(cli, destino, monkeypatch):
    # Como root, `chmod` no impide escribir: se simula la respuesta de os.access.
    acceso = os.access
    monkeypatch.setattr(
        os, "access", lambda ruta, modo, **kw: False if str(ruta) == str(destino.resolve())
        else acceso(ruta, modo, **kw)
    )
    resultado = cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    assert resultado.exit_code == 2
    assert "permiso" in resultado.output


def test_url_vacia(cli, destino):
    assert cli("agregar", "", "--carpeta", destino).exit_code == 2
    assert cli("agregar", "   ", "--carpeta", destino).exit_code == 2
    assert cli("agregar", "--carpeta", destino).exit_code == 2  # ninguna URL


def test_agregar_no_necesita_red(cli, servidor, destino):
    servidor.detener()
    resultado = cli("agregar", servidor.url("watch/v1"), "--carpeta", destino)
    assert resultado.exit_code == 0
    assert "agregada #1" in resultado.output


def test_cola_corrupta(cli, app_home, destino):
    (app_home / "cola.db").write_bytes(os.urandom(4096))
    resultado = cli("agregar", "https://v.ejemplo/a", "--carpeta", destino)
    assert resultado.exit_code == 3
    assert "corrupt" in resultado.output
    assert list(app_home.glob("cola.db.corrupta-*"))
