"""Excepciones propias sin dependencias (research R15 de la feature 002).

Viven aparte de `errors.py`, que carga yt-dlp, para que la ventana de la aplicación pueda
abrir la cola sin cargarlo. `errors` las reexporta con los mismos nombres.
"""


class ErrorPermanente(Exception):
    """Error propio que impide esta descarga (directo, carpeta inválida…)."""


class ErrorFatal(Exception):
    """Error propio que impide seguir con cualquier descarga (base corrupta, ffmpeg ausente…)."""
