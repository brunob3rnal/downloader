"""Publicación sin sobrescribir (FR-022, research R7, escenario 8 de quickstart)."""

import errno
import os
import threading

import pytest

from downloader import publish

NOMBRE = "Título [abc123].mp4"


@pytest.fixture
def trabajo(tmp_path):
    directorio = tmp_path / "destino" / ".downloader-tmp" / "1"
    directorio.mkdir(parents=True)
    return directorio


@pytest.fixture
def carpeta(trabajo):
    return trabajo.parent.parent


def origen(trabajo, contenido=b"nuevo", nombre="abc123.mp4"):
    ruta = trabajo / nombre
    ruta.write_bytes(contenido)
    return ruta


@pytest.fixture(autouse=True)
def sin_renombrar(monkeypatch):
    """`os.rename` y `os.replace` sobrescriben en POSIX: no deben usarse nunca."""

    def prohibido(*args, **kwargs):
        raise AssertionError("os.rename/os.replace no deben usarse al publicar")

    monkeypatch.setattr(os, "rename", prohibido)
    monkeypatch.setattr(os, "replace", prohibido)


def test_publica_con_el_nombre_pedido(trabajo, carpeta):
    fuente = origen(trabajo)
    assert not (carpeta / NOMBRE).exists()
    resultado = publish.publicar(fuente, carpeta, NOMBRE)
    assert resultado.ruta == carpeta / NOMBRE
    assert resultado.aviso is None
    assert resultado.ruta.read_bytes() == b"nuevo"
    assert not fuente.exists()


def test_nombre_repetido_recibe_sufijo_y_el_original_queda_intacto(trabajo, carpeta):
    (carpeta / NOMBRE).write_bytes(b"original")
    primero = publish.publicar(origen(trabajo, b"uno"), carpeta, NOMBRE)
    segundo = publish.publicar(origen(trabajo, b"dos"), carpeta, NOMBRE)
    assert primero.ruta.name == "Título [abc123] (1).mp4"
    assert segundo.ruta.name == "Título [abc123] (2).mp4"
    assert (carpeta / NOMBRE).read_bytes() == b"original"
    assert primero.ruta.read_bytes() == b"uno"
    assert segundo.ruta.read_bytes() == b"dos"
    assert NOMBRE in primero.aviso and primero.ruta.name in primero.aviso


def test_publicaciones_simultaneas_no_se_pisan(trabajo, carpeta):
    fuentes = [origen(trabajo, f"contenido {i}".encode(), f"{i}.mp4") for i in range(8)]
    barrera = threading.Barrier(len(fuentes))
    resultados = []

    def publicar(fuente):
        barrera.wait()
        resultados.append(publish.publicar(fuente, carpeta, NOMBRE))

    hilos = [threading.Thread(target=publicar, args=(f,)) for f in fuentes]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    rutas = {r.ruta for r in resultados}
    assert len(rutas) == len(fuentes)
    assert sorted(r.read_bytes() for r in rutas) == sorted(
        f"contenido {i}".encode() for i in range(len(fuentes))
    )


@pytest.mark.parametrize("codigo", [errno.EXDEV, errno.EPERM])
def test_sin_enlaces_duros_copia_sin_sobrescribir(trabajo, carpeta, monkeypatch, codigo):
    def link(*args, **kwargs):
        raise OSError(codigo, os.strerror(codigo))

    monkeypatch.setattr(os, "link", link)
    (carpeta / NOMBRE).write_bytes(b"original")
    fuente = origen(trabajo, b"copiado")
    resultado = publish.publicar(fuente, carpeta, NOMBRE)
    assert resultado.ruta.name == "Título [abc123] (1).mp4"
    assert resultado.ruta.read_bytes() == b"copiado"
    assert (carpeta / NOMBRE).read_bytes() == b"original"
    assert not fuente.exists()


def test_nombre_publicado_limpia_el_titulo():
    assert publish.nombre_publicado("Título", "abc123", "mp4") == NOMBRE
    nombre = publish.nombre_publicado("a/b: c", "x1", "mkv")
    assert "/" not in nombre
    assert nombre.endswith(" [x1].mkv")
