"""El procesador de la aplicación: `python -m downloader.app.procesador --ejecucion ID`.

Lo lanza la ventana (`procesos.lanzar_trabajador`) y procesa la cola con el mismo código que
`ejecutar` (`operaciones.ejecutar_cola`, research R5). Además escribe el resultado de la
ejecución (FR-012) y lleva sus errores al registro (FR-020, research R10). Con otro procesador
en marcha sale con 2 sin escribir resultado: perdió la carrera por el bloqueo (R6).
"""

import argparse
import logging
import sys
from datetime import UTC, datetime

from downloader import config, operaciones
from downloader.app import registro, resultado
from downloader.excepciones import ErrorFatal
from downloader.store import BaseCorrupta, EjecucionEnCurso, Store

USO_INVALIDO, FATAL, INTERRUMPIDO = 2, 3, 130
_registro = logging.getLogger("downloader.app")


def main(argv=None) -> int:
    argumentos = argparse.ArgumentParser(prog="python -m downloader.app.procesador")
    argumentos.add_argument("--ejecucion", required=True)
    ejecucion = argumentos.parse_args(argv).ejecucion
    directorio = config.directorio_app()
    manejo = registro.configurar("procesador", directorio)
    try:
        return _procesar(directorio, ejecucion,
                         datetime.now(UTC).isoformat(timespec="milliseconds"))
    finally:
        manejo.cerrar()


def _procesar(directorio, ejecucion: str, inicio: str) -> int:
    def terminar(codigo, completadas=0, fallidas=(), error=None) -> int:
        resultado.escribir(directorio, ejecucion, inicio, completadas, list(fallidas), codigo,
                           error)
        return codigo

    def fatal(codigo, mensaje):
        _registro.error("el procesamiento no pudo hacerse: %s", mensaje)
        return terminar(codigo, error={"tipo": "fatal", "mensaje": mensaje})

    try:
        ajustes = config.cargar(directorio)
    except config.ConfiguracionInvalida as error:
        return fatal(USO_INVALIDO, str(error))
    mensaje = operaciones.ffmpeg_ausente()
    if mensaje:
        return fatal(FATAL, mensaje)
    try:
        store = Store.abrir(directorio)
    except BaseCorrupta as error:
        return fatal(FATAL, str(error))
    try:
        try:
            hecho = operaciones.ejecutar_cola(store, ajustes, origen="app", ejecucion=ejecucion)
        except EjecucionEnCurso:
            return USO_INVALIDO  # otro procesador ganó el bloqueo: sin resultado (R6)
        except ErrorFatal as error:
            return fatal(FATAL, f"{error}. El progreso se ha conservado.")
        except KeyboardInterrupt:
            return terminar(INTERRUMPIDO, error={"tipo": "detenida",
                                                 "mensaje": "interrumpido; progreso guardado"})
        except Exception as error:
            _registro.exception("el procesamiento terminó por una excepción inesperada")
            return terminar(1, error={
                "tipo": "inesperado",
                "mensaje": f"{type(error).__name__}: {error}; detalle en el registro de errores",
            })
        fallidas = []
        for id_ in hecho.fallidas:
            descarga = store.obtener(id_)
            fallidas.append({"id": id_, "nombre": descarga.titulo or descarga.url,
                             "causa": descarga.ultimo_error})
        return terminar(1 if fallidas else 0, len(hecho.completadas), fallidas)
    finally:
        store.cerrar()


if __name__ == "__main__":
    sys.exit(main())
