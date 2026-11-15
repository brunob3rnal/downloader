"""Argumentos de WebView2 solo en el proceso de la aplicación (research R14, condición del
usuario; tasks T066). Corre en los dos sistemas: es lógica pura."""

import builtins
import subprocess

import pytest

from downloader.app import entorno_webview2


def test_los_fija_en_el_entorno_recibido():
    entorno = {}
    entorno_webview2.fijar(entorno)
    assert entorno[entorno_webview2.VARIABLE] == (
        "--renderer-process-limit=1 --disable-gpu --disable-extensions")


def test_se_suman_a_un_valor_previo_sin_repetir():
    entorno = {entorno_webview2.VARIABLE: "--lang=es --disable-gpu"}
    entorno_webview2.fijar(entorno)
    assert entorno[entorno_webview2.VARIABLE] == (
        "--lang=es --disable-gpu --renderer-process-limit=1 --disable-extensions")
    entorno_webview2.fijar(entorno)  # idempotente
    assert entorno[entorno_webview2.VARIABLE].count("--disable-gpu") == 1


def test_por_defecto_solo_toca_os_environ(monkeypatch):
    monkeypatch.delenv(entorno_webview2.VARIABLE, raising=False)
    entorno_webview2.fijar()
    import os

    assert "--disable-gpu" in os.environ[entorno_webview2.VARIABLE]


def test_no_escribe_nada_fuera_del_proceso(monkeypatch):
    """Ni `setx` ni el registro de Windows: ninguna orden externa ni `winreg`."""
    def prohibido(*argumentos, **opciones):
        raise AssertionError("no debe lanzar procesos (setx)")

    importar = builtins.__import__

    def importar_sin_winreg(nombre, *argumentos, **opciones):
        if nombre == "winreg":
            raise AssertionError("no debe usar el registro de Windows")
        return importar(nombre, *argumentos, **opciones)

    for nombre in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, nombre, prohibido)
    monkeypatch.setattr(builtins, "__import__", importar_sin_winreg)
    entorno_webview2.fijar({})
