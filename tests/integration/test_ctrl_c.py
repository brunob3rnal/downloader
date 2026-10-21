"""Ctrl+C durante `ejecutar`, en Linux y en Windows (edge case Ctrl+C de la spec).

Lo que ya exige la Fase 3: la interrupción llega al hilo principal a mitad de descarga,
`ejecutar` termina enseguida, libera el bloqueo, conserva el `.part` y no publica nada. El
código de salida 130 y la vuelta de la fila a `queued` son de la Fase 5 (T042, T044).

En Windows no se puede enviar Ctrl+C a un solo proceso: `CTRL_C_EVENT` llega a toda la consola,
pytest incluido. Se lanza `ejecutar` en su propio grupo de procesos y se le envía
`CTRL_BREAK_EVENT`, que `cli.ejecutar` trata igual que Ctrl+C.
"""

import os
import signal
import subprocess
import sys
import time

from downloader.models import Calidad
from downloader.store import Store

LIMITE = 60
SALIDA_TRAS_INTERRUPCION = 15  # s; en Windows, un `recv` bloqueado no se interrumpe al instante


def interrumpir(proceso: subprocess.Popen) -> None:
    if os.name == "nt":
        proceso.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proceso.send_signal(signal.SIGINT)


def test_ctrl_c_a_mitad_de_descarga(app_home, destino, servidor, medios):
    datos = medios.datos_mp4
    servidor.registrar("lento.mp4", datos, tipo_contenido="video/mp4", velocidad=200_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    store.agregar(servidor.url("lento.mp4"), Calidad.MEJOR, str(destino))
    store.cerrar()

    ejecutar = subprocess.Popen(
        [sys.executable, "-m", "downloader", "ejecutar"],
        env={**os.environ, "DOWNLOADER_HOME": str(app_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        trabajo = destino / ".downloader-tmp"
        fin = time.monotonic() + LIMITE
        while True:
            parciales = list(trabajo.glob("*/*.part")) if trabajo.exists() else []
            if parciales and parciales[0].stat().st_size > 0.1 * len(datos):
                break
            assert ejecutar.poll() is None, "ejecutar terminó antes de poder interrumpirlo"
            assert time.monotonic() < fin, "la descarga lenta no avanzó"
            time.sleep(0.05)

        interrumpir(ejecutar)
        _, errores = ejecutar.communicate(timeout=SALIDA_TRAS_INTERRUPCION)
    finally:
        if ejecutar.poll() is None:
            ejecutar.kill()
            ejecutar.wait()

    assert ejecutar.returncode != 0
    assert b"Traceback" not in errores
    assert parciales[0].exists()  # Principio IV: el progreso se conserva
    assert [p.name for p in destino.iterdir()] == [".downloader-tmp"]  # nada publicado
    otro = Store.abrir(app_home)
    try:
        otro.bloquear_ejecucion().liberar()  # el bloqueo quedó libre
    finally:
        otro.cerrar()
