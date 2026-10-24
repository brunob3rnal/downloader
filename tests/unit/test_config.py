"""`config.toml`: carpeta de destino y calidad por defecto (FR-006, US6), claves rechazadas
(Principio VI) y umbrales del vigilante de estancamiento (research R21)."""

import pytest
from typer.testing import CliRunner

from downloader import config
from downloader.models import Calidad
from downloader.store import Store


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


# --- carpeta de destino y calidad (T056, FR-006) --------------------------------------


@pytest.fixture
def inicio(tmp_path, monkeypatch):
    """Directorio personal temporal: `Path.home()` usa HOME en Linux y USERPROFILE en Windows."""
    carpeta = tmp_path / "inicio"
    carpeta.mkdir()
    monkeypatch.setenv("HOME", str(carpeta))
    monkeypatch.setenv("USERPROFILE", str(carpeta))
    return carpeta


def agregar(*opciones):
    from downloader.cli import app

    return CliRunner().invoke(app, ["agregar", "https://videos.ejemplo/watch?v=abc",
                                    *map(str, opciones)])


def unica_fila(app_home):
    store = Store.abrir(app_home)
    try:
        assert store.contar() == 1
        return store.obtener(1)
    finally:
        store.cerrar()


def test_valores_por_defecto(tmp_path, inicio):
    ajustes = config.cargar(tmp_path)
    assert ajustes.carpeta_destino == inicio  # sin Descargas ni Downloads
    assert ajustes.calidad is Calidad.MEJOR
    assert ajustes.sondas_red == ["1.1.1.1:443", "8.8.8.8:443"]


@pytest.mark.parametrize(
    "existentes, esperada",
    [(["Downloads"], "Downloads"), (["Descargas"], "Descargas"),
     (["Downloads", "Descargas"], "Descargas")],
)
def test_carpeta_por_defecto_descargas_o_downloads(tmp_path, inicio, existentes, esperada):
    for nombre in existentes:
        (inicio / nombre).mkdir()
    assert config.cargar(tmp_path).carpeta_destino == inicio / esperada


def test_carpeta_y_calidad_de_config_toml(tmp_path, inicio):
    escribir(tmp_path, 'carpeta_destino = "~/videos"\ncalidad = "720p"\n')
    ajustes = config.cargar(tmp_path)
    assert ajustes.carpeta_destino == inicio / "videos"
    assert ajustes.calidad is Calidad.P720


def test_agregar_usa_la_configuracion(app_home, destino):
    # Cadena literal de TOML: las barras de una ruta de Windows no son escapes.
    escribir(app_home, f"carpeta_destino = '{destino}'\ncalidad = 'audio'\n")
    resultado = agregar()
    assert resultado.exit_code == 0, resultado.output
    fila = unica_fila(app_home)
    assert fila.carpeta_destino == str(destino.resolve())
    assert fila.calidad is Calidad.AUDIO


def test_las_opciones_prevalecen_sobre_la_configuracion(app_home, destino, tmp_path):
    otra = tmp_path / "otra"
    otra.mkdir()
    escribir(app_home, f"carpeta_destino = '{destino}'\ncalidad = 'audio'\n")
    resultado = agregar("--carpeta", otra, "--calidad", "720p")
    assert resultado.exit_code == 0, resultado.output
    fila = unica_fila(app_home)
    assert fila.carpeta_destino == str(otra.resolve())
    assert fila.calidad is Calidad.P720


def test_carpeta_de_la_configuracion_inexistente(app_home, tmp_path):
    falta = tmp_path / "no-existe"
    escribir(app_home, f"carpeta_destino = '{falta}'\n")
    resultado = agregar()
    assert resultado.exit_code == 2
    assert "no existe" in resultado.stderr and falta.name in resultado.stderr


def test_clave_desconocida(tmp_path):
    escribir(tmp_path, 'colour = "rojo"\n')
    with pytest.raises(config.ConfiguracionInvalida) as error:
        config.cargar(tmp_path)
    assert "clave desconocida: colour" in str(error.value)
    assert "carpeta_destino" in str(error.value)  # lista las admitidas


@pytest.mark.parametrize(
    "clave", ["cookies", "cookiefile", "navegador_cookies", "cookies_de", "password", "token"]
)
def test_claves_de_credenciales_o_cookies(tmp_path, clave):
    """Nunca se guardan secretos ni el navegador de las cookies en la configuración (FR-016,
    FR-017, Principio VI): el error lo explica, remite a `--cookies-de` y no repite el valor."""
    escribir(tmp_path, f'{clave} = "valor-secreto-ficticio"\n')
    with pytest.raises(config.ConfiguracionInvalida) as error:
        config.cargar(tmp_path)
    mensaje = str(error.value)
    assert clave in mensaje
    assert "credenciales ni cookies" in mensaje
    assert "--cookies-de" in mensaje
    assert "valor-secreto-ficticio" not in mensaje


def test_agregar_con_una_clave_de_cookies_sale_con_2(app_home, destino):
    escribir(app_home, 'cookies = "SID=valor-secreto-ficticio"\n')
    resultado = agregar("--carpeta", destino)
    assert resultado.exit_code == 2
    assert "credenciales ni cookies" in resultado.stderr
    assert "valor-secreto-ficticio" not in resultado.output


@pytest.mark.parametrize(
    "linea, clave", [("carpeta_destino = 5", "carpeta_destino"),
                     ("carpeta_destino = ['a']", "carpeta_destino"),
                     ('calidad = "4k"', "calidad")],
)
def test_valor_de_tipo_invalido(tmp_path, linea, clave):
    escribir(tmp_path, linea + "\n")
    with pytest.raises(config.ConfiguracionInvalida, match=clave):
        config.cargar(tmp_path)


# --- plantilla del nombre de los archivos publicados (T074) -----------------------------


def test_plantilla_nombre_por_defecto_sin_el_id(tmp_path):
    assert config.cargar(tmp_path).plantilla_nombre == "{titulo}"


def test_plantilla_nombre_configurable(tmp_path):
    escribir(tmp_path, 'plantilla_nombre = "{titulo} [{id}]"\n')
    assert config.cargar(tmp_path).plantilla_nombre == "{titulo} [{id}]"


@pytest.mark.parametrize("valor", ['"{autor}"', '""', "5", '"{titulo}/{id}"', '"sin campos"'])
def test_plantilla_nombre_invalida(tmp_path, valor):
    escribir(tmp_path, f"plantilla_nombre = {valor}\n")
    with pytest.raises(config.ConfiguracionInvalida, match="plantilla_nombre") as error:
        config.cargar(tmp_path)
    assert "clave desconocida" not in str(error.value)  # se valida, no se rechaza la clave


def test_ejecutar_pasa_la_plantilla_al_trabajador(app_home, monkeypatch):
    from downloader import cli, worker

    escribir(app_home, 'sondas_red = ["127.0.0.1:9"]\nplantilla_nombre = "{id} {titulo}"\n')
    recibidos = []
    monkeypatch.setattr(
        worker, "ejecutar", lambda *args: recibidos.append(args) or worker.Resultado()
    )
    monkeypatch.setattr(cli.MonitorRed, "iniciar", lambda self: None)
    monkeypatch.setattr(cli, "_comprobar_ffmpeg", lambda: None)
    resultado = CliRunner().invoke(cli.app, ["ejecutar"])
    assert resultado.exit_code == 0, resultado.output
    ((*_, plantilla),) = recibidos
    assert plantilla == "{id} {titulo}"
