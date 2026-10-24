from pathlib import Path

import pytest

# Tests de integración que no descargan y por tanto no necesitan ffmpeg.
SIN_DESCARGA = {"test_cli_agregar.py", "test_actualizar.py", "test_ffmpeg_ausente.py"}

# Marcadores de los archivos de T016 y T017, que no se modifican (T067): el resto de los tests
# llevan sus marcadores `slow` y `ytdlp_internals` en el propio archivo.
MARCADORES = {
    ("test_crash_recovery.py", "test_cierre_abrupto_a_mitad_y_relanzar"):
        ("slow", "ytdlp_internals"),  # R19: GET de la extracción que quedaba abierto
    ("test_network_cuts.py", "test_servidor_colgado_es_temporal"): ("slow",),
}

_AQUI = Path(__file__).parent


@pytest.hookimpl(tryfirst=True)  # antes de que `-m` seleccione por marcadores
def pytest_collection_modifyitems(items):
    for item in items:
        ruta = Path(item.path)
        if ruta.parent != _AQUI:
            continue
        if ruta.name not in SIN_DESCARGA:
            item.add_marker(pytest.mark.ffmpeg)
        for marcador in MARCADORES.get((ruta.name, item.originalname), ()):
            item.add_marker(marcador)
