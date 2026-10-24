"""Cookies del navegador solo con `--cookies-de` (escenario 9 de quickstart; US6, FR-016,
FR-017, Principio VI; research R12, R25).

La lectura de cookies del navegador se sustituye por `NavegadorFalso`, que devuelve una cookie
ficticia conocida; el servidor de pruebas exige esa cookie con `exigir_sesion`, así que un
video solo se descarga si yt-dlp la envía.
"""

import logging

import pytest

from downloader.models import Estado
from downloader.store import Store
from tests.support import navegador_falso
from tests.support.navegador_falso import NOMBRE, VALOR


@pytest.fixture
def navegador(monkeypatch):
    return navegador_falso.NavegadorFalso().instalar(monkeypatch)


@pytest.fixture
def opciones_ydl(cli, monkeypatch):
    """Opciones de cada `YoutubeDL` que crea la aplicación (tras instalar `fake_extractor`)."""
    from downloader import ytdl

    vistas = []
    crear = ytdl.crear_ydl

    def espiar(opciones):
        vistas.append(dict(opciones))
        return crear(opciones)

    monkeypatch.setattr(ytdl, "crear_ydl", espiar)
    return vistas


@pytest.fixture
def video(servidor, medios):
    """Registra `/watch/<id>` con el MP4 de prueba y devuelve su URL."""

    def registrar(id_video, **fallos):
        servidor.registrar(f"{id_video}.mp4", medios.datos_mp4, tipo_contenido="video/mp4",
                           **fallos)
        servidor.registrar_video(id_video, f"{id_video}.mp4", titulo=f"Video {id_video}")
        return servidor.url(f"watch/{id_video}")

    return registrar


def filas(app_home):
    store = Store.abrir(app_home)
    try:
        return [store.obtener(i) for i in range(1, store.contar() + 1)]
    finally:
        store.cerrar()


def publicados(destino):
    return sorted(p.name for p in destino.iterdir() if p.is_file())


def con_cookie(raiz):
    """Archivos bajo `raiz` cuyo contenido incluye el valor de la cookie ficticia."""
    return [p for p in raiz.rglob("*") if p.is_file() and VALOR.encode() in p.read_bytes()]


def test_sin_cookies_de_no_se_leen_cookies(
    cli, app_home, opciones_ydl, navegador, video, destino, esperas_cortas
):
    url = video("v")
    assert cli("agregar", url, "--carpeta", destino).exit_code == 0
    resultado = cli("ejecutar")
    assert resultado.exit_code == 0, resultado.output
    assert opciones_ydl
    assert all("cookiesfrombrowser" not in o and "cookiefile" not in o for o in opciones_ydl)
    assert navegador.leidos == []
    assert filas(app_home)[0].navegador_cookies is None


def test_agregar_cookies_de_guarda_solo_el_nombre_y_descarga_con_la_sesion(
    cli, app_home, opciones_ydl, navegador, video, servidor, destino, medios, esperas_cortas
):
    servidor.exigir_sesion(NOMBRE, VALOR)
    con_sesion, sin_sesion = video("v"), video("w")
    resultado = cli("agregar", con_sesion, "--carpeta", destino, "--cookies-de", "firefox")
    assert resultado.exit_code == 0, resultado.output
    assert cli("agregar", sin_sesion, "--carpeta", destino).exit_code == 0
    assert filas(app_home)[0].navegador_cookies == "firefox"

    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output  # la que no lleva cookies falla
    v, w = filas(app_home)
    assert v.estado is Estado.COMPLETED
    assert (destino / "Video v [v].mp4").read_bytes() == medios.datos_mp4
    assert w.estado is Estado.FAILED and "403" in w.ultimo_error
    assert servidor.peticiones["w.mp4"] == 0
    con = [o for o in opciones_ydl if "cookiesfrombrowser" in o]
    assert con and all(o["cookiesfrombrowser"] == ("firefox",) for o in con)
    assert all("cookiefile" not in o for o in opciones_ydl)
    assert set(navegador.leidos) == {"firefox"}


def test_ejecutar_cookies_de_prevalece_sobre_la_fila(
    cli, app_home, opciones_ydl, navegador, video, servidor, destino, esperas_cortas
):
    servidor.exigir_sesion(NOMBRE, VALOR)
    cli("agregar", video("v"), "--carpeta", destino, "--cookies-de", "firefox")
    resultado = cli("ejecutar", "--cookies-de", "chrome")
    assert resultado.exit_code == 0, resultado.output
    assert opciones_ydl and all(o["cookiesfrombrowser"] == ("chrome",) for o in opciones_ydl)
    assert set(navegador.leidos) == {"chrome"}
    # Vale solo para esta ejecución: la fila conserva su navegador.
    assert filas(app_home)[0].navegador_cookies == "firefox"


def test_el_nombre_del_navegador_se_normaliza(cli, app_home, destino):
    resultado = cli("agregar", "https://v.ejemplo/a", "--carpeta", destino, "--cookies-de",
                    " Firefox ")
    assert resultado.exit_code == 0, resultado.output
    assert filas(app_home)[0].navegador_cookies == "firefox"


@pytest.mark.parametrize("comando", ["agregar", "ejecutar"])
@pytest.mark.parametrize("valor", ["netscape", "firefox:perfil", ""])
def test_navegador_no_admitido_sale_con_2(cli, app_home, destino, comando, valor):
    argumentos = ["https://v.ejemplo/a", "--carpeta", destino] if comando == "agregar" else []
    resultado = cli(comando, *argumentos, "--cookies-de", valor)
    assert resultado.exit_code == 2
    assert "--cookies-de" in resultado.stderr
    assert "firefox" in resultado.stderr and "chrome" in resultado.stderr  # los admitidos
    assert filas(app_home) == []


@pytest.mark.parametrize("formato", ["mp4", "hls"])
def test_ninguna_cookie_en_disco(
    cli, app_home, navegador, video, servidor, destino, medios, esperas_cortas, monkeypatch,
    formato,
):
    """Ni la cola ni el directorio de trabajo guardan cookies (FR-017): se busca el valor de la
    cookie ficticia en los archivos del directorio de trabajo durante la espera tras un corte y
    justo antes de publicar, y en los del directorio de la app y la carpeta de destino al
    terminar (antes no: en Windows, `ejecutar.lock` y `cola.db-shm` tienen rangos bloqueados
    mientras `ejecutar` corre)."""
    from downloader import publish

    servidor.exigir_sesion(NOMBRE, VALOR)
    if formato == "mp4":
        # Más cortes que los reintentos internos de yt-dlp: el intento falla y se espera.
        url = video("v", cortar_tras_bytes=int(len(medios.datos_mp4) * 0.4), veces=4)
    else:
        manifiesto = servidor.registrar_hls("hls", medios.hls)
        servidor.programar("hls/fragmento2.ts", cortar_tras_bytes=1000, veces=4)
        servidor.registrar_video("v", manifiesto, titulo="Video v")
        url = servidor.url("watch/v")

    trabajo = destino / ".downloader-tmp" / "1"
    revisados = []  # (momento, archivos del directorio de trabajo)
    encontrados = []
    transicion = Store.transicion

    def revisar(momento):
        revisados.append((momento, sorted(p.name for p in trabajo.rglob("*") if p.is_file())))
        encontrados.extend(con_cookie(trabajo))

    def espiar(self, id_, hacia, **campos):
        if hacia is Estado.WAITING_NETWORK:
            revisar("esperando red")
        return transicion(self, id_, hacia, **campos)

    verificar = publish.verificar

    def antes_de_publicar(formatos, archivo):
        revisar("antes de publicar")
        return verificar(formatos, archivo)

    monkeypatch.setattr(Store, "transicion", espiar)
    monkeypatch.setattr(publish, "verificar", antes_de_publicar)
    assert cli("agregar", url, "--carpeta", destino, "--cookies-de", "firefox").exit_code == 0
    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    assert publicados(destino) == ["Video v [v].mp4"]
    momentos = dict(revisados)
    # La búsqueda vio los archivos del intento: el `.part` mientras se esperaba la red.
    assert any(nombre.endswith(".part") for nombre in momentos["esperando red"])
    assert momentos["antes de publicar"]
    encontrados += con_cookie(app_home) + con_cookie(destino)
    assert encontrados == []
    # Y lee de verdad la cola: el nombre del navegador sí está en ella.
    assert any(b"firefox" in p.read_bytes() for p in app_home.iterdir() if p.is_file())
    assert VALOR not in resultado.output


def test_los_mensajes_de_error_no_contienen_la_cookie(
    cli, app_home, navegador, video, servidor, destino, esperas_cortas, caplog
):
    """Un 403 constante con cookies pasa por `waiting_network` y acaba en `failed` (FR-026):
    ni la salida, ni los registros, ni `ultimo_error` incluyen la cookie."""
    caplog.set_level(logging.DEBUG)
    servidor.exigir_sesion(NOMBRE, VALOR)
    url = video("v", errores=[403] * 10)
    cli("agregar", url, "--carpeta", destino, "--cookies-de", "firefox")
    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output
    (fila,) = filas(app_home)
    assert fila.estado is Estado.FAILED and "--cookies-de" in fila.ultimo_error
    assert navegador.leidos
    assert VALOR not in resultado.output
    assert VALOR not in caplog.text
    assert VALOR not in (fila.ultimo_error or "")


def test_reencolar_con_cookies_de_tras_un_acceso_denegado(
    cli, app_home, navegador, video, servidor, destino, medios, esperas_cortas
):
    """El consejo de FR-026 funciona: «vuelva a agregarlo con --cookies-de NAVEGADOR»."""
    servidor.exigir_sesion(NOMBRE, VALOR)
    url = video("v")
    cli("agregar", url, "--carpeta", destino)
    assert cli("ejecutar").exit_code == 1
    resultado = cli("agregar", url, "--carpeta", destino, "--cookies-de", "firefox")
    assert resultado.exit_code == 0, resultado.output
    assert "reencolada #1" in resultado.output
    assert filas(app_home)[0].navegador_cookies == "firefox"

    resultado = cli("ejecutar")
    assert resultado.exit_code == 0, resultado.output
    assert (destino / "Video v [v].mp4").read_bytes() == medios.datos_mp4


# --- cookies que no se pueden leer (research R25) ---------------------------------------


def test_cookies_ilegibles_de_una_fila_fallan_solo_esa_descarga(
    cli, app_home, navegador, video, servidor, destino, esperas_cortas
):
    navegador.fallo = navegador_falso.no_encontrada
    cli("agregar", video("v"), "--carpeta", destino, "--cookies-de", "firefox")
    cli("agregar", video("w"), "--carpeta", destino)
    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output
    v, w = filas(app_home)
    assert v.estado is Estado.FAILED
    assert v.ultimo_error.startswith("No se pudieron leer las cookies de firefox: ")
    assert "no se encontró su base de datos de cookies" in v.ultimo_error
    assert v.ultimo_error in resultado.output  # también en el resumen
    assert w.estado is Estado.COMPLETED
    assert servidor.peticiones["v.mp4"] == 0  # sin cookies no se pide nada


def test_cookies_ilegibles_de_ejecutar_detienen_la_ejecucion(
    cli, app_home, navegador, video, servidor, destino, esperas_cortas
):
    """`ejecutar --cookies-de` vale para todas las descargas: si no se pueden leer, se detiene
    como un error fatal (código 3) sin dar ninguna por fallida; al corregirlo, continúa."""
    navegador.fallo = navegador_falso.bloqueada
    cli("agregar", video("v"), "--carpeta", destino)
    cli("agregar", video("w"), "--carpeta", destino)
    resultado = cli("ejecutar", "--cookies-de", "edge")

    assert resultado.exit_code == 3, resultado.output
    assert "No se pudieron leer las cookies de edge: " in resultado.stderr
    assert "bloqueada" in resultado.stderr and "cierre el navegador" in resultado.stderr
    assert "El progreso se ha conservado" in resultado.stderr
    assert [f.estado for f in filas(app_home)] == [Estado.QUEUED, Estado.QUEUED]
    assert servidor.peticiones["v.mp4"] == servidor.peticiones["w.mp4"] == 0

    navegador.fallo = None  # el usuario cerró el navegador
    resultado = cli("ejecutar", "--cookies-de", "edge")
    assert resultado.exit_code == 0, resultado.output
    assert [f.estado for f in filas(app_home)] == [Estado.COMPLETED, Estado.COMPLETED]
