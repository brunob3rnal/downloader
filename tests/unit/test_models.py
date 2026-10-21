import itertools

import pytest

from downloader.models import (
    ETIQUETAS,
    PENDIENTES,
    Calidad,
    Estado,
    TransicionInvalida,
    validar_transicion,
)

PERMITIDAS = {
    (None, Estado.QUEUED),
    (Estado.FAILED, Estado.QUEUED),
    (Estado.QUEUED, Estado.DOWNLOADING),
    (Estado.DOWNLOADING, Estado.WAITING_NETWORK),
    (Estado.WAITING_NETWORK, Estado.DOWNLOADING),
    (Estado.DOWNLOADING, Estado.COMPLETED),
    (Estado.DOWNLOADING, Estado.FAILED),
    (Estado.DOWNLOADING, Estado.QUEUED),
    (Estado.WAITING_NETWORK, Estado.QUEUED),
}


def test_estados_internos():
    assert {e.value for e in Estado} == {
        "queued", "downloading", "waiting_network", "completed", "failed",
    }


@pytest.mark.parametrize("desde, hacia", sorted(PERMITIDAS, key=str))
def test_transiciones_permitidas(desde, hacia):
    validar_transicion(desde, hacia)


@pytest.mark.parametrize(
    "desde, hacia",
    [
        (desde, hacia)
        for desde, hacia in itertools.product([None, *Estado], Estado)
        if (desde, hacia) not in PERMITIDAS
    ],
)
def test_cualquier_otra_transicion_se_rechaza(desde, hacia):
    with pytest.raises(TransicionInvalida):
        validar_transicion(desde, hacia)


def test_pendientes():
    assert PENDIENTES == {Estado.QUEUED, Estado.DOWNLOADING, Estado.WAITING_NETWORK}


def test_etiquetas_visibles():
    assert ETIQUETAS == {
        Estado.QUEUED: "en cola",
        Estado.DOWNLOADING: "descargando",
        Estado.WAITING_NETWORK: "esperando red",
        Estado.COMPLETED: "completada",
        Estado.FAILED: "fallida",
    }


def test_calidades_admitidas():
    assert {c.value for c in Calidad} == {"mejor", "1080p", "720p", "audio"}
    assert Calidad("720p") is Calidad.P720


@pytest.mark.parametrize("valor", ["4k", "480p", "", "MEJOR"])
def test_calidad_desconocida(valor):
    with pytest.raises(ValueError):
        Calidad(valor)
