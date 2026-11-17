"""API JSON de la ventana (contracts/api-local.md).

El servidor ya ha comprobado que la petición viene de la ventana; aquí solo se enruta. La
ventana se recibe como un objeto con cuatro operaciones (`Ventana`), lo que permite probar la
lógica en Linux con una ventana falsa (research R19).
"""

import json
import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from downloader import archivos_json, config, operaciones
from downloader.app import parada, procesos, registro, resultado, urls, vista
from downloader.models import NAVEGADORES, Calidad
from downloader.store import BaseCorrupta, Store

NOMBRES_CALIDAD = {"mejor": "Mejor calidad", "1080p": "1080p", "720p": "720p",
                   "audio": "Solo audio"}
AVISOS_NAVEGADOR = {
    "firefox": "Fiable, abierto o cerrado.",
    "edge": "Solo con Edge cerrado del todo, también en segundo plano.",
    "chrome": "Chrome 127 o posterior no funciona, ni cerrado.",
}
AVISO_CHROMIUM = "Usa el mismo código que Chrome y Edge."
MOTIVO_CLI = "Este procesamiento se lanzó desde la terminal; deténgalo allí con Ctrl+C."
MOTIVO_EN_MARCHA = "Ya hay un procesamiento en marcha."
MOTIVO_ARRANCANDO = "El procesamiento está arrancando."
MOTIVO_DETENIENDO = "El procesamiento se está deteniendo."
MOTIVO_TERMINANDO = "El procesamiento está terminando."
MOTIVO_NINGUNO = "No hay ningún procesamiento en marcha."
SIN_PARADA = "No se pudo pedir la parada; vuelva a intentarlo."
SIN_DIALOGO = "El diálogo de carpeta no está disponible; escriba la ruta"
SIN_ENLACES = "Pegue al menos un enlace"
_registro = logging.getLogger("downloader.app")


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
        self._cerrojo = threading.Lock()
        # El trabajador que lanzó esta ventana; se conserva al terminar, con su código.
        self.lanzamiento: procesos.Lanzamiento | None = None
        self._deteniendo: str | None = None  # ejecución para la que se pidió la parada
        self._cola_corrupta: str | None = None
        self._rutas = {
            ("GET", "opciones"): self._opciones,
            ("GET", "estado"): self._estado,
            ("POST", "agregar"): self._agregar,
            ("POST", "iniciar"): self._iniciar,
            ("POST", "detener"): self._detener,
            ("POST", "examinar"): self._examinar,
        }

    def atender(self, metodo: str, ruta: str, cuerpo: bytes, cabeceras) -> Respuesta:
        manejador = self._rutas.get((metodo, ruta))
        if manejador is None:
            return Respuesta(404, {"error": "ruta desconocida"})
        return manejador(cuerpo, cabeceras)

    # --- opciones ----------------------------------------------------------------------

    def _opciones(self, cuerpo, cabeceras) -> Respuesta:
        datos = {}
        try:
            ajustes = config.cargar(self.directorio)
        except config.ConfiguracionInvalida as error:
            ajustes = config.Configuracion()
            datos["aviso_config"] = str(error)
        datos.update(
            calidades=[{"id": c.value, "nombre": NOMBRES_CALIDAD[c.value]} for c in Calidad],
            calidad_inicial=ajustes.calidad.value,
            carpeta_inicial=str(ajustes.carpeta_destino),
            navegadores=[{"id": n, "nombre": n.capitalize(),
                          "aviso": AVISOS_NAVEGADOR.get(n, AVISO_CHROMIUM)}
                         for n in sorted(NAVEGADORES)],
            examinar_disponible=self._con_dialogo(),
        )
        return Respuesta(200, datos)

    def _con_dialogo(self) -> bool:
        return self.ventana is not None and getattr(self.ventana, "con_dialogo", True)

    # --- examinar ----------------------------------------------------------------------

    def _examinar(self, cuerpo, cabeceras) -> Respuesta:
        if not self._con_dialogo():
            return Respuesta(501, {"error": SIN_DIALOGO})
        return Respuesta(200, {"carpeta": self.ventana.examinar_carpeta()})

    # --- estado ------------------------------------------------------------------------

    def _procesador(self):
        """El procesador para la vista y lo que la ventana puede hacer con él (data-model).

        `procesador.json` dice quién procesa. Del trabajador que lanzó esta ventana se sabe
        además si el proceso sigue vivo: está `arrancando` hasta que se anuncia y, cuando
        retira el archivo al terminar, sigue ocupado hasta que el proceso acaba, así que otro
        Iniciar no puede adelantarse a que suelte el bloqueo de la cola.
        """
        estado = procesos.estado_procesador(self.directorio)
        lanzamiento = self.lanzamiento
        if lanzamiento is not None:
            lanzamiento.observar(estado)

        def ocupado(nombre, origen, motivo, puede_detener=False):
            return {"estado": nombre, "origen": origen, "puede_iniciar": False,
                    "puede_detener": puede_detener, "motivo": motivo}

        if estado.estado == "en_marcha":
            if estado.origen != "app":
                info = ocupado("en_marcha", estado.origen, MOTIVO_CLI)
            elif estado.ejecucion == self._deteniendo:
                info = ocupado("deteniendo", "app", MOTIVO_DETENIENDO)
            else:
                info = ocupado("en_marcha", "app", None, puede_detener=True)
        elif lanzamiento is not None and lanzamiento.vivo():
            if not lanzamiento.anunciado:
                info = ocupado("arrancando", "app", MOTIVO_ARRANCANDO)
            elif lanzamiento.ejecucion == self._deteniendo:
                info = ocupado("deteniendo", "app", MOTIVO_DETENIENDO)
            else:
                info = ocupado("en_marcha", "app", MOTIVO_TERMINANDO)
        else:
            info = {"estado": "ninguno", "origen": None, "puede_iniciar": True,
                    "puede_detener": False, "motivo": None}
        return estado, info

    def _leer_cola(self):
        if self._cola_corrupta is not None:
            return []  # tras la primera vez, sin volver a copiar la base cada segundo (R13)
        try:
            return Store.leer(self.directorio)
        except BaseCorrupta as error:
            self._cola_corrupta = str(error)
            return []

    def _estado(self, cuerpo, cabeceras) -> Respuesta:
        estado, info = self._procesador()
        vivo = archivos_json.leer(self.directorio / archivos_json.ARCHIVO_VIVO)
        ultima = resultado.leer(self.directorio)
        descargas = self._leer_cola()
        etiqueta = vista.etag(descargas, vivo, estado, ultima)[:-1] + (
            f"-{info['estado']}-{int(registro.disponible())}-{bool(self._cola_corrupta)}\"")
        if cabeceras.get("If-None-Match") == etiqueta:
            return Respuesta(304, None, {"ETag": etiqueta})
        ahora = datetime.now(UTC)
        directo = vista.en_directo(vivo, estado, ahora)
        datos = {
            "procesador": info,
            "en_linea": vivo.get("en_linea") if directo else None,
            "en_directo": directo,
            "cola": {"total": len(descargas),
                     "filas": vista.filas(descargas, vivo, estado, ahora)},
            "ultima_ejecucion": ultima,
            "avisos": {"registro_no_disponible": not registro.disponible(),
                       "cola_corrupta": self._cola_corrupta},
        }
        return Respuesta(200, datos, {"ETag": etiqueta})

    # --- agregar -----------------------------------------------------------------------

    def _agregar(self, cuerpo, cabeceras) -> Respuesta:
        try:
            datos = json.loads(cuerpo.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError):
            datos = None
        if not isinstance(datos, dict):
            return Respuesta(400, {"error": "petición inválida"})
        try:
            ajustes = config.cargar(self.directorio)
        except config.ConfiguracionInvalida as error:
            if datos.get("calidad") is None or datos.get("carpeta") is None:
                return Respuesta(400, {"error": str(error)})
            ajustes = config.Configuracion()
        try:
            calidad = operaciones.validar_calidad(datos.get("calidad"), ajustes.calidad)
            navegador = operaciones.validar_navegador(datos.get("navegador"))
            carpeta = operaciones.validar_carpeta(datos.get("carpeta"), ajustes.carpeta_destino)
        except ValueError as error:
            return Respuesta(400, {"error": str(error)})
        validas, invalidas = urls.separar(str(datos.get("texto") or ""))
        if not validas and not invalidas:
            return Respuesta(400, {"error": SIN_ENLACES})
        try:
            store = Store.abrir(self.directorio)
        except BaseCorrupta as error:
            return Respuesta(500, {"error": str(error)})
        try:
            resultados = operaciones.agregar_urls(store, validas, calidad, carpeta, navegador)
        finally:
            store.cerrar()
        respuesta = {"agregadas": [], "ya_en_cola": [], "reencoladas": [],
                     "lineas_invalidas": [{"linea": n, "texto": t} for n, t in invalidas]}
        for hecho in resultados:
            descarga = hecho.descarga
            entrada = {"id": descarga.id, "url": vista.url_visible(descarga.url)}
            if hecho.accion == "ya_en_cola":
                entrada["estado"] = descarga.estado.value
            clave = {"agregada": "agregadas", "ya_en_cola": "ya_en_cola",
                     "reencolada": "reencoladas"}[hecho.accion]
            respuesta[clave].append(entrada)
        return Respuesta(200, respuesta)

    # --- iniciar -----------------------------------------------------------------------

    def _iniciar(self, cuerpo, cabeceras) -> Respuesta:
        with self._cerrojo:
            _, info = self._procesador()
            if not info["puede_iniciar"]:
                return Respuesta(409, {"motivo": info["motivo"] or MOTIVO_EN_MARCHA})
            ejecucion = secrets.token_hex(8)
            try:
                proceso = procesos.lanzar_trabajador(self.directorio, ejecucion)
            except procesos.SinSegundoPlano as error:
                return Respuesta(409, {"motivo": str(error)})
            except OSError as error:
                _registro.exception("no se pudo lanzar el procesamiento")
                return Respuesta(500, {"error": f"No se pudo iniciar el procesamiento: {error}"})
            self.lanzamiento = procesos.Lanzamiento(proceso, ejecucion)
        return Respuesta(202, {"ejecucion": ejecucion})

    # --- detener -----------------------------------------------------------------------

    def _detener(self, cuerpo, cabeceras) -> Respuesta:
        """Pide la parada ordenada del procesador de la aplicación (FR-009, research R7)."""
        with self._cerrojo:
            estado, info = self._procesador()
            if info["estado"] == "deteniendo":
                return Respuesta(202, {"estado": "deteniendo"})  # repetido: sin efecto
            if not info["puede_detener"]:
                return Respuesta(409, {"motivo": info["motivo"] or MOTIVO_NINGUNO})
            try:
                pedida = parada.pedir(self.directorio, estado.ejecucion)
            except OSError as error:
                pedida = False
                _registro.error("no se pudo escribir la petición de parada: %s", error)
            else:
                if not pedida:
                    _registro.error("no se pudo escribir la petición de parada: en uso")
            if not pedida:
                return Respuesta(500, {"error": SIN_PARADA})
            self._deteniendo = estado.ejecucion
        return Respuesta(202, {"estado": "deteniendo"})
