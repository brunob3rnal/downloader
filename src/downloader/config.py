"""Directorio de la app y `config.toml` (ver data-model.md, entidad Configuración)."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import typer

from downloader import publish
from downloader.models import Calidad

ARCHIVO = "config.toml"
CLAVES = (
    "carpeta_destino", "calidad", "sondas_red",
    "estancamiento_segundos", "estancamiento_velocidad_minima", "plantilla_nombre",
)
SONDAS_POR_DEFECTO = ["1.1.1.1:443", "8.8.8.8:443"]
# Fragmentos de nombres de clave que delatan una credencial o las cookies (FR-016, FR-017,
# Principio VI): el error lo explica en lugar de tratarlas como una clave desconocida más.
SECRETOS = ("cookie", "password", "contrase", "token", "secret", "credencial", "auth")


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
    # Vigilante de estancamiento (research R21): segundos sin datos, o por debajo de la
    # velocidad mínima (bytes/s), antes de abortar el intento y reconectar.
    estancamiento_segundos: float = 30
    estancamiento_velocidad_minima: float = 1024
    # Nombre de los archivos publicados, sin la extensión: campos {titulo} e {id}.
    plantilla_nombre: str = publish.PLANTILLA_NOMBRE


def cargar(directorio: Path | None = None) -> Configuracion:
    """Lee `config.toml`; sin archivo, valores por defecto.

    Solo admite las claves conocidas: no hay claves de credenciales ni cookies, ni del navegador
    de las cookies, que solo se elige con `--cookies-de` (FR-016, Principio VI).
    """
    ruta = (directorio or directorio_app()) / ARCHIVO
    if not ruta.exists():
        return Configuracion()
    try:
        valores = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfiguracionInvalida(f"{ruta} no es un TOML válido: {error}") from error

    desconocidas = set(valores) - set(CLAVES)
    secretas = sorted(c for c in desconocidas if any(s in c.lower() for s in SECRETOS))
    if secretas:  # sin repetir el valor: podría ser un secreto
        raise ConfiguracionInvalida(
            f"{ruta}: clave no admitida: {', '.join(secretas)}. config.toml no admite "
            "credenciales ni cookies: las cookies del navegador solo se leen al pedirlo con "
            "--cookies-de NAVEGADOR en agregar o ejecutar, y nunca se guardan. Quite esa clave."
        )
    if desconocidas:
        raise ConfiguracionInvalida(
            f"{ruta}: clave desconocida: {', '.join(sorted(desconocidas))}. "
            f"Claves admitidas: {', '.join(CLAVES)}"
        )

    configuracion = Configuracion()
    if "carpeta_destino" in valores:
        carpeta = valores["carpeta_destino"]
        if not isinstance(carpeta, str) or not carpeta.strip():
            raise ConfiguracionInvalida(
                f"{ruta}: carpeta_destino debe ser una ruta entre comillas, "
                "p. ej. carpeta_destino = '~/Videos'"
            )
        configuracion.carpeta_destino = Path(carpeta).expanduser()
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
    for clave in ("estancamiento_segundos", "estancamiento_velocidad_minima"):
        if clave in valores:
            valor = valores[clave]
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
                raise ConfiguracionInvalida(f"{ruta}: {clave} debe ser un número positivo")
            setattr(configuracion, clave, float(valor))
    if "plantilla_nombre" in valores:
        try:
            publish.validar_plantilla(valores["plantilla_nombre"])
        except ValueError as error:
            raise ConfiguracionInvalida(f"{ruta}: plantilla_nombre {error}") from None
        configuracion.plantilla_nombre = valores["plantilla_nombre"]
    return configuracion


def _es_sonda(valor) -> bool:
    if not isinstance(valor, str):
        return False
    host, _, puerto = valor.rpartition(":")
    return bool(host) and puerto.isdigit()
