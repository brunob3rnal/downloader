"""Directorio de la app y `config.toml` (ver data-model.md, entidad Configuración)."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import typer

from downloader.models import Calidad

ARCHIVO = "config.toml"
SONDAS_POR_DEFECTO = ["1.1.1.1:443", "8.8.8.8:443"]


class ConfiguracionInvalida(ValueError):
    pass


def directorio_app() -> Path:
    """`DOWNLOADER_HOME` si está definida; si no, el directorio de la app según el sistema."""
    directorio = Path(os.environ.get("DOWNLOADER_HOME") or typer.get_app_dir("downloader"))
    directorio.mkdir(parents=True, exist_ok=True)
    return directorio


def carpeta_por_defecto() -> Path:
    inicio = Path.home()
    for nombre in ("Descargas", "Downloads"):
        if (inicio / nombre).is_dir():
            return inicio / nombre
    return inicio


@dataclass
class Configuracion:
    carpeta_destino: Path = field(default_factory=carpeta_por_defecto)
    calidad: Calidad = Calidad.MEJOR
    sondas_red: list[str] = field(default_factory=lambda: list(SONDAS_POR_DEFECTO))


def cargar(directorio: Path | None = None) -> Configuracion:
    """Lee `config.toml`; sin archivo, valores por defecto.

    Solo admite las claves conocidas: no hay claves de credenciales ni cookies (Principio VI).
    """
    ruta = (directorio or directorio_app()) / ARCHIVO
    if not ruta.exists():
        return Configuracion()
    try:
        valores = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfiguracionInvalida(f"{ruta} no es un TOML válido: {error}") from error

    desconocidas = set(valores) - {"carpeta_destino", "calidad", "sondas_red"}
    if desconocidas:
        raise ConfiguracionInvalida(
            f"{ruta}: clave desconocida: {', '.join(sorted(desconocidas))}. "
            "Claves admitidas: carpeta_destino, calidad, sondas_red"
        )

    configuracion = Configuracion()
    if "carpeta_destino" in valores:
        configuracion.carpeta_destino = Path(valores["carpeta_destino"]).expanduser()
    if "calidad" in valores:
        try:
            configuracion.calidad = Calidad(valores["calidad"])
        except ValueError:
            admitidas = ", ".join(c.value for c in Calidad)
            raise ConfiguracionInvalida(
                f"{ruta}: calidad desconocida {valores['calidad']!r}; admitidas: {admitidas}"
            ) from None
    if "sondas_red" in valores:
        sondas = valores["sondas_red"]
        if not (isinstance(sondas, list) and sondas and all(_es_sonda(s) for s in sondas)):
            raise ConfiguracionInvalida(
                f'{ruta}: sondas_red debe ser una lista de "host:puerto", p. ej. ["1.1.1.1:443"]'
            )
        configuracion.sondas_red = sondas
    return configuracion


def _es_sonda(valor) -> bool:
    if not isinstance(valor, str):
        return False
    host, _, puerto = valor.rpartition(":")
    return bool(host) and puerto.isdigit()
