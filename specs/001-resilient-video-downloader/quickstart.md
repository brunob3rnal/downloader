# Quickstart: validación del gestor de descargas

## Requisitos

- uv instalado.
- ffmpeg y ffprobe en el `PATH` (sin ellos, los tests marcados `ffmpeg` se omiten y `ejecutar`
  falla con código 3).

## Preparación

```bash
uv sync
```

## Tests

```bash
uv run pytest                 # todo; los marcados ffmpeg se omiten si falta
uv run pytest -m "not ffmpeg" # solo lo que no necesita ffmpeg
```

Ningún test usa internet: todos van contra el servidor local de `tests/`.

## Escenarios de validación

| # | Escenario | Cómo | Resultado esperado | Spec |
|---|-----------|------|--------------------|------|
| 1 | Descarga básica | `uv run downloader agregar URL --calidad 720p` y `uv run downloader ejecutar` | archivo en la carpeta; progreso, velocidad y ETA visibles; código 0 | US1, FR-005 |
| 2 | Corte a mitad | test de integración: el servidor corta tras el 40 % | pasa a "esperando red", reanuda; bytes servidos ≤ 101 % del tamaño | US2, SC-002 |
| 2b | Timeout y 5xx | test: el servidor deja de enviar datos; luego responde 503 | ambos se tratan como temporales y la descarga termina íntegra | FR-010, Principio II |
| 3 | Red caída y vuelta | test: servidor apagado y relevantado en el mismo puerto | sigue en `waiting_network` hasta que vuelve; termina íntegro | US2, FR-007 |
| 4 | Cierre abrupto | test: `SIGKILL` a `ejecutar` y relanzar | la cola se restaura y la descarga continúa | US3, SC-003 |
| 5 | URL caducada | test con el extractor de tokens | 403 → nueva extracción → continúa el mismo `.part` | US4, FR-012 |
| 6 | Error permanente | test: extractor que lanza "video privado" | `failed` sin reintentos, mensaje claro, resto de la cola sigue; código 1 | US4, FR-011 |
| 7 | Integridad | test: tamaño anunciado mayor que el servido; HLS truncado (marcado `ffmpeg`) | no aparece el nombre final en la carpeta; archivos en `invalidos/` | US5, SC-004 |
| 8 | Nombre repetido | test: archivo con el mismo nombre ya existente | se publica `nombre (1).ext`, el original intacto | FR-022 |
| 9 | Cookies | test: sin `--cookies-de` no se leen; con él, opción pasada a yt-dlp y nada escrito en disco | sin cookies en la base de datos ni en el directorio de trabajo | US6, FR-017 |
| 10 | Directo o DRM | test: extractor con `is_live` o `has_drm` | `failed` con mensaje claro, sin intento de descarga | FR-015 |
| 11 | Servidor sin reanudación | test: el servidor ignora `Range` tras un corte | la descarga se reinicia desde cero, se muestra el aviso y termina íntegra | FR-024 |
| 12 | Actualizar yt-dlp | `uv run downloader actualizar` | muestra versión anterior y nueva, o "ya está al día"; código 0 | FR-025 |

Contrato de comandos y códigos de salida: [contracts/cli.md](contracts/cli.md).
Estados y transiciones: [data-model.md](data-model.md).
