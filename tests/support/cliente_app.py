"""Cliente HTTP de la API local de la ventana (specs/002-desktop-app, contracts/api-local.md).

Por defecto envía lo que enviaría la página de la ventana: el secreto como primer segmento de
la ruta, `Host: 127.0.0.1:<puerto>`, `Origin` en los `POST` y `X-Gestor: 1` en `api/*`. Cada
cosa se puede quitar o falsear para probar los rechazos de FR-013 (research R4).
"""

import http.client
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

QUITAR = object()  # valor para no enviar una cabecera


@dataclass
class Respuesta:
    estado: int
    cabeceras: dict
    cuerpo: bytes

    def json(self):
        return json.loads(self.cuerpo.decode("utf-8"))


class ClienteApp:
    def __init__(self, url: str):
        partes = urlsplit(url)
        self.puerto = partes.port
        self.secreto = partes.path.strip("/")
        self.origen = f"http://127.0.0.1:{self.puerto}"

    def peticion(self, metodo, ruta="", cuerpo=None, *, secreto=None, ruta_cruda=None,
                 host=None, origin=None, x_gestor=None, cabeceras=None) -> Respuesta:
        """`ruta` va tras el secreto (`api/estado`); `ruta_cruda` sustituye la ruta entera."""
        secreto = self.secreto if secreto is None else secreto
        camino = ruta_cruda if ruta_cruda is not None else f"/{secreto}/{ruta}"
        es_api = ruta.startswith("api/")
        valores = {
            "Host": f"127.0.0.1:{self.puerto}" if host is None else host,
            "Origin": (self.origen if metodo == "POST" else QUITAR) if origin is None else origin,
            "X-Gestor": ("1" if es_api else QUITAR) if x_gestor is None else x_gestor,
        }
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode("utf-8")
            valores["Content-Type"] = "application/json"
            valores["Content-Length"] = str(len(datos))
        elif metodo == "POST":
            valores["Content-Length"] = "0"
        valores.update(cabeceras or {})

        conexion = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        try:
            conexion.putrequest(metodo, camino, skip_host=True, skip_accept_encoding=True)
            for nombre, valor in valores.items():
                if valor is not QUITAR:
                    conexion.putheader(nombre, valor)
            conexion.endheaders(datos)
            respuesta = conexion.getresponse()
            return Respuesta(respuesta.status, dict(respuesta.getheaders()), respuesta.read())
        finally:
            conexion.close()

    def get(self, ruta="", **opciones) -> Respuesta:
        return self.peticion("GET", ruta, **opciones)

    def post(self, ruta, cuerpo=None, **opciones) -> Respuesta:
        return self.peticion("POST", ruta, cuerpo, **opciones)
