"""Servidor local de la ventana (research R3, R4; contracts/api-local.md).

`http.server` de la biblioteca estándar, solo en `127.0.0.1` y en un puerto que elige el
sistema. Atiende únicamente a la ventana (FR-013): cada petición pasa, en orden, los controles
de `Host`, secreto, `Origin`, cabecera `X-Gestor` y método; el primero que falla responde 403
sin cuerpo y sin cabeceras CORS. No registra peticiones: con `pythonw` no hay `sys.stderr`, y
la línea de petición lleva el secreto.
"""

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import urlsplit

CSP = "default-src 'self'; frame-ancestors 'none'"
# Los únicos archivos que se sirven: nunca se resuelve la ruta pedida contra el disco.
ESTATICOS = {
    "": ("index.html", "text/html; charset=utf-8"),
    "index.html": ("index.html", "text/html; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
}
MAXIMO_CUERPO = 1_000_000  # bytes


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, api, secreto: str):
        super().__init__(("127.0.0.1", 0), _Manejador)
        self.api = api
        self.secreto = secreto
        self.host = f"127.0.0.1:{self.server_address[1]}"
        self.origen = f"http://{self.host}"
        self._hilo = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.2},
                                      name="servidor-local", daemon=True)

    @property
    def url(self) -> str:
        return f"{self.origen}/{self.secreto}/"

    def iniciar(self) -> None:
        self._hilo.start()

    def cerrar(self) -> None:
        self.shutdown()
        self.server_close()


class _Manejador(BaseHTTPRequestHandler):
    server: Servidor

    def log_message(self, *argumentos):
        pass

    def do_GET(self):
        self._atender("GET")

    def do_POST(self):
        self._atender("POST")

    def __getattr__(self, nombre):
        # OPTIONS, PUT, HEAD… y cualquier otro método: 403, nunca el 501 con HTML por defecto.
        if nombre.startswith("do_"):
            return self._prohibido
        raise AttributeError(nombre)

    # --- controles (research R4) ---------------------------------------------------

    def _atender(self, metodo: str) -> None:
        servidor = self.server
        if self.headers.get("Host") != servidor.host:
            return self._prohibido()
        camino = urlsplit(self.path).path
        secreto, _, resto = camino.removeprefix("/").partition("/")
        if not hmac.compare_digest(secreto.encode(), servidor.secreto.encode()):
            return self._prohibido()
        if ".." in resto or "\\" in resto or "%" in resto:
            return self._prohibido()  # recorrido de directorios, también codificado
        origen = self.headers.get("Origin")
        if (origen is not None and origen != servidor.origen) or (metodo == "POST"
                                                                 and origen is None):
            return self._prohibido()
        if resto.startswith("api/"):
            if self.headers.get("X-Gestor") != "1":
                return self._prohibido()
            return self._api(metodo, resto.removeprefix("api/"))
        if metodo != "GET" or resto not in ESTATICOS:
            return self._responder(404)
        nombre, tipo = ESTATICOS[resto]
        cuerpo = resources.files("downloader.app").joinpath("web", nombre).read_bytes()
        cabeceras = {"Content-Security-Policy": CSP} if nombre == "index.html" else {}
        return self._responder(200, cuerpo, tipo, cabeceras)

    def _prohibido(self) -> None:
        self._responder(403)

    def _api(self, metodo: str, ruta: str) -> None:
        longitud = int(self.headers.get("Content-Length") or 0)
        if longitud > MAXIMO_CUERPO:
            return self._responder(413)
        cuerpo = self.rfile.read(longitud) if longitud else b""
        respuesta = self.server.api.atender(metodo, ruta, cuerpo, self.headers)
        datos = b"" if respuesta.datos is None else json.dumps(
            respuesta.datos, ensure_ascii=False).encode("utf-8")
        tipo = "application/json; charset=utf-8" if respuesta.datos is not None else None
        return self._responder(respuesta.estado, datos, tipo, respuesta.cabeceras)

    def _responder(self, estado: int, cuerpo: bytes = b"", tipo: str | None = None,
                   cabeceras: dict | None = None) -> None:
        self.send_response(estado)
        if tipo:
            self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for nombre, valor in (cabeceras or {}).items():
            self.send_header(nombre, valor)
        self.end_headers()
        if cuerpo:
            self.wfile.write(cuerpo)
