"""Parada ordenada del procesador de la aplicación: el equivalente de Ctrl+C (research R7).

La ventana pide la parada escribiendo `parada.json` con el identificador de la ejecución
(contracts/archivos.md). En el subproceso, un hilo vigilante lo consulta cada 0,25 s y, si es
para su ejecución, provoca un `KeyboardInterrupt` en el hilo principal por la misma vía que una
pulsación de Ctrl+C. A partir de ahí el camino es el de `ejecutar`: se guarda el progreso, la
descarga vuelve a la cola y el proceso termina con 130. Un `ejecutar` de la CLI no tiene
vigilante: la aplicación no puede detenerlo (FR-009).
"""

import logging
import signal
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from downloader import archivos_json

ARCHIVO = "parada.json"
INTERVALO = 0.25  # s entre consultas del vigilante
_registro = logging.getLogger("downloader.app")


def pedir(directorio, ejecucion: str) -> bool:
    """Escribe la petición de parada para `ejecucion`; `False` si el archivo está en uso."""
    datos = {"ejecucion": ejecucion,
             "pedida": datetime.now(UTC).isoformat(timespec="milliseconds")}
    return archivos_json.escribir_atomico(Path(directorio) / ARCHIVO, datos, reintentar=True)


def asegurar_interrupcion() -> None:
    """Hace que `SIGINT` lance `KeyboardInterrupt` en el hilo principal.

    Python solo instala ese manejador al arrancar si la señal no llega ignorada, y un proceso
    la hereda ignorada si quien lo lanza la ignora (un trabajo en segundo plano de una shell
    no interactiva, por ejemplo). Sin él, la interrupción del vigilante no haría nada. Solo se
    puede fijar desde el hilo principal; desde otro no hace nada.
    """
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.default_int_handler)


def interrumpir_principal() -> None:
    """Provoca en el hilo principal lo mismo que Ctrl+C; se llama desde otro hilo (R7)."""
    if sys.platform == "win32":
        # `raise()` de C ejecuta el manejador que Python instaló para SIGINT, el mismo que usa
        # la consola con Ctrl+C: marca la señal y activa el evento que despierta `time.sleep`.
        signal.raise_signal(signal.SIGINT)
    else:
        # Al hilo principal: interrumpe su llamada al sistema en curso (EINTR), como Ctrl+C.
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)


class Vigilante(threading.Thread):
    """Hilo que obedece la petición de parada de su ejecución, una sola vez.

    Una petición para otra ejecución es antigua (solo hay un procesador a la vez): se borra sin
    interrumpir. Tras interrumpir deja de vigilar, porque una segunda interrupción a media
    limpieza cortaría el guardado del progreso. La primera consulta llega tras un intervalo, no
    al arrancar: un proceso que pierde el bloqueo de la cola termina antes y no toca nada.
    """

    def __init__(self, directorio, ejecucion: str, interrumpir=interrumpir_principal,
                 intervalo: float = INTERVALO):
        super().__init__(name="vigilante-de-parada", daemon=True)
        self.ruta = Path(directorio) / ARCHIVO
        self.ejecucion = ejecucion
        self.interrumpir = interrumpir
        self.intervalo = intervalo
        self._parar = threading.Event()
        self._avisado = False

    def run(self) -> None:
        while not self._parar.wait(self.intervalo):
            if self._consultar():
                return

    def _consultar(self) -> bool:
        """Si había una petición para esta ejecución (y se ha interrumpido)."""
        datos = archivos_json.leer(self.ruta)
        if datos is None:
            return False  # no hay petición, o está a medio escribir: se mira en la siguiente
        try:
            archivos_json.borrar(self.ruta)
        except OSError as error:
            # No impide parar: si se queda atrás, la siguiente ejecución la borra por antigua.
            if not self._avisado:  # una vez, no cada 0,25 s
                self._avisado = True
                _registro.warning("no se puede borrar %s: %s", self.ruta, error)
        if datos.get("ejecucion") != self.ejecucion:
            return False
        if self._parar.is_set():
            return True  # ya se terminó de procesar: no hay nada que interrumpir
        self.interrumpir()
        return True

    def detener(self) -> None:
        """Deja de vigilar y espera al hilo: después de volver ya no interrumpe."""
        self._parar.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join()
