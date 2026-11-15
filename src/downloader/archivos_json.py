"""JSON compartidos entre procesos: escritura atómica y lectura tolerante.

Los usan `ejecutar` (`procesador.json`, `progreso-vivo.json`) y la aplicación de escritorio
(specs/002-desktop-app, contracts/archivos.md). Son informativos: la verdad sigue en `cola.db`.
"""

import json
import os
import time
from pathlib import Path

ARCHIVO_VIVO = "progreso-vivo.json"  # el progreso en directo de `ejecutar` (research R9)
ESPERA_REINTENTO = 0.05  # s; en Windows, `os.replace` falla si otro proceso tiene el destino abierto


def escribir_atomico(ruta: Path, datos: dict, reintentar: bool = False) -> bool:
    """Escribe `datos` en `ruta` sin que nadie pueda leerlo a medias.

    Escribe un temporal en el mismo directorio y lo pone en su sitio con `os.replace`, sin
    `fsync`. Si `os.replace` falla con `PermissionError` (el destino abierto en Windows), lo
    reintenta una vez tras `ESPERA_REINTENTO` si `reintentar`; si no lo consigue, devuelve
    `False`. Cualquier otro error se lanza. No deja temporales.
    """
    ruta = Path(ruta)
    temporal = ruta.with_name(f".{ruta.name}.{os.getpid()}.tmp")
    try:
        temporal.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        for intento in range(2 if reintentar else 1):
            try:
                os.replace(temporal, ruta)
                return True
            except PermissionError:
                if intento == 0 and reintentar:
                    time.sleep(ESPERA_REINTENTO)
        return False
    finally:
        try:
            temporal.unlink()
        except FileNotFoundError:
            pass


def leer(ruta: Path) -> dict | None:
    """El objeto JSON de `ruta`; `None` si no existe, está a medias o no es un objeto."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def borrar(ruta: Path) -> None:
    """Borra `ruta` si existe; lanza los demás errores (sin permiso, es un directorio…)."""
    try:
        Path(ruta).unlink()
    except FileNotFoundError:
        pass
