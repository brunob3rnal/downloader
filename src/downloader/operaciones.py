"""Operaciones que comparten la CLI y la aplicación de escritorio (research R5 de la feature 002).

Validación de calidad, navegador y carpeta; agregar URLs a la cola; y procesar la cola, el
cuerpo de `ejecutar`. La CLI convierte los `ValueError` en `error: …` con el código 2 y la
aplicación los muestra en la ventana, así que los mensajes son los de siempre (FR-017).

No carga yt-dlp al importarse (research R15): `worker` y `network` se importan dentro de
`ejecutar_cola`, que solo llaman la CLI y el procesador de la aplicación.
"""

from pathlib import Path

from downloader import publish
from downloader.models import NAVEGADORES, Calidad
from downloader.store import ResultadoAgregar, Store


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
        return _procesar(store, ajustes, navegador, presentador)
    finally:
        bloqueo.liberar()  # `Store.cerrar` también lo libera; liberar dos veces no hace nada


def _procesar(store, ajustes, navegador, presentador):
    from downloader import worker
    from downloader.network import MonitorRed
    from downloader.progress import Presentador

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
        return worker.ejecutar(
            store, monitor, presentador or Presentador(), tiempos, navegador,
            ajustes.plantilla_nombre,
        )
    finally:
        monitor.detener()
