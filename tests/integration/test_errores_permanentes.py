"""Errores permanentes: fallan sin reintentos, con la causa, y la cola sigue (escenario 6 de
quickstart; US4, FR-011, FR-018, FR-021, FR-026, SC-005)."""

import time

import pytest

from downloader.models import Estado
from downloader.store import Store

# Página que el extractor genérico de yt-dlp descarga y en la que no encuentra ningún video.
PAGINA_SIN_VIDEO = (
    b"<!DOCTYPE html><html><head><title>Sin video</title></head>"
    b"<body><p>Nada que descargar.</p></body></html>"
)


@pytest.fixture
def transiciones(monkeypatch):
    """(id, estado) de cada transición, hecha desde cualquier conexión a la cola."""
    registro = []
    transicion = Store.transicion

    def espiar(self, id_, hacia, **campos):
        registro.append((id_, hacia))
        return transicion(self, id_, hacia, **campos)

    monkeypatch.setattr(Store, "transicion", espiar)
    return registro


def filas(app_home):
    store = Store.abrir(app_home)
    try:
        return [store.obtener(i) for i in range(1, store.contar() + 1)]
    finally:
        store.cerrar()


def registrar(servidor, medios, id_video, **fallos):
    servidor.registrar(f"{id_video}.mp4", medios.datos_mp4, tipo_contenido="video/mp4", **fallos)
    servidor.registrar_video(id_video, f"{id_video}.mp4", titulo=f"Video {id_video}")


def publicados(carpeta):
    return sorted(p.name for p in carpeta.iterdir() if p.is_file())


def test_permanentes_fallan_sin_reintentos_y_la_cola_sigue(
    cli, app_home, destino, servidor, medios, transiciones
):
    registrar(servidor, medios, "v1")
    servidor.registrar("pagina.html", PAGINA_SIN_VIDEO, tipo_contenido="text/html")
    urls = [
        servidor.url("watch/privado"),
        servidor.url("watch/borrado"),
        servidor.url("pagina.html"),  # sitio no soportado
        servidor.url("watch/v1"),
    ]
    assert cli("agregar", *urls, "--carpeta", destino).exit_code == 0

    inicio = time.monotonic()
    resultado = cli("ejecutar")
    duracion = time.monotonic() - inicio

    assert resultado.exit_code == 1, resultado.output  # cola vacía con fallos (FR-021)
    assert duracion < 30  # cada fallo, notificado en menos de 30 s (SC-005)
    privado, borrado, no_soportada, valida = filas(app_home)
    assert "Video privado" in privado.ultimo_error
    assert "borrado" in borrado.ultimo_error
    assert "Unsupported URL" in no_soportada.ultimo_error
    for fila in (privado, borrado, no_soportada):
        assert fila.estado is Estado.FAILED
        # Sin reintentos: nunca pasó por `waiting_network` ni contó intentos.
        assert (fila.id, Estado.WAITING_NETWORK) not in transiciones
        assert fila.intentos_sin_avance == 0
        assert f"#{fila.id} fallida  {fila.ultimo_error}" in resultado.output
        # El resumen final lista la causa de cada fallo.
        assert f"  #{fila.id} {fila.url}: {fila.ultimo_error}" in resultado.output
    # La página del sitio no soportado se pidió una sola vez: la única extracción.
    assert servidor.peticiones["pagina.html"] == 1

    assert valida.estado is Estado.COMPLETED  # FR-018
    assert publicados(destino) == ["Video v1.mp4"]
    assert "Resumen: 1 completada, 3 fallidas" in resultado.output


def test_carpeta_borrada_tras_agregar_falla_solo_esa_descarga(
    cli, app_home, destino, tmp_path, servidor, medios
):
    registrar(servidor, medios, "v1")
    registrar(servidor, medios, "v2")
    efimera = tmp_path / "efimera"
    efimera.mkdir()
    assert cli("agregar", servidor.url("watch/v1"), "--carpeta", efimera).exit_code == 0
    assert cli("agregar", servidor.url("watch/v2"), "--carpeta", destino).exit_code == 0
    efimera.rmdir()

    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output
    primera, segunda = filas(app_home)
    assert primera.estado is Estado.FAILED
    assert "ya no existe" in primera.ultimo_error
    assert servidor.peticiones["v1.mp4"] == 0  # falla antes de pedir los medios
    assert not efimera.exists()  # no se recrea la carpeta
    assert segunda.estado is Estado.COMPLETED
    assert publicados(destino) == ["Video v2.mp4"]


def test_403_constante_falla_tras_3_intentos_sin_avance_y_la_cola_sigue(
    cli, app_home, destino, servidor, medios, esperas_cortas
):
    registrar(servidor, medios, "vetado", errores=[403] * 50)
    registrar(servidor, medios, "v1")
    urls = [servidor.url("watch/vetado"), servidor.url("watch/v1")]
    assert cli("agregar", *urls, "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output
    vetado, valida = filas(app_home)
    assert vetado.estado is Estado.FAILED
    assert "403" in vetado.ultimo_error
    assert "--cookies-de" in vetado.ultimo_error  # FR-026
    assert f"#{vetado.id} fallida  " in resultado.output
    # Un intento por cada uno de los 3 sin avance, cada uno con una extracción nueva.
    assert servidor.peticiones["vetado.mp4"] == 3
    assert valida.estado is Estado.COMPLETED
    assert publicados(destino) == ["Video v1.mp4"]


def test_403_al_extraer_falla_en_menos_de_30_s_y_sugiere_cookies_de(
    cli, app_home, destino, servidor, medios, transiciones
):
    """Un 403 al obtener la información del video es permanente (research R4) y suele indicar
    que el video exige una sesión: el mensaje lo explica y sugiere `--cookies-de` (SC-005)."""
    registrar(servidor, medios, "v1")
    servidor.exigir_sesion("SID", "valor-ficticio")  # responde 403 a quien no traiga la cookie
    assert cli("agregar", servidor.url("watch/v1"), "--carpeta", destino).exit_code == 0

    inicio = time.monotonic()
    resultado = cli("ejecutar")
    duracion = time.monotonic() - inicio

    assert resultado.exit_code == 1, resultado.output
    assert duracion < 30  # SC-005
    (fila,) = filas(app_home)
    assert fila.estado is Estado.FAILED
    assert "403" in fila.ultimo_error
    assert "--cookies-de" in fila.ultimo_error
    assert (fila.id, Estado.WAITING_NETWORK) not in transiciones  # sin reintentos
    assert servidor.peticiones["v1.mp4"] == 0  # no llegó a pedir los medios
    assert f"#{fila.id} fallida  {fila.ultimo_error}" in resultado.output
