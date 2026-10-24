"""Servidor HTTP local que simula cortes de red para los tests.

Sirve archivos registrados con soporte de `Range` y permite programar, por archivo, fallos
que se consumen petición a petición: cortar la conexión tras N bytes, colgarse sin enviar
datos o responder códigos de error. Además admite propiedades persistentes por archivo:
ignorar `Range`, exigir token, anunciar un tamaño falso, responder 404 a un fragmento HLS y
limitar la velocidad; y, para todo el servidor, exigir una cookie de sesión.

También sirve las páginas que consulta `fake_extractor`: `/watch/<id>` y `/playlist/<id>`
devuelven JSON con la URL de medios (con un token nuevo en cada consulta) o las entradas de
la lista. Así, con el servidor detenido, la extracción también falla por la red.
"""

import json
import sys
import re
import secrets
import select
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_RANGE = re.compile(r"bytes=(\d+)-(\d*)$")
_TROZO = 16 * 1024
# Con velocidad limitada se envía en trozos pequeños: si el cliente cierra pronto (p. ej. el
# extractor genérico de yt-dlp, que solo lee el principio), apenas se cuentan bytes no leídos.
_TROZO_LENTO = 1024


@dataclass
class _Archivo:
    datos: bytes
    tipo_contenido: str
    sin_range: bool = False
    tokens: bool = False
    anunciar_tamano: int | None = None
    ausente: bool = False
    velocidad: int | None = None  # bytes por segundo (sirve lentamente); None = sin límite
    manifiesto_hls: bool = False  # con tokens, añade el token de la petición a cada fragmento
    # ("cortar", n) | ("colgar", None) | ("error", código) | ("caducar", None) | ("cortar_caducar", n)
    # | ("pausar", n) | ("gotear", (n, intervalo))
    fallos: deque = field(default_factory=deque)


@dataclass
class _Video:
    archivo: str
    titulo: str
    alturas: list[int]


class FlakyServer:
    """Servidor en un hilo, en `127.0.0.1` con puerto efímero.

    Uso típico::

        servidor = FlakyServer()
        servidor.registrar("video.mp4", datos, cortar_tras_bytes=1000, veces=4)
        url = servidor.url("video.mp4")
        ...
        servidor.cerrar()
    """

    def __init__(self):
        self._archivos: dict[str, _Archivo] = {}
        self._videos: dict[str, _Video] = {}
        self._listas: dict[str, tuple[str, list[str]]] = {}
        self._tokens_validos: set[str] = set()
        self._sesion: tuple[str, str] | None = None  # cookie que exige `exigir_sesion`
        self._cerrojo = threading.Lock()
        self._parado = threading.Event()
        self._conexiones: set[socket.socket] = set()
        self.bytes_servidos: defaultdict[str, int] = defaultdict(int)
        self.peticiones: defaultdict[str, int] = defaultdict(int)
        self.puerto = 0
        self._httpd = None
        self._hilo = None
        self._arrancar()

    # --- ciclo de vida -------------------------------------------------------------

    def _arrancar(self):
        self._parado.clear()
        self._httpd = _Servidor(("127.0.0.1", self.puerto), _Manejador)
        self._httpd.flaky = self
        self.puerto = self._httpd.server_address[1]
        self._hilo = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._hilo.start()

    def detener(self):
        """Deja de aceptar conexiones y corta las que estén abiertas."""
        if self._httpd is None:
            return
        self._parado.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._hilo.join(timeout=5)
        with self._cerrojo:
            conexiones = list(self._conexiones)
        for conexion in conexiones:
            try:
                conexion.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._httpd = None

    def reanudar(self):
        """Vuelve a escuchar en el mismo puerto."""
        if self._httpd is None:
            self._arrancar()

    def cerrar(self):
        self.detener()

    # --- registro ------------------------------------------------------------------

    def url(self, nombre, token=None):
        url = f"http://127.0.0.1:{self.puerto}/{nombre}"
        return f"{url}?token={token}" if token else url

    def registrar(
        self,
        nombre,
        datos,
        *,
        tipo_contenido="application/octet-stream",
        sin_range=False,
        tokens=False,
        anunciar_tamano=None,
        velocidad=None,
        **fallos,
    ):
        """Registra (o sustituye) un archivo servido en `/<nombre>`.

        Los fallos (`cortar_tras_bytes`, `colgarse`, `errores`, `veces`) se programan como en
        `programar`.
        """
        with self._cerrojo:
            self._archivos[nombre] = _Archivo(
                datos=datos,
                tipo_contenido=tipo_contenido,
                sin_range=sin_range,
                tokens=tokens,
                anunciar_tamano=anunciar_tamano,
                velocidad=velocidad,
            )
        self.programar(nombre, **fallos)

    def configurar(self, nombre, **propiedades):
        """Cambia propiedades persistentes de un archivo ya registrado (p. ej. `sin_range`)."""
        with self._cerrojo:
            archivo = self._archivos[nombre]
            for clave, valor in propiedades.items():
                if clave not in ("sin_range", "tokens", "anunciar_tamano", "velocidad"):
                    raise TypeError(f"propiedad desconocida: {clave}")
                setattr(archivo, clave, valor)

    def programar(
        self, nombre, *, cortar_tras_bytes=None, colgarse=False, errores=None,
        caducar_tokens=False, pausar_tras_bytes=None, gotear_tras_bytes=None, intervalo=1.0,
        veces=1,
    ):
        """Añade fallos que se consumen en las siguientes peticiones al archivo, en orden.

        - `cortar_tras_bytes=N`: envía N bytes del cuerpo y cierra la conexión (`veces` peticiones).
        - `colgarse=True`: no envía nada hasta que el cliente cierre (`veces` peticiones).
        - `errores=[503, 500, ...]`: una petición por código.
        - `caducar_tokens=True`: invalida todos los tokens emitidos y sirve la petición, que ya
          presentó uno válido (una URL temporal que caduca a mitad de la descarga). Junto con
          `cortar_tras_bytes`, corta y caduca en la misma petición (`veces` peticiones).
        - `pausar_tras_bytes=N`: envía N bytes del cuerpo y deja la conexión abierta sin enviar
          nada más hasta que el cliente la cierre (`veces` peticiones).
        - `gotear_tras_bytes=N`: envía N bytes del cuerpo y luego un byte cada `intervalo` s
          hasta terminar o hasta que el cliente cierre (`veces` peticiones).
        """
        with self._cerrojo:
            fallos = self._archivos[nombre].fallos
            if cortar_tras_bytes is not None:
                tipo = "cortar_caducar" if caducar_tokens else "cortar"
                fallos.extend([(tipo, cortar_tras_bytes)] * veces)
            elif caducar_tokens:
                fallos.append(("caducar", None))
            if colgarse:
                fallos.extend([("colgar", None)] * veces)
            for codigo in errores or ():
                fallos.append(("error", codigo))
            if pausar_tras_bytes is not None:
                fallos.extend([("pausar", pausar_tras_bytes)] * veces)
            if gotear_tras_bytes is not None:
                fallos.extend([("gotear", (gotear_tras_bytes, intervalo))] * veces)

    def registrar_hls(self, prefijo, directorio, *, fragmento_ausente=None, tokens=False):
        """Registra un manifiesto HLS y sus fragmentos desde un directorio generado por ffmpeg.

        El manifiesto queda en `/<prefijo>/<nombre>.m3u8` y cada fragmento en
        `/<prefijo>/<fragmento>`. `fragmento_ausente=i` hace que el fragmento i-ésimo (desde 0,
        en el orden del manifiesto) responda siempre 404. Con `tokens=True`, el manifiesto y los
        fragmentos exigen token, y el manifiesto devuelve cada fragmento con el token con el que
        se pidió. Devuelve el nombre del manifiesto.
        """
        directorio = Path(directorio)
        (manifiesto,) = directorio.glob("*.m3u8")
        nombre_manifiesto = f"{prefijo}/{manifiesto.name}"
        self.registrar(
            nombre_manifiesto,
            manifiesto.read_bytes(),
            tipo_contenido="application/vnd.apple.mpegurl",
            tokens=tokens,
        )
        self._archivos[nombre_manifiesto].manifiesto_hls = True
        fragmentos = [
            linea.strip()
            for linea in manifiesto.read_text().splitlines()
            if linea.strip() and not linea.startswith("#")
        ]
        for indice, fragmento in enumerate(fragmentos):
            nombre = f"{prefijo}/{fragmento}"
            self.registrar(
                nombre, (directorio / fragmento).read_bytes(), tipo_contenido="video/mp2t",
                tokens=tokens,
            )
            self._archivos[nombre].ausente = indice == fragmento_ausente
        return nombre_manifiesto

    def registrar_video(self, id_video, archivo, *, titulo=None, alturas=(720,)):
        """Hace que `/watch/<id_video>` describa el archivo registrado `archivo`."""
        self._videos[id_video] = _Video(archivo, titulo or f"Video {id_video}", list(alturas))

    def registrar_lista(self, id_lista, ids_videos, *, titulo=None):
        """Hace que `/playlist/<id_lista>` devuelva esas entradas."""
        self._listas[id_lista] = (titulo or f"Lista {id_lista}", list(ids_videos))

    def exigir_sesion(self, nombre, valor):
        """Responde 403 a toda petición (páginas y medios) cuya cabecera `Cookie` no traiga
        `nombre=valor`: un video que exige la sesión del usuario (US6)."""
        self._sesion = (nombre, valor)

    # --- tokens --------------------------------------------------------------------

    def emitir_token(self):
        token = secrets.token_hex(8)
        with self._cerrojo:
            self._tokens_validos.add(token)
        return token

    def caducar_tokens(self):
        """Invalida todos los tokens emitidos hasta ahora."""
        with self._cerrojo:
            self._tokens_validos.clear()

    # --- uso interno del manejador -------------------------------------------------

    def _siguiente_fallo(self, archivo):
        with self._cerrojo:
            return archivo.fallos.popleft() if archivo.fallos else None

    def _token_valido(self, token):
        with self._cerrojo:
            return token in self._tokens_validos

    def _sesion_valida(self, cabecera):
        if self._sesion is None:
            return True
        return "=".join(self._sesion) in (cabecera or "").replace(" ", "").split(";")


def _con_token(manifiesto, token):
    lineas = [
        linea if not linea.strip() or linea.startswith(b"#") else linea + f"?token={token}".encode()
        for linea in manifiesto.splitlines()
    ]
    return b"\n".join(lineas) + b"\n"


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, client_address):
        with self.flaky._cerrojo:
            self.flaky._conexiones.add(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        with self.flaky._cerrojo:
            self.flaky._conexiones.discard(request)
        super().shutdown_request(request)

    def handle_error(self, request, client_address):
        # Que el cliente cierre la conexión es lo normal en estas pruebas (cortes, timeouts, el
        # extractor genérico que solo lee el principio); no es un error del servidor.
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


class _Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    @property
    def flaky(self) -> FlakyServer:
        return self.server.flaky

    def do_GET(self):
        partes = urlsplit(self.path)
        ruta = partes.path.lstrip("/")
        consulta = parse_qs(partes.query)
        if not self.flaky._sesion_valida(self.headers.get("Cookie")):
            return self._responder_vacio(403)
        if ruta.startswith("watch/"):
            return self._pagina_video(ruta.removeprefix("watch/"), consulta)
        if ruta.startswith("playlist/"):
            return self._pagina_lista(ruta.removeprefix("playlist/"))
        archivo = self.flaky._archivos.get(ruta)
        if archivo is None or archivo.ausente:
            return self._responder_vacio(404)
        self.flaky.peticiones[ruta] += 1

        token = consulta.get("token", [None])[0]
        if archivo.tokens and not self.flaky._token_valido(token):
            return self._responder_vacio(403)

        fallo = self.flaky._siguiente_fallo(archivo)
        if fallo and fallo[0] == "error":
            return self._responder_vacio(fallo[1])
        if fallo and fallo[0] == "colgar":
            return self._colgarse()

        datos = archivo.datos
        if archivo.manifiesto_hls and token:
            datos = _con_token(datos, token)
        tipo = fallo[0] if fallo else None
        if tipo in ("caducar", "cortar_caducar"):
            # Antes de servir: esta petición ya presentó un token válido, y si se invalidaran al
            # terminar, la siguiente petición del cliente podría llegar antes.
            self.flaky.caducar_tokens()
        if tipo in ("pausar", "gotear"):
            return self._servir_estancado(ruta, archivo, datos, tipo, fallo[1])
        self._servir(ruta, archivo, datos, fallo[1] if tipo in ("cortar", "cortar_caducar") else None)

    def _servir(self, ruta, archivo, datos, cortar_tras):
        total = archivo.anunciar_tamano if archivo.anunciar_tamano is not None else len(datos)
        inicio, fin = 0, total - 1
        rango = _RANGE.match(self.headers.get("Range", ""))
        parcial = bool(rango) and not archivo.sin_range
        if parcial:
            inicio = int(rango.group(1))
            if rango.group(2):
                fin = min(int(rango.group(2)), total - 1)
            if inicio >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        longitud = fin - inicio + 1

        self.send_response(206 if parcial else 200)
        self.send_header("Content-Type", archivo.tipo_contenido)
        self.send_header("Content-Length", str(longitud))
        if not archivo.sin_range:
            self.send_header("Accept-Ranges", "bytes")
        if parcial:
            self.send_header("Content-Range", f"bytes {inicio}-{fin}/{total}")
        self.end_headers()

        cuerpo = datos[inicio : fin + 1]
        if cortar_tras is not None:
            cuerpo = cuerpo[:cortar_tras]
        try:
            tamano_trozo = _TROZO_LENTO if archivo.velocidad else _TROZO
            for desplazamiento in range(0, len(cuerpo), tamano_trozo):
                if self.flaky._parado.is_set():
                    break
                trozo = cuerpo[desplazamiento : desplazamiento + tamano_trozo]
                # Se cuenta antes de enviar para que el cliente nunca vea más bytes de los
                # contados; si el envío falla, se descuenta.
                self.flaky.bytes_servidos[ruta] += len(trozo)
                try:
                    self.wfile.write(trozo)
                    self.wfile.flush()
                except OSError:
                    self.flaky.bytes_servidos[ruta] -= len(trozo)
                    raise
                if archivo.velocidad:
                    time.sleep(len(trozo) / archivo.velocidad)
        except OSError:
            pass
        if cortar_tras is not None or len(cuerpo) < longitud:
            self._cerrar_conexion()

    def _servir_estancado(self, ruta, archivo, datos, tipo, parametro):
        """Respuesta completa (200, o 206 con `Range`) que se estanca tras N bytes del cuerpo."""
        n, intervalo = (parametro, None) if tipo == "pausar" else parametro
        total = len(datos)
        inicio = 0
        rango = _RANGE.match(self.headers.get("Range", ""))
        if rango and not archivo.sin_range:
            inicio = int(rango.group(1))
        cuerpo = datos[inicio:]
        self.send_response(206 if inicio else 200)
        self.send_header("Content-Type", archivo.tipo_contenido)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Accept-Ranges", "bytes")
        if inicio:
            self.send_header("Content-Range", f"bytes {inicio}-{total - 1}/{total}")
        self.end_headers()
        try:
            self._enviar(ruta, cuerpo[:n])
            if tipo == "pausar":
                return self._colgarse()
            for posicion in range(n, len(cuerpo)):
                if self.flaky._parado.wait(intervalo):
                    break
                self._enviar(ruta, cuerpo[posicion : posicion + 1])
        except OSError:
            pass
        self._cerrar_conexion()

    def _enviar(self, ruta, datos):
        for desplazamiento in range(0, len(datos), _TROZO):
            trozo = datos[desplazamiento : desplazamiento + _TROZO]
            self.flaky.bytes_servidos[ruta] += len(trozo)
            try:
                self.wfile.write(trozo)
                self.wfile.flush()
            except OSError:
                self.flaky.bytes_servidos[ruta] -= len(trozo)
                raise

    def _colgarse(self):
        """No envía nada hasta que el cliente cierre la conexión o el servidor se detenga."""
        while not self.flaky._parado.is_set():
            legible, _, _ = select.select([self.connection], [], [], 0.1)
            if legible:
                try:
                    if not self.connection.recv(1024):
                        break
                except OSError:
                    break
        self._cerrar_conexion()

    def _cerrar_conexion(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _responder_vacio(self, codigo):
        self.send_response(codigo)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _responder_json(self, objeto):
        cuerpo = json.dumps(objeto).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _pagina_video(self, id_video, consulta):
        video = self.flaky._videos.get(id_video)
        if video is None:
            return self._responder_vacio(404)
        respuesta = {
            "id": id_video,
            "titulo": video.titulo,
            "url": self.flaky.url(video.archivo, self.flaky.emitir_token()),
            "alturas": video.alturas,
        }
        if "list" in consulta:
            respuesta["lista"] = consulta["list"][0]
        self._responder_json(respuesta)

    def _pagina_lista(self, id_lista):
        if id_lista not in self.flaky._listas:
            return self._responder_vacio(404)
        titulo, entradas = self.flaky._listas[id_lista]
        self._responder_json({"id": id_lista, "titulo": titulo, "entradas": entradas})
