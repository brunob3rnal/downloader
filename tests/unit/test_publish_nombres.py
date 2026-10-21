"""Publicación sin sobrescribir (FR-022, research R7, escenario 8 de quickstart)."""

import errno
import os
import shutil
import threading
import uuid
from pathlib import Path

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


@pytest.mark.parametrize(
    "winerror", [1, 17, 50, 1142],
    ids=["ERROR_INVALID_FUNCTION", "ERROR_NOT_SAME_DEVICE", "ERROR_NOT_SUPPORTED",
         "ERROR_TOO_MANY_LINKS"],
)
def test_sin_enlaces_duros_en_windows_copia_sin_sobrescribir(trabajo, carpeta, monkeypatch,
                                                              winerror):
    """En FAT32/exFAT, Windows indica el error en `winerror` y `errno` es un EINVAL genérico."""

    def link(*args, **kwargs):
        error = OSError(errno.EINVAL, "no se admiten enlaces duros")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(os, "link", link)
    (carpeta / NOMBRE).write_bytes(b"original")
    resultado = publish.publicar(origen(trabajo, b"copiado"), carpeta, NOMBRE)
    assert resultado.ruta.name == "Título [abc123] (1).mp4"
    assert resultado.ruta.read_bytes() == b"copiado"
    assert (carpeta / NOMBRE).read_bytes() == b"original"


def test_otro_error_de_os_link_no_se_oculta(trabajo, carpeta, monkeypatch):
    def link(*args, **kwargs):
        raise OSError(errno.EINVAL, "otro error")

    monkeypatch.setattr(os, "link", link)
    fuente = origen(trabajo)
    with pytest.raises(OSError, match="otro error"):
        publish.publicar(fuente, carpeta, NOMBRE)
    assert fuente.exists()
    assert not (carpeta / NOMBRE).exists()


# Volúmenes reales sin enlaces duros (FAT32, exFAT) que crea el CI, separados por `os.pathsep`.
SIN_ENLACES = [r for r in os.environ.get("DOWNLOADER_TEST_SIN_ENLACES", "").split(os.pathsep) if r]


@pytest.mark.skipif(
    not SIN_ENLACES,
    reason="sin volumen FAT32/exFAT: el CI lo crea y define DOWNLOADER_TEST_SIN_ENLACES",
)
@pytest.mark.parametrize("raiz", SIN_ENLACES)
def test_publicar_en_un_volumen_sin_enlaces_duros(raiz):
    carpeta = Path(raiz) / f"prueba-{uuid.uuid4().hex[:8]}"
    trabajo = carpeta / ".downloader-tmp" / "1"
    trabajo.mkdir(parents=True)
    try:
        fuente = origen(trabajo, b"copiado")
        with pytest.raises(OSError) as error:
            os.link(fuente, carpeta / "sonda")
        assert publish._sin_enlaces(error.value), error.value  # el volumen no los admite
        (carpeta / NOMBRE).write_bytes(b"original")
        resultado = publish.publicar(fuente, carpeta, NOMBRE)
        assert resultado.ruta.name == "Título [abc123] (1).mp4"
        assert resultado.ruta.read_bytes() == b"copiado"
        assert (carpeta / NOMBRE).read_bytes() == b"original"
        assert not fuente.exists()
    finally:
        shutil.rmtree(carpeta)


def test_nombre_publicado_limpia_el_titulo():
    assert publish.nombre_publicado("Título", "abc123", "mp4") == NOMBRE
    nombre = publish.nombre_publicado("a/b: c", "x1", "mkv")
    assert "/" not in nombre
    assert nombre.endswith(" [x1].mkv")


def test_nombre_publicado_sin_caracteres_no_validos_en_windows():
    nombre = publish.nombre_publicado('a: b? c| d" e< f> g* h\\ i/', "x1", "mp4")
    assert not set(':?|"<>*\\/') & set(nombre)
    assert nombre.endswith(" [x1].mp4")


@pytest.mark.parametrize("titulo", ["CON.parte 1", "nul.x", "Com1.a", "LPT9 .b", "AUX"])
def test_nombre_publicado_evita_nombres_reservados_de_windows(titulo):
    nombre = publish.nombre_publicado(titulo, "x1", "mp4")
    assert nombre.split(".")[0].rstrip(" ").upper() not in publish._RESERVADOS
    assert nombre.endswith(" [x1].mp4")


def test_nombre_publicado_respeta_255_bytes_con_hueco_para_el_sufijo():
    nombre = publish.nombre_publicado("é" * 300, "abc123", "mp4")
    assert nombre.endswith(" [abc123].mp4")
    assert len(f"{nombre[:-4]} (9999).mp4".encode()) <= 255


def test_nombre_publicado_respeta_la_ruta_maxima_de_windows():
    carpeta = Path("C:/") / ("d" * 150)
    nombre = publish.nombre_publicado("t" * 300, "abc123", "mp4", carpeta, limite_ruta=259)
    assert nombre.startswith("t") and nombre.endswith(" [abc123].mp4")
    assert len(str(carpeta)) + 1 + len(f"{nombre[:-4]} (9999).mp4") <= 259


def test_nombre_publicado_sin_sitio_para_el_titulo_conserva_id_y_extension():
    carpeta = Path("C:/") / ("d" * 240)
    nombre = publish.nombre_publicado("título", "abc123", "mp4", carpeta, limite_ruta=259)
    assert nombre == "[abc123].mp4"


def test_carpeta_escribible(tmp_path):
    assert publish.escribible(tmp_path)
    assert not publish.escribible(tmp_path / "no-existe")
    assert list(tmp_path.iterdir()) == []  # la prueba no deja archivos
