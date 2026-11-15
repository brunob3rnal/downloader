"""Enlaces pegados en el formulario (specs/002-desktop-app, FR-002, research R16)."""

from downloader.app import urls


def test_separa_validas_e_invalidas_con_su_numero_de_linea():
    texto = ("https://a.ejemplo/v/1\n"
             "\n"
             "   \n"
             "  http://b.ejemplo/v?x=1  \n"
             "ftp://c.ejemplo/v\n"
             "hola\n"
             "https://\n"
             "javascript:alert(1)\n")
    validas, invalidas = urls.separar(texto)
    assert validas == ["https://a.ejemplo/v/1", "http://b.ejemplo/v?x=1"]
    assert invalidas == [(5, "ftp://c.ejemplo/v"), (6, "hola"), (7, "https://"),
                         (8, "javascript:alert(1)")]


def test_texto_vacio():
    assert urls.separar("") == ([], [])
    assert urls.separar("\n \n\t\n") == ([], [])


def test_finales_de_linea_de_windows():
    assert urls.separar("https://a.ejemplo/1\r\nhttps://b.ejemplo/2\r\n") == (
        ["https://a.ejemplo/1", "https://b.ejemplo/2"], [])
