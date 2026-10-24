# Contrato CLI: `downloader`

Punto de entrada: `uv run downloader <comando>` (script de consola del paquete).

La configuración (carpeta de destino, calidad por defecto y plantilla del nombre de los archivos
publicados) vive en `config.toml`, en el
directorio que devuelve `typer.get_app_dir("downloader")`; ver
[data-model.md](../data-model.md). Las opciones de línea de comandos prevalecen sobre ella.

## `agregar URL... [--calidad C] [--carpeta RUTA] [--cookies-de NAVEGADOR]`

Guarda una o varias URLs en la cola. No necesita red.

| Opción | Valores | Por defecto |
|--------|---------|-------------|
| `--calidad` | `mejor`, `1080p`, `720p`, `audio` | `calidad` de la configuración |
| `--carpeta` | ruta existente y escribible | `carpeta_destino` de la configuración |
| `--cookies-de` | nombre de navegador admitido por yt-dlp (`brave`, `chrome`, `chromium`, `edge`, `firefox`, `opera`, `safari`, `vivaldi`, `whale`), sin perfil; se guarda solo el nombre | ninguno: no se leen cookies |

Salida (stdout), una línea por URL:

```text
agregada #12  https://…  (720p)
ya en cola #7  https://…  (720p, downloading)
reencolada #3  https://…  (antes: failed)
```

Reencolar con `--cookies-de` una descarga fallida guarda el navegador nuevo (research R25).

| Código | Significado |
|--------|-------------|
| 0 | todas las URLs agregadas, ya presentes o reencoladas |
| 2 | uso inválido (calidad desconocida, carpeta inexistente o sin permisos, URL vacía, navegador no admitido en `--cookies-de`, `config.toml` inválido) |
| 3 | archivo de estado de la cola corrupto; se guardó una copia |

## `ejecutar [--cookies-de NAVEGADOR]`

Procesa la cola en primer plano. Solo termina cuando no quedan descargas en `queued`,
`downloading` ni `waiting_network` (FR-021). Recoge las URLs agregadas mientras corre.
`--cookies-de` aplica a esta ejecución y prevalece sobre el valor de cada descarga; no lo
modifica.

Si las cookies no se pueden leer, el error nombra el navegador y la causa
(`No se pudieron leer las cookies de edge: …`): las de `agregar --cookies-de` hacen fallar solo
esa descarga; las de `ejecutar --cookies-de`, que valen para todas, detienen la ejecución con
el código 3 (research R25).

Salida: una línea por descarga activa, actualizada al menos una vez por segundo (SC-007):

```text
#11 en cola  Título del video
#12 descargando  43.1%  3.2 MiB/s  ETA 00:01:52  Título del video
#13 esperando red  reintento en 40 s  (conexión rechazada)
#14 completada  ~/Descargas/Título del video.mp4
#15 fallida  Video privado: el propietario restringió el acceso
#16 aviso  reiniciada desde cero: el servidor no permite reanudar
#17 fallida  no se encontraron videos; la URL puede no estar soportada o requerir sesión (ver --cookies-de)
#18 aviso  ya existía «Título del video.mp4»; se publicó como «Título del video (1).mp4» sin tocar el anterior
```

El nombre publicado sale de `plantilla_nombre` (por defecto `{titulo}`, sin el id del video); en
Windows el título se recorta para que la ruta completa no pase de 240 caracteres. Un 403 o 410 al
obtener la información del video falla sin reintentos y el mensaje sugiere `--cookies-de`.

Con el disco lleno, sale con 3 y la descarga vuelve a la cola con su parcial:

```text
error: No queda espacio en el disco de /home/ana/Descargas (disco lleno). Libere espacio y vuelva a ejecutar `downloader ejecutar`. El progreso se ha conservado.
```

Al terminar, un resumen con el número de completadas y fallidas, y la causa de cada fallo.

| Código | Significado |
|--------|-------------|
| 0 | cola vacía; todas completadas |
| 1 | cola vacía; al menos una falló de forma permanente (también por cookies de su `agregar --cookies-de` que no se pudieron leer) |
| 2 | uso inválido (navegador no admitido en `--cookies-de`, `config.toml` inválido) u otro `ejecutar` ya en marcha |
| 3 | error fatal (disco lleno, archivo de estado corrupto, ffmpeg o ffprobe ausentes, cookies de `ejecutar --cookies-de` que no se pueden leer); progreso conservado |
| 130 | interrumpido con Ctrl+C (en Windows, también Ctrl+Break); progreso conservado |

Los errores van a stderr y nunca incluyen cookies, cabeceras de autenticación ni tokens.

## `estado`

Muestra la cola sin modificarla (FR-032). Es de solo lectura: no toma el bloqueo de
`ejecutar`, así que funciona mientras otro `ejecutar` corre, y no cambia ninguna fila. No
necesita red.

Salida (stdout), una línea por descarga en orden de `id`, con la etiqueta visible del estado
(data-model.md), el progreso, el título y, si lo hay, el último aviso o error, con los
secretos ocultos:

```text
#11 en cola  —  Título del video
#12 descargando  43.1%  Título del video
#13 esperando red  12.0%  Título del video  (conexión rechazada)
#14 completada  100%  Título del video  ~/Descargas/Título del video.mp4
#15 fallida  —  https://…/watch/abc  Video privado: el propietario restringió el acceso
#16 completada  lista (3 videos)  https://…/playlist/xyz
```

El progreso sale de `bytes_descargados` y `bytes_totales`, que `ejecutar` guarda como mucho
cada 2 s: puede ir hasta 2 s por detrás de la línea de `ejecutar`. Sin `bytes_totales`, se
muestran los bytes descargados (`35.2 MiB`); sin título todavía, la URL. Con la cola vacía, o
si `cola.db` no existe (no la crea), imprime `cola vacía`.

| Código | Significado |
|--------|-------------|
| 0 | cola mostrada (también si alguna descarga está `failed`) o cola vacía |
| 2 | uso inválido |
| 3 | archivo de estado de la cola corrupto; se guardó una copia, como en `agregar` |

## `actualizar`

Actualiza yt-dlp a su última versión dentro del proyecto (FR-025 enmendado, research R14):
`uv lock --upgrade-package yt-dlp` y `uv sync`, y después `uv run pytest -m ytdlp_internals`,
los tests del comportamiento interno de yt-dlp del que depende el programa. Muestra
`yt-dlp 2026.8.19 → 2026.10.21` o `yt-dlp ya está al día (2026.10.21)`.

Si esos tests fallan, restaura el `uv.lock` anterior, vuelve a sincronizar y lo explica:

```text
comprobando yt-dlp 2026.11.2 con los tests de su comportamiento interno…
error: no se pudo actualizar yt-dlp: yt-dlp 2026.11.2 no supera los tests de su comportamiento interno (pytest -m ytdlp_internals):
FAILED tests/integration/test_rango_no_satisfacible.py::test_… - AssertionError: …
1 failed, 39 passed in 22.10s
Se ha restaurado uv.lock y se conserva yt-dlp 2026.10.21.
```

Necesita ffmpeg y ffprobe: sin ellos, los tests que descargan se omitirían, así que no modifica
nada y sale con 1. Fuera del proyecto (instalado con `uv tool install`), no modifica nada e
indica `uv tool upgrade downloader`.

| Código | Significado |
|--------|-------------|
| 0 | actualizado y verificado, o ya al día |
| 1 | no se actualizó (sin red, uv o ffmpeg ausentes, fuera del proyecto, tests de los internos de yt-dlp fallidos); se muestra el motivo y se conserva la versión anterior |
