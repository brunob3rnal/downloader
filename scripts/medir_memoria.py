"""Medición de memoria de la aplicación de escritorio (specs/002-desktop-app, SC-005, research R14).

Mide la memoria privada (USS) sumada de todos los procesos de la aplicación mientras descarga
la primera de N descargas en cola, y la compara con el límite de SC-005 (300 MB). La medición
real se hace a mano en Windows (tasks.md, T011); las funciones puras de la parte de arriba
tienen tests en `tests/unit/test_medir_memoria.py` y se importan también en Linux.

    uv run python scripts/medir_memoria.py [--cola-de-prueba 500] [--limite 300]
        [--ventana minima|app] [--sin-uv] [--importar-store] [--tiempo-maximo 900]

Procesos de la aplicación: la ventana y sus descendientes (WebView2), los ascendientes de la
ventana que son de la cadena del lanzador (uv y el lanzador del entorno virtual), y el
trabajador con sus descendientes (ffmpeg, ffprobe). MB decimales: 1 MB = 1 000 000 bytes.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

RAIZ = Path(__file__).resolve().parents[1]
# Ejecutables de la cadena del lanzador: uv y el lanzador del entorno virtual (R11, R14).
CADENA = {"uv", "uv.exe", "uvw", "uvw.exe", "python", "python.exe", "pythonw", "pythonw.exe"}
WEBVIEW2 = "msedgewebview2.exe"
ARGUMENTOS_WEBVIEW2 = (
    "--disable-features", "--renderer-process-limit", "--disable-gpu", "--disable-extensions",
)
INTERVALO = 0.5  # s entre muestras
TAMANO = 200_000_000  # bytes del archivo de prueba
VELOCIDAD = 2_000_000  # bytes/s


@dataclass
class Muestra:
    instante: float  # s desde el inicio de la medición
    total: int  # bytes de USS sumados
    desglose: list = field(default_factory=list)  # [(pid, nombre, uss)]
    sin_acceso: list = field(default_factory=list)  # [(pid, nombre)]: no se pudo medir


def mb(octetos: float) -> float:
    return octetos / 1_000_000


def _descendientes(proceso) -> list:
    try:
        return proceso.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def procesos_de_la_aplicacion(ventana, trabajador, tope=None) -> list:
    """Los procesos que cuentan para SC-005, sin repetir.

    `tope` es el pid donde se deja de subir por los ascendientes: el del propio script de
    medición, que también es `python.exe` y no forma parte de la aplicación.
    """
    procesos = [ventana, *_descendientes(ventana)]
    padre = ventana.parent()
    while padre is not None and padre.pid != tope:
        try:
            nombre = padre.name().lower()
        except psutil.NoSuchProcess:
            break
        if nombre not in CADENA:
            break
        procesos.append(padre)
        padre = padre.parent()
    if trabajador is not None:
        procesos += [trabajador, *_descendientes(trabajador)]
    unicos, vistos = [], set()
    for proceso in procesos:
        if proceso.pid not in vistos:
            vistos.add(proceso.pid)
            unicos.append(proceso)
    return unicos


def suma_uss(procesos, instante: float) -> Muestra:
    """Suma la USS; ignora los procesos que terminan durante la muestra."""
    muestra = Muestra(instante, 0)
    for proceso in procesos:
        try:
            nombre = proceso.name()
            uss = proceso.memory_full_info().uss
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            muestra.sin_acceso.append((proceso.pid, nombre))
            continue
        muestra.total += uss
        muestra.desglose.append((proceso.pid, nombre, uss))
    return muestra


def maximo(muestras):
    """La muestra con la suma mayor, con su desglose; `None` si no hay muestras."""
    return max(muestras, key=lambda m: m.total, default=None)


# --- medición (solo Windows) ---------------------------------------------------------


class MedicionFallida(Exception):
    pass


def _esperar(condicion, segundos: float, que: str):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        resultado = condicion()
        if resultado:
            return resultado
        time.sleep(0.25)
    raise MedicionFallida(f"no se ha conseguido en {segundos:.0f} s: {que}")


def _linea_de_ordenes(proceso) -> list[str]:
    try:
        return proceso.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def _buscar_ventana(lanzado, script: Path):
    """El intérprete que ejecuta la ventana: el más profundo de la cadena con el script."""
    candidatos = [
        p for p in [lanzado, *_descendientes(lanzado)]
        if any(script.name in parte for parte in _linea_de_ordenes(p))
    ]
    for candidato in candidatos:
        hijos = _descendientes(candidato)
        if not any(h in candidatos for h in hijos) and any(
            _nombre(h) == WEBVIEW2 for h in hijos
        ):
            return candidato
    return None


def _nombre(proceso) -> str:
    try:
        return proceso.name().lower()
    except psutil.NoSuchProcess:
        return ""


def _navegador_webview2(ventana):
    """El proceso principal de WebView2: un `msedgewebview2.exe` sin `--type=`."""
    for proceso in _descendientes(ventana):
        if _nombre(proceso) == WEBVIEW2:
            orden = _linea_de_ordenes(proceso)
            if orden and not any(parte.startswith("--type=") for parte in orden):
                return proceso
    return None


def _terminar(proceso) -> None:
    if proceso is None:
        return
    for p in [*_descendientes(proceso), proceso]:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def _primera_descarga(home: Path):
    from downloader.store import Store

    descargas = Store.leer(home)
    return descargas[0] if descargas else None


def _preparar_cola(home: Path, destino: Path, url: str, cantidad: int) -> None:
    from downloader.models import Calidad
    from downloader.store import Store

    store = Store.abrir(home)
    try:
        for i in range(cantidad):
            store.agregar(f"{url}?n={i}", Calidad.MEJOR, str(destino))
    finally:
        store.cerrar()


def medir(args) -> tuple[Muestra | None, dict]:
    """Lanza servidor, trabajador y ventana; devuelve el máximo y los datos del informe."""
    informe: dict = {"muestras": 0}
    temporal = Path(tempfile.mkdtemp(prefix="gestor-memoria-"))
    home, destino = temporal / "home", temporal / "destino"
    home.mkdir()
    destino.mkdir()
    entorno = {**os.environ, "DOWNLOADER_HOME": str(home)}
    servidor = trabajador = lanzado = None
    try:
        servidor = subprocess.Popen(
            [sys.executable, "-m", "tests.support.flaky_server", "--servir", "0",
             "--limite", str(VELOCIDAD), "--archivo", "video.mp4", "--tamano", str(TAMANO)],
            cwd=RAIZ, stdout=subprocess.PIPE, text=True,
        )
        linea = servidor.stdout.readline().strip()
        if not linea.startswith("sirviendo "):
            raise MedicionFallida(f"el servidor de prueba no arrancó: {linea!r}")
        _preparar_cola(home, destino, linea.removeprefix("sirviendo "), args.cola_de_prueba)

        script = (RAIZ / "scripts" / "ventana_minima.py" if args.ventana == "minima"
                  else RAIZ / "src" / "downloader" / "app" / "gestor.pyw")
        if not script.exists():
            raise MedicionFallida(f"no existe {script.relative_to(RAIZ)} (la ventana de la "
                                  "aplicación llega en la Fase 8)")
        extra = ["--importar-store"] if args.importar_store else []
        if args.sin_uv:
            orden = [str(Path(sys.executable).with_name("pythonw.exe")), str(script), *extra]
        else:
            uv = shutil.which("uv")
            if uv is None:
                raise MedicionFallida("no se encuentra uv en el PATH")
            orden = [uv, "run", "--gui-script", str(script), *extra]

        inicio = time.monotonic()
        trabajador = psutil.Popen(
            [sys.executable, "-m", "downloader", "ejecutar"], cwd=RAIZ, env=entorno,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        lanzado = psutil.Popen(orden, cwd=RAIZ, env=entorno)
        ventana = _esperar(lambda: _buscar_ventana(lanzado, script), 90,
                           "abrir la ventana con WebView2")
        navegador = _esperar(lambda: _navegador_webview2(ventana), 30,
                             "encontrar el proceso principal de WebView2")
        informe["webview2"] = _linea_de_ordenes(navegador)
        try:
            informe["webview2_exe"] = navegador.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            informe["webview2_exe"] = "?"

        muestras: list[Muestra] = []
        ultima_consulta = 0.0
        while True:
            ahora = time.monotonic() - inicio
            if ahora > args.tiempo_maximo:
                informe["fin"] = f"tiempo máximo ({args.tiempo_maximo} s) sin terminar la descarga"
                break
            if not ventana.is_running():
                raise MedicionFallida("la ventana se cerró durante la medición")
            if trabajador.poll() is not None:
                if "primera" not in informe:
                    raise MedicionFallida(
                        f"el trabajador terminó con el código {trabajador.returncode} antes de "
                        "descargar nada (con 3, compruebe `ffmpeg -version` y `ffprobe -version`)"
                    )
                informe["fin"] = f"el trabajador terminó con el código {trabajador.returncode}"
                break
            procesos = procesos_de_la_aplicacion(ventana, trabajador, tope=os.getpid())
            muestras.append(suma_uss(procesos, round(ahora, 1)))
            if ahora - ultima_consulta >= 2:
                ultima_consulta = ahora
                primera = _primera_descarga(home)
                if primera is not None:
                    informe["primera"] = (primera.estado.value, primera.bytes_descargados,
                                          primera.bytes_totales)
                    completa = primera.bytes_totales and (
                        primera.bytes_descargados >= primera.bytes_totales
                    )
                    if primera.estado.value in ("completed", "failed") or completa:
                        informe["fin"] = "terminó la transferencia de la primera descarga"
                        break
            time.sleep(INTERVALO)
        informe["muestras"] = len(muestras)
        informe["duracion"] = round(time.monotonic() - inicio, 1)
        return maximo(muestras), informe
    finally:
        _terminar(lanzado)
        _terminar(trabajador)
        if servidor is not None:
            servidor.terminate()
            servidor.wait(timeout=10)
        shutil.rmtree(temporal, ignore_errors=True)


def _imprimir(args, mayor: Muestra | None, informe: dict) -> int:
    memoria = psutil.virtual_memory().total
    lineas = [
        "=== COPIAR DESDE AQUÍ ===",
        f"fecha: {time.strftime('%Y-%m-%d %H:%M')}",
        f"equipo: {platform.platform()}, {mb(memoria) / 1000:.1f} GB de RAM, "
        f"{psutil.cpu_count()} CPU lógicas",
        f"variante: ventana={args.ventana}, uv={'no' if args.sin_uv else 'sí'}, "
        f"importar-store={'sí' if args.importar_store else 'no'}, "
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="
        f"{os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '(sin definir)')}",
        f"cola: {args.cola_de_prueba} descargas; fin: {informe.get('fin', '?')}; "
        f"{informe['muestras']} muestras en {informe.get('duracion', '?')} s",
        f"primera descarga (estado, bytes, total): {informe.get('primera', '?')}",
    ]
    if mayor is None:
        lineas.append("máximo: sin muestras")
        codigo = 2
    else:
        lineas.append(f"MÁXIMO: {mb(mayor.total):.1f} MB ({mayor.total / 2**20:.1f} MiB) "
                      f"a los {mayor.instante} s; límite {args.limite} MB")
        lineas.append("desglose en ese instante (pid, proceso, MB):")
        for pid, nombre, uss in sorted(mayor.desglose, key=lambda d: -d[2]):
            lineas.append(f"  {pid:>7}  {nombre:<22} {mb(uss):8.1f}")
        if mayor.sin_acceso:
            lineas.append(f"SIN ACCESO (medición incompleta): {mayor.sin_acceso}")
        codigo = 0 if mb(mayor.total) <= args.limite and not mayor.sin_acceso else 1
        if mayor.sin_acceso:
            codigo = 2
    orden = informe.get("webview2", [])
    lineas.append(f"WebView2: {informe.get('webview2_exe', '?')}")
    for argumento in ARGUMENTOS_WEBVIEW2:
        valores = [parte for parte in orden if parte.startswith(argumento)]
        lineas.append(f"  {argumento}: {' '.join(valores) if valores else 'no aparece'}")
    resultado = {0: "DENTRO del límite", 1: "FUERA del límite"}.get(codigo, "medición incompleta")
    lineas.append(f"resultado: {resultado}")
    lineas.append("=== HASTA AQUÍ ===")
    print("\n".join(lineas))
    return codigo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cola-de-prueba", type=int, default=500, metavar="N")
    parser.add_argument("--limite", type=float, default=300, metavar="MB")
    parser.add_argument("--ventana", choices=("minima", "app"), default="minima")
    parser.add_argument("--sin-uv", action="store_true",
                        help="lanza la ventana con pythonw directamente, sin uv run")
    parser.add_argument("--importar-store", action="store_true",
                        help="la ventana mínima importa downloader.store (research R15)")
    parser.add_argument("--tiempo-maximo", type=float, default=900, metavar="S")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("La medición solo se hace en Windows (la ventana usa WebView2).", file=sys.stderr)
        return 2
    try:
        mayor, informe = medir(args)
    except MedicionFallida as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return _imprimir(args, mayor, informe)


if __name__ == "__main__":
    sys.exit(main())
