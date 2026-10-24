"""Nombre de los archivos publicados: `plantilla_nombre` en `config.toml` (T074; FR-006,
FR-022). Por defecto es el título, sin el id del video: dos videos con el mismo título se
publican sin pisarse, el segundo con ` (1)`."""

from downloader.store import Store


def registrar(servidor, medios, id_video, titulo):
    servidor.registrar(f"{id_video}.mp4", medios.datos_mp4, tipo_contenido="video/mp4")
    servidor.registrar_video(id_video, f"{id_video}.mp4", titulo=titulo)
    return servidor.url(f"watch/{id_video}")


def publicados(destino):
    return sorted(p.name for p in destino.iterdir() if p.is_file())


def test_por_defecto_el_titulo_y_las_colisiones_con_sufijo(cli, app_home, destino, servidor,
                                                         medios):
    urls = [registrar(servidor, medios, i, "Mismo título") for i in ("a1", "b2")]
    assert cli("agregar", *urls, "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    assert publicados(destino) == ["Mismo título (1).mp4", "Mismo título.mp4"]
    for nombre in publicados(destino):
        assert (destino / nombre).read_bytes() == medios.datos_mp4
    aviso = "ya existía «Mismo título.mp4»; se publicó como «Mismo título (1).mp4»"
    assert f"#2 aviso  {aviso}" in resultado.output
    store = Store.abrir(app_home)
    try:
        assert aviso in store.obtener(2).ultimo_aviso
    finally:
        store.cerrar()


def test_plantilla_de_config_toml(cli, app_home, destino, servidor, medios):
    configuracion = app_home / "config.toml"
    configuracion.write_text(
        configuracion.read_text() + 'plantilla_nombre = "{id} - {titulo}"\n', encoding="utf-8"
    )
    url = registrar(servidor, medios, "a1", "Video uno")
    assert cli("agregar", url, "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    assert publicados(destino) == ["a1 - Video uno.mp4"]
