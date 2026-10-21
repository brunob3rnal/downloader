"""Monitor de conectividad (research R9)."""

import socket
import threading
from typing import Protocol


class Monitor(Protocol):
    """Lo que el trabajador necesita de un monitor; los tests usan uno controlado a mano."""

    en_linea: threading.Event

    def iniciar(self) -> None: ...

    def detener(self) -> None: ...


class MonitorRed:
    """Hilo que cada `intervalo` s intenta una conexión TCP a cada sonda ("host:puerto").

    `en_linea` está activo si alguna sonda conecta y desactivado si fallan todas.
    """

    def __init__(self, sondas: list[str], intervalo: float = 5, timeout: float = 3):
        self.sondas = [_separar(s) for s in sondas]
        self.intervalo = intervalo
        self.timeout = timeout
        self.en_linea = threading.Event()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None

    def comprobar(self) -> bool:
        conectado = any(self._conecta(host, puerto) for host, puerto in self.sondas)
        if conectado:
            self.en_linea.set()
        else:
            self.en_linea.clear()
        return conectado

    def iniciar(self) -> None:
        """Hace una primera comprobación (para empezar con un estado conocido) y arranca el hilo."""
        self._parar.clear()
        self.comprobar()
        self._hilo = threading.Thread(target=self._bucle, name="monitor-red", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=self.timeout + 1)

    def vivo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def _bucle(self) -> None:
        while not self._parar.wait(self.intervalo):
            self.comprobar()

    def _conecta(self, host: str, puerto: int) -> bool:
        try:
            with socket.create_connection((host, puerto), timeout=self.timeout):
                return True
        except OSError:
            return False


def _separar(sonda: str) -> tuple[str, int]:
    host, _, puerto = sonda.rpartition(":")
    return host.strip("[]"), int(puerto)
