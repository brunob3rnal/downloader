"""Registro de errores de la aplicación (specs/002-desktop-app, FR-020, research R10)."""

import logging
import sys
import threading

import pytest

from downloader.app import registro

LIMITE_ARCHIVO = 500_000
LIMITE_TOTAL = 2_000_000


@pytest.fixture
def directorio(tmp_path):
    return tmp_path / "app"


def _escribir_mucho(logger, megas=5):
    linea = "e" * 999
    for _ in range(megas * 1000):
        logger.error(linea)


def test_cuatro_archivos_y_2_mb_como_maximo(directorio):
    for origen in ("ventana", "procesador"):
        manejo = registro.configurar(origen, directorio)
        try:
            _escribir_mucho(logging.getLogger("downloader.app"))
        finally:
            manejo.cerrar()
    archivos = sorted((directorio / "registro").iterdir())
    assert [a.name for a in archivos] == [
        "procesador.log", "procesador.log.1", "ventana.log", "ventana.log.1"
    ]
    assert all(a.stat().st_size <= LIMITE_ARCHIVO for a in archivos)
    assert sum(a.stat().st_size for a in archivos) <= LIMITE_TOTAL


def test_formato_y_secretos_ocultos(directorio):
    manejo = registro.configurar("ventana", directorio)
    try:
        log = logging.getLogger("downloader.app")
        try:
            raise RuntimeError("fallo en https://cdn.x/v.mp4?token=abc123&sig=def456")
        except RuntimeError:
            log.exception("no se pudo con Cookie: SID=cookie-ficticia-789")
    finally:
        manejo.cerrar()
    texto = (directorio / "registro" / "ventana.log").read_text(encoding="utf-8")
    assert " ERROR ventana: no se pudo con " in texto
    assert "Traceback (most recent call last)" in texto
    for secreto in ("abc123", "def456", "cookie-ficticia-789"):
        assert secreto not in texto


def test_lo_de_downloader_vivo_tambien_va_al_registro(directorio):
    manejo = registro.configurar("procesador", directorio)
    try:
        logging.getLogger("downloader.vivo").warning("no se puede escribir procesador.json")
        logging.getLogger("downloader.ytdl").error("ruido de yt-dlp que no es un fallo nuestro")
    finally:
        manejo.cerrar()
    texto = (directorio / "registro" / "procesador.log").read_text(encoding="utf-8")
    assert "procesador.json" in texto
    assert "ruido de yt-dlp" not in texto


def test_sin_poder_escribir_no_lanza_y_lo_indica(directorio):
    directorio.mkdir()
    (directorio / "registro").write_text("un archivo donde iría la carpeta", encoding="utf-8")
    manejo = registro.configurar("ventana", directorio)
    try:
        logging.getLogger("downloader.app").error("esto no se puede guardar")
        assert registro.disponible() is False
    finally:
        manejo.cerrar()


def test_disponible_cuando_todo_va_bien(directorio):
    manejo = registro.configurar("ventana", directorio)
    try:
        logging.getLogger("downloader.app").error("algo")
        assert registro.disponible() is True
    finally:
        manejo.cerrar()


def test_las_excepciones_no_capturadas_van_al_registro(directorio):
    manejo = registro.configurar("ventana", directorio)
    try:
        try:
            raise ValueError("del hilo principal ?token=secreto1")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        def hilo():
            raise KeyError("de otro hilo")

        h = threading.Thread(target=hilo)
        h.start()
        h.join()
    finally:
        manejo.cerrar()
    texto = (directorio / "registro" / "ventana.log").read_text(encoding="utf-8")
    assert "ValueError: del hilo principal" in texto and "secreto1" not in texto
    assert "KeyError: 'de otro hilo'" in texto


def test_cerrar_restaura_los_ganchos(directorio):
    antes = (sys.excepthook, threading.excepthook)
    manejo = registro.configurar("ventana", directorio)
    manejo.cerrar()
    assert (sys.excepthook, threading.excepthook) == antes
