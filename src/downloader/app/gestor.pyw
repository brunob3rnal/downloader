"""Punto de entrada sin consola del lanzador (research R11).

`Gestor de descargas.vbs` lo ejecuta con `uv run --gui-script`, que usa `pythonw.exe`.
"""

import sys

from downloader.app.principal import main

sys.exit(main())
