"""Directos y contenido con DRM: rechazados sin pedir los medios (escenario 10 de quickstart;
FR-015, Principio V, research R5)."""

import pytest

from downloader.models import Estado
from downloader.store import Store
from tests.support import hls


@pytest.mark.parametrize(
    ("id_video", "causa"),
    [("directo", "transmisión en directo"), ("drm", "DRM")],
)
def test_rechazado_sin_peticion_de_medios(cli, app_home, destino, servidor, medios, id_video,
                                          causa):
    # Los medios existen y se servirían: si no se piden es porque se rechazó antes.
    medios_video = f"medios/{id_video}.mp4"
    servidor.registrar(medios_video, medios.datos_mp4, tipo_contenido="video/mp4")
    assert cli("agregar", servidor.url(f"watch/{id_video}"), "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.FAILED
    assert causa in descarga.ultimo_error
    assert f"#1 fallida  {descarga.ultimo_error}" in resultado.output
    assert servidor.peticiones[medios_video] == 0
    assert not any(p.is_file() for p in destino.iterdir())


# --- HLS cifrado: DRM y métodos no soportados (Principio V v3.0.0; FR-015, FR-008; R5) ---
#
# El HLS de `hls_cifrado` con otra etiqueta de clave. La clave (`<id>/clave.key`) y los
# fragmentos existen y se servirían: si no se piden es porque se rechazó antes.

NO_SOPORTADO = (
    "no soportado: el cifrado SAMPLE-AES solo se puede descargar a través de ffmpeg, que no "
    "permite reanudar de forma fiable"
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


@pytest.fixture
def ffmpeg_lanzado(monkeypatch):
    """Cada vez que yt-dlp delega la descarga en ffmpeg (`FFmpegFD`), que no llega a lanzarse."""
    from yt_dlp.downloader.external import FFmpegFD

    llamadas = []

    def real_download(self, filename, info_dict):
        llamadas.append(filename)
        return False

    monkeypatch.setattr(FFmpegFD, "real_download", real_download)
    return llamadas


def registrar_hls(servidor, tmp_path, hls_cifrado, id_video, *lineas, maestro=None,
                  incrustado=False):
    """Registra el HLS cifrado con `lineas` en lugar de su `#EXT-X-KEY`, su clave y su página.

    Con `maestro` (las líneas del manifiesto maestro), la página apunta a un maestro con esas
    líneas y una variante, el HLS. Con `incrustado`, el extractor entrega el manifiesto ya leído
    (`hls_media_playlist_data`). Devuelve los nombres de la clave y de los fragmentos.
    """
    directorio = hls.con_claves(hls_cifrado, tmp_path / id_video, *lineas)
    manifiesto = servidor.registrar_hls(id_video, directorio)
    servidor.registrar(f"{id_video}/clave.key", hls.CLAVE)
    if maestro is not None:
        servidor.registrar(f"{id_video}/maestro.m3u8", hls.maestro(*maestro),
                           tipo_contenido="application/vnd.apple.mpegurl")
        servidor.registrar_video(id_video, f"{id_video}/maestro.m3u8", maestro=True)
    else:
        texto = (directorio / "index.m3u8").read_text() if incrustado else None
        servidor.registrar_video(id_video, manifiesto, incrustado=texto)
    fragmentos = [f"{id_video}/{p.name}" for p in sorted(directorio.glob("*.ts"))]
    assert fragmentos
    return [f"{id_video}/clave.key", *fragmentos]


def rechazada_sin_peticiones(cli, app_home, destino, servidor, transiciones, url, pedibles):
    """`ejecutar` la deja `failed` en un solo intento, sin pedir la clave ni los fragmentos."""
    assert cli("agregar", url, "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.FAILED
    assert (1, Estado.WAITING_NETWORK) not in transiciones  # sin reintentos: permanente
    assert descarga.intentos_sin_avance == 0
    assert f"#1 fallida  {descarga.ultimo_error}" in resultado.output
    assert {nombre: servidor.peticiones[nombre] for nombre in pedibles} == dict.fromkeys(
        pedibles, 0
    )
    assert not any(p.is_file() for p in destino.iterdir())
    return descarga


IV = f"IV={hls.IV}"
PSSH_WIDEVINE = hls.uri_data(hls.caja_pssh(hls.WIDEVINE))


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize(
    ("lineas", "sistema"),
    [
        # Los que ya reconoce yt-dlp, según el orden de sus atributos (T089 a).
        (['#EXT-X-KEY:METHOD=AES-128,URI="skd://clave-ficticia"'], "FairPlay"),
        ([f'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://clave-ficticia",KEYFORMAT="{hls.FAIRPLAY}",'
          'KEYFORMATVERSIONS="1"'], "FairPlay"),
        ([f'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="clave.key",KEYFORMAT="{hls.PLAYREADY}"'],
         "PlayReady"),
        # Los que yt-dlp descargaría o pasaría a ffmpeg (T090).
        ([f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV},KEYFORMAT="{hls.WIDEVINE}"'],
         "Widevine"),
        ([f'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="{PSSH_WIDEVINE}",KEYFORMAT="{hls.WIDEVINE}"'],
         "Widevine"),
        ([f'#EXT-X-KEY:METHOD=AES-128,URI="{PSSH_WIDEVINE}",{IV}'], "Widevine"),
        # Con `URI` antes que `METHOD`, que la expresión de yt-dlp no reconoce.
        ([f'#EXT-X-KEY:URI="clave.key",KEYFORMAT="{hls.WIDEVINE}",METHOD=AES-128,{IV}'],
         "Widevine"),
        ([f'#EXT-X-KEY:URI="skd://clave-ficticia",METHOD=SAMPLE-AES,KEYFORMAT="{hls.FAIRPLAY}"'],
         "FairPlay"),
    ],
    ids=["aes-skd", "fairplay", "playready", "aes-widevine", "sample-widevine", "aes-pssh",
         "uri-primero-widevine", "uri-primero-fairplay"],
)
def test_hls_con_drm_rechazado_sin_pedir_clave_ni_fragmentos(
    cli, app_home, destino, servidor, tmp_path, hls_cifrado, transiciones, lineas, sistema
):
    pedibles = registrar_hls(servidor, tmp_path, hls_cifrado, "protegido", *lineas)
    descarga = rechazada_sin_peticiones(cli, app_home, destino, servidor, transiciones,
                                        servidor.url("watch/protegido"), pedibles)
    assert descarga.ultimo_error == f"contenido con DRM ({sistema}): no se puede descargar"


@pytest.mark.ytdlp_internals
def test_session_key_del_maestro_con_widevine(cli, app_home, destino, servidor, tmp_path,
                                               hls_cifrado, transiciones):
    """La variante está cifrada sin licencias, pero el maestro declara Widevine (T090)."""
    pedibles = registrar_hls(
        servidor, tmp_path, hls_cifrado, "maestro",
        f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV}',
        maestro=[f'#EXT-X-SESSION-KEY:METHOD=AES-128,URI="{PSSH_WIDEVINE}",'
                 f'KEYFORMAT="{hls.WIDEVINE}"'],
    )
    descarga = rechazada_sin_peticiones(cli, app_home, destino, servidor, transiciones,
                                        servidor.url("watch/maestro"), pedibles)
    assert descarga.ultimo_error == "contenido con DRM (Widevine): no se puede descargar"


@pytest.mark.ytdlp_internals
def test_manifiesto_entregado_por_el_extractor_con_widevine(
    cli, app_home, destino, servidor, tmp_path, hls_cifrado, transiciones
):
    """`hls_media_playlist_data`: la descarga no pide el manifiesto, lo trae la extracción."""
    pedibles = registrar_hls(
        servidor, tmp_path, hls_cifrado, "incrustado",
        f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV},KEYFORMAT="{hls.WIDEVINE}"',
        incrustado=True,
    )
    descarga = rechazada_sin_peticiones(cli, app_home, destino, servidor, transiciones,
                                        servidor.url("watch/incrustado"),
                                        [*pedibles, "incrustado/index.m3u8"])
    assert descarga.ultimo_error == "contenido con DRM (Widevine): no se puede descargar"


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize(
    ("linea", "metodo"),
    [
        ('#EXT-X-KEY:METHOD=SAMPLE-AES,URI="clave.key",KEYFORMAT="identity",'
         'KEYFORMATVERSIONS="1"', "SAMPLE-AES"),
        ('#EXT-X-KEY:URI="clave.key",METHOD=SAMPLE-AES,KEYFORMAT="identity"', "SAMPLE-AES"),
        ('#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="clave.key",KEYFORMAT="identity"',
         "SAMPLE-AES-CTR"),
        ('#EXT-X-KEY:METHOD=AES-256,URI="clave.key",KEYFORMAT="identity"', "AES-256"),
    ],
    ids=["sample-aes", "uri-primero-sample-aes", "sample-aes-ctr", "metodo-desconocido"],
)
def test_metodo_no_soportado_con_clave_identity(
    cli, app_home, destino, servidor, tmp_path, hls_cifrado, transiciones, ffmpeg_lanzado,
    linea, metodo
):
    """No es DRM, pero solo ffmpeg sabría descargarlo, sin reanudar (T090a; FR-008)."""
    pedibles = registrar_hls(servidor, tmp_path, hls_cifrado, "muestra", linea)
    descarga = rechazada_sin_peticiones(cli, app_home, destino, servidor, transiciones,
                                        servidor.url("watch/muestra"), pedibles)
    assert descarga.ultimo_error.startswith(NO_SOPORTADO)
    assert metodo in descarga.ultimo_error
    assert "DRM" not in descarga.ultimo_error
    assert ffmpeg_lanzado == []
