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

**Primary Dependencies**: yt-dlp (librería), Typer (CLI; aporta rich para el progreso);
ffmpeg/ffprobe como binarios del sistema

**Storage**: SQLite (WAL, `synchronous=FULL`) en el directorio de la app; `config.toml` para la
configuración; archivos de trabajo en `<carpeta_destino>/.downloader-tmp/`

**Testing**: pytest (dependencia de desarrollo), servidor HTTP local con cortes simulados,
extractor de prueba para yt-dlp, marcador `ffmpeg` que omite tests si falta

**Target Platform**: Linux y macOS (bloqueo con `fcntl.flock`); Windows fuera de alcance en v1

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
| I. Código simple | un paquete, ~10 módulos, 2 dependencias Python, sin daemon ni concurrencia de descargas | Pasa |
| II. Tests con cortes de red | servidor local con cortes, apagado/rearranque, tokens caducados y `SIGKILL`; sin internet (R15) | Pasa; CI MUST instalar ffmpeg para no omitir en silencio |
| III. Errores claros | clasificación TEMPORAL/PERMANENTE/FATAL (R4); códigos de salida en [contracts/cli.md](contracts/cli.md) | Pasa |
| IV. No perder progreso | `.part` en directorio de trabajo, `skip_unavailable_fragments=False`, recuperación al arrancar, Ctrl+C conserva todo | **Pasa con excepción a confirmar** (R3, ver Complexity Tracking) |
| V. Sin DRM | rechazo de `has_drm` e `is_live` antes de descargar (R5) | Pasa |
| VI. Sin credenciales | `cookiesfrombrowser` en memoria, sin `cookiefile`; la base de datos solo guarda el nombre del navegador; logger que filtra secretos (R12) | Pasa |

**Re-check post-diseño**: sin cambios; la única excepción sigue siendo R3.

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
├── cli.py               # Typer: agregar, ejecutar, update; códigos de salida
├── config.py            # config.toml, rutas de la app, valores por defecto
├── models.py            # Estado, Calidad, Descarga, transiciones permitidas
├── store.py             # SQLite: esquema, altas, transiciones, recuperación, bloqueo
├── errors.py            # clasificación TEMPORAL / PERMANENTE / FATAL, filtro de secretos
├── retry.py             # cálculo del backoff
├── network.py           # hilo monitor de conectividad
├── ytdl.py              # adaptador yt-dlp: opciones, formatos, progreso, listas
├── publish.py           # verificación (tamaño, ffprobe) y publicación sin sobrescribir
├── worker.py            # bucle de ejecutar: tomar, descargar, clasificar, esperar
└── progress.py          # presentación del progreso en terminal

tests/
├── conftest.py          # marcador ffmpeg, fixtures de servidor y base de datos temporal
├── support/
│   ├── flaky_server.py  # HTTP con Range, cortes, 403 por token, apagado/rearranque
│   └── test_extractor.py
├── unit/                # models, store, errors, retry, publish, ytdl (formatos)
└── integration/         # cortes, caída de red, SIGKILL, URL caducada, permanentes, listas, CLI
```

**Structure Decision**: proyecto único con estructura `src/`, generado con
`uv init --package --name downloader --python 3.12`. Cada módulo tiene una responsabilidad
(Principio I); `worker.py` es el único que combina las demás piezas.

## Decisiones que difieren de la entrada o de la spec

1. `uv add --dev pytest` en lugar de `uv add pytest` (R16).
2. `uv init --package` en lugar de `uv init` para tener `src/` y un script de consola (R16).
3. Publicación con `os.link` + `unlink` en lugar de renombrado: `os.rename` sobrescribe en
   POSIX y violaría FR-022 (R7).
4. Concurrencia fija en 1 descarga; la spec suponía "configurable". Se difiere por YAGNI.
5. `update` se interpreta como "actualizar yt-dlp" (R14). Falta confirmación, y el nombre en
   inglés no sigue a `agregar`/`ejecutar`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Principio IV: si el servidor no permite reanudar, yt-dlp borra el `.part` y empieza de cero (R3) | Es comportamiento interno de yt-dlp; coincide con el caso límite de la spec ("se reinicia solo esa descarga") | Impedirlo exige reemplazar el descargador HTTP de yt-dlp. Mitigación propuesta: mover el `.part` a `.part.N.bak` antes de reintentar cuando el servidor no anuncie `Accept-Ranges`. **Requiere decisión: aceptar la mitigación o enmendar el principio IV.** |
