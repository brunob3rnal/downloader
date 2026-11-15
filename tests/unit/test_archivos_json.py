"""Escritura atómica y lectura tolerante de los JSON compartidos (specs/002-desktop-app, T028;
contracts/archivos.md)."""

import json
import os
import threading

from downloader import archivos_json


def test_escribe_y_lee(tmp_path):
    ruta = tmp_path / "x.json"
    assert archivos_json.escribir_atomico(ruta, {"a": 1, "texto": "ñ"}) is True
    assert archivos_json.leer(ruta) == {"a": 1, "texto": "ñ"}
    assert "ñ" in ruta.read_text(encoding="utf-8")  # ensure_ascii=False


def test_nunca_se_lee_un_archivo_a_medias(tmp_path):
    ruta = tmp_path / "vivo.json"
    archivos_json.escribir_atomico(ruta, {"n": 0, "relleno": "x" * 20_000})
    invalidos = []
    fin = threading.Event()

    def leer():
        while not fin.is_set():
            try:
                texto = ruta.read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                continue  # Windows: el instante del reemplazo
            try:
                json.loads(texto)
            except ValueError:
                invalidos.append(texto[:40])

    lector = threading.Thread(target=leer)
    lector.start()
    try:
        for n in range(500):
            archivos_json.escribir_atomico(ruta, {"n": n, "relleno": "x" * 20_000},
                                           reintentar=True)
    finally:
        fin.set()
        lector.join()
    assert invalidos == []
    # En Windows, alguna escritura puede ceder ante la lectura (devuelve `False`): basta con
    # que el archivo siga siendo un JSON completo.
    assert archivos_json.leer(ruta)["relleno"] == "x" * 20_000


def test_reintenta_una_vez_si_reintentar(tmp_path, monkeypatch):
    ruta = tmp_path / "x.json"
    real = os.replace
    llamadas = []

    def replace(origen, destino):
        llamadas.append(destino)
        if len(llamadas) == 1:
            raise PermissionError("en uso")  # como en Windows con el destino abierto
        return real(origen, destino)

    esperas = []
    monkeypatch.setattr(archivos_json.os, "replace", replace)
    monkeypatch.setattr(archivos_json.time, "sleep", esperas.append)
    assert archivos_json.escribir_atomico(ruta, {"a": 1}, reintentar=True) is True
    assert esperas == [0.05] and len(llamadas) == 2
    assert archivos_json.leer(ruta) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]  # sin temporales


def test_sin_reintentar_devuelve_false_sin_lanzar(tmp_path, monkeypatch):
    ruta = tmp_path / "x.json"

    def replace(origen, destino):
        raise PermissionError("en uso")

    monkeypatch.setattr(archivos_json.os, "replace", replace)
    assert archivos_json.escribir_atomico(ruta, {"a": 1}, reintentar=False) is False
    assert list(tmp_path.iterdir()) == []  # sin temporales ni destino


def test_un_destino_imposible_no_se_escribe_ni_deja_temporales(tmp_path):
    ruta = tmp_path / "x.json"
    ruta.mkdir()  # un directorio en su lugar: no se puede reemplazar
    # Linux lanza `IsADirectoryError`; Windows da `PermissionError`, que se trata como un
    # destino en uso y devuelve `False`. En ninguno de los dos se escribe nada.
    try:
        resultado = archivos_json.escribir_atomico(ruta, {"a": 1})
    except OSError:
        resultado = None
    assert resultado in (None, False)
    assert ruta.is_dir()
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_leer_tolera_ausente_vacio_invalido_y_no_objeto(tmp_path):
    assert archivos_json.leer(tmp_path / "no.json") is None
    vacio = tmp_path / "vacio.json"
    vacio.write_text("", encoding="utf-8")
    assert archivos_json.leer(vacio) is None
    roto = tmp_path / "roto.json"
    roto.write_text('{"a": ', encoding="utf-8")
    assert archivos_json.leer(roto) is None
    lista = tmp_path / "lista.json"
    lista.write_text("[1, 2]", encoding="utf-8")
    assert archivos_json.leer(lista) is None
    directorio = tmp_path / "dir.json"
    directorio.mkdir()
    assert archivos_json.leer(directorio) is None


def test_borrar_tolera_que_no_exista(tmp_path):
    ruta = tmp_path / "x.json"
    archivos_json.borrar(ruta)  # no lanza
    archivos_json.escribir_atomico(ruta, {})
    archivos_json.borrar(ruta)
    assert not ruta.exists()
