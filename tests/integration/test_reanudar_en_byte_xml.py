"""Reanudar cuando el primer byte del resto es `<` (defecto D2; 002 research R20).

`_vigilar_xml` retenía entero un cuerpo que empezaba por `<`, también el de una reanudación
del video, y un corte lo perdía. Aquí el corte cae justo antes de un `<` del archivo y la
reanudación se vuelve a cortar: lo recibido debe quedar en el `.part`.
"""

from tests.integration.test_network_cuts import comprobar_integra, entorno  # noqa: F401


def test_reanudar_en_un_byte_menor_que_no_pierde_lo_recibido(entorno, destino, servidor,  # noqa: F811
                                                             medios):
    datos = medios.datos_mp4
    tamano = len(datos)
    corte = datos.index(b"<", int(tamano * 0.15), int(tamano * 0.6))
    servidor.programar("v1.mp4", cortar_tras_bytes=corte)
    servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)
    # La reanudación (`Range: bytes=corte-`) empieza por `<`: se corta tras un 20 % más.
    servidor.programar("v1.mp4", cortar_tras_bytes=int(tamano * 0.2))
    servidor.programar("v1.mp4", cortar_tras_bytes=0, veces=3)
    entorno.ejecutar()
    comprobar_integra(entorno, destino, servidor, medios)
