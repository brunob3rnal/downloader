"""Directos y contenido con DRM: rechazados sin pedir los medios (escenario 10 de quickstart;
FR-015, Principio V, research R5)."""

import pytest

from downloader import publish
from downloader.models import Estado
from downloader.store import Store
from tests.integration.test_hls_aes import descifrado_entero
from tests.integration.test_solo_audio import tipos_de_flujo
from tests.support import dash, hls


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


# --- DASH, ISM y F4M: detección propia sobre el manifiesto (FR-015, hallazgo G1; R5) ---
#
# Los medios de cada representación (el video y el audio de `medios` por separado) existen y
# se servirían: si no se piden es porque se excluyeron o porque se rechazó antes.

TIPO_MPD = "application/dash+xml"


def registrar_manifiesto(servidor, id_video, nombre, cuerpo, tipo_contenido, archivos=None,
                         pagina=True):
    """Registra el manifiesto `<id_video>/<nombre>`, sus `archivos` y, con `pagina`, la página
    del video que apunta a él. Devuelve el nombre del manifiesto."""
    manifiesto = f"{id_video}/{nombre}"
    servidor.registrar(manifiesto, cuerpo, tipo_contenido=tipo_contenido)
    for archivo, datos in (archivos or {}).items():
        servidor.registrar(f"{id_video}/{archivo}", datos, tipo_contenido="video/mp4")
    if pagina:
        servidor.registrar_video(id_video, manifiesto)
    return manifiesto


def registrar_mpd(servidor, pistas, id_video, *periodos, dentro="", pagina=True):
    archivos = {"video-1440.mp4": pistas.video, "video-1080.mp4": pistas.video,
                "video-720.mp4": pistas.video, "audio.m4a": pistas.audio}
    return registrar_manifiesto(servidor, id_video, "manifiesto.mpd",
                                dash.mpd(*periodos, dentro=dentro), TIPO_MPD, archivos, pagina)


def rechazado_como_drm(cli, app_home, destino, servidor, transiciones, url, manifiesto, sistema,
                       calidad="mejor"):
    """`ejecutar` la deja `failed` en un solo intento sin pedir nada más que el manifiesto.

    Con un rechazo que depende de la calidad, el manifiesto se pide dos veces: la extracción
    sin descarga no conoce la calidad, y el rechazo llega al volver a extraer para descargar.
    """
    assert cli("agregar", url, "--calidad", calidad, "--carpeta", destino).exit_code == 0

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
    assert descarga.ultimo_error == f"contenido con DRM ({sistema}): no se puede descargar"
    assert f"#1 fallida  {descarga.ultimo_error}" in resultado.output
    assert {r for r in servidor.pedidas if not r.startswith("watch/")} == {manifiesto}
    assert not any(p.is_file() for p in destino.iterdir())


def descargada(cli, app_home, destino, url, calidad="mejor"):
    """`ejecutar` la completa; devuelve la descarga, el archivo publicado y la salida."""
    assert cli("agregar", url, "--calidad", calidad, "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.COMPLETED
    archivo = destino / descarga.archivo_final
    assert [p for p in destino.iterdir() if p.is_file()] == [archivo]
    publish.verificar([], archivo)  # se lee entero, con paquetes en todos los flujos (R8)
    descifrado_entero(archivo)  # y dura lo que el original
    return descarga, archivo, resultado.output


def video(id_, altura, dentro=""):
    return dash.representacion(id_, f"{id_}.mp4", dash.VIDEO, altura=altura, dentro=dentro)


AUDIO = dash.conjunto(dash.AUDIO, dash.representacion("audio", "audio.m4a", dash.AUDIO))
SUBREPRESENTACION = '<SubRepresentation level="0" contentComponent="1">{}</SubRepresentation>'


def video_protegido(nivel, proteccion):
    """El conjunto del video de 720p con `proteccion` en el `nivel` pedido."""
    if nivel == "adaptationset":
        return dash.conjunto(dash.VIDEO, video("video-720", 720), dentro=proteccion)
    if nivel == "subrepresentation":
        proteccion = SUBREPRESENTACION.format(proteccion)
    return dash.conjunto(dash.VIDEO, video("video-720", 720, dentro=proteccion))


def omitidas_en_el_aviso(descarga, salida, aviso):
    assert descarga.ultimo_aviso == aviso
    assert f"#1 aviso  {aviso}" in salida


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize("nivel", ["adaptationset", "representation", "subrepresentation"])
def test_mpd_con_el_video_protegido_se_rechaza_sin_descargar_el_audio(
    cli, app_home, destino, servidor, pistas, transiciones, nivel
):
    """(a) yt-dlp descartaba el video y descargaba solo el audio, sin aviso (R5)."""
    manifiesto = registrar_mpd(servidor, pistas, "protegido",
                               dash.periodo(video_protegido(nivel, dash.WIDEVINE_CP), AUDIO))
    rechazado_como_drm(cli, app_home, destino, servidor, transiciones,
                       servidor.url("watch/protegido"), manifiesto, "DASH")


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize("nivel", ["adaptationset", "representation"])
def test_mpd_con_el_video_protegido_y_calidad_audio_descarga_solo_el_audio(
    cli, app_home, destino, servidor, pistas, nivel
):
    """(b) Lo que queda sin proteger cubre `audio`: se descarga con el aviso (N = 1)."""
    registrar_mpd(servidor, pistas, "protegido",
                  dash.periodo(video_protegido(nivel, dash.WIDEVINE_CP), AUDIO))

    descarga, archivo, salida = descargada(cli, app_home, destino,
                                           servidor.url("watch/protegido"), "audio")

    tipos = tipos_de_flujo(archivo)
    assert "audio" in tipos and "video" not in tipos
    assert "protegido/video-720.mp4" not in servidor.pedidas
    assert servidor.bytes_servidos["protegido/audio.m4a"] == len(pistas.audio)
    omitidas_en_el_aviso(descarga, salida, "1 representación protegida omitida")


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize("calidad", ["mejor", "audio"])
@pytest.mark.parametrize("nivel", ["period", "mpd"])
def test_proteccion_en_el_period_o_en_el_mpd_rechaza_todo(
    cli, app_home, destino, servidor, pistas, transiciones, nivel, calidad
):
    """(c) yt-dlp no la detectaba y lo descargaba todo como si no estuviera protegido (R5)."""
    contenido = (dash.conjunto(dash.VIDEO, video("video-720", 720)), AUDIO)
    if nivel == "period":
        manifiesto = registrar_mpd(servidor, pistas, "protegido",
                                   dash.periodo(*contenido, dentro=dash.WIDEVINE_CP))
    else:
        manifiesto = registrar_mpd(servidor, pistas, "protegido", dash.periodo(*contenido),
                                   dentro=dash.WIDEVINE_CP)
    rechazado_como_drm(cli, app_home, destino, servidor, transiciones,
                       servidor.url("watch/protegido"), manifiesto, "DASH", calidad)


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize("proteccion", [dash.MP4PROTECTION, dash.MP4PROTECTION_KID],
                         ids=["sin-kid", "con-kid"])
@pytest.mark.parametrize("nivel", ["adaptationset", "representation"])
def test_mp4protection_solo_cuenta_como_proteccion(
    cli, app_home, destino, servidor, pistas, transiciones, nivel, proteccion
):
    """(d) `urn:mpeg:dash:mp4protection:2011` no nombra ningún sistema, pero protege."""
    manifiesto = registrar_mpd(servidor, pistas, "protegido",
                               dash.periodo(video_protegido(nivel, proteccion), AUDIO))
    rechazado_como_drm(cli, app_home, destino, servidor, transiciones,
                       servidor.url("watch/protegido"), manifiesto, "DASH")


def mpd_mixto():
    """1080p protegida; 720p y audio sin proteger, en el mismo conjunto que la 1080p."""
    return dash.periodo(
        dash.conjunto(dash.VIDEO, video("video-1080", 1080, dentro=dash.WIDEVINE_CP),
                      video("video-720", 720)),
        AUDIO,
    )


def mixto_descargado(servidor, pistas, archivo, prefijo):
    tipos = tipos_de_flujo(archivo)
    assert "video" in tipos and "audio" in tipos
    assert f"{prefijo}/video-1080.mp4" not in servidor.pedidas
    assert servidor.bytes_servidos[f"{prefijo}/video-720.mp4"] == len(pistas.video)
    assert servidor.bytes_servidos[f"{prefijo}/audio.m4a"] == len(pistas.audio)


@pytest.mark.ytdlp_internals
def test_mpd_mixto_descarga_la_720p_sin_pedir_la_1080p_protegida(cli, app_home, destino,
                                                                   servidor, pistas):
    """(e) Aviso en singular: una representación omitida."""
    registrar_mpd(servidor, pistas, "mixto", mpd_mixto())

    descarga, archivo, salida = descargada(cli, app_home, destino, servidor.url("watch/mixto"))

    mixto_descargado(servidor, pistas, archivo, "mixto")
    omitidas_en_el_aviso(descarga, salida, "1 representación protegida omitida")


@pytest.mark.ytdlp_internals
def test_aviso_en_plural_con_dos_representaciones_omitidas(cli, app_home, destino, servidor,
                                                           pistas):
    """La protección del conjunto alcanza a sus dos representaciones (1440p y 1080p)."""
    registrar_mpd(servidor, pistas, "plural", dash.periodo(
        dash.conjunto(dash.VIDEO, video("video-1440", 1440), video("video-1080", 1080),
                      dentro=dash.WIDEVINE_CP),
        dash.conjunto(dash.VIDEO, video("video-720", 720)),
        AUDIO,
    ))

    descarga, archivo, salida = descargada(cli, app_home, destino, servidor.url("watch/plural"))

    mixto_descargado(servidor, pistas, archivo, "plural")
    assert "plural/video-1440.mp4" not in servidor.pedidas
    omitidas_en_el_aviso(descarga, salida, "2 representaciones protegidas omitidas")


@pytest.mark.ytdlp_internals
def test_mpd_mixto_por_url_directa_con_el_extractor_generico(cli, app_home, destino, servidor,
                                                             pistas):
    """El extractor genérico lee 512 bytes del MPD antes que el resto."""
    manifiesto = registrar_mpd(servidor, pistas, "directo-dash", mpd_mixto(), pagina=False)

    descarga, archivo, salida = descargada(cli, app_home, destino, servidor.url(manifiesto))

    mixto_descargado(servidor, pistas, archivo, "directo-dash")
    omitidas_en_el_aviso(descarga, salida, "1 representación protegida omitida")


@pytest.mark.ytdlp_internals
def test_mpd_sin_proteccion_se_descarga_integro_y_sin_aviso(cli, app_home, destino, servidor,
                                                            pistas):
    registrar_mpd(servidor, pistas, "libre", dash.periodo(
        dash.conjunto(dash.VIDEO, video("video-1080", 1080), video("video-720", 720)), AUDIO,
    ))

    descarga, archivo, salida = descargada(cli, app_home, destino, servidor.url("watch/libre"))

    assert servidor.bytes_servidos["libre/video-1080.mp4"] == len(pistas.video)
    assert servidor.bytes_servidos["libre/audio.m4a"] == len(pistas.audio)
    assert "libre/video-720.mp4" not in servidor.pedidas
    assert descarga.ultimo_aviso is None
    assert "aviso" not in salida


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize(
    "proteccion",
    [dash.PROTECTION, dash.PROTECTION_SOLA, dash.PROTECTION_HEADER],
    ids=["protection-y-header", "protection", "protection-header"],
)
def test_ism_con_proteccion_se_rechaza(cli, app_home, destino, servidor, transiciones,
                                       proteccion):
    """(f) yt-dlp solo veía un `Protection` hijo directo de la raíz, con su propio mensaje."""
    manifiesto = registrar_manifiesto(servidor, "ism", "manifiesto.ism", dash.ism(proteccion),
                                      "text/xml")
    rechazado_como_drm(cli, app_home, destino, servidor, transiciones, servidor.url("watch/ism"),
                       manifiesto, "ISM")


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize("junto", [False, True], ids=["solo", "con-media-sin-cifrar"])
@pytest.mark.parametrize(
    "indicador",
    [dash.DRM_HEADER, dash.DRM_HEADER_SET, dash.MEDIA_CON_HEADER_ID,
     dash.MEDIA_CON_HEADER_SET_ID],
    ids=["drm-header", "drm-header-set", "media-header-id", "media-header-set-id"],
)
def test_f4m_con_drm_se_rechaza_entero(cli, app_home, destino, servidor, transiciones,
                                       indicador, junto):
    """(g) yt-dlp descartaba en silencio los `media` cifrados, sin marcarlos (R5)."""
    elementos = [indicador, dash.MEDIA_SIN_CIFRAR] if junto else [indicador]
    manifiesto = registrar_manifiesto(servidor, "hds", "manifiesto.f4m", dash.f4m(*elementos),
                                      "application/f4m+xml")
    rechazado_como_drm(cli, app_home, destino, servidor, transiciones, servidor.url("watch/hds"),
                       manifiesto, "F4M")


# --- Formatos que el extractor entrega ya marcados `has_drm` (R5, hallazgo H1; FR-015) ---
#
# Sin manifiesto que analizar: yt-dlp descarta el video protegido y, con cualquier selector,
# elige el audio sin avisar. Los medios existen y se servirían.


def registrar_pistas(servidor, pistas, id_video):
    servidor.registrar(f"medios/{id_video}/video.mp4", pistas.video, tipo_contenido="video/mp4")
    servidor.registrar(f"medios/{id_video}/audio.m4a", pistas.audio, tipo_contenido="audio/mp4")


@pytest.mark.ytdlp_internals
@pytest.mark.parametrize("calidad", ["mejor", "1080p", "720p"])
def test_video_marcado_has_drm_se_rechaza_sin_descargar_el_audio(
    cli, app_home, destino, servidor, pistas, transiciones, calidad
):
    """(a) Con una calidad de video, la selección sin video se rechaza como DRM."""
    registrar_pistas(servidor, pistas, "drm-video")
    assert cli("agregar", servidor.url("watch/drm-video"), "--calidad", calidad,
               "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.FAILED
    assert (1, Estado.WAITING_NETWORK) not in transiciones  # sin reintentos: permanente
    assert descarga.ultimo_error == "contenido con DRM (video protegido): no se puede descargar"
    assert f"#1 fallida  {descarga.ultimo_error}" in resultado.output
    assert servidor.pedidas == []  # ni el video ni el audio
    assert not any(p.is_file() for p in destino.iterdir())


@pytest.mark.ytdlp_internals
def test_video_marcado_has_drm_con_calidad_audio_descarga_el_audio(cli, app_home, destino,
                                                                   servidor, pistas):
    """(b) Con `audio`, lo que se pide no está protegido."""
    registrar_pistas(servidor, pistas, "drm-video")

    _, archivo, _ = descargada(cli, app_home, destino, servidor.url("watch/drm-video"),
                               "audio")

    tipos = tipos_de_flujo(archivo)
    assert "audio" in tipos and "video" not in tipos
    assert "medios/drm-video/video.mp4" not in servidor.pedidas
    assert servidor.bytes_servidos["medios/drm-video/audio.m4a"] == len(pistas.audio)


@pytest.mark.ytdlp_internals
def test_solo_audio_sin_has_drm_se_descarga(cli, app_home, destino, servidor, pistas):
    """(c) Un contenido que solo es audio, sin `has_drm`, no es DRM."""
    registrar_pistas(servidor, pistas, "solo-audio")

    descarga, archivo, _ = descargada(cli, app_home, destino,
                                      servidor.url("watch/solo-audio"))

    assert tipos_de_flujo(archivo) == ["audio"]
    assert servidor.bytes_servidos["medios/solo-audio/audio.m4a"] == len(pistas.audio)
    assert descarga.ultimo_aviso is None


@pytest.mark.ytdlp_internals
def test_vcodec_desconocido_sin_has_drm_se_descarga(cli, app_home, destino, servidor, medios):
    """(d) Un `vcodec` `None` (códec desconocido) cuenta como video."""
    servidor.registrar("medios/sin-vcodec.mp4", medios.datos_mp4, tipo_contenido="video/mp4")

    _, archivo, _ = descargada(cli, app_home, destino, servidor.url("watch/sin-vcodec"))

    assert sorted(tipos_de_flujo(archivo)) == ["audio", "video"]
    assert servidor.bytes_servidos["medios/sin-vcodec.mp4"] == len(medios.datos_mp4)
