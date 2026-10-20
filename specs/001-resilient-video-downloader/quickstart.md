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
| 1 | Descarga básica | `agregar URL --calidad 720p` y `ejecutar` | archivo en la carpeta; progreso, velocidad y ETA visibles; código 0 | US1, FR-005 |
| 2 | Corte a mitad | test de integración: el servidor corta tras el 40 % | pasa a "esperando red", reanuda; bytes servidos ≤ 101 % del tamaño | US2, SC-002 |
| 3 | Red caída y vuelta | test: servidor apagado y relevantado en el mismo puerto | sigue en `waiting_network` hasta que vuelve; termina íntegro | US2, FR-007 |
| 4 | Cierre abrupto | test: `SIGKILL` a `ejecutar` y relanzar | la cola se restaura y la descarga continúa | US3, SC-003 |
| 5 | URL caducada | test con el extractor de tokens | 403 → nueva extracción → continúa el mismo `.part` | US4, FR-012 |
| 6 | Error permanente | test: extractor que lanza "video privado" | `failed` sin reintentos, mensaje claro, resto de la cola sigue; código 1 | US4, FR-011 |
| 7 | Integridad | test: tamaño anunciado mayor que el servido; HLS truncado | no aparece el nombre final en la carpeta | US5, SC-004 |
| 8 | Nombre repetido | test: archivo con el mismo nombre ya existente | se publica `nombre (1).ext`, el original intacto | FR-022 |
| 9 | Cookies | test: sin `--cookies-de` no se leen; con él, opción pasada a yt-dlp y nada escrito en disco | sin cookies en la base de datos ni en el directorio de trabajo | US6, FR-017 |
| 10 | Directo o DRM | test: extractor con `is_live` o `has_drm` | `failed` con mensaje claro, sin intento de descarga | FR-015 |

Contrato de comandos y códigos de salida: [contracts/cli.md](contracts/cli.md).
Estados y transiciones: [data-model.md](data-model.md).
