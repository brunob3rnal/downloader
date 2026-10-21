# Implementation Plan: Gestor de descargas de video resiliente

**Branch**: `001-resilient-video-downloader` | **Date**: 2026-10-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-resilient-video-downloader/spec.md`

## Summary

CLI en Python que mantiene una cola persistente de descargas de video y las procesa con yt-dlp
usado como librería. La resistencia se apoya en cuatro piezas: cola en SQLite con transiciones
transaccionales y recuperación al arrancar; reintentos indefinidos con backoff exponencial
(tope 5 minutos) y re-extracción en cada intento, que también resuelve las URLs caducadas;
un hilo que vigila la conectividad y despierta los reintentos al volver la red; y un
directorio de trabajo por descarga donde se verifican los archivos (tamaño y ffprobe) antes de
publicarlos sin sobrescribir.

## Technical Context

**Language/Version**: Python 3.12, gestionado con uv

**Primary Dependencies**: `yt-dlp[default]` (librería, con sus extras: AES nativo para HLS,
manejador HTTP `requests`; R1), Typer (CLI; aporta rich para el progreso); ffmpeg/ffprobe como
binarios del sistema

**Storage**: SQLite (WAL, `synchronous=FULL`) en el directorio de la app; `config.toml` para la
configuración; archivos de trabajo en `<carpeta_destino>/.downloader-tmp/`

**Testing**: pytest (dependencia de desarrollo), servidor HTTP local con cortes simulados,
extractor de prueba para yt-dlp, marcador `ffmpeg` que omite tests si falta; los mismos tests
en Linux y en Windows (cierre abrupto con `Popen.kill()`, Ctrl+C con `CTRL_BREAK_EVENT` en
Windows, detector de usos de un solo sistema sin proteger)

**Target Platform**: Windows 10/11 y Linux (constitución v2.1.0, sección Plataformas). El
bloqueo de un único `ejecutar` usa `msvcrt.locking` en Windows y `fcntl.flock` en Linux, con
import condicional (R17); nombres de archivo válidos en Windows desde cualquier sistema
(`windowsfilenames=True`, nombres reservados y límite de 260 caracteres por ruta, R7); el CI
ejecuta la suite en `ubuntu-latest` y `windows-latest` con ffmpeg. macOS no es plataforma
objetivo: nada lo excluye, pero no se verifica.

**Project Type**: CLI de un solo paquete

**Performance Goals**: progreso actualizado ≥ 1 vez/s (SC-007); retransmisión ≤ 1 % tras un
corte (SC-002); error permanente notificado en < 30 s (SC-005)

**Constraints**: sin servicio en segundo plano; un solo `ejecutar` a la vez; una descarga
simultánea; ningún secreto en disco ni en logs

**Scale/Scope**: uso personal; colas de hasta miles de entradas; videos de varios GB

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Cumplimiento en el diseño | Estado |
|-----------|---------------------------|--------|
| I. Código simple | un paquete, ~10 módulos, 2 dependencias Python directas (yt-dlp con sus extras por defecto y Typer), sin daemon ni concurrencia de descargas | Pasa |
| II. Tests con cortes de red | servidor local con respuestas truncadas, timeouts, 5xx, apagado/rearranque, tokens caducados y `SIGKILL`; sin internet (R15) | Pasa; CI MUST instalar ffmpeg para no omitir en silencio |
| III. Errores claros | clasificación TEMPORAL/PERMANENTE/FATAL con una tabla única por fase y tope de 3 intentos sin avance para 403/410 y ffmpeg (R4, FR-026); códigos de salida en [contracts/cli.md](contracts/cli.md) | Pasa |
| IV. No perder progreso reanudable (v2.0.0) | `.part` en directorio de trabajo, `skip_unavailable_fragments=False`, recuperación al arrancar, Ctrl+C conserva todo; si el servidor no permite reanudar, reinicio con aviso detectado por retroceso de bytes (R3, FR-024) | Pasa |
| V. Sin DRM | rechazo de `has_drm` e `is_live` antes de descargar (R5) | Pasa |
| VI. Sin credenciales | `cookiesfrombrowser` en memoria, sin `cookiefile`; la base de datos solo guarda el nombre del navegador; logger que filtra secretos (R12) | Pasa |
| Plataformas (v2.1.0) | sin dependencias nuevas: `msvcrt`/`fcntl` con import condicional (R17); publicación con `os.link` también en NTFS y copia exclusiva en FAT32/exFAT (R7); nombres válidos en Windows; `tests/unit/test_plataforma.py` falla ante un `import fcntl` o un `signal.SIGKILL` sin proteger; CI en Linux y Windows | Pasa; la verificación real en Windows es el job `windows-latest` |

**Re-check post-diseño**: pasa. La excepción de R3 quedó resuelta al enmendar el Principio IV (constitución v2.0.0). La sección Plataformas (v2.1.0) se cumple con R7 y R17.

## Project Structure

### Documentation (this feature)

```text
specs/001-resilient-video-downloader/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── cli.md
└── tasks.md             # /speckit-tasks (no lo crea este comando)
```

### Source Code (repository root)

```text
pyproject.toml           # uv init --package; script de consola "downloader"
uv.lock
src/downloader/
├── __init__.py
├── __main__.py          # `python -m downloader` (los tests lanzan así los subprocesos)
├── cli.py               # Typer: agregar, ejecutar, actualizar; códigos de salida
├── config.py            # config.toml, rutas de la app, valores por defecto
├── models.py            # Estado, Calidad, Descarga, transiciones permitidas
├── store.py             # SQLite: esquema, altas, transiciones, recuperación, bloqueo
├── errors.py            # clasificación TEMPORAL / PERMANENTE / FATAL, filtro de secretos
├── retry.py             # cálculo del backoff
├── network.py           # hilo monitor de conectividad
├── ytdl.py              # adaptador yt-dlp: opciones, formatos, progreso, listas, registro de la última petición
├── publish.py           # verificación (tamaño, ffprobe) y publicación sin sobrescribir
├── worker.py            # bucle de ejecutar: tomar, descargar, clasificar, esperar
└── progress.py          # presentación del progreso en terminal

tests/
├── conftest.py          # marcador ffmpeg, fixtures de servidor y base de datos temporal
├── support/
│   ├── flaky_server.py  # HTTP con Range, cortes, 403 por token, apagado/rearranque
│   └── fake_extractor.py   # InfoExtractor de prueba (no empieza por test_)
├── unit/                # models, store, errors, retry, publish, ytdl (formatos)
└── integration/         # cortes, caída de red, cierre abrupto, Ctrl+C, URL caducada, permanentes, listas, CLI
```

**Structure Decision**: proyecto único con estructura `src/`, generado con
`uv init --package --name downloader --python 3.12`. Cada módulo tiene una responsabilidad
(Principio I); `worker.py` es el único que combina las demás piezas.

## Decisiones que difieren de la entrada o de la spec

1. `uv add --dev pytest` en lugar de `uv add pytest` (R16).
2. `uv init --package` en lugar de `uv init` para tener `src/` y un script de consola (R16).
3. Publicación con `os.link` + `unlink` en lugar de renombrado: `os.rename` sobrescribe en
   POSIX y violaría FR-022 (R7). Funciona igual en NTFS; en FAT32/exFAT, copia exclusiva.
4. Una sola descarga simultánea en v1; la concurrencia configurable queda para una feature
   futura (spec, Assumptions).
5. El comando de mantenimiento se llama `actualizar` y actualiza yt-dlp (FR-025, R14).
6. `ytdl.crear_ydl` usa una subclase de `YoutubeDL` que registra el resultado de la última
   petición (incluida la lectura del cuerpo), la fase y si descarga un programa externo,
   porque yt-dlp descarta la excepción original al agotar sus reintentos; `errors.clasificar`
   lo usa cuando la cadena de causas no trae código HTTP (R4). Es la única subclase de yt-dlp
   del proyecto y sobrescribe dos métodos (`urlopen` y `dl`).
7. Los 403/410 durante la descarga se reintentan como URL caducada, con un tope de 3 intentos
   seguidos sin bytes nuevos (`retry.TopeSinAvance`, FR-026); es una excepción acotada a
   "reintentar indefinidamente" (FR-010).
8. Dependencia `yt-dlp[default]` en lugar de `yt-dlp`, y `hls_prefer_native=True`, para que
   HLS no se delegue en ffmpeg y sus peticiones queden registradas (R1, R4). Incluye `mutagen`
   (GPL-2.0-or-later).
9. Windows 10/11 es plataforma objetivo además de Linux (constitución v2.1.0). El bloqueo usa
   `msvcrt`/`fcntl` con import condicional en lugar de la dependencia `portalocker` (R17).
10. Los archivos del directorio de trabajo se llaman `descarga.<ext>` en lugar de
    `<id_video>.<ext>`: el id podría ser largo o un nombre reservado de Windows (R7).
11. `nombre_publicado` acorta el título para que el nombre quepa en 255 bytes y, en Windows, la
    ruta en 259 caracteres, con hueco para el sufijo ` (N)`; antepone `_` si queda un nombre
    reservado (R7).
12. En Windows, `ejecutar` trata Ctrl+Break como Ctrl+C y la salida redirigida no se detiene
    por caracteres que no caben en la codificación de la consola (R17).

## Complexity Tracking

Sin violaciones de la constitución que justificar.
