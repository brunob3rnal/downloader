"""La ventana no carga yt-dlp (specs/002-desktop-app, research R15).

yt-dlp ocupa unos 16 MB en el proceso de la ventana (research R14, medición del 2026-11-14) y
retrasa su arranque. Los módulos que usa la ventana se importan en un intérprete limpio y se
comprueba que `yt_dlp` no está en `sys.modules`.
"""

import subprocess
import sys

import pytest

MODULOS = [
    "downloader.models",
    "downloader.config",
    "downloader.publish",
    "downloader.store",
    "downloader.bloqueo",
    "downloader.excepciones",
    "downloader.secretos",
    "downloader.mensajes",
    "downloader.operaciones",
    "downloader.archivos_json",
    "downloader.app.servidor",
    "downloader.app.api",
    "downloader.app.registro",
    "downloader.app.resultado",
    "downloader.app.procesos",
    "downloader.app.principal",
    "downloader.app.vista",
    "downloader.app.urls",
]


@pytest.mark.parametrize("modulo", MODULOS)
def test_importar_no_carga_yt_dlp(modulo):
    codigo = (
        f"import sys, {modulo}\n"
        "cargados = sorted(m for m in sys.modules if m == 'yt_dlp' or m.startswith('yt_dlp.'))\n"
        "print(len(cargados), cargados[:3])\n"
        "sys.exit(1 if cargados else 0)\n"
    )
    resultado = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                               timeout=60)
    assert resultado.returncode == 0, f"{modulo} carga yt-dlp: {resultado.stdout}{resultado.stderr}"
