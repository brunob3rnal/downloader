"""`config.toml`: umbrales del vigilante de estancamiento (research R21)."""

import pytest

from downloader import config


def escribir(directorio, texto):
    (directorio / config.ARCHIVO).write_text(texto, encoding="utf-8")


def test_umbrales_por_defecto(tmp_path):
    ajustes = config.cargar(tmp_path)
    assert ajustes.estancamiento_segundos == 30
    assert ajustes.estancamiento_velocidad_minima == 1024


def test_umbrales_configurables(tmp_path):
    escribir(tmp_path, "estancamiento_segundos = 45\nestancamiento_velocidad_minima = 2048.5\n")
    ajustes = config.cargar(tmp_path)
    assert ajustes.estancamiento_segundos == 45
    assert ajustes.estancamiento_velocidad_minima == 2048.5


@pytest.mark.parametrize("valor", ["0", "-5", '"30"', "true"])
def test_umbral_invalido(tmp_path, valor):
    escribir(tmp_path, f"estancamiento_segundos = {valor}\n")
    with pytest.raises(config.ConfiguracionInvalida, match="estancamiento_segundos"):
        config.cargar(tmp_path)


def test_ejecutar_pasa_los_umbrales_al_trabajador(app_home, monkeypatch):
    from typer.testing import CliRunner

    from downloader import cli, worker

    escribir(app_home, 'sondas_red = ["127.0.0.1:9"]\nestancamiento_segundos = 12\n'
                       "estancamiento_velocidad_minima = 300\n")
    recibidos = []
    monkeypatch.setattr(
        worker, "ejecutar", lambda *args: recibidos.append(args[3]) or worker.Resultado()
    )
    monkeypatch.setattr(cli.MonitorRed, "iniciar", lambda self: None)
    # El trabajador está sustituido: no hace falta ffmpeg (su comprobación: test_ffmpeg_ausente).
    monkeypatch.setattr(cli, "_comprobar_ffmpeg", lambda: None)
    resultado = CliRunner().invoke(cli.app, ["ejecutar"])
    assert resultado.exit_code == 0, resultado.output
    (tiempos,) = recibidos
    assert tiempos.estancamiento == 12
    assert tiempos.velocidad_minima == 300
