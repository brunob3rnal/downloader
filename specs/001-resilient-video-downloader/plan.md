# Implementation Plan: Gestor de descargas de video resiliente

**Branch**: `main`, mediante PRs desde ramas de trabajo `claude/*` (no existe una rama `001-resilient-video-downloader`; el directorio de la feature lo fija `SPECIFY_FEATURE_DIRECTORY`) | **Date**: 2026-10-20 | **Spec**: [spec.md](spec.md)

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

**Performance Goals**: progreso actualizado ≥ 1 vez/s (SC-007); total servido ≤ 1 % por encima
del tamaño del archivo, acumulado en todas las reanudaciones (SC-002); error permanente notificado en < 30 s (SC-005)

**Constraints**: sin servicio en segundo plano; un solo `ejecutar` a la vez; una descarga
simultánea; ningún secreto en disco ni en logs

**Scale/Scope**: uso personal; colas de hasta miles de entradas; videos de varios GB

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Cumplimiento en el diseño | Estado |
|-----------|---------------------------|--------|
| I. Código simple | un paquete, 14 módulos (`drm.py` desde la Fase 10, decisión 30), 2 dependencias Python directas (yt-dlp con sus extras por defecto y Typer), sin daemon ni concurrencia de descargas | Pasa |
| II. Tests con cortes de red | servidor local con respuestas truncadas, timeouts, 5xx, apagado/rearranque, tokens caducados y `SIGKILL`; sin internet (R15) | Pasa; CI MUST instalar ffmpeg para no omitir en silencio |
| III. Errores claros | clasificación TEMPORAL/PERMANENTE/FATAL con una tabla única por fase y tope de 3 intentos sin avance para 403/410 y ffmpeg (R4, FR-026); códigos de salida en [contracts/cli.md](contracts/cli.md) | Pasa |
| IV. No perder progreso reanudable (v2.0.0) | `.part` en directorio de trabajo, `skip_unavailable_fragments=False`, recuperación al arrancar, Ctrl+C conserva todo; si el servidor no permite reanudar, reinicio con aviso detectado por retroceso de bytes (R3, FR-024) | Pasa |
| V. Sin DRM ni sistemas de licencias (v3.0.0) | el criterio es el sistema de licencias: `skd://`, FairPlay, PlayReady, Widevine, PSSH y cualquier `KEYFORMAT` distinto de `identity` se rechazan como DRM con un analizador propio de los atributos de `#EXT-X-KEY`/`#EXT-X-SESSION-KEY`, en cualquier orden y antes de pedir la clave; `AES-128` con `KEYFORMAT` ausente o `identity` se descarga y descifra, con la clave por HTTP(S) (misma sesión), `data:` o `hls_aes`; `SAMPLE-AES` y otros métodos con clave `identity`, error permanente "no soportado" (FR-008, no Principio V); `is_live` rechazado en `process_info` (R5, decisión 30) | Pasa (Fase 10, T089–T091) |
| VI. Sin credenciales | `cookiesfrombrowser` en memoria, sin `cookiefile`; la base de datos solo guarda el nombre del navegador; `config.toml` rechaza claves de credenciales o cookies; logger que filtra secretos (R12, R25) | Pasa |
| Plataformas (v2.1.0) | sin dependencias nuevas: `msvcrt`/`fcntl` con import condicional (R17); publicación con `os.link` también en NTFS y copia exclusiva en FAT32/exFAT (R7); nombres válidos en Windows; `tests/unit/test_plataforma.py` falla ante un `import fcntl` o un `signal.SIGKILL` sin proteger; CI en Linux y Windows | Pasa; la verificación real en Windows es el job `windows-latest` |

**Re-check post-diseño**: pasa. La excepción de R3 quedó resuelta al enmendar el Principio IV (constitución v2.0.0). La sección Plataformas (v2.1.0) se cumple con R7 y R17. El descifrado de HLS AES-128, que la redacción anterior del Principio V prohibía (hallazgo C1 de `/speckit-analyze`), quedó resuelto al enmendarlo (constitución v3.0.0); su plan de migración es la Fase 10 de tasks.md.

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
├── cli.py               # Typer: agregar, ejecutar, estado, actualizar; códigos de salida
├── actualizacion.py     # actualizar yt-dlp con uv y verificarlo con los tests de sus internos
├── config.py            # config.toml, rutas de la app, valores por defecto
├── models.py            # Estado, Calidad, Descarga, transiciones permitidas
├── store.py             # SQLite: esquema, altas, transiciones, recuperación, bloqueo
├── errors.py            # clasificación TEMPORAL / PERMANENTE / FATAL, filtro de secretos
├── drm.py               # atributos de #EXT-X-KEY y política DRM / no soportado (R5; Fase 10)
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
│   ├── fake_extractor.py   # InfoExtractor de prueba (no empieza por test_)
│   └── navegador_falso.py  # lectura de cookies del navegador simulada, con una cookie ficticia (R25)
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
    ruta en 240 caracteres (antes 259; decisión 22), con hueco para el sufijo ` (N)`; antepone
    `_` si queda un nombre reservado (R7).
12. En Windows, `ejecutar` trata Ctrl+Break como Ctrl+C y la salida redirigida no se detiene
    por caracteres que no caben en la codificación de la consola (R17).
13. El envoltorio de lectura de `YoutubeDLRegistrado` lee el cuerpo en trozos de 4 KiB y, si
    la conexión se corta, entrega lo acumulado antes de relanzar el error en la lectura
    siguiente: yt-dlp pide bloques de hasta 4 MiB y urllib3 descarta lo recibido de un bloque
    incompleto, lo que incumplía SC-002. Cuesta CPU: la descarga local baja de ~700 a
    ~275 MiB/s (R18).
14. `YoutubeDLRegistrado` cierra las respuestas abiertas durante la extracción al acabar esta
    (`process_ie_result`), al empezar la descarga (`dl`) y al cerrarse: con URLs directas de
    `Content-Type: video/*`, el extractor genérico de yt-dlp deja abierto un GET completo que
    seguía recibiendo el video durante toda la descarga (R19).
15. SC-002 enmendado (spec, Clarifications 2026-10-22): tras una interrupción abrupta del
    proceso se admite un exceso del 1 % más 128 KiB, por lo que no se puede capturar (sondeos
    del extractor genérico, búfer del archivo, bloque en vuelo). Tras un corte de red sigue el
    1 % estricto (R20).
16. Vigilante de estancamiento en `YoutubeDLRegistrado`: aborta el intento con `SinDatos`
    (TEMPORAL) tras 30 s sin bytes o con menos de 1 KiB/s sostenido durante 30 s,
    configurables en `config.toml`; reintento tras ~5 s, sin contar para el tope de ambiguos
    ni como intento sin avance. `retries=1`, `fragment_retries=1` y `socket_timeout=15`. El
    cuerpo se lee con `read1` de urllib3 para que un goteo no bloquee el envoltorio (R21).

17. `YoutubeDLRegistrado.urlopen` resuelve el HTTP 416 al reanudar con el tamaño exacto de
    `Content-Range: bytes */TOTAL`, porque yt-dlp repite la petición con el mismo `Range` (su
    `Request` comparte el diccionario de cabeceras) y, si no, tolera ±100 bytes: `.part`
    completo → terminado sin pedir más bytes y verificado como siempre; `.part` mayor que el
    archivo → reinicio con aviso que da el motivo (FR-024); 416 sin `Content-Range` → `TEMPORAL`
    ambiguo con el tope de FR-026. Es un tercer comportamiento de `urlopen`, sin métodos nuevos
    sobrescritos (R23).

18. El trabajador llama a `gc.collect()` antes de cada intento de descarga: cuando un
    fragmento falla con `DownloadError`, yt-dlp deja el `.part` abierto en un ciclo de
    referencias, y en Windows el intento siguiente no podía renombrarlo al terminar
    (`WinError 32`). `apartar_invalidos` añade `-1`, `-2`… si ya existe la carpeta de esa
    fecha, porque en Windows el reloj avanza a saltos (R24).

19. Las cookies que no se pueden leer se informan con el navegador y la causa
    (`errors.CookiesIlegibles`), porque los mensajes de yt-dlp no siempre nombran ninguno de
    los dos. Su alcance depende de dónde se pidieron: las de `agregar --cookies-de` hacen
    fallar solo esa descarga; las de `ejecutar --cookies-de` detienen la ejecución como error
    FATAL (código 3), sin dar ninguna descarga por fallida (R25).
20. Reencolar con `agregar --cookies-de` una descarga fallida guarda el navegador nuevo, para
    que funcione el consejo del mensaje de FR-026 (R25).

21. `actualizar` (FR-025 enmendado, R14) vive en `actualizacion.py` y no en `cli.py`: orquesta
    uv, lee versiones del `uv.lock` y restaura el anterior, una responsabilidad distinta de la
    línea de comandos (Principio I). Tras `uv lock --upgrade-package yt-dlp` y `uv sync`
    ejecuta `uv run pytest -m ytdlp_internals`; si fallan, restaura el `uv.lock` byte a byte y
    vuelve a sincronizar. Exige ffmpeg antes de tocar nada, porque sin él los tests marcados que
    descargan se omitirían y la verificación no valdría. Fuera del proyecto sale con 1 e indica
    `uv tool upgrade downloader`.
22. `plantilla_nombre` en `config.toml` (campos `{titulo}` e `{id}`), por defecto `{titulo}`:
    el id del video sobraba en el nombre porque las colisiones ya se resuelven con sufijo
    (FR-022). En Windows la ruta completa, con el sufijo más largo, no pasa de 240 caracteres:
    margen bajo MAX_PATH (259) para programas que añaden algo a la ruta al abrirla. Si no queda
    sitio para el título, el nombre es el id (R7).
23. `publish.publicar` reintenta cada paso (enlazar, copiar, borrar el origen) hasta 3 veces,
    tras 0,2, 0,5 y 1 s, si falla con `PermissionError` de `winerror` 32 o 5 (el antivirus o el
    indexador de Windows con el archivo abierto); cualquier otro error sale enseguida. Por pasos
    y no la publicación entera, que tras enlazar crearía una segunda copia. Si el origen no se
    puede borrar, el archivo ya está publicado: se avisa, y `_limpiar_trabajo` deja lo que no
    puede borrar sin convertir la descarga completada en un fallo (R26).
24. Una lista sin entradas falla como `PERMANENTE` ("no se encontraron videos; …"). Como toda
    descarga, pasa por `downloading` mientras se extrae, porque la máquina de estados solo llega
    a `failed` desde ahí; no se intenta descargar nada.
25. Un 403 o 410 al extraer sigue siendo `PERMANENTE` (R4), con `Clasificacion.consejo`: el
    mensaje sugiere `--cookies-de` (el mismo texto que el tope de FR-026).
26. `ocultar_secretos` decide por el nombre del parámetro de la URL, dividido en palabras por
    `-`, `_` y `.`: `X-Amz-Signature`, `X-Amz-Security-Token`, `Key-Pair-Id`, `access_token`,
    `lsig`… se ocultan; `monkey`, `itag` o `X-Amz-Date`, no (R27).
27. Licencia MIT para el código propio (`LICENSE`, `license` en `pyproject.toml`). `yt-dlp[default]`
    instala `mutagen` (GPL-2.0-or-later), que no se incluye en el repositorio; el README lo
    advierte. Es una decisión del propietario del repositorio, sin asesoría legal.
28. Marcadores `slow` (más de unos 5 s) y `ytdlp_internals` (R1, R3, R4, R19, R21, R23, R24,
    R25), registrados en `pyproject.toml`. Los de `test_network_cuts.py` y
    `test_crash_recovery.py` (T016 y T017, que no se modifican) se aplican desde
    `tests/integration/conftest.py`; `tests/unit/test_marcadores.py` comprueba la lista exacta.

29. `estado` (FR-032, hallazgo A1) lee la cola sin tomar `ejecutar.lock` ni hacer transiciones,
    para poder usarse mientras `ejecutar` corre (WAL, R11). Vive en `cli.py` con una consulta de
    solo lectura en `store.py` y el formato de línea en `progress.py`; no crea `cola.db` si no
    existe. Con la base corrupta sale con 3, como `agregar`.
30. Principio V enmendado (constitución v3.0.0): el criterio de rechazo es el sistema de
    licencias. Lo comprueba un módulo nuevo, `src/downloader/drm.py`, con funciones puras: un
    analizador de los atributos de `#EXT-X-KEY` y `#EXT-X-SESSION-KEY` (comas fuera de comillas,
    cualquier orden) y la decisión DRM / no soportado / aceptado de R5. No se usa la expresión
    de yt-dlp, que depende del orden de los atributos, ni `parse_m3u8_attributes`, que es
    interno. Es un módulo aparte (Principio I: una responsabilidad, la política de cifrado, que
    se prueba sin red ni yt-dlp); `ytdl.py` lo llama sobre cada manifiesto leído, al extraer y
    al descargar, y en `process_info`, antes de pedir la clave (R5, "Dónde se aplica"). Es
    código nuevo frente a "confiar en
    `has_drm`" porque yt-dlp descarga el `AES-128` con `KEYFORMAT` de Widevine o con un PSSH, y
    pasa a ffmpeg el `SAMPLE-AES`, que no permite reanudar (FR-008).

## Complexity Tracking

Sin violaciones de la constitución que justificar.
