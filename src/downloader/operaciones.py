"""Operaciones que comparten la CLI y la aplicación de escritorio (research R5 de la feature 002).

Validación de calidad, navegador y carpeta; agregar URLs a la cola; y procesar la cola, el
cuerpo de `ejecutar`. La CLI convierte los `ValueError` en `error: …` con el código 2 y la
aplicación los muestra en la ventana, así que los mensajes son los de siempre (FR-017).

No carga yt-dlp al importarse (research R15): `worker` y `network` se importan dentro de
`ejecutar_cola`, que solo llaman la CLI y el procesador de la aplicación.
"""

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import psutil

from downloader import archivos_json, publish
from downloader.models import NAVEGADORES, Calidad
from downloader.store import ResultadoAgregar, Store

# Quién procesa la cola (research R6 y contracts/archivos.md de la feature 002). Informativo:
# la exclusión la garantiza `ejecutar.lock`.
ARCHIVO_PROCESADOR = "procesador.json"
_registro_vivo = logging.getLogger("downloader.vivo")


def ffmpeg_ausente() -> str | None:
    """El mensaje de `ejecutar` si faltan ffmpeg o ffprobe en el PATH; `None` si están (R8).

    ffmpeg une y convierte los formatos y ffprobe verifica antes de publicar.
    """
    ausentes = [programa for programa in ("ffmpeg", "ffprobe") if shutil.which(programa) is None]
    if not ausentes:
        return None
    return (
        f"no se encuentra {' ni '.join(ausentes)} en el PATH. Instale ffmpeg, que incluye "
        "ffprobe, y vuelva a ejecutar:\n"
        "  Windows: winget install Gyan.FFmpeg  (o: choco install ffmpeg)\n"
        "  macOS:   brew install ffmpeg\n"
        "  Linux:   sudo apt install ffmpeg  (Debian, Ubuntu; o el gestor de su distribución)\n"
        "La cola no se ha modificado."
    )


def validar_calidad(valor: str | None, por_defecto: Calidad) -> Calidad:
    if valor is None:
        return por_defecto
    try:
        return Calidad(valor)
    except ValueError:
        admitidas = ", ".join(c.value for c in Calidad)
        raise ValueError(f"calidad desconocida «{valor}»; admitidas: {admitidas}") from None


def validar_navegador(valor: str | None) -> str | None:
    """El nombre del navegador, si es uno de los que admite yt-dlp."""
    if valor is None:
        return None
    navegador = valor.strip().lower()
    if navegador not in NAVEGADORES:
        admitidos = ", ".join(sorted(NAVEGADORES))
        raise ValueError(f"navegador no admitido en --cookies-de: «{valor}»; admitidos: {admitidos}")
    return navegador


def validar_carpeta(valor: Path | str | None, por_defecto: Path) -> Path:
    carpeta = Path(valor or por_defecto).expanduser().resolve()
    if not carpeta.is_dir():
        raise ValueError(f"la carpeta de destino {carpeta} no existe")
    # A través del módulo, para que los tests puedan sustituir `publish.escribible`.
    if not publish.escribible(carpeta):
        raise ValueError(f"sin permiso de escritura en la carpeta de destino {carpeta}")
    return carpeta


def agregar_urls(store: Store, urls, calidad: Calidad, carpeta: Path,
                 navegador: str | None) -> list[ResultadoAgregar]:
    """Agrega cada URL como `agregar`: sin duplicar las que están en cola y devolviendo a la
    cola las fallidas (FR-004 de la feature 002)."""
    return [
        store.agregar(url.strip(), calidad, str(carpeta), navegador_cookies=navegador)
        for url in urls
    ]


def ejecutar_cola(store: Store, ajustes, origen: str, ejecucion: str | None = None,
                  navegador: str | None = None, presentador=None):
    """Procesa la cola hasta que no quedan pendientes y devuelve el `worker.Resultado`.

    Toma `ejecutar.lock` (lanza `EjecucionEnCurso` si otro procesador lo tiene), devuelve a la
    cola lo que un cierre abrupto dejó a medias (R11), y procesa con el monitor de red. Lanza
    `ErrorFatal` y deja pasar `KeyboardInterrupt` como `ejecutar`. Comprobar ffmpeg y cargar la
    configuración es cosa del llamador. `origen` (`cli` o `app`) y `ejecucion` identifican al
    procesador (research R6 de la feature 002).
    """
    bloqueo = store.bloquear_ejecucion()
    try:
        pid, creado = _anunciar(store.directorio, origen, ejecucion)
        try:
            return _procesar(store, ajustes, navegador, presentador, pid, creado)
        finally:
            _retirar(store.directorio)
    finally:
        bloqueo.liberar()  # `Store.cerrar` también lo libera; liberar dos veces no hace nada


def _anunciar(directorio: Path, origen: str, ejecucion: str | None):
    """Escribe `procesador.json` justo después de tomar el bloqueo.

    Si no puede (disco lleno, sin permiso, error de `psutil`), lo anota en `downloader.vivo` y
    sigue: el archivo es informativo y nunca cambia la salida ni el código de `ejecutar` (R9).
    """
    ruta = Path(directorio) / ARCHIVO_PROCESADOR
    pid, creado = os.getpid(), None
    try:
        creado = psutil.Process(pid).create_time()
        datos = {"pid": pid, "creado": creado, "origen": origen, "ejecucion": ejecucion,
                 "inicio": datetime.now(UTC).isoformat(timespec="milliseconds")}
        if not archivos_json.escribir_atomico(ruta, datos, reintentar=True):
            _registro_vivo.warning("no se puede escribir %s: el archivo está en uso", ruta)
    except (OSError, psutil.Error) as error:
        _registro_vivo.warning("no se puede escribir %s: %s", ruta, error)
    return pid, creado


def _retirar(directorio: Path) -> None:
    ruta = Path(directorio) / ARCHIVO_PROCESADOR
    try:
        archivos_json.borrar(ruta)
    except OSError as error:
        _registro_vivo.warning("no se puede borrar %s: %s", ruta, error)


def _procesar(store, ajustes, navegador, presentador, pid, creado):
    from downloader import worker
    from downloader.network import MonitorRed
    from downloader.progress import Observadores, Presentador, Publicador

    # Lo que un cierre abrupto dejó en `downloading` o `waiting_network` vuelve a la cola;
    # el bloqueo garantiza que no es de otro `ejecutar` en marcha (R11).
    store.recuperar()
    monitor = MonitorRed(ajustes.sondas_red)
    monitor.iniciar()
    try:
        # A través del módulo, para que los tests puedan sustituir `worker.Tiempos`.
        tiempos = worker.Tiempos(
            estancamiento=ajustes.estancamiento_segundos,
            velocidad_minima=ajustes.estancamiento_velocidad_minima,
        )
        # La salida de la CLI es la del `Presentador`; el `Publicador` escribe
        # `progreso-vivo.json` para la ventana sin tocarla (R9).
        observadores = Observadores([
            presentador or Presentador(),
            Publicador(store.directorio, pid, creado, monitor=monitor),
        ])
        return worker.ejecutar(
            store, monitor, observadores, tiempos, navegador, ajustes.plantilla_nombre
        )
    finally:
        monitor.detener()
