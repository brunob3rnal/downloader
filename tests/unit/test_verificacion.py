"""Verificación antes de publicar (FR-023, research R8 y R22).

El tamaño de cada formato se mide cuando yt-dlp lo da por terminado (hook `finished`): después
se une, se remuxa o se borra, así que `formato_terminado` lo anota en ese momento y
`verificar` compara después con el `total_bytes` exacto, si lo hubo.
"""

import subprocess
from datetime import UTC, datetime

import pytest

from downloader import publish, ytdl
from downloader.errors import ErrorPermanente
from downloader.models import Calidad, Descarga, Estado


def terminado(archivo, total=None, total_exacto=None):
    archivo = str(archivo) if archivo is not None else None
    return ytdl.Progreso(None, total, None, None, archivo, "finished", total_exacto)


# --- tamaño contra el anunciado ---------------------------------------------------------


def test_tamano_distinto_del_total_exacto_falla(tmp_path):
    formato = tmp_path / "descarga.mp4"
    formato.write_bytes(b"x" * 100)
    formatos = [publish.formato_terminado(terminado(formato, 120, total_exacto=120))]

    # Falla antes de mirar el archivo final: no hace falta ffprobe.
    with pytest.raises(publish.VerificacionFallida, match="100.*120"):
        publish.verificar(formatos, tmp_path / "no_existe.mp4")


def test_la_verificacion_fallida_es_un_error_permanente():
    assert issubclass(publish.VerificacionFallida, ErrorPermanente)


def test_el_tamano_se_mide_al_terminar_el_formato(tmp_path):
    """Lo que pase después con el archivo (unirlo, remuxarlo) no cambia la comparación."""
    formato = tmp_path / "descarga.f1.mp4"
    formato.write_bytes(b"x" * 100)
    anotado = publish.formato_terminado(terminado(formato, 100, total_exacto=100))
    formato.write_bytes(b"x" * 10)  # remux: otro tamaño, ya no cuenta
    assert anotado.tamano == 100
    assert anotado.total == 100


def test_sin_archivo_no_se_anota_nada():
    assert publish.formato_terminado(terminado(None, 100, total_exacto=100)) is None


@pytest.mark.ffmpeg
def test_con_solo_total_estimado_no_se_compara_tamano(tmp_path, medios):
    formato = tmp_path / "descarga.mp4"
    formato.write_bytes(medios.datos_mp4)
    estimado = len(medios.datos_mp4) * 2
    formatos = [publish.formato_terminado(terminado(formato, estimado, total_exacto=None))]
    publish.verificar(formatos, formato)  # no lanza


@pytest.mark.ffmpeg
def test_tamano_igual_al_exacto_y_legible_pasa(tmp_path, medios):
    formato = tmp_path / "descarga.mp4"
    formato.write_bytes(medios.datos_mp4)
    tamano = len(medios.datos_mp4)
    publish.verificar([publish.formato_terminado(terminado(formato, tamano, tamano))], formato)


def test_el_hook_distingue_total_exacto_de_estimado(tmp_path, monkeypatch):
    """`total_bytes` llega como `total_exacto`; `total_bytes_estimate`, solo como `total`."""
    eventos = [
        {"status": "finished", "filename": "a.mp4", "total_bytes": 10, "downloaded_bytes": 10},
        {"status": "finished", "filename": "b.ts", "total_bytes_estimate": 20,
         "downloaded_bytes": 18},
    ]

    def ejecutar_falso(opciones, url, download):
        for evento in eventos:
            opciones["progress_hooks"][0](evento)
        return {}

    monkeypatch.setattr(ytdl, "_ejecutar", ejecutar_falso)
    recibidos = []
    descarga = Descarga(id=1, url="https://v.ejemplo/a", calidad=Calidad.MEJOR,
                        carpeta_destino=str(tmp_path), estado=Estado.DOWNLOADING)
    ytdl.descargar(descarga, tmp_path, None, recibidos.append)

    assert [(p.total, p.total_exacto) for p in recibidos] == [(10, 10), (20, None)]


# --- legible hasta el final (ffprobe) ---------------------------------------------------


@pytest.mark.ffmpeg
def test_mp4_valido_pasa(medios):
    publish.verificar([], medios.mp4)


@pytest.mark.ffmpeg
def test_mp4_truncado_a_la_mitad_falla(tmp_path, medios):
    truncado = tmp_path / "truncado.mp4"
    truncado.write_bytes(medios.datos_mp4[: len(medios.datos_mp4) // 2])
    with pytest.raises(publish.VerificacionFallida, match="ffprobe"):
        publish.verificar([], truncado)


@pytest.mark.ffmpeg
def test_mp4_sin_paquetes_ni_duracion_falla(tmp_path):
    vacio = tmp_path / "vacio.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
         "-frames:v", "0", "-c:v", "libx264", str(vacio)],
        check=True,
    )
    with pytest.raises(publish.VerificacionFallida):
        publish.verificar([], vacio)


@pytest.mark.ffmpeg
def test_archivo_que_no_es_video_falla(tmp_path):
    basura = tmp_path / "basura.mp4"
    basura.write_bytes(b"no es un video" * 100)
    with pytest.raises(publish.VerificacionFallida):
        publish.verificar([], basura)


# --- archivos inválidos -----------------------------------------------------------------


def test_apartar_invalidos_mueve_sin_borrar(tmp_path):
    trabajo = tmp_path / "7"
    trabajo.mkdir()
    (trabajo / "descarga.mp4").write_bytes(b"truncado")
    (trabajo / "descarga.mp4.ytdl").write_bytes(b"estado")

    destino = publish.apartar_invalidos(trabajo)

    assert destino.parent == trabajo / publish.INVALIDOS
    assert sorted(p.name for p in trabajo.iterdir()) == [publish.INVALIDOS]
    assert (destino / "descarga.mp4").read_bytes() == b"truncado"
    assert (destino / "descarga.mp4.ytdl").read_bytes() == b"estado"
    # El nombre es una fecha válida también en Windows (sin `:`).
    assert ":" not in destino.name
    assert datetime.strptime(destino.name[:19], "%Y-%m-%dT%H-%M-%S")


def test_apartar_dos_veces_conserva_los_anteriores(tmp_path):
    trabajo = tmp_path / "7"
    trabajo.mkdir()
    (trabajo / "descarga.mp4").write_bytes(b"primero")
    primero = publish.apartar_invalidos(trabajo)
    (trabajo / "descarga.mp4").write_bytes(b"segundo")
    segundo = publish.apartar_invalidos(trabajo)

    assert primero != segundo
    assert (primero / "descarga.mp4").read_bytes() == b"primero"
    assert (segundo / "descarga.mp4").read_bytes() == b"segundo"
    assert sorted(p.name for p in trabajo.iterdir()) == [publish.INVALIDOS]


def test_apartar_dos_veces_en_el_mismo_instante(tmp_path, monkeypatch):
    """En Windows el reloj avanza a saltos: dos llamadas seguidas pueden dar la misma fecha."""
    instante = datetime(2026, 10, 24, 3, 12, 18, 373500, tzinfo=UTC)

    class Parado(datetime):
        @classmethod
        def now(cls, tz=None):
            return instante

    monkeypatch.setattr(publish, "datetime", Parado)
    trabajo = tmp_path / "7"
    trabajo.mkdir()
    (trabajo / "descarga.mp4").write_bytes(b"primero")
    primero = publish.apartar_invalidos(trabajo)
    (trabajo / "descarga.mp4").write_bytes(b"segundo")
    segundo = publish.apartar_invalidos(trabajo)

    assert primero != segundo
    assert primero.parent == segundo.parent == trabajo / publish.INVALIDOS
    assert segundo.name.startswith(primero.name)  # la misma fecha, distinguida
    assert (primero / "descarga.mp4").read_bytes() == b"primero"
    assert (segundo / "descarga.mp4").read_bytes() == b"segundo"
