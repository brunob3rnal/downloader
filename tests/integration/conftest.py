from pathlib import Path

import pytest

# Tests de integración que no descargan y por tanto no necesitan ffmpeg.
SIN_DESCARGA = {"test_cli_agregar.py", "test_actualizar.py", "test_ffmpeg_ausente.py"}

_AQUI = Path(__file__).parent


def pytest_collection_modifyitems(items):
    for item in items:
        ruta = Path(item.path)
        if ruta.parent == _AQUI and ruta.name not in SIN_DESCARGA:
            item.add_marker(pytest.mark.ffmpeg)
