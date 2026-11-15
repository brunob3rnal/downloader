"""Filas que muestra la ventana (specs/002-desktop-app, R13, data-model "Fila de la vista")."""

from datetime import UTC, datetime, timedelta

import pytest

from downloader.app import vista
from downloader.app.procesos import EstadoProcesador
from downloader.models import Calidad, Descarga, Estado, TipoDescarga
from downloader.mensajes import CONSEJO_SESION

AHORA = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
PROCESADOR = EstadoProcesador("en_marcha", "app", "e1", pid=4321, creado=99.5)


def _d(id_, estado=Estado.QUEUED, **campos):
    datos = dict(url=f"https://usuario:clave@cdn.ejemplo.com:8443/v/{id_}?token=secreto{id_}",
                 calidad=Calidad.MEJOR, carpeta_destino="/tmp", estado=estado,
                 creada="2026-11-15T09:00:00.000+00:00",
                 actualizada="2026-11-15T09:59:00.000+00:00")
    datos.update(campos)
    return Descarga(id=id_, **datos)


def _vivo(activa=None, edad=1, pid=4321, creado=99.5):
    return {"pid": pid, "creado": creado, "en_linea": True,
            "actualizado": (AHORA - timedelta(seconds=edad)).isoformat(timespec="milliseconds"),
            "activa": activa}


def _fila(filas, id_):
    return next(f for f in filas if f["id"] == id_)


def test_sitio_solo_el_host():
    assert vista.sitio("https://usuario:clave@CDN.Ejemplo.com:8443/v/1?token=x#a") == "cdn.ejemplo.com"
    assert vista.sitio("no es una url") == ""


@pytest.mark.parametrize("bps, texto", [(2_400_000, "2,4 MB/s"), (860_000, "0,9 MB/s"),
                                        (12_345_678, "12,3 MB/s")])
def test_velocidad(bps, texto):
    assert vista.velocidad(bps) == texto


@pytest.mark.parametrize("segundos, texto", [(72, "quedan 1 min 12 s"), (45, "quedan 45 s"),
                                             (60, "quedan 1 min 0 s"),
                                             (3725, "quedan 1 h 2 min")])
def test_tiempo(segundos, texto):
    assert vista.tiempo(segundos) == texto


def test_fila_activa_con_vivo():
    activa = {"id": 1, "bytes": 500, "total": 1000, "velocidad": 2_400_000, "eta": 72,
              "sin_datos": None, "espera": None}
    filas = vista.filas([_d(1, Estado.DOWNLOADING, bytes_descargados=100, bytes_totales=1000)],
                        _vivo(activa), PROCESADOR, AHORA)
    fila = _fila(filas, 1)
    assert fila["etiqueta"] == "Descargando" and fila["porcentaje"] == 50.0
    assert fila["velocidad"] == 2_400_000 and fila["eta"] == 72
    assert fila["detalle"] == "2,4 MB/s · quedan 1 min 12 s"


def test_sin_datos():
    activa = {"id": 1, "bytes": 610, "total": 1000, "velocidad": None, "eta": None,
              "sin_datos": 14, "espera": None}
    fila = _fila(vista.filas([_d(1, Estado.DOWNLOADING)], _vivo(activa), PROCESADOR, AHORA), 1)
    assert fila["etiqueta"] == "Sin datos hace 14 s, reintentando"
    assert fila["porcentaje"] == 61.0


def test_esperando_red():
    activa = {"id": 1, "bytes": None, "total": None, "velocidad": None, "eta": None,
              "sin_datos": None, "espera": {"causa": "conexión rechazada", "reintento_en": 40}}
    fila = _fila(vista.filas([_d(1, Estado.WAITING_NETWORK, ultimo_error="conexión rechazada")],
                             _vivo(activa), PROCESADOR, AHORA), 1)
    assert fila["etiqueta"] == "Esperando red"
    assert fila["detalle"] == "Esperando red · reintento en 40 s"
    sin_cuenta = dict(activa, espera={"causa": "sin red", "reintento_en": None})
    fila = _fila(vista.filas([_d(1, Estado.WAITING_NETWORK)], _vivo(sin_cuenta), PROCESADOR,
                             AHORA), 1)
    assert fila["detalle"] == "Esperando red · reintento al volver la conexión"


def test_etiquetas_con_mayuscula_y_porcentaje_de_la_cola():
    filas = vista.filas([
        _d(1, Estado.QUEUED, bytes_descargados=250, bytes_totales=1000),
        _d(2, Estado.COMPLETED, archivo_final="/tmp/v.mp4"),
        _d(3, Estado.FAILED, ultimo_error="HTTP 404 en https://x/?sig=abc"),
    ], None, vista.NINGUNO, AHORA)
    assert [f["etiqueta"] for f in filas] == ["En cola", "Completada", "Fallida"]
    assert [f["porcentaje"] for f in filas] == [25.0, 100.0, 0.0]


@pytest.mark.parametrize("vivo", [
    None,
    _vivo({"id": 1, "bytes": 900, "total": 1000, "velocidad": 1.0, "eta": 1, "sin_datos": None,
           "espera": None}, pid=1),
    _vivo({"id": 1, "bytes": 900, "total": 1000, "velocidad": 1.0, "eta": 1, "sin_datos": None,
           "espera": None}, edad=6),
], ids=["ausente", "otro-pid", "viejo"])
def test_sin_vivo_valido_no_hay_datos_viejos(vivo):
    filas = vista.filas([_d(1, Estado.DOWNLOADING, bytes_descargados=100, bytes_totales=1000)],
                        vivo, PROCESADOR, AHORA)
    fila = _fila(filas, 1)
    assert fila["velocidad"] is None and fila["eta"] is None
    assert fila["porcentaje"] == 10.0  # el de la cola, no el del vivo
    assert fila["detalle"] == "Sin datos en directo"
    assert vista.en_directo(vivo, PROCESADOR, AHORA) is False


def test_en_directo():
    assert vista.en_directo(_vivo(None), PROCESADOR, AHORA) is True
    assert vista.en_directo(None, vista.NINGUNO, AHORA) is None


def test_listas():
    filas = vista.filas([_d(1, Estado.COMPLETED, tipo=TipoDescarga.PLAYLIST),
                         _d(2, padre_id=1), _d(3, padre_id=1)], None, vista.NINGUNO, AHORA)
    lista = _fila(filas, 1)
    assert lista["hijas"] == 2 and lista["detalle"] == "lista (2 videos)"
    assert lista["porcentaje"] is None


def test_aviso_segun_el_estado():
    filas = vista.filas([
        _d(1, Estado.FAILED, ultimo_error="Video privado", ultimo_aviso="ignorado"),
        _d(2, Estado.WAITING_NETWORK, ultimo_error="timeout"),
        _d(3, Estado.COMPLETED, archivo_final="/tmp/v.mp4", ultimo_aviso="reiniciada desde cero"),
        _d(4, Estado.QUEUED),
    ], None, vista.NINGUNO, AHORA)
    assert [f["aviso"] for f in filas] == [
        "Video privado", "timeout", "/tmp/v.mp4 · reiniciada desde cero", None]


def test_secretos_ocultos_en_titulo_aviso_y_url():
    filas = vista.filas([
        _d(1, Estado.FAILED, ultimo_error="falló https://x/v?sig=secreto-del-error"),
        _d(2, titulo="Título con https://x/?token=secreto-del-titulo"),
    ], None, vista.NINGUNO, AHORA)
    texto = repr(filas)
    for secreto in ("secreto1", "secreto2", "secreto-del-error", "secreto-del-titulo", "clave"):
        assert secreto not in texto


def test_reintentar_con_cookies():
    filas = vista.filas([
        _d(1, Estado.FAILED, ultimo_error=f"HTTP 403. {CONSEJO_SESION}"),
        _d(2, Estado.FAILED, ultimo_error="HTTP 404"),
        _d(3, Estado.QUEUED, ultimo_error=f"HTTP 403. {CONSEJO_SESION}"),
    ], None, vista.NINGUNO, AHORA)
    assert [f["reintentar_con_cookies"] for f in filas] == [True, False, False]


def test_etag():
    descargas = [_d(1)]
    base = vista.etag(descargas, None, vista.NINGUNO, None)
    assert vista.etag([_d(1)], None, vista.NINGUNO, None) == base
    assert vista.etag([_d(1, actualizada="2026-11-15T10:00:01.000+00:00")], None,
                      vista.NINGUNO, None) != base
    assert vista.etag(descargas, _vivo(None), vista.NINGUNO, None) != base
    assert vista.etag(descargas, None, PROCESADOR, None) != base
    assert vista.etag(descargas, None, vista.NINGUNO, {"fin": "x"}) != base


def test_url_visible_quita_usuario_y_contrasena():
    assert vista.url_visible("https://ana:clave@x.ejemplo:8443/v?token=t1") == (
        "https://x.ejemplo:8443/v?token=***")
