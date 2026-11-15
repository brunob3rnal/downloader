"""Resultado de la última ejecución lanzada por la aplicación (FR-012, research R10).

`ultima-ejecucion.json` en el directorio de la app (contracts/archivos.md). Lo escribe el
procesador al terminar, o la ventana con `escribir_abrupto` si el procesador murió sin hacerlo.
"""

from datetime import UTC, datetime
from pathlib import Path

from downloader import archivos_json
from downloader.secretos import ocultar_secretos

ARCHIVO = "ultima-ejecucion.json"
TIPOS = ("fatal", "inesperado", "detenida", "abrupto")
ABRUPTO = "el procesamiento terminó de forma inesperada; el progreso guardado se conserva"


def _ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def escribir(directorio, ejecucion: str, inicio: str | None, completadas: int, fallidas,
             codigo: int | None, error: dict | None) -> None:
    if error is not None and error.get("tipo") not in TIPOS:
        raise ValueError(f"tipo de error desconocido: {error.get('tipo')!r}")
    datos = {
        "ejecucion": ejecucion,
        "inicio": inicio,
        "fin": _ahora(),
        "completadas": completadas,
        "fallidas": [
            {"id": f["id"], "nombre": ocultar_secretos(str(f.get("nombre") or "")),
             "causa": ocultar_secretos(str(f.get("causa") or ""))}
            for f in fallidas
        ],
        "codigo": codigo,
        "error": None if error is None else {
            "tipo": error["tipo"], "mensaje": ocultar_secretos(str(error.get("mensaje") or ""))
        },
    }
    archivos_json.escribir_atomico(Path(directorio) / ARCHIVO, datos, reintentar=True)


def escribir_abrupto(directorio, ejecucion: str, inicio: str | None = None) -> None:
    escribir(directorio, ejecucion, inicio, completadas=0, fallidas=[], codigo=None,
             error={"tipo": "abrupto", "mensaje": ABRUPTO})


def leer(directorio) -> dict | None:
    return archivos_json.leer(Path(directorio) / ARCHIVO)
