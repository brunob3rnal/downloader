"""URL de medios que caduca a mitad de la descarga (escenario 5 de quickstart; US4, FR-012,
SC-008)."""

from downloader.models import Estado
from downloader.store import Store


def test_403_por_url_caducada_vuelve_a_extraer_y_continua_el_mismo_parcial(
    cli, app_home, destino, servidor, medios, esperas_cortas, monkeypatch
):
    datos = medios.datos_mp4
    servidor.registrar("v.mp4", datos, tipo_contenido="video/mp4", tokens=True)
    servidor.registrar_video("v", "v.mp4", titulo="Video con URL temporal")
    # Corta al 40 % e invalida los tokens emitidos: repetir la petición con la misma URL
    # recibe 403, y solo una extracción nueva trae un token válido.
    servidor.programar("v.mp4", cortar_tras_bytes=int(len(datos) * 0.4), caducar_tokens=True)

    esperas = []  # `ultimo_error` de cada paso a `waiting_network`
    transicion = Store.transicion

    def espiar(self, id_, hacia, **campos):
        if hacia is Estado.WAITING_NETWORK:
            esperas.append(campos.get("ultimo_error"))
        return transicion(self, id_, hacia, **campos)

    monkeypatch.setattr(Store, "transicion", espiar)

    assert cli("agregar", servidor.url("watch/v"), "--carpeta", destino).exit_code == 0
    resultado = cli("ejecutar")  # una sola ejecución: sin intervención del usuario

    assert resultado.exit_code == 0, resultado.output
    assert esperas and all("403" in causa for causa in esperas)
    (publicado,) = [p for p in destino.iterdir() if p.is_file()]
    assert publicado.read_bytes() == datos
    # Continuó el mismo `.part` con la URL nueva en lugar de volver a empezar (SC-002).
    assert servidor.bytes_servidos["v.mp4"] <= 1.01 * len(datos)
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.COMPLETED
    assert descarga.ultimo_aviso is None  # ningún reinicio desde cero
    assert "reiniciada" not in resultado.output
