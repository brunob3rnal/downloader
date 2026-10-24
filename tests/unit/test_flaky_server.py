import http.client
import socket
import urllib.error
import urllib.request

import pytest

DATOS = bytes(range(256)) * 400  # 102 400 bytes deterministas


def pedir(url, rango=None, timeout=5):
    peticion = urllib.request.Request(url)
    if rango:
        peticion.add_header("Range", rango)
    return urllib.request.urlopen(peticion, timeout=timeout)


def test_sirve_archivo_completo(servidor):
    servidor.registrar("a.bin", DATOS)
    with pedir(servidor.url("a.bin")) as respuesta:
        assert respuesta.status == 200
        assert respuesta.headers["Accept-Ranges"] == "bytes"
        assert int(respuesta.headers["Content-Length"]) == len(DATOS)
        assert respuesta.read() == DATOS
    assert servidor.bytes_servidos["a.bin"] == len(DATOS)


def test_range_parcial(servidor):
    servidor.registrar("a.bin", DATOS)
    with pedir(servidor.url("a.bin"), "bytes=1000-") as respuesta:
        assert respuesta.status == 206
        assert respuesta.headers["Content-Range"] == f"bytes 1000-{len(DATOS) - 1}/{len(DATOS)}"
        assert respuesta.read() == DATOS[1000:]
    with pedir(servidor.url("a.bin"), "bytes=10-19") as respuesta:
        assert respuesta.read() == DATOS[10:20]


def test_corta_tras_n_bytes_las_veces_indicadas(servidor):
    servidor.registrar("a.bin", DATOS, cortar_tras_bytes=1000, veces=2)
    for _ in range(2):
        with pedir(servidor.url("a.bin")) as respuesta:
            with pytest.raises(http.client.IncompleteRead) as error:
                respuesta.read()
        assert len(error.value.partial) == 1000
    with pedir(servidor.url("a.bin")) as respuesta:
        assert respuesta.read() == DATOS
    assert servidor.bytes_servidos["a.bin"] == 2000 + len(DATOS)


def test_se_cuelga_hasta_el_timeout_del_cliente(servidor):
    servidor.registrar("a.bin", DATOS, colgarse=True)
    with pytest.raises((TimeoutError, socket.timeout, urllib.error.URLError)):
        pedir(servidor.url("a.bin"), timeout=0.5)
    with pedir(servidor.url("a.bin")) as respuesta:
        assert respuesta.read() == DATOS


def test_errores_y_luego_200(servidor):
    servidor.registrar("a.bin", DATOS, errores=[503, 500, 503])
    for codigo in (503, 500, 503):
        with pytest.raises(urllib.error.HTTPError) as error:
            pedir(servidor.url("a.bin"))
        assert error.value.code == codigo
    with pedir(servidor.url("a.bin")) as respuesta:
        assert respuesta.status == 200
        assert respuesta.read() == DATOS


def test_sin_range_ignora_la_cabecera(servidor):
    servidor.registrar("a.bin", DATOS, sin_range=True)
    with pedir(servidor.url("a.bin"), "bytes=1000-") as respuesta:
        assert respuesta.status == 200
        assert "Accept-Ranges" not in respuesta.headers
        assert respuesta.read() == DATOS


def test_tokens_caducados_dan_403(servidor):
    servidor.registrar("a.bin", DATOS, tokens=True)
    token = servidor.emitir_token()
    with pedir(servidor.url("a.bin", token)) as respuesta:
        assert respuesta.read() == DATOS
    with pytest.raises(urllib.error.HTTPError) as sin_token:
        pedir(servidor.url("a.bin"))
    assert sin_token.value.code == 403
    servidor.caducar_tokens()
    with pytest.raises(urllib.error.HTTPError) as caducado:
        pedir(servidor.url("a.bin", token))
    assert caducado.value.code == 403
    with pedir(servidor.url("a.bin", servidor.emitir_token())) as respuesta:
        assert respuesta.read() == DATOS


def test_anuncia_un_tamano_distinto(servidor):
    servidor.registrar("a.bin", DATOS, anunciar_tamano=len(DATOS) + 500)
    with pedir(servidor.url("a.bin")) as respuesta:
        assert int(respuesta.headers["Content-Length"]) == len(DATOS) + 500
        with pytest.raises(http.client.IncompleteRead) as error:
            respuesta.read()
    assert error.value.partial == DATOS


def test_fragmento_ausente_responde_404(servidor, tmp_path):
    (tmp_path / "index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nf0.ts\n#EXTINF:2,\nf1.ts\n#EXT-X-ENDLIST\n"
    )
    (tmp_path / "f0.ts").write_bytes(b"cero")
    (tmp_path / "f1.ts").write_bytes(b"uno")
    manifiesto = servidor.registrar_hls("hls", tmp_path, fragmento_ausente=1)
    assert manifiesto == "hls/index.m3u8"
    with pedir(servidor.url("hls/f0.ts")) as respuesta:
        assert respuesta.read() == b"cero"
    for _ in range(3):
        with pytest.raises(urllib.error.HTTPError) as error:
            pedir(servidor.url("hls/f1.ts"))
        assert error.value.code == 404


def test_detener_y_reanudar_en_el_mismo_puerto(servidor):
    servidor.registrar("a.bin", DATOS)
    puerto = servidor.puerto
    servidor.detener()
    with pytest.raises(urllib.error.URLError):
        pedir(servidor.url("a.bin"), timeout=1)
    servidor.reanudar()
    assert servidor.puerto == puerto
    with pedir(servidor.url("a.bin")) as respuesta:
        assert respuesta.read() == DATOS


def test_detener_corta_una_conexion_colgada(servidor):
    servidor.registrar("a.bin", DATOS, colgarse=True)
    conexion = socket.create_connection(("127.0.0.1", servidor.puerto), timeout=5)
    conexion.sendall(b"GET /a.bin HTTP/1.1\r\nHost: x\r\n\r\n")
    servidor.detener()
    try:
        assert conexion.recv(1024) == b""
    except ConnectionResetError:
        pass
    conexion.close()


def test_paginas_del_extractor(servidor):
    import json

    servidor.registrar("v.mp4", DATOS, tokens=True)
    servidor.registrar_video("abc", "v.mp4", titulo="Título")
    servidor.registrar_lista("l1", ["abc", "borrado"])
    with pedir(servidor.url("watch/abc")) as respuesta:
        pagina = json.load(respuesta)
    assert pagina["titulo"] == "Título"
    with pedir(pagina["url"]) as respuesta:
        assert respuesta.read() == DATOS
    with pedir(servidor.url("playlist/l1")) as respuesta:
        assert json.load(respuesta)["entradas"] == ["abc", "borrado"]


def test_velocidad_limitada(servidor):
    import time

    servidor.registrar("a.bin", DATOS, velocidad=400_000)
    inicio = time.monotonic()
    with pedir(servidor.url("a.bin")) as respuesta:
        assert respuesta.read() == DATOS
    assert time.monotonic() - inicio >= 0.2  # 102 400 bytes a 400 kB/s ≈ 0,25 s


def test_caducar_tokens_tras_servir(servidor):
    servidor.registrar("a.bin", DATOS, tokens=True)
    servidor.programar("a.bin", caducar_tokens=True)
    token = servidor.emitir_token()
    with pedir(servidor.url("a.bin", token)) as respuesta:
        assert respuesta.read() == DATOS
    with pytest.raises(urllib.error.HTTPError) as error:
        pedir(servidor.url("a.bin", token))
    assert error.value.code == 403


def test_cortar_y_caducar_en_la_misma_peticion(servidor):
    servidor.registrar("a.bin", DATOS, tokens=True)
    servidor.programar("a.bin", cortar_tras_bytes=1000, caducar_tokens=True)
    token = servidor.emitir_token()
    with pedir(servidor.url("a.bin", token)) as respuesta:
        with pytest.raises(http.client.IncompleteRead):
            respuesta.read()
    with pytest.raises(urllib.error.HTTPError) as error:
        pedir(servidor.url("a.bin", token), "bytes=1000-")
    assert error.value.code == 403


def test_manifiesto_hls_con_token(servidor, tmp_path):
    (tmp_path / "index.m3u8").write_text("#EXTM3U\n#EXTINF:2,\nf0.ts\n#EXT-X-ENDLIST\n")
    (tmp_path / "f0.ts").write_bytes(b"cero")
    servidor.registrar_hls("hls", tmp_path, tokens=True)
    token = servidor.emitir_token()
    with pedir(servidor.url("hls/index.m3u8", token)) as respuesta:
        assert f"f0.ts?token={token}" in respuesta.read().decode()
    with pedir(servidor.url("hls/f0.ts", token)) as respuesta:
        assert respuesta.read() == b"cero"
    with pytest.raises(urllib.error.HTTPError):
        pedir(servidor.url("hls/f0.ts"))


def test_exigir_sesion(servidor):
    servidor.registrar("a.bin", DATOS)
    servidor.registrar_video("v", "a.bin")
    servidor.exigir_sesion("SID", "ficticia")

    def pedir_con(ruta, cookie):
        peticion = urllib.request.Request(servidor.url(ruta))
        if cookie:
            peticion.add_header("Cookie", cookie)
        return urllib.request.urlopen(peticion, timeout=5)

    for ruta in ("a.bin", "watch/v"):
        for cookie in (None, "SID=otra", "XSID=ficticia"):
            with pytest.raises(urllib.error.HTTPError) as error:
                pedir_con(ruta, cookie)
            assert error.value.code == 403
        with pedir_con(ruta, "tema=oscuro; SID=ficticia") as respuesta:
            assert respuesta.status == 200
