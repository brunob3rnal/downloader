import socket
import time

from downloader.network import MonitorRed


def esperar(condicion, limite=5):
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def puerto_cerrado():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_en_linea_si_alguna_sonda_conecta_y_sin_conexion_si_fallan_todas():
    escucha = socket.create_server(("127.0.0.1", 0))
    puerto = escucha.getsockname()[1]
    monitor = MonitorRed(
        [f"127.0.0.1:{puerto_cerrado()}", f"127.0.0.1:{puerto}"], intervalo=0.05, timeout=0.5
    )
    monitor.iniciar()
    try:
        assert esperar(monitor.en_linea.is_set)
        escucha.close()
        assert esperar(lambda: not monitor.en_linea.is_set())
        escucha = socket.create_server(("127.0.0.1", puerto))
        assert esperar(monitor.en_linea.is_set)
    finally:
        monitor.detener()
        escucha.close()


def test_sin_conexion_desde_el_inicio():
    monitor = MonitorRed([f"127.0.0.1:{puerto_cerrado()}"], intervalo=0.05, timeout=0.5)
    monitor.iniciar()
    try:
        assert not monitor.en_linea.is_set()
    finally:
        monitor.detener()


def test_se_detiene_limpiamente():
    monitor = MonitorRed([f"127.0.0.1:{puerto_cerrado()}"], intervalo=0.05, timeout=0.5)
    monitor.iniciar()
    inicio = time.monotonic()
    monitor.detener()
    assert time.monotonic() - inicio < 2
    assert not monitor.vivo()
