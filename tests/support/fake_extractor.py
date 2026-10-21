"""Extractor de yt-dlp para las URLs del servidor de pruebas.

Reconoce `http://127.0.0.1:<puerto>/watch/<id>` y `http://127.0.0.1:<puerto>/playlist/<id>`.
Los videos normales y las listas se consultan al `flaky_server` (que devuelve un token nuevo en
cada consulta), así que la extracción pasa por la red local igual que con un sitio real.

Escenarios por `<id>`:

- `privado`, `borrado`: `ExtractorError(expected=True)` con mensaje claro.
- `directo`: `is_live=True`.
- `drm`: todos los formatos con `has_drm=True` (yt-dlp lo rechaza como "DRM protected").
- cualquier otro: lo que el servidor tenga registrado con `registrar_video`; si el archivo es
  un manifiesto `.m3u8`, el formato es HLS.
"""

from yt_dlp import YoutubeDL
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

MENSAJES = {
    "privado": "Video privado: el propietario restringió el acceso",
    "borrado": "Video no disponible: fue borrado por su autor",
}


class FalsoIE(InfoExtractor):
    IE_NAME = "falso"
    _VALID_URL = r"http://127\.0\.0\.1:(?P<puerto>\d+)/(?P<tipo>watch|playlist)/(?P<id>[^/?#&]+)"

    def _real_extract(self, url):
        puerto, tipo, id_ = self._match_valid_url(url).group("puerto", "tipo", "id")
        base = f"http://127.0.0.1:{puerto}"
        if tipo == "playlist":
            return self._lista(base, id_)
        if id_ in MENSAJES:
            raise ExtractorError(MENSAJES[id_], expected=True, video_id=id_)
        if id_ in ("directo", "drm"):
            return self._especial(base, id_)

        pagina = self._download_json(url, id_)
        if pagina.get("lista") and not self.get_param("noplaylist"):
            return self._lista(base, pagina["lista"])

        if pagina["url"].split("?")[0].endswith(".m3u8"):
            formatos = [{"format_id": "hls", "url": pagina["url"], "ext": "mp4",
                         "protocol": "m3u8_native", "height": pagina["alturas"][0]}]
        else:
            formatos = [
                {"format_id": f"{altura}p", "url": pagina["url"], "ext": "mp4", "height": altura,
                 "vcodec": "h264", "acodec": "aac"}
                for altura in pagina["alturas"]
            ]
        return {"id": id_, "title": pagina["titulo"], "formats": formatos}

    def _lista(self, base, id_lista):
        pagina = self._download_json(f"{base}/playlist/{id_lista}", id_lista)
        entradas = [
            self.url_result(f"{base}/watch/{id_video}", FalsoIE, id_video)
            for id_video in pagina["entradas"]
        ]
        return self.playlist_result(entradas, id_lista, pagina["titulo"])

    def _especial(self, base, id_):
        formato = {"format_id": "720p", "url": f"{base}/medios/{id_}.mp4", "ext": "mp4",
                   "height": 720, "vcodec": "h264", "acodec": "aac"}
        if id_ == "drm":
            formato["has_drm"] = True
        return {"id": id_, "title": f"Video {id_}", "formats": [formato],
                "is_live": id_ == "directo"}


def registrar(ydl):
    """Añade `FalsoIE` a un `YoutubeDL`.

    yt-dlp prueba los extractores en el orden en que se añadieron y el genérico acepta
    cualquier URL, así que hay que llamarlo antes de cargar los extractores por defecto
    (`YoutubeDL(..., auto_init=False)`); `crear_ydl` lo hace así.
    """
    ydl.add_info_extractor(FalsoIE())


def crear_ydl(opciones):
    """Sustituto de `downloader.ytdl.crear_ydl` que reconoce las URLs de prueba."""
    ydl = YoutubeDL(opciones, auto_init=False)
    registrar(ydl)
    ydl.add_default_info_extractors()
    return ydl


def instalar(monkeypatch):
    """Hace que `downloader.ytdl` use este extractor durante el test.

    Importa `downloader` aquí, no al cargar el módulo, para que un test de una fase aún no
    implementada falle dentro del test (xfail) y no al recoger los tests.
    """
    from downloader import ytdl

    monkeypatch.setattr(ytdl, "crear_ydl", crear_ydl)
