"""Lo que muestra la ventana de cada descarga (research R13, data-model "Fila de la vista").

Combina la cola (`cola.db`) con `progreso-vivo.json`, que solo vale si es del procesador en
marcha y tiene 5 s o menos; si no, la fila activa dice "Sin datos en directo" en lugar de
mostrar cifras viejas (research R9). Las cifras salen ya formateadas, como en la maqueta.
"""

import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlsplit

from downloader.app.procesos import NINGUNO, EstadoProcesador
from downloader.mensajes import requiere_sesion
from downloader.models import ETIQUETAS, Estado, TipoDescarga
from downloader.secretos import ocultar_secretos

VIGENCIA_VIVO = 5  # s; un `progreso-vivo.json` más antiguo no se muestra
SIN_DATOS_EN_DIRECTO = "Sin datos en directo"

__all__ = ["NINGUNO", "filas", "en_directo", "etag", "sitio", "url_visible", "velocidad",
           "tiempo"]


def sitio(url: str) -> str:
    """Solo el host: sin usuario, contraseña, puerto, ruta ni consulta."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def url_visible(url: str) -> str:
    """La URL sin usuario ni contraseña y con los tokens ocultos (Principio VI)."""
    try:
        partes = urlsplit(url)
        if partes.username is not None or partes.password is not None:
            host = partes.hostname or ""
            if partes.port:
                host = f"{host}:{partes.port}"
            url = partes._replace(netloc=host).geturl()
    except ValueError:
        pass
    return ocultar_secretos(url)


def velocidad(bytes_por_segundo: float) -> str:
    return f"{bytes_por_segundo / 1_000_000:.1f} MB/s".replace(".", ",")


def tiempo(segundos: float) -> str:
    segundos = int(math.ceil(segundos))
    if segundos >= 3600:
        return f"quedan {segundos // 3600} h {segundos // 60 % 60} min"
    if segundos >= 60:
        return f"quedan {segundos // 60} min {segundos % 60} s"
    return f"quedan {segundos} s"


def _vivo_valido(vivo, procesador: EstadoProcesador, ahora: datetime):
    if vivo is None or procesador.estado != "en_marcha" or vivo.get("pid") != procesador.pid:
        return None
    if procesador.creado is not None and vivo.get("creado") != procesador.creado:
        return None
    try:
        actualizado = datetime.fromisoformat(vivo["actualizado"])
    except (KeyError, TypeError, ValueError):
        return None
    if (ahora - actualizado).total_seconds() > VIGENCIA_VIVO:
        return None
    return vivo


def en_directo(vivo, procesador: EstadoProcesador, ahora: datetime | None = None) -> bool | None:
    """`None` sin procesador; si no, si hay un `progreso-vivo.json` válido."""
    if procesador.estado != "en_marcha":
        return None
    return _vivo_valido(vivo, procesador, ahora or datetime.now(UTC)) is not None


def _porcentaje(bytes_, total):
    if bytes_ is None or not total:
        return None
    return round(min(100.0, 100 * bytes_ / total), 1)


def _aviso(descarga) -> str | None:
    if descarga.estado in (Estado.FAILED, Estado.WAITING_NETWORK):
        return ocultar_secretos(descarga.ultimo_error) if descarga.ultimo_error else None
    partes = [descarga.archivo_final, descarga.ultimo_aviso]
    texto = " · ".join(ocultar_secretos(p) for p in partes if p)
    return texto or None


def _fila(descarga, hijas: int, activa, sin_directo: bool) -> dict:
    etiqueta = ETIQUETAS[descarga.estado]
    fila = {
        "id": descarga.id,
        "titulo": ocultar_secretos(descarga.titulo) if descarga.titulo else url_visible(
            descarga.url),
        "sitio": sitio(descarga.url),
        "estado": descarga.estado.value,
        "etiqueta": etiqueta[:1].upper() + etiqueta[1:],
        "porcentaje": _porcentaje(descarga.bytes_descargados, descarga.bytes_totales),
        "velocidad": None,
        "eta": None,
        "sin_datos": None,
        "espera": None,
        "detalle": None,
        "aviso": _aviso(descarga),
        "reintentar_con_cookies": (descarga.estado is Estado.FAILED
                                   and requiere_sesion(descarga.ultimo_error)),
        "hijas": None,
    }
    if descarga.tipo is TipoDescarga.PLAYLIST:
        fila.update(porcentaje=None, hijas=hijas,
                    detalle=f"lista ({hijas} video{'s' * (hijas != 1)})")
        return fila
    if descarga.estado is Estado.COMPLETED:
        fila["porcentaje"] = 100.0
    elif fila["porcentaje"] is None:
        fila["porcentaje"] = 0.0
    if activa is not None:
        fila.update(velocidad=activa.get("velocidad"), eta=activa.get("eta"),
                    sin_datos=activa.get("sin_datos"), espera=activa.get("espera"))
        porcentaje = _porcentaje(activa.get("bytes"), activa.get("total"))
        if porcentaje is not None:
            fila["porcentaje"] = porcentaje
        if fila["espera"] is not None:
            reintento = fila["espera"].get("reintento_en")
            cuando = (f"reintento en {reintento} s" if reintento is not None
                      else "reintento al volver la conexión")
            fila["etiqueta"] = "Esperando red"
            fila["detalle"] = f"Esperando red · {cuando}"
        elif fila["sin_datos"]:
            fila["etiqueta"] = f"Sin datos hace {fila['sin_datos']} s, reintentando"
        elif fila["velocidad"]:
            partes = [velocidad(fila["velocidad"])]
            if fila["eta"] is not None:
                partes.append(tiempo(fila["eta"]))
            fila["detalle"] = " · ".join(partes)
    elif sin_directo and descarga.estado in (Estado.DOWNLOADING, Estado.WAITING_NETWORK):
        fila["detalle"] = SIN_DATOS_EN_DIRECTO
    return fila


def filas(descargas, vivo, procesador: EstadoProcesador, ahora: datetime | None = None) -> list:
    ahora = ahora or datetime.now(UTC)
    valido = _vivo_valido(vivo, procesador, ahora)
    activa = valido.get("activa") if valido else None
    sin_directo = procesador.estado == "en_marcha" and valido is None
    hijas = Counter(d.padre_id for d in descargas if d.padre_id is not None)
    return [
        _fila(d, hijas[d.id], activa if activa and activa.get("id") == d.id else None,
              sin_directo)
        for d in descargas
    ]


def etag(descargas, vivo, procesador: EstadoProcesador, ultima) -> str:
    """Cambia si cambia la cola, el vivo, el procesador o el último resultado (research R13)."""
    huella = json.dumps([
        len(descargas), max((d.actualizada or "" for d in descargas), default=""),
        vivo.get("actualizado") if vivo else None,
        [procesador.estado, procesador.origen, procesador.ejecucion, procesador.pid],
        ultima.get("fin") if ultima else None,
    ], default=str)
    return '"' + hashlib.sha1(huella.encode("utf-8")).hexdigest()[:16] + '"'
