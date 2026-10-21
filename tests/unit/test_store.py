import errno
import importlib.util
import os
import sqlite3
import sys

import pytest

from downloader import store as modulo_store
from downloader.errors import ErrorFatal
from downloader.models import Calidad, Estado, TipoDescarga
from downloader.store import EjecucionEnCurso, Store

CAMPOS = {
    "id", "url", "calidad", "carpeta_destino", "navegador_cookies", "tipo", "padre_id",
    "estado", "titulo", "bytes_descargados", "bytes_totales", "intentos_sin_avance",
    "proximo_intento", "ultimo_error", "ultimo_aviso", "archivo_final", "creada", "actualizada",
}

URL = "https://videos.ejemplo/watch?v=abc"


@pytest.fixture
def store(app_home):
    store = Store.abrir(app_home)
    yield store
    store.cerrar()


def agregar(store, url=URL, calidad=Calidad.P720, carpeta="/tmp/destino"):
    return store.agregar(url, calidad, carpeta)


def test_esquema_con_todos_los_campos(store, app_home):
    conexion = sqlite3.connect(app_home / "cola.db")
    columnas = {fila[1] for fila in conexion.execute("PRAGMA table_info(descargas)")}
    conexion.close()
    assert columnas == CAMPOS


def test_wal_y_synchronous_full(store):
    assert store.pragma("journal_mode") == "wal"
    assert store.pragma("synchronous") == 2


def test_agregar_crea_fila_en_cola(store):
    resultado = agregar(store)
    assert resultado.accion == "agregada"
    descarga = store.obtener(resultado.descarga.id)
    assert descarga.estado is Estado.QUEUED
    assert descarga.calidad is Calidad.P720
    assert descarga.tipo is TipoDescarga.VIDEO
    assert descarga.intentos_sin_avance == 0
    assert descarga.creada and descarga.actualizada


def avanzar(store, id_, *estados, **campos):
    for estado in estados:
        store.transicion(id_, estado, **(campos if estado is Estado.COMPLETED else {}))


@pytest.mark.parametrize(
    "camino",
    [
        (),
        (Estado.DOWNLOADING,),
        (Estado.DOWNLOADING, Estado.WAITING_NETWORK),
        (Estado.DOWNLOADING, Estado.COMPLETED),
    ],
    ids=["queued", "downloading", "waiting_network", "completed"],
)
def test_agregar_pareja_existente_no_crea_fila(store, camino):
    original = agregar(store).descarga
    avanzar(store, original.id, *camino, archivo_final="/tmp/destino/v.mp4")
    resultado = agregar(store)
    assert resultado.accion == "ya_en_cola"
    assert resultado.descarga.id == original.id
    assert resultado.descarga.estado is (camino[-1] if camino else Estado.QUEUED)
    assert store.contar() == 1


def test_misma_url_con_otra_calidad_es_otra_descarga(store):
    agregar(store)
    resultado = agregar(store, calidad=Calidad.AUDIO)
    assert resultado.accion == "agregada"
    assert store.contar() == 2


def test_agregar_una_fallida_la_reencola_con_el_mismo_id(store):
    original = agregar(store).descarga
    store.transicion(original.id, Estado.DOWNLOADING)
    store.transicion(original.id, Estado.FAILED, ultimo_error="Video privado")
    resultado = agregar(store)
    assert resultado.accion == "reencolada"
    assert resultado.descarga.id == original.id
    assert resultado.descarga.estado is Estado.QUEUED
    assert store.contar() == 1


def test_transicion_invalida_no_cambia_nada(store):
    from downloader.models import TransicionInvalida

    descarga = agregar(store).descarga
    with pytest.raises(TransicionInvalida):
        store.transicion(descarga.id, Estado.COMPLETED, archivo_final="/x.mp4")
    assert store.obtener(descarga.id).estado is Estado.QUEUED


def test_recuperar_devuelve_a_cola_las_interrumpidas(store):
    a = agregar(store, url="https://v/a").descarga
    b = agregar(store, url="https://v/b").descarga
    c = agregar(store, url="https://v/c").descarga
    d = agregar(store, url="https://v/d").descarga
    avanzar(store, a.id, Estado.DOWNLOADING)
    avanzar(store, b.id, Estado.DOWNLOADING, Estado.WAITING_NETWORK)
    avanzar(store, c.id, Estado.DOWNLOADING, Estado.COMPLETED, archivo_final="/tmp/c.mp4")
    assert store.recuperar() == 2
    assert [store.obtener(x.id).estado for x in (a, b, c, d)] == [
        Estado.QUEUED, Estado.QUEUED, Estado.COMPLETED, Estado.QUEUED,
    ]


def test_siguiente_y_pendientes(store):
    assert store.siguiente() is None
    assert not store.hay_pendientes()
    a = agregar(store, url="https://v/a").descarga
    b = agregar(store, url="https://v/b").descarga
    assert store.siguiente().id == a.id
    avanzar(store, a.id, Estado.DOWNLOADING)
    assert store.siguiente().id == b.id
    avanzar(store, b.id, Estado.DOWNLOADING, Estado.FAILED)
    assert store.siguiente() is None
    assert store.hay_pendientes()
    avanzar(store, a.id, Estado.COMPLETED, archivo_final="/tmp/a.mp4")
    assert not store.hay_pendientes()


def test_archivo_final_solo_en_videos_completados(store):
    descarga = agregar(store).descarga
    store.transicion(descarga.id, Estado.DOWNLOADING)
    with pytest.raises(sqlite3.IntegrityError):
        store.transicion(descarga.id, Estado.COMPLETED)
    assert store.obtener(descarga.id).estado is Estado.DOWNLOADING
    with pytest.raises(sqlite3.IntegrityError):
        store.transicion(descarga.id, Estado.FAILED, archivo_final="/tmp/v.mp4")
    store.transicion(descarga.id, Estado.COMPLETED, archivo_final="/tmp/v.mp4")
    assert store.obtener(descarga.id).archivo_final == "/tmp/v.mp4"


def test_lista_completada_sin_archivo_final(store):
    lista = agregar(store, url="https://v/playlist?list=1").descarga
    store.transicion(lista.id, Estado.DOWNLOADING)
    with pytest.raises(sqlite3.IntegrityError):
        store.transicion(
            lista.id, Estado.COMPLETED, tipo=TipoDescarga.PLAYLIST, archivo_final="/tmp/x"
        )
    store.transicion(lista.id, Estado.COMPLETED, tipo=TipoDescarga.PLAYLIST)
    assert store.obtener(lista.id).tipo is TipoDescarga.PLAYLIST


def test_base_corrupta_se_copia_y_es_fatal(app_home):
    contenido = os.urandom(8192)
    (app_home / "cola.db").write_bytes(contenido)
    with pytest.raises(ErrorFatal) as error:
        Store.abrir(app_home)
    assert "corrupt" in str(error.value).lower()
    copias = list(app_home.glob("cola.db.corrupta-*"))
    assert len(copias) == 1
    assert copias[0].read_bytes() == contenido
    # No se crea una cola nueva en su lugar.
    assert (app_home / "cola.db").read_bytes() == contenido


def test_un_solo_bloqueo_de_ejecucion(store, app_home):
    primero = store.bloquear_ejecucion()
    otro = Store.abrir(app_home)
    try:
        with pytest.raises(EjecucionEnCurso):
            otro.bloquear_ejecucion()
        primero.liberar()
        segundo = otro.bloquear_ejecucion()
        segundo.liberar()
    finally:
        otro.cerrar()
    assert (app_home / "ejecutar.lock").exists()


def test_el_bloqueo_se_libera_al_cerrar(app_home):
    store = Store.abrir(app_home)
    store.bloquear_ejecucion()
    store.cerrar()
    otro = Store.abrir(app_home)
    try:
        otro.bloquear_ejecucion().liberar()
    finally:
        otro.cerrar()


class MsvcrtFalso:
    """`msvcrt.locking` con la semántica de Windows: bloqueos por región, no reentrantes."""

    LK_UNLCK, LK_NBLCK = 0, 2

    def __init__(self):
        self.bloqueos = {}  # (dispositivo, inodo, posición, bytes) -> descriptor

    def locking(self, descriptor, modo, bytes_):
        estado = os.fstat(descriptor)
        clave = (estado.st_dev, estado.st_ino, os.lseek(descriptor, 0, os.SEEK_CUR), bytes_)
        if modo == self.LK_NBLCK:
            if clave in self.bloqueos:
                raise PermissionError(errno.EACCES, "Permission denied")
            self.bloqueos[clave] = descriptor
        elif modo == self.LK_UNLCK:
            if self.bloqueos.get(clave) != descriptor:
                raise PermissionError(errno.EACCES, "Permission denied")
            del self.bloqueos[clave]


@pytest.mark.skipif(os.name == "nt", reason="en Windows, los dos tests anteriores usan msvcrt real")
def test_bloqueo_con_msvcrt_simulado(app_home, monkeypatch):
    """La rama de Windows de `store` con un `msvcrt` simulado (la real la prueba el CI)."""
    falso = MsvcrtFalso()
    monkeypatch.setitem(sys.modules, "msvcrt", falso)
    monkeypatch.setattr(os, "name", "nt")
    spec = importlib.util.spec_from_file_location("store_windows", modulo_store.__file__)
    windows = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(windows)
    monkeypatch.undo()

    primero, otro = windows.Store.abrir(app_home), windows.Store.abrir(app_home)
    try:
        bloqueo = primero.bloquear_ejecucion()
        assert len(falso.bloqueos) == 1
        with pytest.raises(windows.EjecucionEnCurso):
            otro.bloquear_ejecucion()
        bloqueo.liberar()
        assert falso.bloqueos == {}
        otro.bloquear_ejecucion()
    finally:
        primero.cerrar()
        otro.cerrar()
    assert falso.bloqueos == {}
