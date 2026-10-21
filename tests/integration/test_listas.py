"""Listas de reproducción (US1 escenario 5, FR-004, research R13)."""

from downloader.models import Estado, TipoDescarga
from downloader.store import Store


def todas(app_home):
    store = Store.abrir(app_home)
    try:
        return [store.obtener(i) for i in range(1, store.contar() + 1)]
    finally:
        store.cerrar()


def registrar(servidor, medios, *ids):
    for id_video in ids:
        servidor.registrar(f"{id_video}.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
        servidor.registrar_video(id_video, f"{id_video}.mp4", titulo=f"Video {id_video}")


def test_lista_con_un_video_no_disponible(cli, app_home, destino, servidor, medios):
    registrar(servidor, medios, "v1", "v2")
    servidor.registrar_lista("l1", ["v1", "borrado", "v2"], titulo="Mi lista")

    assert cli("agregar", servidor.url("playlist/l1"), "--calidad", "720p",
               "--carpeta", destino).exit_code == 0
    resultado = cli("ejecutar")

    padre, *hijas = todas(app_home)
    assert padre.tipo is TipoDescarga.PLAYLIST
    assert padre.estado is Estado.COMPLETED
    assert padre.archivo_final is None
    assert [h.url for h in hijas] == [servidor.url(f"watch/{i}") for i in ("v1", "borrado", "v2")]
    for hija in hijas:
        assert hija.padre_id == padre.id
        assert hija.calidad == padre.calidad
        assert hija.carpeta_destino == padre.carpeta_destino
    assert [h.estado for h in hijas] == [Estado.COMPLETED, Estado.FAILED, Estado.COMPLETED]
    assert "borrado" in hijas[1].ultimo_error
    assert f"#{hijas[1].id} fallida  " in resultado.output
    assert sorted(p.name for p in destino.iterdir() if p.is_file()) == [
        "Video v1 [v1].mp4", "Video v2 [v2].mp4",
    ]


def test_video_dentro_de_una_lista_descarga_solo_el_video(cli, app_home, destino, servidor,
                                                          medios):
    registrar(servidor, medios, "v1", "v2")
    servidor.registrar_lista("l1", ["v1", "v2"])

    assert cli("agregar", servidor.url("watch/v1") + "?list=l1",
               "--carpeta", destino).exit_code == 0
    assert cli("ejecutar").exit_code == 0

    (unica,) = todas(app_home)
    assert unica.tipo is TipoDescarga.VIDEO
    assert unica.estado is Estado.COMPLETED
    assert [p.name for p in destino.iterdir() if p.is_file()] == ["Video v1 [v1].mp4"]
