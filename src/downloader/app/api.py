"""API JSON de la ventana (contracts/api-local.md).

El servidor ya ha comprobado que la petición viene de la ventana; aquí solo se enruta. La
ventana se recibe como un objeto con cuatro operaciones (`Ventana`), lo que permite probar la
lógica en Linux con una ventana falsa (research R19).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class Ventana(Protocol):
    def examinar_carpeta(self) -> str | None: ...
    def enfocar(self) -> None: ...
    def ejecutar_js(self, codigo: str) -> None: ...
    def destruir(self) -> None: ...


@dataclass
class Respuesta:
    estado: int
    datos: dict | None = None
    cabeceras: dict = field(default_factory=dict)


class Api:
    def __init__(self, directorio, ventana: Ventana | None = None):
        self.directorio = Path(directorio)
        self.ventana = ventana
        self._rutas: dict = {}

    def atender(self, metodo: str, ruta: str, cuerpo: bytes, cabeceras) -> Respuesta:
        manejador = self._rutas.get((metodo, ruta))
        if manejador is None:
            return Respuesta(404, {"error": "ruta desconocida"})
        return manejador(cuerpo, cabeceras)
