# Quickstart: validación del gestor de descargas

## Requisitos

- Windows 10/11 o Linux.
- uv instalado.
- ffmpeg y ffprobe en el `PATH`; sin ellos, los tests marcados `ffmpeg` se omiten y `ejecutar`
  falla con código 3. En Windows se instalan, por ejemplo, con `winget install Gyan.FFmpeg` o
  `choco install ffmpeg`.

## Preparación

```bash
uv sync
```

## Tests

```bash
uv run pytest                      # todo; los marcados ffmpeg se omiten si falta
uv run pytest -m "not ffmpeg"      # solo lo que no necesita ffmpeg
uv run pytest -m "not slow"        # los rápidos
uv run pytest -m slow              # los de más de unos 5 s
uv run pytest -m ytdlp_internals   # los que ejecuta `actualizar` (FR-025)
```

Ningún test usa internet: todos van contra el servidor local de `tests/`.

## Escenarios de validación

| # | Escenario | Cómo | Resultado esperado | Spec |
|---|-----------|------|--------------------|------|
| 1 | Descarga básica | `uv run downloader agregar URL --calidad 720p` y `uv run downloader ejecutar` | archivo en la carpeta; progreso, velocidad y ETA visibles; código 0 | US1, FR-005 |
| 2 | Corte a mitad | test de integración: el servidor corta tras el 40 % | pasa a "esperando red", reanuda; bytes servidos ≤ 101 % del tamaño | US2, SC-002 |
| 2b | Timeout y 5xx | test: el servidor deja de enviar datos; luego responde 503 | ambos se tratan como temporales y la descarga termina íntegra | FR-010, Principio II |
| 3 | Red caída y vuelta | test: servidor apagado y relevantado en el mismo puerto | sigue en `waiting_network` hasta que vuelve; termina íntegro | US2, FR-007 |
| 4 | Cierre abrupto | test: `Popen.kill()` a `ejecutar` (`SIGKILL` en Linux, `TerminateProcess` en Windows) y relanzar | la cola se restaura y la descarga continúa | US3, SC-003 |
| 5 | URL caducada | test con el extractor de tokens | 403 → nueva extracción → continúa el mismo `.part` | US4, FR-012 |
| 6 | Error permanente | test: extractor que lanza "video privado" | `failed` sin reintentos, mensaje claro, resto de la cola sigue; código 1 | US4, FR-011 |
| 7 | Integridad | test: archivo servido truncado con su tamaño truncado anunciado; tamaño anunciado mayor que el servido; fragmento HLS cortado (marcado `ffmpeg`) | el truncado no aparece con su nombre final y sus archivos quedan en `invalidos/`; el anunciado mayor sigue reintentando sin publicar; el HLS cortado termina íntegro | US5, SC-004, R22 |
| 8 | Nombre repetido | test: archivo con el mismo nombre ya existente | se publica `nombre (1).ext`, el original intacto | FR-022 |
| 9 | Cookies | test: sin `--cookies-de` no se leen; con él, opción pasada a yt-dlp y nada escrito en disco; cookies ilegibles | el video que exige sesión se descarga; sin cookies en la base de datos ni en el directorio de trabajo; el error de lectura nombra el navegador y la causa | US6, FR-016, FR-017, R25 |
| 10 | Directo o DRM | test: extractor con `is_live` o `has_drm` | `failed` con mensaje claro, sin intento de descarga | FR-015 |
| 11 | Servidor sin reanudación | test: el servidor ignora `Range` tras un corte | la descarga se reinicia desde cero, se muestra el aviso y termina íntegra | FR-024 |
| 12 | Actualizar yt-dlp | `uv run downloader actualizar` | muestra versión anterior y nueva tras pasar `pytest -m ytdlp_internals`, o "ya está al día"; código 0. Si esos tests fallan, se restaura la versión anterior y sale con 1 | FR-025 |

## Validación (T065, 2026-10-24)

Linux, con ffmpeg, yt-dlp 2026.10.21. Windows, en el job `windows-latest` del CI.

| # | Validado con | Resultado |
|---|--------------|-----------|
| 1 | a mano: `agregar` de dos URLs directas del servidor local con `--calidad 720p` y `ejecutar`; y `test_descarga_basica.py` | ✓ dos archivos íntegros, líneas `descargando  NN.N%  X MiB/s  ETA`, código 0 |
| 2 | `test_network_cuts.py` (casos a y e) | ✓ |
| 2b | `test_network_cuts.py` (casos b y c), `test_estancamiento.py` | ✓ |
| 3 | `test_network_cuts.py` (caso d) | ✓ |
| 4 | `test_crash_recovery.py` | ✓ |
| 5 | `test_url_caducada.py`, `test_registro_peticiones.py` | ✓ |
| 6 | `test_errores_permanentes.py` | ✓ |
| 7 | `test_integridad.py`, `test_rango_no_satisfacible.py` | ✓ |
| 8 | `test_publish_nombres.py`, `test_plantilla_nombre.py` | ✓ |
| 9 | `test_cookies.py`, `test_secretos.py` | ✓ |
| 10 | `test_directo_drm.py` | ✓ |
| 11 | `test_sin_reanudacion.py` | ✓ |
| 12 | a mano: `uv run downloader actualizar` dos veces; y `test_actualizar.py` | ✓ `yt-dlp 2026.8.19 → 2026.10.21` (40 tests marcados en verde) y después `yt-dlp ya está al día (2026.10.21)` |

Contrato de comandos y códigos de salida: [contracts/cli.md](contracts/cli.md).
Estados y transiciones: [data-model.md](data-model.md).
