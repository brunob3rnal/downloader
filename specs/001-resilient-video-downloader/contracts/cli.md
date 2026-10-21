# Contrato CLI: `downloader`

Punto de entrada: `uv run downloader <comando>` (script de consola del paquete).

La configuración (carpeta de destino y calidad por defecto) vive en `config.toml`, en el
directorio que devuelve `typer.get_app_dir("downloader")`; ver
[data-model.md](../data-model.md). Las opciones de línea de comandos prevalecen sobre ella.

## `agregar URL... [--calidad C] [--carpeta RUTA] [--cookies-de NAVEGADOR]`

Guarda una o varias URLs en la cola. No necesita red.

| Opción | Valores | Por defecto |
|--------|---------|-------------|
| `--calidad` | `mejor`, `1080p`, `720p`, `audio` | `calidad` de la configuración |
| `--carpeta` | ruta existente y escribible | `carpeta_destino` de la configuración |
| `--cookies-de` | nombre de navegador admitido por yt-dlp | ninguno: no se leen cookies |

Salida (stdout), una línea por URL:

```text
agregada #12  https://…  (720p)
ya en cola #7  https://…  (720p, downloading)
reencolada #3  https://…  (antes: failed)
```

| Código | Significado |
|--------|-------------|
| 0 | todas las URLs agregadas, ya presentes o reencoladas |
| 2 | uso inválido (calidad desconocida, carpeta inexistente o sin permisos, URL vacía) |
| 3 | archivo de estado de la cola corrupto; se guardó una copia |

## `ejecutar [--cookies-de NAVEGADOR]`

Procesa la cola en primer plano. Solo termina cuando no quedan descargas en `queued`,
`downloading` ni `waiting_network` (FR-021). Recoge las URLs agregadas mientras corre.
`--cookies-de` aplica a esta ejecución y prevalece sobre el valor de cada descarga.

Salida: una línea por descarga activa, actualizada al menos una vez por segundo (SC-007):

```text
#11 en cola  Título del video
#12 descargando  43.1%  3.2 MiB/s  ETA 00:01:52  Título del video
#13 esperando red  reintento en 40 s  (conexión rechazada)
#14 completada  ~/Descargas/Título [abc123].mp4
#15 fallida  Video privado: el propietario restringió el acceso
#16 aviso  reiniciada desde cero: el servidor no permite reanudar
```

Al terminar, un resumen con el número de completadas y fallidas, y la causa de cada fallo.

| Código | Significado |
|--------|-------------|
| 0 | cola vacía; todas completadas |
| 1 | cola vacía; al menos una falló de forma permanente |
| 2 | uso inválido u otro `ejecutar` ya en marcha |
| 3 | error fatal (disco lleno, archivo de estado corrupto, ffmpeg o ffprobe ausentes); progreso conservado |
| 130 | interrumpido con Ctrl+C; progreso conservado |

Los errores van a stderr y nunca incluyen cookies, cabeceras de autenticación ni tokens.

## `actualizar`

Actualiza yt-dlp a su última versión dentro del proyecto (FR-025, research R14). Muestra `yt-dlp 2026.08.19 → 2026.10.01` o
`yt-dlp ya está al día (2026.08.19)`.

| Código | Significado |
|--------|-------------|
| 0 | actualizado o ya al día |
| 1 | la actualización falló (sin red, uv ausente); se muestra el motivo |
