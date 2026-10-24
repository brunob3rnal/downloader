"""Cola persistente en SQLite: esquema, altas, transiciones, recuperación y bloqueo (R11)."""

import errno
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from downloader.errors import ErrorFatal
from downloader.models import (
    PENDIENTES,
    Calidad,
    Descarga,
    Estado,
    TipoDescarga,
    validar_transicion,
)

# Bloqueo de `ejecutar.lock` (research R11): `msvcrt.locking` en Windows y `fcntl.flock` en
# el resto. En ambos casos el sistema libera el bloqueo si el proceso muere.
if os.name == "nt":
    import msvcrt

    def _bloquear(descriptor: int) -> bool:
        os.lseek(descriptor, 0, os.SEEK_SET)  # se bloquea el primer byte (aunque no exista)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
        return True

    def _desbloquear(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _bloquear(descriptor: int) -> bool:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _desbloquear(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)

ARCHIVO = "cola.db"
BLOQUEO = "ejecutar.lock"
INTERVALO_PROGRESO = 2  # s entre escrituras de progreso; la verdad está en los .part

_ESQUEMA = f"""
CREATE TABLE IF NOT EXISTS descargas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT NOT NULL,
    calidad             TEXT NOT NULL CHECK (calidad IN ({", ".join(repr(c.value) for c in Calidad)})),
    carpeta_destino     TEXT NOT NULL,
    navegador_cookies   TEXT,
    tipo                TEXT NOT NULL DEFAULT 'video'
                        CHECK (tipo IN ({", ".join(repr(t.value) for t in TipoDescarga)})),
    padre_id            INTEGER REFERENCES descargas(id),
    estado              TEXT NOT NULL CHECK (estado IN ({", ".join(repr(e.value) for e in Estado)})),
    titulo              TEXT,
    bytes_descargados   INTEGER NOT NULL DEFAULT 0,
    bytes_totales       INTEGER,
    intentos_sin_avance INTEGER NOT NULL DEFAULT 0,
    proximo_intento     TEXT,
    ultimo_error        TEXT,
    ultimo_aviso        TEXT,
    archivo_final       TEXT,
    creada              TEXT NOT NULL,
    actualizada         TEXT NOT NULL,
    CHECK ((archivo_final IS NOT NULL) = (estado = 'completed' AND tipo = 'video'))
);
CREATE UNIQUE INDEX IF NOT EXISTS descargas_url_calidad_no_fallidas
    ON descargas (url, calidad) WHERE estado != 'failed';
"""

# Campos que `transicion` puede modificar junto con el estado.
_EDITABLES = {f.name for f in fields(Descarga)} - {"id", "estado", "creada", "actualizada"}


class BaseCorrupta(ErrorFatal):
    pass


class EjecucionEnCurso(Exception):
    pass


@dataclass
class ResultadoAgregar:
    accion: str  # "agregada" | "ya_en_cola" | "reencolada"
    descarga: Descarga


class Bloqueo:
    """Bloqueo exclusivo de `ejecutar.lock`; el sistema lo libera si el proceso muere."""

    def __init__(self, descriptor: int):
        self._descriptor = descriptor

    def liberar(self) -> None:
        if self._descriptor is not None:
            # Windows libera al cerrar, pero puede tardar: se desbloquea antes explícitamente.
            try:
                _desbloquear(self._descriptor)
            finally:
                os.close(self._descriptor)
                self._descriptor = None


def _ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _valor(valor):
    return valor.value if isinstance(valor, Enum) else valor


def _descarga(fila: sqlite3.Row) -> Descarga:
    datos = dict(fila)
    datos["estado"] = Estado(datos["estado"])
    datos["calidad"] = Calidad(datos["calidad"])
    datos["tipo"] = TipoDescarga(datos["tipo"])
    return Descarga(**datos)


class Store:
    def __init__(self, directorio: Path, conexion: sqlite3.Connection):
        self.directorio = directorio
        self._conexion = conexion
        self._cerrojo = threading.RLock()
        self._ultimo_progreso: dict[int, float] = {}
        self._bloqueos: list[Bloqueo] = []

    @classmethod
    def abrir(cls, directorio: Path) -> "Store":
        """Abre (o crea) la cola; si el archivo está corrupto, guarda una copia y lanza FATAL."""
        directorio = Path(directorio)
        ruta = directorio / ARCHIVO
        conexion = None
        try:
            conexion = sqlite3.connect(ruta, isolation_level=None, check_same_thread=False)
            resultado = conexion.execute("PRAGMA quick_check").fetchone()[0]
            if resultado != "ok":
                raise sqlite3.DatabaseError(resultado)
            conexion.execute("PRAGMA journal_mode = WAL")
            conexion.execute("PRAGMA synchronous = FULL")
            conexion.execute("PRAGMA foreign_keys = ON")
            conexion.executescript(_ESQUEMA)
        except sqlite3.DatabaseError as error:
            if conexion is not None:
                conexion.close()
            copia = ruta.with_name(f"{ARCHIVO}.corrupta-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
            shutil.copy2(ruta, copia)
            raise BaseCorrupta(
                f"El archivo de estado de la cola ({ruta}) está corrupto ({error}). "
                f"Se guardó una copia en {copia}. No se ha creado una cola nueva ni se ha tocado "
                f"ningún archivo descargado; mueva o borre {ruta} para empezar una cola vacía."
            ) from error
        conexion.row_factory = sqlite3.Row
        return cls(directorio, conexion)

    def cerrar(self) -> None:
        for bloqueo in self._bloqueos:
            bloqueo.liberar()
        self._bloqueos.clear()
        self._conexion.close()

    def _leer(self, sql: str, parametros=()) -> list[sqlite3.Row]:
        with self._cerrojo:
            return self._conexion.execute(sql, parametros).fetchall()

    def pragma(self, nombre: str):
        return self._leer(f"PRAGMA {nombre}")[0][0]

    # --- consultas -----------------------------------------------------------------

    def obtener(self, id_: int) -> Descarga:
        filas = self._leer("SELECT * FROM descargas WHERE id = ?", (id_,))
        if not filas:
            raise KeyError(f"no existe la descarga #{id_}")
        return _descarga(filas[0])

    def contar(self) -> int:
        return self._leer("SELECT COUNT(*) FROM descargas")[0][0]

    def siguiente(self) -> Descarga | None:
        """La descarga en cola más antigua."""
        filas = self._leer(
            "SELECT * FROM descargas WHERE estado = ? ORDER BY id LIMIT 1", (Estado.QUEUED.value,)
        )
        return _descarga(filas[0]) if filas else None

    def hay_pendientes(self) -> bool:
        marcas = ", ".join("?" * len(PENDIENTES))
        return self._leer(
            f"SELECT EXISTS (SELECT 1 FROM descargas WHERE estado IN ({marcas}))",
            [e.value for e in PENDIENTES],
        )[0][0] == 1

    # --- escrituras ----------------------------------------------------------------

    def _transaccion(self):
        return _Transaccion(self._conexion, self._cerrojo)

    def agregar(
        self,
        url: str,
        calidad: Calidad | str,
        carpeta_destino: str,
        navegador_cookies: str | None = None,
        padre_id: int | None = None,
    ) -> ResultadoAgregar:
        """Añade una URL a la cola.

        Si la pareja (url, calidad) ya existe sin haber fallado, no crea fila; si existe fallida,
        la vuelve a poner en cola con el mismo id (y por tanto el mismo directorio de trabajo) y,
        si se indica un navegador, con ese navegador: así funciona el consejo de FR-026, «vuelva
        a agregarlo con --cookies-de NAVEGADOR».
        """
        calidad = Calidad(calidad)
        with self._transaccion() as c:
            fila = c.execute(
                "SELECT * FROM descargas WHERE url = ? AND calidad = ? "
                "ORDER BY estado = 'failed' LIMIT 1",
                (url, calidad.value),
            ).fetchone()
            if fila is not None and fila["estado"] != Estado.FAILED:
                return ResultadoAgregar("ya_en_cola", _descarga(fila))
            if fila is not None:
                validar_transicion(Estado.FAILED, Estado.QUEUED)
                c.execute(
                    "UPDATE descargas SET estado = ?, ultimo_error = NULL, proximo_intento = NULL,"
                    " intentos_sin_avance = 0, navegador_cookies = COALESCE(?, navegador_cookies),"
                    " actualizada = ? WHERE id = ?",
                    (Estado.QUEUED.value, navegador_cookies, _ahora(), fila["id"]),
                )
                accion, id_ = "reencolada", fila["id"]
            else:
                validar_transicion(None, Estado.QUEUED)
                ahora = _ahora()
                cursor = c.execute(
                    "INSERT INTO descargas (url, calidad, carpeta_destino, navegador_cookies,"
                    " padre_id, estado, creada, actualizada) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, calidad.value, str(carpeta_destino), navegador_cookies, padre_id,
                     Estado.QUEUED.value, ahora, ahora),
                )
                accion, id_ = "agregada", cursor.lastrowid
        return ResultadoAgregar(accion, self.obtener(id_))

    def transicion(self, id_: int, hacia: Estado, **campos) -> Descarga:
        """Cambia el estado en una transacción, validando la transición, junto con `campos`.

        Al salir de `waiting_network` se borra `proximo_intento` salvo que se indique otro.
        """
        desconocidos = set(campos) - _EDITABLES
        if desconocidos:
            raise TypeError(f"campos no editables: {', '.join(sorted(desconocidos))}")
        if hacia is not Estado.WAITING_NETWORK:
            campos.setdefault("proximo_intento", None)
        with self._transaccion() as c:
            fila = c.execute("SELECT estado FROM descargas WHERE id = ?", (id_,)).fetchone()
            if fila is None:
                raise KeyError(f"no existe la descarga #{id_}")
            validar_transicion(Estado(fila["estado"]), hacia)
            asignaciones = ", ".join(f"{campo} = ?" for campo in ["estado", "actualizada", *campos])
            c.execute(
                f"UPDATE descargas SET {asignaciones} WHERE id = ?",
                [hacia.value, _ahora(), *map(_valor, campos.values()), id_],
            )
        return self.obtener(id_)

    def guardar_progreso(
        self, id_: int, bytes_descargados: int, bytes_totales: int | None, forzar: bool = False
    ) -> bool:
        """Guarda el progreso informativo, como mucho cada `INTERVALO_PROGRESO` s por descarga."""
        ahora = time.monotonic()
        anterior = self._ultimo_progreso.get(id_)
        if not forzar and anterior is not None and ahora - anterior < INTERVALO_PROGRESO:
            return False
        self._ultimo_progreso[id_] = ahora
        with self._transaccion() as c:
            c.execute(
                "UPDATE descargas SET bytes_descargados = ?, bytes_totales = ?, actualizada = ?"
                " WHERE id = ?",
                (bytes_descargados, bytes_totales, _ahora(), id_),
            )
        return True

    def guardar_aviso(self, id_: int, aviso: str) -> None:
        """Guarda `ultimo_aviso` sin cambiar de estado (p. ej. un reinicio desde cero, FR-024)."""
        with self._transaccion() as c:
            c.execute(
                "UPDATE descargas SET ultimo_aviso = ?, actualizada = ? WHERE id = ?",
                (aviso, _ahora(), id_),
            )

    def recuperar(self) -> int:
        """Devuelve a la cola las descargas que un cierre abrupto dejó a medias."""
        interrumpidos = (Estado.DOWNLOADING, Estado.WAITING_NETWORK)
        for estado in interrumpidos:
            validar_transicion(estado, Estado.QUEUED)
        with self._transaccion() as c:
            cursor = c.execute(
                "UPDATE descargas SET estado = ?, proximo_intento = NULL, actualizada = ?"
                " WHERE estado IN (?, ?)",
                (Estado.QUEUED.value, _ahora(), *(e.value for e in interrumpidos)),
            )
            return cursor.rowcount

    # --- un único ejecutar ---------------------------------------------------------

    def bloquear_ejecucion(self) -> Bloqueo:
        ruta = self.directorio / BLOQUEO
        descriptor = os.open(ruta, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            bloqueado = _bloquear(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        if not bloqueado:
            os.close(descriptor)
            raise EjecucionEnCurso(
                "Ya hay otro `downloader ejecutar` en marcha; espere a que termine. "
                "Puede seguir agregando URLs con `downloader agregar` y las recogerá."
            )
        bloqueo = Bloqueo(descriptor)
        self._bloqueos.append(bloqueo)
        return bloqueo


class _Transaccion:
    """`BEGIN IMMEDIATE` … `COMMIT`, o `ROLLBACK` si hay una excepción."""

    def __init__(self, conexion: sqlite3.Connection, cerrojo: threading.RLock):
        self._conexion = conexion
        self._cerrojo = cerrojo

    def __enter__(self) -> sqlite3.Connection:
        self._cerrojo.acquire()
        try:
            self._conexion.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._cerrojo.release()
            raise
        return self._conexion

    def __exit__(self, tipo, *_):
        try:
            self._conexion.execute("ROLLBACK" if tipo else "COMMIT")
        finally:
            self._cerrojo.release()
        return False
