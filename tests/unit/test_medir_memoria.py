"""Funciones puras de `scripts/medir_memoria.py` (specs/002-desktop-app, T008; research R14).

La medición real se hace a mano en Windows; aquí se prueban, con procesos falsos, qué procesos
cuentan como de la aplicación, la suma de memoria privada (USS) y la elección del máximo.
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

RAIZ = Path(__file__).resolve().parents[2]


def _cargar():
    especificacion = importlib.util.spec_from_file_location(
        "medir_memoria", RAIZ / "scripts" / "medir_memoria.py"
    )
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


medir = _cargar()


class ProcesoFalso:
    def __init__(self, pid, nombre, uss=0, padre=None, muere=False, sin_acceso=False):
        self.pid = pid
        self._nombre = nombre
        self._uss = uss
        self._padre = padre
        self._hijos = []
        self.muere = muere
        self.sin_acceso = sin_acceso
        if padre is not None:
            padre._hijos.append(self)

    def name(self):
        if self.muere:
            raise psutil.NoSuchProcess(self.pid)
        return self._nombre

    def parent(self):
        return self._padre

    def children(self, recursive=False):
        hijos = []
        for hijo in self._hijos:
            hijos.append(hijo)
            if recursive:
                hijos.extend(hijo.children(recursive=True))
        return hijos

    def memory_full_info(self):
        if self.muere:
            raise psutil.NoSuchProcess(self.pid)
        if self.sin_acceso:
            raise psutil.AccessDenied(self.pid)
        return SimpleNamespace(uss=self._uss)


@pytest.fixture
def arbol():
    """explorer → medir (python) → uv → lanzador del venv (pythonw) → ventana (pythonw) → WebView2;
    medir → trabajador (python) → ffmpeg; además, un proceso ajeno."""
    explorer = ProcesoFalso(1, "explorer.exe")
    medir_ = ProcesoFalso(2, "python.exe", padre=explorer)
    uv = ProcesoFalso(3, "uv.exe", uss=8, padre=medir_)
    lanzador = ProcesoFalso(4, "pythonw.exe", uss=3, padre=uv)
    ventana = ProcesoFalso(5, "pythonw.exe", uss=60, padre=lanzador)
    navegador = ProcesoFalso(6, "msedgewebview2.exe", uss=40, padre=ventana)
    renderizador = ProcesoFalso(7, "msedgewebview2.exe", uss=30, padre=navegador)
    trabajador = ProcesoFalso(8, "python.exe", uss=70, padre=medir_)
    ffmpeg = ProcesoFalso(9, "ffmpeg.exe", uss=20, padre=trabajador)
    ajeno = ProcesoFalso(10, "notepad.exe", uss=500, padre=explorer)
    return SimpleNamespace(**locals())


def _pids(procesos):
    return [p.pid for p in procesos]


def test_incluye_ventana_descendientes_cadena_del_lanzador_y_trabajador(arbol):
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador, tope=arbol.medir_.pid)
    assert sorted(_pids(procesos)) == [3, 4, 5, 6, 7, 8, 9]


def test_la_cadena_se_detiene_en_el_primer_ascendiente_que_no_es_del_lanzador(arbol):
    # Sin tope, el ascenso sigue por los procesos de Python y uv, pero no pasa de explorer.
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador)
    assert 2 in _pids(procesos)  # el propio medir es python.exe: por eso se pasa `tope`
    assert 1 not in _pids(procesos) and 10 not in _pids(procesos)


def test_sin_duplicados(arbol):
    # Si el trabajador colgara de la ventana, no se cuenta dos veces.
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.navegador, tope=2)
    assert len(_pids(procesos)) == len(set(_pids(procesos)))


def test_sin_trabajador(arbol):
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, None, tope=2)
    assert sorted(_pids(procesos)) == [3, 4, 5, 6, 7]


def test_suma_uss_y_desglose(arbol):
    procesos = medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador, tope=2)
    muestra = medir.suma_uss(procesos, instante=1.5)
    assert muestra.total == 8 + 3 + 60 + 40 + 30 + 70 + 20
    assert muestra.instante == 1.5
    assert {(pid, nombre, uss) for pid, nombre, uss in muestra.desglose} >= {
        (5, "pythonw.exe", 60), (9, "ffmpeg.exe", 20)
    }
    assert muestra.sin_acceso == []


def test_suma_uss_ignora_los_procesos_que_terminan_durante_la_muestra(arbol):
    arbol.renderizador.muere = True
    procesos = [arbol.ventana, arbol.renderizador, arbol.trabajador]
    muestra = medir.suma_uss(procesos, instante=0)
    assert muestra.total == 60 + 70
    assert 7 not in [pid for pid, _, _ in muestra.desglose]


def test_suma_uss_informa_de_los_procesos_sin_acceso(arbol):
    arbol.navegador.sin_acceso = True
    muestra = medir.suma_uss([arbol.ventana, arbol.navegador], instante=0)
    assert muestra.total == 60
    assert muestra.sin_acceso == [(6, "msedgewebview2.exe")]


def test_maximo_devuelve_la_muestra_mayor_con_su_desglose():
    muestras = [
        medir.Muestra(0.0, 100, [(1, "a", 100)], []),
        medir.Muestra(0.5, 250, [(1, "a", 150), (2, "b", 100)], []),
        medir.Muestra(1.0, 200, [(1, "a", 200)], []),
    ]
    mayor = medir.maximo(muestras)
    assert mayor.total == 250 and mayor.instante == 0.5
    assert mayor.desglose == [(1, "a", 150), (2, "b", 100)]
    assert medir.maximo([]) is None


def test_megabytes_decimales():
    assert medir.mb(300_000_000) == pytest.approx(300.0)


def test_motor_webview2_se_acepta():
    medir.comprobar_motor({"motor": "edgechromium"})  # no lanza


def test_otro_motor_termina_la_medicion_con_un_error_explicito():
    # Sin WebView2, pywebview cae en silencio a MSHTML (Internet Explorer): no se da una cifra.
    with pytest.raises(medir.MedicionFallida, match="mshtml.*WebView2"):
        medir.comprobar_motor({"motor": "mshtml"})


def test_motor_desconocido_tambien_es_error():
    with pytest.raises(medir.MedicionFallida, match="WebView2"):
        medir.comprobar_motor({"error": "No module named 'clr'"})



# --- `--union`: una descarga que une audio y vídeo (research R14, condición del usuario) ---


def test_ffmpeg_y_ffprobe_del_trabajador_cuentan(arbol):
    ffmpeg = ProcesoFalso(30, "ffmpeg.exe", 70, padre=arbol.trabajador)
    ffprobe = ProcesoFalso(31, "ffprobe.exe", 20, padre=arbol.trabajador)
    nombres = {p.name() for p in medir.procesos_de_la_aplicacion(arbol.ventana, arbol.trabajador,
                                                                  tope=1)}
    assert {"ffmpeg.exe", "ffprobe.exe"} <= nombres
    assert ffmpeg.pid != ffprobe.pid


def _descarga(estado, bytes_=0, total=100):
    return SimpleNamespace(estado=SimpleNamespace(value=estado), bytes_descargados=bytes_,
                           bytes_totales=total)


@pytest.mark.parametrize("descarga, union, fin", [
    (_descarga("downloading", 50), False, False),
    (_descarga("downloading", 100), False, True),  # sin unión: basta la transferencia
    (_descarga("downloading", 100), True, False),  # con unión: falta unir y verificar
    (_descarga("completed", 100), True, True),
    (_descarga("failed", 10), True, True),
    (_descarga("queued", 0, None), True, False),
])
def test_cuando_termina_la_medicion(descarga, union, fin):
    assert medir.medicion_terminada(descarga, union) is fin


def test_el_manifiesto_de_la_union_tiene_video_y_audio_separados():
    texto = medir.manifiesto_union().decode("utf-8")
    assert texto.count("<AdaptationSet") == 2
    assert "<BaseURL>video.mp4</BaseURL>" in texto and "<BaseURL>audio.m4a</BaseURL>" in texto
    assert 'contentType="video"' in texto and 'contentType="audio"' in texto


def test_el_argumento_union_existe():
    assert medir.analizar(["--union"]).union is True
    assert medir.analizar([]).union is False


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="requiere ffmpeg y ffprobe en el PATH")
def test_las_pistas_de_la_union_se_unen_con_ffmpeg(tmp_path):
    video, audio = medir.preparar_pistas(tmp_path, repeticiones=2)
    assert video.stat().st_size > 0 and audio.stat().st_size > 0
    unido = tmp_path / "unido.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-i", str(audio), "-c", "copy",
                    "-map", "0:v:0", "-map", "1:a:0", str(unido)], check=True)
    salida = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                             "-of", "csv=p=0", str(unido)], capture_output=True, text=True,
                            check=True).stdout.split()
    assert sorted(salida) == ["audio", "video"]
