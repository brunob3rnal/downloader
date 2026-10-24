"""HLS cifrado con AES-128: la clave se pide durante la descarga (research R4, R19).

Cerrar las respuestas de la extracción al empezar la descarga no debe afectar al manifiesto,
la clave ni los fragmentos, que la descarga pide con sus propias peticiones.
"""

import subprocess

import pytest

from downloader import ytdl
from downloader.models import Calidad, Descarga, Estado
from downloader.store import Store
from tests.support import fake_extractor, hls, navegador_falso

# HLS con el descargador nativo y AES-128 con pycryptodomex, no con ffmpeg (R1).
pytestmark = pytest.mark.ytdlp_internals

CLAVE = bytes(range(16))  # valor ficticio


def test_hls_aes_128_se_descarga_y_descifra(tmp_path, destino, servidor, medios, monkeypatch,
                                             tiempos_rapidos):
    hls = tmp_path / "aes"
    hls.mkdir()
    (tmp_path / "clave.key").write_bytes(CLAVE)
    # Formato de -hls_key_info_file: URI de la clave en el manifiesto y archivo local.
    (tmp_path / "clave.info").write_text(f"clave.key\n{tmp_path / 'clave.key'}\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(medios.mp4), "-c", "copy",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
         "-hls_key_info_file", str(tmp_path / "clave.info"),
         "-hls_segment_filename", str(hls / "fragmento%d.ts"), str(hls / "index.m3u8")],
        check=True,
    )
    assert "METHOD=AES-128" in (hls / "index.m3u8").read_text()
    manifiesto = servidor.registrar_hls("aes", hls)
    servidor.registrar("aes/clave.key", CLAVE)
    servidor.registrar_video("cifrado", manifiesto)
    fake_extractor.instalar(monkeypatch)

    descarga = Descarga(id=1, url=servidor.url("watch/cifrado"), calidad=Calidad.MEJOR,
                        carpeta_destino=str(destino), estado=Estado.DOWNLOADING)
    info = ytdl.descargar(descarga, ytdl.directorio_trabajo(descarga), None, lambda _: None,
                          tiempos_rapidos.socket_timeout)

    assert servidor.peticiones["aes/clave.key"] >= 1
    archivo = ytdl.archivo_descargado(info)
    # Descifrado correcto: ffmpeg lo decodifica entero sin errores y dura lo que el original.
    decodificar = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(archivo), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    assert decodificar.returncode == 0 and not decodificar.stderr
    duracion = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(archivo)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert abs(float(duracion) - 10) < 0.5


# --- AES-128 sin sistema de licencias: se descarga (Principio V v3.0.0; FR-015; R5) ---


def descifrado_entero(archivo):
    """ffmpeg decodifica el archivo entero sin errores y dura lo que el original (10 s)."""
    decodificar = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(archivo), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    assert decodificar.returncode == 0 and not decodificar.stderr, decodificar.stderr
    duracion = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(archivo)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert abs(float(duracion) - 10) < 0.5


def descargar(servidor, destino, id_video, tiempos_rapidos):
    descarga = Descarga(id=1, url=servidor.url(f"watch/{id_video}"), calidad=Calidad.MEJOR,
                        carpeta_destino=str(destino), estado=Estado.DOWNLOADING)
    info = ytdl.descargar(descarga, ytdl.directorio_trabajo(descarga), None, lambda _: None,
                          tiempos_rapidos.socket_timeout)
    return ytdl.archivo_descargado(info)


def registrar_cifrado(servidor, tmp_path, hls_cifrado, id_video, linea, *, clave=hls.CLAVE,
                      **pagina):
    directorio = hls.con_claves(hls_cifrado, tmp_path / id_video, linea)
    manifiesto = servidor.registrar_hls(id_video, directorio)
    servidor.registrar(f"{id_video}/clave.key", clave)
    servidor.registrar_video(id_video, manifiesto, **pagina)
    return f"{id_video}/clave.key"


IV = f"IV={hls.IV}"


@pytest.mark.parametrize(
    "linea",
    [
        f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV},KEYFORMAT="identity"',
        f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV},KEYFORMAT="identity",'
        'KEYFORMATVERSIONS="1"',
        # `URI` antes que `METHOD`: el mismo AES-128 con otro orden de atributos.
        f'#EXT-X-KEY:URI="clave.key",KEYFORMAT="identity",METHOD=AES-128,{IV}',
    ],
    ids=["identity", "identity-versiones", "uri-primero"],
)
def test_aes_128_identity_se_descarga_y_descifra(tmp_path, destino, servidor, hls_cifrado,
                                                 monkeypatch, tiempos_rapidos, linea):
    clave = registrar_cifrado(servidor, tmp_path, hls_cifrado, "identity", linea)
    fake_extractor.instalar(monkeypatch)

    archivo = descargar(servidor, destino, "identity", tiempos_rapidos)

    assert servidor.peticiones[clave] >= 1
    descifrado_entero(archivo)


def test_aes_128_con_la_clave_en_una_uri_data(tmp_path, destino, servidor, hls_cifrado,
                                              monkeypatch, tiempos_rapidos):
    linea = f'#EXT-X-KEY:METHOD=AES-128,URI="{hls.uri_data(hls.CLAVE)}",{IV}'
    clave = registrar_cifrado(servidor, tmp_path, hls_cifrado, "data", linea)
    fake_extractor.instalar(monkeypatch)

    archivo = descargar(servidor, destino, "data", tiempos_rapidos)

    assert servidor.peticiones[clave] == 0
    descifrado_entero(archivo)


def test_aes_128_con_la_clave_del_extractor(tmp_path, destino, servidor, hls_cifrado,
                                            monkeypatch, tiempos_rapidos):
    """`info['hls_aes']`: el manifiesto apunta a una clave que no es la buena y no se pide."""
    linea = f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV}'
    clave = registrar_cifrado(servidor, tmp_path, hls_cifrado, "extractor", linea,
                              clave=bytes(16), hls_aes={"key": "0x" + hls.CLAVE.hex()})
    fake_extractor.instalar(monkeypatch)

    archivo = descargar(servidor, destino, "extractor", tiempos_rapidos)

    assert servidor.peticiones[clave] == 0
    descifrado_entero(archivo)


def test_hls_sin_cifrar_sigue_descargandose(destino, servidor, medios, monkeypatch,
                                            tiempos_rapidos):
    assert "#EXT-X-KEY" not in (medios.hls / "index.m3u8").read_text()
    servidor.registrar_video("claro", servidor.registrar_hls("claro", medios.hls))
    fake_extractor.instalar(monkeypatch)

    archivo = descargar(servidor, destino, "claro", tiempos_rapidos)

    descifrado_entero(archivo)


def test_la_clave_se_pide_con_la_sesion_del_usuario(cli, app_home, destino, servidor, tmp_path,
                                                    hls_cifrado, monkeypatch, esperas_cortas):
    """T089 c: la petición de la clave lleva la misma cookie que la del manifiesto."""
    navegador_falso.NavegadorFalso().instalar(monkeypatch)
    servidor.exigir_sesion(navegador_falso.NOMBRE, navegador_falso.VALOR)
    linea = f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV}'
    clave = registrar_cifrado(servidor, tmp_path, hls_cifrado, "sesion", linea)
    assert cli("agregar", servidor.url("watch/sesion"), "--carpeta", destino,
               "--cookies-de", "firefox").exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 0, resultado.output
    sesion = f"{navegador_falso.NOMBRE}={navegador_falso.VALOR}"
    (del_manifiesto,) = set(servidor.cookies["sesion/index.m3u8"])
    assert sesion in del_manifiesto
    assert servidor.cookies[clave] and set(servidor.cookies[clave]) == {del_manifiesto}
    store = Store.abrir(app_home)
    try:
        descarga = store.obtener(1)
    finally:
        store.cerrar()
    assert descarga.estado is Estado.COMPLETED
    descifrado_entero(descarga.archivo_final)


def test_clave_data_con_base64_invalido_falla_sin_excepcion(cli, app_home, destino, servidor,
                                                            tmp_path, hls_cifrado,
                                                            esperas_cortas):
    """No es DRM (no se puede decodificar, así que no es un PSSH) ni se rechaza de antemano:
    yt-dlp no puede leer la clave y la descarga falla como permanente, sin que `ejecutar` se
    detenga con una excepción."""
    linea = f'#EXT-X-KEY:METHOD=AES-128,URI="data:text/plain;base64,@@no-es-base64@@",{IV}'
    registrar_cifrado(servidor, tmp_path, hls_cifrado, "ilegible", linea)
    fragmento = f"ilegible/{min(p.name for p in hls_cifrado.glob('*.ts'))}"
    otro = registrar_cifrado(servidor, tmp_path, hls_cifrado, "otro",
                             f'#EXT-X-KEY:METHOD=AES-128,URI="clave.key",{IV}')
    assert cli("agregar", servidor.url("watch/ilegible"), servidor.url("watch/otro"),
               "--carpeta", destino).exit_code == 0

    resultado = cli("ejecutar")

    assert resultado.exit_code == 1, resultado.output  # cola vacía con un fallo (FR-021)
    assert isinstance(resultado.exception, SystemExit)  # sin traza
    store = Store.abrir(app_home)
    try:
        ilegible, completada = store.obtener(1), store.obtener(2)
    finally:
        store.cerrar()
    assert ilegible.estado is Estado.FAILED
    assert ilegible.intentos_sin_avance == 0
    assert "DRM" not in ilegible.ultimo_error and "no soportado" not in ilegible.ultimo_error
    # El análisis del manifiesto lo dejó pasar sin lanzar: yt-dlp llegó a pedir un fragmento.
    assert servidor.peticiones[fragmento] >= 1
    assert completada.estado is Estado.COMPLETED  # la cola sigue
    assert servidor.peticiones[otro] >= 1
