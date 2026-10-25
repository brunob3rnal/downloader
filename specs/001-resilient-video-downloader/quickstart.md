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
| 10 | Directo, DRM o cifrado no soportado | test: extractor con `is_live` o `has_drm`; HLS con `skd://`, FairPlay, PlayReady, Widevine o un PSSH (en `#EXT-X-KEY` o `#EXT-X-SESSION-KEY`, con los atributos en cualquier orden); HLS `SAMPLE-AES` con clave `identity`; HLS `AES-128` `identity` con la clave por HTTP(S), `data:` o `hls_aes`; DASH, ISM y F4M con protección, y MPD con representaciones protegidas y sin proteger; formatos del extractor con el video marcado `has_drm` y el audio sin proteger | directo, DRM y `SAMPLE-AES`: `failed` permanente con mensaje claro ("contenido con DRM" o "no soportado"), sin pedir la clave ni ningún segmento; `AES-128` `identity`: se descarga y descifra; MPD mixto: las protegidas se omiten y, si lo sin proteger cubre lo pedido, se descarga con el aviso "N representaciones protegidas omitidas" ("1 representación protegida omitida" con una); video marcado `has_drm`: rechazado como DRM salvo con `--calidad audio` | FR-015, Principio V |
| 11 | Servidor sin reanudación | test: el servidor ignora `Range` tras un corte | la descarga se reinicia desde cero, se muestra el aviso y termina íntegra | FR-024 |
| 12 | Actualizar yt-dlp | `uv run downloader actualizar` | muestra versión anterior y nueva tras pasar `pytest -m ytdlp_internals`, o "ya está al día"; código 0. Si esos tests fallan, se restaura la versión anterior y sale con 1 | FR-025 |
| 13 | Estado de la cola | `uv run downloader estado`, también con `ejecutar` en marcha | una línea por descarga con id, estado, progreso, título y último aviso o error, con los secretos ocultos; la cola no cambia; `cola vacía` sin crear `cola.db`; código 0, o 3 con `cola.db` corrupta | US1 escenario 6, FR-032 |
| 14 | Solo audio | `uv run downloader agregar URL --calidad audio` y `uv run downloader ejecutar` | el archivo publicado tiene al menos un flujo de audio y ninguno de video; código 0 | US1 escenario 4, FR-002 |

## Validación (T065 y T094, 2026-10-24)

Linux, con ffmpeg, yt-dlp 2026.10.21. Windows, en el job `windows-latest` del CI. Los
escenarios 13 y 14, y la parte de HLS del 10, se validaron en la Fase 10 (T094; CI run
40115747729).

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
| 10 | `test_directo_drm.py`, `test_hls_aes.py`, `test_drm.py` | ✓ directos, HLS, DASH (también por URL directa del MPD), ISM y F4M |
| 11 | `test_sin_reanudacion.py` | ✓ |
| 12 | a mano: `uv run downloader actualizar` dos veces; y `test_actualizar.py` | ✓ `yt-dlp 2026.8.19 → 2026.10.21` (40 tests marcados en verde) y después `yt-dlp ya está al día (2026.10.21)` |
| 13 | a mano: `uv run downloader estado` durante un `ejecutar` real; y `test_estado.py` | ✓ 0,1 % → 41,0 % → 84,3 %, token oculto; `ejecutar` terminó con 0; `estado --desconocida` y `estado extra` salen con 2 sin crear `cola.db` |
| 14 | `test_solo_audio.py` | ✓ |

Contrato de comandos y códigos de salida: [contracts/cli.md](contracts/cli.md).
Estados y transiciones: [data-model.md](data-model.md).
