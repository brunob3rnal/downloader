"""Un manifiesto mayor que el tope de `_vigilar_xml` se entrega sin analizar, con un aviso
visible (defecto D2, Principio III; 002 research R20)."""

from downloader import ytdl
from tests.integration.test_directo_drm import AUDIO, descargada, registrar_mpd, video
from tests.support import dash


# Depende de que el extractor genérico lea 512 bytes antes que el resto (R5). Sin el marcador
# `ytdlp_internals`: `tests/unit/test_marcadores.py` (001) fija la lista exacta de esos tests.
def test_mpd_mayor_que_el_tope_se_descarga_con_aviso(cli, app_home, destino, servidor, pistas,
                                                     monkeypatch):
    monkeypatch.setattr(ytdl, "TOPE_XML", 4096)
    relleno = "<!--" + "x" * 3 * 4096 + "-->"
    # Por URL directa: el extractor genérico lee 512 bytes antes que el resto, así que el
    # cuerpo se lee a trozos y el tope actúa. (Un `read()` entero ya lo tiene todo en memoria
    # y se analiza siempre.)
    manifiesto = registrar_mpd(
        servidor, pistas, "grande",
        dash.periodo(dash.conjunto(dash.VIDEO, video("video-720", 720)), AUDIO),
        dentro=relleno, pagina=False,
    )

    descarga, _, salida = descargada(cli, app_home, destino, servidor.url(manifiesto))

    aviso = descarga.ultimo_aviso
    assert aviso.startswith("manifiesto de más de 16 MB entregado sin comprobar si tiene DRM: ")
    assert "/grande/manifiesto.mpd" in aviso
    assert f"#1 aviso  {aviso}" in salida


def test_el_aviso_no_muestra_secretos():
    aviso = ytdl.aviso_sin_analizar(["https://u:clave@ejemplo.com/m.mpd?token=abc123def456"])
    assert "clave" not in aviso and "abc123def456" not in aviso
    assert ytdl.aviso_sin_analizar([]) is None
