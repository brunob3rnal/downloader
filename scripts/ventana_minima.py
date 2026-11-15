"""Ventana mínima para medir memoria (specs/002-desktop-app, T009; research R14). Solo Windows.

No es la aplicación: abre con pywebview (WebView2) una página de 500 filas con la estructura de
la ventana real (contracts/ventana.md) y la actualiza cada segundo desde un servidor de la
biblioteca estándar en 127.0.0.1, como hará la aplicación. La lanza `scripts/medir_memoria.py`.

    uv run --gui-script scripts/ventana_minima.py [--importar-store] [--informe-motor RUTA]

Con `--importar-store` importa además `downloader.store`, que hoy carga yt-dlp (research R15).
Con `--informe-motor`, escribe en RUTA qué motor eligió pywebview y, si no es WebView2 (caída
silenciosa a MSHTML, research R2), termina con el código 3 sin abrir la ventana.
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview

FILAS = 500
PAGINA = Path(__file__).with_name("ventana_minima.html")
ETIQUETAS = ["Descargando", "En cola", "Completada", "Esperando red", "Falló"]


def _filas() -> list[dict]:
    """500 filas; la primera avanza con el tiempo para que la página tenga algo que actualizar."""
    avance = (time.monotonic() * 2) % 100
    filas = []
    for i in range(FILAS):
        etiqueta = ETIQUETAS[0] if i == 0 else ETIQUETAS[1 + i % (len(ETIQUETAS) - 1)]
        filas.append({
            "id": i + 1,
            "titulo": f"Video de prueba número {i + 1} con un título de longitud realista",
            "sitio": f"ejemplo.com/video/prueba-{i + 1}",
            "etiqueta": etiqueta,
            "porcentaje": round(avance, 1) if i == 0 else (100 if etiqueta == "Completada" else 0),
            "detalle": "2,4 MB/s · quedan 1 min 12 s" if i == 0 else "Esperando su turno",
        })
    return filas


class _Manejador(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            cuerpo, tipo = PAGINA.read_bytes(), "text/html; charset=utf-8"
        elif self.path == "/filas.json":
            cuerpo, tipo = json.dumps(_filas()).encode("utf-8"), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args):
        pass  # con pythonw no hay `sys.stderr` (research R3)


def _motor() -> dict:
    """El motor que usará pywebview; se decide al importar su backend WinForms (research R2)."""
    try:
        from webview.platforms import winforms
    except Exception as error:  # pythonnet sin .NET, por ejemplo
        return {"error": f"{type(error).__name__}: {error}"}
    return {"motor": getattr(winforms, "renderer", None)}


def main() -> None:
    argumentos = sys.argv[1:]
    if "--informe-motor" in argumentos:
        ruta = Path(argumentos[argumentos.index("--informe-motor") + 1])
        motor = _motor()
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(motor), encoding="utf-8")
        temporal.replace(ruta)
        if motor.get("motor") != "edgechromium":
            sys.exit(3)
    if "--importar-store" in argumentos:
        import downloader.store  # noqa: F401  (mide el coste de cargar yt-dlp, R15)

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Manejador)
    servidor.daemon_threads = True
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    puerto = servidor.server_address[1]
    webview.create_window("Gestor de descargas (medición)", f"http://127.0.0.1:{puerto}/",
                          width=1100, height=760)
    try:
        webview.start(gui="edgechromium", private_mode=True)
    finally:
        servidor.shutdown()


if __name__ == "__main__":
    main()
