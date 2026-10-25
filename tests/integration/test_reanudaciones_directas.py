"""SC-002 con una URL directa y el extractor genérico, tras tres reanudaciones (Fase 13).

Un corte de red, un Ctrl+C y una terminación abrupta sobre la misma descarga, cada una en
mitad de la transferencia, con `ejecutar` en un subproceso real: no registra `fake_extractor`,
así que la URL la resuelve el extractor genérico de yt-dlp, con sus sondeos (research R19). El
total servido se compara con la fórmula de SC-002 para el número real de intentos (R20).
"""

import os
import subprocess
import sys
import time

import pytest

from downloader.models import Calidad, Estado
from downloader.store import Store
from tests.integration.test_ctrl_c import SALIDA_TRAS_INTERRUPCION, interrumpir
from tests.integration.test_interrupcion import INTERRUMPIDO, LIMITE, ejecutar, estado
from tests.support import sc002

RUTA = "directo.mp4"


def esperar(proceso, condicion, motivo):
    """Sondea `condicion` hasta que se cumpla, con `ejecutar` vivo y dentro de `LIMITE`."""
    fin = time.monotonic() + LIMITE
    while not condicion():
        assert proceso.poll() is None, f"ejecutar terminó antes de {motivo}"
        assert time.monotonic() < fin, f"no se llegó a {motivo}"
        time.sleep(0.05)  # intervalo de sondeo


@pytest.mark.slow
@pytest.mark.ytdlp_internals  # R19: sondeos del extractor genérico y cierre de sus respuestas
def test_corte_de_red_ctrl_c_y_cierre_abrupto_cumplen_sc002(app_home, destino, servidor, medios):
    datos = medios.datos_mp4
    servidor.registrar(RUTA, datos, tipo_contenido="video/mp4", velocidad=400_000)
    (app_home / "config.toml").write_text(f'sondas_red = ["127.0.0.1:{servidor.puerto}"]\n')
    store = Store.abrir(app_home)
    id_ = store.agregar(servidor.url(RUTA), Calidad.MEJOR, str(destino)).descarga.id
    store.cerrar()
    trabajo = destino / ".downloader-tmp" / str(id_)

    def en_disco():
        parciales = list(trabajo.glob("*.part")) if trabajo.exists() else []
        return parciales[0].stat().st_size if parciales else 0

    def avanza_hasta(fraccion):
        return lambda: (en_disco() > fraccion * len(datos)
                        and estado(app_home, id_) == Estado.DOWNLOADING)

    # 1. Corte de red y, en el mismo `ejecutar`, Ctrl+C tras reanudar.
    proceso = ejecutar(app_home)
    try:
        esperar(proceso, avanza_hasta(0.2), "un 20 % descargado")
        servidor.detener()
        esperar(proceso, lambda: estado(app_home, id_) == Estado.WAITING_NETWORK,
                "esperar la red")
        cortado = en_disco()
        servidor.reanudar()
        esperar(proceso, avanza_hasta(cortado / len(datos) + 0.15), "reanudar tras el corte")
        interrumpir(proceso)
        _, errores = proceso.communicate(timeout=SALIDA_TRAS_INTERRUPCION)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
    assert proceso.returncode == INTERRUMPIDO, errores
    assert b"interrumpido; progreso guardado" in errores

    # 2. Terminación abrupta tras avanzar otra vez, poco después de una escritura del `.part`
    # (bloque pequeño, como T017).
    proceso = ejecutar(app_home)
    try:
        esperar(proceso, avanza_hasta(en_disco() / len(datos) + 0.15), "reanudar tras Ctrl+C")
        proceso.kill()
        proceso.wait(timeout=LIMITE)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
    assert estado(app_home, id_) == Estado.DOWNLOADING

    # 3. Relanzar hasta terminar.
    relanzado = ejecutar(app_home)
    _, errores = relanzado.communicate(timeout=LIMITE)
    assert relanzado.returncode == 0, errores
    publicados = [p for p in destino.iterdir() if p.is_file()]
    assert len(publicados) == 1
    assert publicados[0].read_bytes() == datos

    intentos = sc002.intentos(servidor, RUTA)
    assert intentos >= 4  # el primero y uno tras cada interrupción
    servidos = servidor.bytes_servidos[RUTA]
    assert servidos <= sc002.limite(len(datos), intentos, abruptas=1), (servidos, intentos)
