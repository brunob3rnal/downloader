---

description: "Lista de tareas del gestor de descargas de video resiliente"
---

# Tasks: Gestor de descargas de video resiliente

**Input**: Documentos de diseño en `/specs/001-resilient-video-downloader/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Obligatorios (Principio II de la constitución y petición del usuario). Orden
tests primero: cada test se escribe y se ejecuta en rojo antes de la tarea que lo hace pasar.
Los tests de corte de red (respuesta truncada, timeout, 5xx, servidor caído) existen y fallan
en la Fase 2, **antes** de implementar la máquina de estados, la cola persistente y la
reanudación.

**Organization**: Una fase por historia de usuario, en orden de prioridad (P1: US1, US2, US3,
US5; P2: US4; P3: US6). FR-025 (`actualizar`) no pertenece a ninguna historia y va en la fase
final.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Puede ejecutarse en paralelo (archivo distinto, sin dependencias pendientes)
- **[Story]**: Historia de usuario a la que pertenece (US1…US6)
- Todas las rutas son relativas a la raíz del repositorio
- Todos los comandos se ejecutan con `uv run`

## Convenciones para todos los tests

- Ningún test accede a internet: todo va contra `tests/support/flaky_server.py` y
  `tests/support/fake_extractor.py`.
- Los tests que necesitan ffmpeg o ffprobe llevan `@pytest.mark.ffmpeg`.
- Cada test usa un directorio de la app temporal (fixture `app_home`, variable de entorno
  `DOWNLOADER_HOME`) y una carpeta de destino temporal; nunca el directorio real del usuario.
- Los tiempos de backoff y del monitor de red se inyectan reducidos (base 0,05 s, tope 0,5 s)
  para que los tests duren segundos.
- Los tests corren en Linux y en Windows (constitución v2.1.0): rutas con `pathlib`, nunca
  comparando cadenas con `/`; los subprocesos se lanzan con `sys.executable -m downloader`; el
  cierre abrupto es `Popen.kill()` y la interrupción, `SIGINT` en Linux y `CTRL_BREAK_EVENT`
  en Windows. Un test se omite en una plataforma solo si es imposible allí, con
  `pytest.mark.skipif` y el motivo.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Inicializar el proyecto con uv (research R16)

- [X] T001 Ejecutar `uv init --package --name downloader --python 3.12` en la raíz del repositorio y verificar que crea `pyproject.toml`, `src/downloader/__init__.py` y el script de consola `downloader`; borrar el `main` de ejemplo que genere y dejar el script apuntando a `downloader.cli:app` en `pyproject.toml`
- [X] T002 Ejecutar `uv add yt-dlp typer` y `uv add --dev pytest`; comprobar en `pyproject.toml` que pytest está en el grupo de desarrollo y no en `dependencies`, y que se generó `uv.lock`
- [X] T003 [P] Configurar pytest en `pyproject.toml` (`[tool.pytest.ini_options]`): `testpaths = ["tests"]`, `addopts = "--strict-markers"`, marcador `ffmpeg: requiere ffmpeg y ffprobe en el PATH`
- [X] T004 [P] Crear `.gitignore` con `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.db`, `*.db-wal`, `*.db-shm`, `config.toml`, `cookies*.txt` (Principio VI: ningún archivo local con secretos en el repositorio)
- [X] T005 [P] Crear `.github/workflows/tests.yml` que instale uv y ffmpeg (`apt-get install -y ffmpeg`) y ejecute `uv run pytest`; el CI MUST tener ffmpeg para que los tests marcados `ffmpeg` no se omitan (research R15)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infraestructura de pruebas, tests de corte de red en rojo y núcleo (modelos,
configuración, cola, errores, reintentos, monitor de red, adaptador yt-dlp).

**⚠️ CRITICAL**: Ninguna historia empieza hasta terminar esta fase.

### 2a. Infraestructura de pruebas

- [X] T006 [P] Implementar `tests/support/flaky_server.py`: servidor HTTP en un hilo (`http.server.ThreadingHTTPServer`) en `127.0.0.1` con puerto efímero, que sirve los archivos que se le registren (bytes deterministas para sus propios tests; MP4 y HLS válidos para los tests de descarga, research R15) con soporte de `Range` (206, `Content-Range`, `Accept-Ranges: bytes`) y `Content-Length`. Modos configurables por archivo y por petición, cada uno con un número de repeticiones (`veces=K`, para superar los 3 reintentos internos de yt-dlp): `cortar_tras_bytes=N` (cierra la conexión tras N bytes: respuesta truncada), `colgarse=True` (acepta y no envía datos hasta que el cliente agote su timeout), `errores=[503, 503, 500, 503, 500]` (responde esos códigos en las primeras peticiones), `sin_range=True` (ignora `Range` y sirve 200 completo, sin `Accept-Ranges`), `tokens=True` (exige `?token=` válido y responde 403 a tokens caducados), `anunciar_tamano=X` (anuncia un `Content-Length` distinto del real), `fragmento_ausente=i` (responde 404 siempre al fragmento HLS `i`). Métodos `detener()` y `reanudar()` en el mismo puerto. Contador `bytes_servidos` por archivo
- [X] T007 [P] Implementar `tests/support/fake_extractor.py`: subclase de `yt_dlp.extractor.common.InfoExtractor` con `_VALID_URL` para `http://127.0.0.1:<puerto>/watch/<id>` y `http://127.0.0.1:<puerto>/playlist/<id>`. Cada extracción devuelve la URL de medios del `flaky_server` con un token nuevo. Escenarios por `<id>`: video normal, `privado` y `borrado` (`ExtractorError(expected=True)` con mensaje claro), `directo` (`is_live=True`), `drm` (formatos con `has_drm=True`), `hls` (manifiesto m3u8 local de varios fragmentos), y una lista con tres entradas, una de ellas `borrado`. Función `registrar(ydl)` que lo añade con `ydl.add_info_extractor`
- [X] T008 Crear `tests/conftest.py` y `tests/integration/conftest.py` (depende de T006, T007): omitir los tests marcados `ffmpeg` si `shutil.which("ffmpeg")` o `shutil.which("ffprobe")` es `None`; en `tests/integration/conftest.py`, marcar automáticamente con `ffmpeg` todo test de integración que descargue (todos salvo `test_cli_agregar.py`, `test_actualizar.py` y `test_ffmpeg_ausente.py`); fixture de sesión `medios` que genera con `ffmpeg -f lavfi` un MP4 válido de unos 2 MB y una versión HLS de 5 fragmentos; fixture `cli` que ejecuta la app Typer en proceso con `typer.testing.CliRunner` registrando `fake_extractor` mediante `ytdl.crear_ydl`; fixtures `app_home` (directorio temporal en `DOWNLOADER_HOME`), `destino` (carpeta temporal), `servidor` (inicia y detiene `flaky_server`), `monitor_falso` (monitor de red controlable a mano con `poner_en_linea()` / `poner_sin_conexion()`) y `tiempos_rapidos` (backoff base 0,05 s, tope 0,5 s, `socket_timeout` 1 s)
- [X] T009 [P] Escribir `tests/unit/test_flaky_server.py`: comprueba con `urllib` cada modo de T006 (Range parcial, corte tras N bytes, cuelgue hasta timeout, 503 y luego 200, sin Range, 403 por token caducado, detener/reanudar, `bytes_servidos`)

### 2b. Tests que MUST existir y fallar antes de la implementación del núcleo

- [X] T010 [P] Escribir `tests/unit/test_models.py`: las transiciones permitidas son exactamente las de la tabla de `data-model.md` (`— → queued`, `failed → queued`, `queued → downloading`, `downloading → waiting_network`, `waiting_network → downloading`, `downloading → completed`, `downloading → failed`, `downloading`/`waiting_network → queued`) y cualquier otra lanza error; `PENDIENTES == {queued, downloading, waiting_network}`; las etiquetas visibles son "en cola", "descargando", "esperando red", "completada", "fallida"; `Calidad` admite solo `mejor`, `1080p`, `720p`, `audio`
- [X] T011 [P] Escribir `tests/unit/test_store.py`: crea el esquema de `descargas` con todos los campos de `data-model.md`; `PRAGMA journal_mode` es `wal` y `synchronous` es `FULL` (2); restricción "Única entre las filas no fallidas: (`url`, `calidad`)": agregar una pareja existente en `queued`/`downloading`/`waiting_network`/`completed` no crea fila y devuelve el estado existente, y si la existente está en `failed`, vuelve a `queued` conservando su `id`; `recuperar()` devuelve a `queued` las filas en `downloading` o `waiting_network`; "`archivo_final` no nulo ⇔ `estado = completed` y `tipo = video`"; base corrupta (bytes aleatorios en `cola.db`) → se copia a `cola.db.corrupta-<fecha>`, se lanza un error FATAL claro y no se crea una cola nueva; un segundo bloqueo de `ejecutar.lock` falla mientras el primero está activo y se libera al cerrar
- [X] T012 [P] Escribir `tests/unit/test_retry.py`: la espera es `min(300, 2 * 2**n)` s con variación de ±20 % (comprobar límites con semilla fija); nunca supera 300 s para ningún `n` (FR-010); `n` vuelve a 0 tras un avance de bytes
- [X] T013 [P] Escribir `tests/unit/test_errors.py`: clasificación de research R4 — `TransportError`, `TimeoutError`, `ConnectionError`, HTTP 500/503 y 403/410 durante la descarga → `TEMPORAL`; cualquier error con el monitor "sin conexión" → `TEMPORAL`; `UnsupportedError`, `ExtractorError(expected=True)`, directo, DRM, carpeta de destino inexistente o sin permisos → `PERMANENTE`; `OSError(ENOSPC)`, base corrupta, ffmpeg o ffprobe ausentes → `FATAL`; cualquier otra excepción (p. ej. `DownloadError` por un fragmento ausente) → `PERMANENTE` con el mensaje original filtrado, salvo con el monitor "sin conexión"; recorre la cadena `exc_info`/`cause`; `ocultar_secretos()` elimina cabeceras `Cookie`/`Authorization` y parámetros `token`, `sig`, `signature`, `key`, `auth` de las URLs
- [X] T014 [P] Escribir `tests/unit/test_network.py`: el monitor marca "en línea" si alguna sonda TCP conecta y "sin conexión" si fallan todas, usando un socket local que se abre y se cierra; expone un `threading.Event`; se detiene limpiamente
- [X] T015 [P] Escribir `tests/unit/test_ytdl.py`: las opciones de `YoutubeDL` incluyen `continuedl=True`, `nopart=False`, `skip_unavailable_fragments=False`, `retries=3`, `fragment_retries=3`, `socket_timeout=20` (por defecto; parametrizable para tests), `noplaylist=True`, `quiet=True` y un `logger` propio; `skip_unavailable_fragments` es exactamente `False` (no ausente: su valor por defecto en yt-dlp es `True`, research R1); nunca incluyen `cookiefile`; sin navegador no incluyen `cookiesfrombrowser`; los selectores de formato son exactamente los de research R6 (`mejor`: `bv*+ba/b`; `1080p`: `bv*[height<=1080]+ba/b[height<=1080]/wv*+ba/w`; `720p`: `bv*[height<=720]+ba/b[height<=720]/wv*+ba/w`; `audio`: `ba/b` con `FFmpegExtractAudio`); la plantilla de salida es `%(id)s.%(ext)s` dentro de `<carpeta_destino>/.downloader-tmp/<id_descarga>/`
- [X] T016 [P] Escribir `tests/integration/test_network_cuts.py` (escenarios 2, 2b y 3 de quickstart; US2), marcando todo el módulo con `pytest.mark.xfail(strict=True, reason="pendiente de la Fase 4 (US2)")` e importando `downloader` dentro de cada test (no al nivel del módulo, para que la falta de implementación cuente como xfail y no como error de colección). Cada caso falla más veces seguidas que los 3 reintentos internos de yt-dlp y comprueba que la fila pasó por `waiting_network`: (a) el servidor corta al 40 % cuatro veces seguidas → la descarga pasa por `waiting_network`, se reanuda y termina con un archivo idéntico byte a byte al original y `bytes_servidos` ≤ 101 % del tamaño (SC-002); (b) el servidor se cuelga → timeout tratado como `TEMPORAL`, termina íntegra; (c) el servidor responde cinco errores 503/500 seguidos → `TEMPORAL`, termina íntegra; (d) el servidor se detiene con `monitor_falso` sin conexión → la fila permanece en `waiting_network` sin consumir intentos hasta `poner_en_linea()` + `reanudar()`, y entonces termina íntegra; (e) cortes repetidos en tres momentos distintos de la misma descarga → termina íntegra. Ejecuta el bucle de `downloader.worker` en proceso con `monitor_falso` y `tiempos_rapidos`
- [X] T017 [P] Escribir `tests/integration/test_crash_recovery.py` (escenario 4 de quickstart; US3), marcado con `pytest.mark.xfail(strict=True, reason="pendiente de la Fase 5 (US3)")` e importando `downloader` dentro del test: escribe en `DOWNLOADER_HOME` un `config.toml` con `sondas_red = ["127.0.0.1:<puerto del servidor>"]` (sin internet), agrega la URL directa del MP4 del servidor (la resuelve el extractor genérico de yt-dlp, ya que el subproceso no registra `fake_extractor`), lanza `uv run downloader ejecutar` en un subproceso con el servidor sirviendo lentamente y espera cada `ejecutar` con `timeout=60` (un bucle sin recuperación no debe colgar la suite), envía `SIGKILL` a mitad (desde T035a, `python -m downloader` y `Popen.kill()`, que funcionan también en Windows), comprueba que la fila quedó en `downloading` y que el `.part` existe; relanza `ejecutar` y comprueba que termina con código 0, el archivo publicado es íntegro y `bytes_servidos` ≤ 101 % del tamaño + 128 KiB (SC-003; SC-002 enmendado para interrupciones abruptas, research R20); las descargas ya `completed` antes del cierre no se vuelven a pedir al servidor
- [X] T018 Ejecutar `uv run pytest tests/ --continue-on-collection-errors -rxX` y confirmar: T009 (`tests/unit/test_flaky_server.py`) pasa; T010–T015 (`tests/unit/test_models.py` … `tests/unit/test_ytdl.py`) fallan por importación o aserción; T016 y T017 (`tests/integration/test_network_cuts.py`, `tests/integration/test_crash_recovery.py`) se informan como `xfailed`, nunca como error de colección ni `xpassed`. Anotar el resultado en el commit

### 2c. Implementación del núcleo

- [X] T019 [P] Implementar `src/downloader/models.py`: `Estado` (enum `queued`, `downloading`, `waiting_network`, `completed`, `failed`), `ETIQUETAS`, `PENDIENTES`, `TRANSICIONES` y `validar_transicion(desde, hacia)` según `data-model.md`; `Calidad` (`mejor`, `1080p`, `720p`, `audio`); dataclass `Descarga` con los campos de la tabla de `data-model.md`. Hace pasar T010
- [X] T020 [P] Implementar `src/downloader/config.py`: directorio de la app = `DOWNLOADER_HOME` si existe, si no `typer.get_app_dir("downloader")`; lee `config.toml` con `tomllib`; claves `carpeta_destino` (por defecto "`~/Descargas` o `~/Downloads` si existe, si no `~`"), `calidad` (por defecto `mejor`), `sondas_red` (por defecto `["1.1.1.1:443", "8.8.8.8:443"]`); "No admite claves de credenciales ni cookies; una clave desconocida produce un error claro"
- [X] T021 Implementar `src/downloader/store.py` (depende de T019, T020): abre `cola.db` en modo WAL con `synchronous=FULL`, ejecuta `PRAGMA quick_check` y, si falla o hay `DatabaseError`, copia a `cola.db.corrupta-<fecha>` y lanza error FATAL; crea el esquema; `agregar()` con la regla de unicidad y el reencolado de `failed`; `transicion()` en una transacción validada con `models.validar_transicion`; `guardar_progreso()` (como mucho cada 2 s); `recuperar()`; `siguiente()` (la `queued` de menor `id`); `hay_pendientes()`; `bloquear_ejecucion()` con `fcntl.flock` sobre `ejecutar.lock`. Hace pasar T011
- [X] T022 [P] Implementar `src/downloader/retry.py`: `espera(n, base=2, tope=300, rng=random)` = `min(tope, base * 2**n)` ± 20 %. Hace pasar T012
- [X] T023 [P] Implementar `src/downloader/errors.py`: enum `Tipo` (`TEMPORAL`, `PERMANENTE`, `FATAL`), `clasificar(exc, en_linea)` según research R4 y `ocultar_secretos(texto)`. Hace pasar T013
- [X] T024 [P] Implementar `src/downloader/network.py`: clase `MonitorRed(sondas, intervalo=5, timeout=3)` con hilo daemon, `threading.Event` `en_linea`, `iniciar()`, `detener()`, y un protocolo común que cumpla también `monitor_falso`. Hace pasar T014
- [X] T025 Implementar `src/downloader/ytdl.py` (depende de T023): `crear_ydl(opciones)` como único punto donde se instancia `YoutubeDL` (los tests lo sustituyen para registrar `fake_extractor`); `opciones(descarga, directorio_trabajo, navegador, hook, socket_timeout=20)` con las opciones de research R1 y R6, `logger` que pasa todo mensaje por `errors.ocultar_secretos`; `extraer_plano(url)` con `extract_flat='in_playlist'` y `noplaylist=True`; `descargar(descarga, ...)` que llama a `extract_info(url, download=True)` y traduce el hook de progreso a `(bytes, total, velocidad, eta, archivo, estado)`. Hace pasar T015

**Checkpoint**: T009–T015 en verde; T016 y T017 en `xfailed` hasta las fases de US2 y US3. Un `xpassed` en esta fase es un fallo (`strict=True`): indica que el test no prueba nuestros reintentos.

---

## Phase 3: User Story 1 - Descargar videos desde una cola (Priority: P1) 🎯 MVP

**Goal**: `agregar` encola URLs y `ejecutar` las descarga una a una, muestra progreso,
velocidad y ETA, expande listas y publica sin sobrescribir.

**Independent Test**: escenario 1 de quickstart — `agregar` dos URLs del extractor de prueba
con `--calidad 720p` y `ejecutar`: ambos archivos aparecen en la carpeta, la salida muestra
porcentaje, velocidad y ETA, y el código de salida es 0.

### Tests for User Story 1 ⚠️

> Escribir primero y comprobar que fallan.

- [X] T026 [P] [US1] Escribir `tests/integration/test_cli_agregar.py` (contracts/cli.md): `agregar URL1 URL2 --calidad 720p` imprime `agregada #N  <url>  (720p)` por URL y sale con 0; repetir una URL pendiente imprime `ya en cola #N` y sale con 0; una URL `failed` imprime `reencolada #N` y sale con 0; calidad desconocida, carpeta inexistente o sin permisos y URL vacía salen con 2; `agregar` funciona sin red (servidor detenido); con `cola.db` corrupta sale con 3 (edge case de la spec)
- [X] T027 [P] [US1] Escribir `tests/unit/test_publish_nombres.py` (FR-022, escenario 8 de quickstart): publicar en una carpeta donde ya existe `Título [id].mp4` crea `Título [id] (1).mp4` y luego `(2)`, el original queda intacto byte a byte, se devuelve un aviso; dos publicaciones simultáneas con el mismo nombre no se pisan; el nombre final no existe en la carpeta antes de publicar; con `os.link` forzado a fallar con `OSError(EXDEV)` y con `OSError(EPERM)`, la alternativa `O_CREAT | O_EXCL` + copia publica igual sin sobrescribir; `os.rename` y `os.replace` sustituidos por funciones que lanzan error nunca se llaman
- [X] T028 [P] [US1] Escribir `tests/integration/test_descarga_basica.py` (escenario 1 de quickstart, FR-001, FR-002, FR-005, FR-006, SC-006, SC-007): dos URLs agregadas y `ejecutar` → dos archivos publicados en `destino` como `<titulo> [<id_video>].<ext>`, filas en `completed` con `archivo_final`, `.downloader-tmp/<id>/` borrado tras publicar, la salida (no es una terminal: fixture `cli`) contiene líneas `descargando  NN.N%  X MiB/s  ETA hh:mm:ss` con marcas de tiempo separadas como mucho 1 s mientras dura la descarga, y el código de salida es 0
- [X] T029 [P] [US1] Escribir `tests/integration/test_listas.py` (US1 escenario 5, FR-004, research R13): agregar `/playlist/<id>` crea al procesarla tres hijas con la misma calidad y carpeta y `padre_id`, la padre queda `completed` con `tipo=playlist`; la entrada `borrado` queda `failed` y las otras dos se completan (edge case "Lista de reproducción con videos no disponibles"); una URL `watch?v=…&list=…` descarga solo el video
- [X] T030 [P] [US1] Escribir `tests/integration/test_salida_ejecutar.py` (FR-021) con la fixture `cli`: `ejecutar` con todas completadas sale con 0; `ejecutar` recoge una URL agregada desde otro proceso mientras corre y no termina hasta completarla; un segundo `ejecutar` simultáneo sale con 2 con un mensaje claro; con la cola vacía desde el inicio sale con 0
- [X] T031 [P] [US1] Escribir `tests/unit/test_calidad.py` (edge case "La calidad pedida no existe"): con formatos solo hasta 480p y `--calidad 720p` se elige 480p y se guarda un `ultimo_aviso`; con solo 1440p se usa la alternativa `wv*+ba/w` y se avisa

### Implementation for User Story 1

- [X] T032 [P] [US1] Implementar en `src/downloader/publish.py` la función `publicar(origen, carpeta, nombre)`: `os.link` + `os.unlink`, y ante `FileExistsError` probar `nombre (1).ext`, `nombre (2).ext`…; si el sistema de archivos no admite enlaces duros, `os.open(destino, O_CREAT | O_EXCL)` + copia + `fsync` + borrado del origen (research R7); nunca `os.rename` ni `os.replace`. Hace pasar T027
- [X] T033 [P] [US1] Implementar `src/downloader/progress.py`: una línea por descarga con las etiquetas de `models.ETIQUETAS` y los formatos de contracts/cli.md (`#12 descargando  43.1%  3.2 MiB/s  ETA 00:01:52  Título`, `#14 completada  <ruta>`, `#15 fallida  <causa>`, `#16 aviso  <texto>`), refresco ≥ 1 vez/s con `rich.live` cuando la salida es una terminal y, si no lo es (`Console.is_terminal` falso: `rich.live` solo imprimiría el estado final), una línea nueva por descarga activa como mucho cada segundo; resumen final con completadas, fallidas y causa de cada fallo
- [X] T034 [US1] Implementar `src/downloader/worker.py` (depende de T021, T025, T032, T033): bucle `ejecutar(store, monitor, presentador, tiempos)` que toma `store.siguiente()`, pasa a `downloading`, extrae en plano; si es lista inserta las hijas y marca la padre `completed`/`playlist`; si es video descarga en `<carpeta>/.downloader-tmp/<id>/`, publica con `publish.publicar`, guarda `archivo_final` y pasa a `completed`; repite hasta que `store.hay_pendientes()` sea falso (FR-021). Hace pasar T028, T029 y T031
- [X] T035 [US1] Implementar `src/downloader/cli.py` (depende de T034): app Typer con `agregar URL... [--calidad] [--carpeta]` y `ejecutar`; validación de opciones y carpeta; bloqueo de un único `ejecutar` (`store.bloquear_ejecucion`); códigos de salida 0, 2 y 3 de contracts/cli.md; errores a stderr pasados por `errors.ocultar_secretos`. Hace pasar T026 y T030

**Checkpoint**: US1 completa y demostrable con el escenario 1 de quickstart.

### Compatibilidad con Windows 10/11 (añadida tras el MVP)

- [X] T035a Windows 10/11 como plataforma objetivo junto a Linux (constitución v2.1.0; spec, Assumptions; plan, decisiones 9–12; research R7 y R17). Origen: en Windows, `uv run downloader` fallaba con `ModuleNotFoundError: No module named 'fcntl'`. Tests primero: `tests/unit/test_plataforma.py` importa todos los módulos de `downloader` y falla si en `src/` o `tests/` hay un `import fcntl` (u otro módulo o atributo de un solo sistema: `termios`, `msvcrt`, `signal.SIGKILL`, `os.fork`, `os.getuid`…) sin protección; en `tests/unit/test_store.py`, la rama de Windows del bloqueo con un `msvcrt` simulado; en `tests/unit/test_publish_nombres.py`, respaldo sin enlaces duros con `winerror` de Windows, otro `OSError` de `os.link` que no se oculta, publicación en volúmenes FAT32/exFAT reales (`DOWNLOADER_TEST_SIN_ENLACES`, los crea el CI), nombres sin `: ? | " < > *`, sin nombres reservados y dentro de 255 bytes y 259 caracteres de ruta, y `publish.escribible`; `tests/integration/test_ctrl_c.py`: interrumpir `ejecutar` a mitad (`SIGINT` o `CTRL_BREAK_EVENT`) lo termina sin traza, conserva el `.part`, no publica nada y libera el bloqueo; en `tests/integration/test_cli_agregar.py`, salida redirigida en cp1252 con caracteres que no caben; `tests/integration/test_crash_recovery.py` usa `python -m downloader` y `Popen.kill()` en lugar de `uv run`, `start_new_session` y `os.killpg(…, SIGKILL)`; `tests/unit/test_ytdl.py` compara rutas con `pathlib`. Implementación: `src/downloader/store.py` con `msvcrt.locking`/`fcntl.flock` por import condicional; `src/downloader/publish.py` con `_sin_enlaces` (errno y `winerror`), `nombre_publicado` con nombres reservados y límites de longitud, y `escribible` en lugar de `os.access` (usado por `cli.py` y `worker.py`); `src/downloader/ytdl.py` con `windowsfilenames=True` y plantilla `descarga.%(ext)s`; `src/downloader/progress.py` sin `/` a mano; `src/downloader/cli.py` con Ctrl+Break como Ctrl+C y `stdout`/`stderr` con `errors="backslashreplace"`; `src/downloader/__main__.py`; `.github/workflows/tests.yml` con matriz `ubuntu-latest` y `windows-latest`, ffmpeg en ambos (`apt-get`, `choco`) y volúmenes FAT32/exFAT

---

## Phase 4: User Story 2 - Sobrevivir a cortes de red (Priority: P1)

**Goal**: los errores temporales llevan a `waiting_network`, se reintenta indefinidamente con
backoff (tope 5 min), se reanuda en cuanto vuelve la red y se avisa si el servidor obliga a
reiniciar.

**Independent Test**: escenarios 2, 2b, 3 y 11 de quickstart (`tests/integration/test_network_cuts.py`
y `tests/integration/test_sin_reanudacion.py`) en verde.

### Tests for User Story 2 ⚠️

- [X] T036 [P] [US2] Escribir `tests/integration/test_sin_reanudacion.py` (escenario 11 de quickstart, FR-024, research R3) con dos casos: (a) el servidor corta al 40 % una vez y luego ignora `Range`, de modo que yt-dlp reinicia dentro de la misma llamada; (b) el servidor corta al 40 % más veces que los reintentos internos y luego ignora `Range`, de modo que el reinicio ocurre en un intento nuestro posterior. En ambos, la descarga se reinicia desde cero, termina íntegra, `ultimo_aviso` contiene "reiniciada desde cero: el servidor no permite reanudar" y la salida muestra la línea `#N aviso  reiniciada desde cero: el servidor no permite reanudar`; con un servidor que sí admite `Range` no aparece ese aviso
- [X] T037 [P] [US2] Escribir `tests/unit/test_worker_espera.py`: durante la espera de backoff la fila está en `waiting_network` con `proximo_intento` y `ultimo_error` filtrado; si `monitor_falso.poner_en_linea()` ocurre antes de `proximo_intento`, el reintento empieza de inmediato; `intentos_sin_avance` vuelve a 0 tras progreso y crece sin progreso; nunca se pasa a `failed` por un error `TEMPORAL` no ambiguo (FR-010, SC-001); tres errores ambiguos seguidos sin avance sí pasan a `failed` con el mensaje de `retry.TopeSinAvance` (FR-026)

### Implementation for User Story 2

- [X] T037a [US2] Registro de la última petición para los `DownloadError` sin causa (research R4, decisión 6 del plan). Tests primero: en `tests/unit/test_errors.py`, un caso de `clasificar` por cada fila de la tabla de R4 (sin conexión; transporte, timeout y cuerpo incompleto; 5xx y 429; 404, 410 y 403; petición correcta, ninguna o sin registro), que la causa encadenada prevalece sobre el registro y que `FATAL` prevalece; en `tests/integration/test_registro_peticiones.py`, contra `flaky_server` y con más fallos que los reintentos de yt-dlp: corte repetido → `TEMPORAL`, 503 repetido → `TEMPORAL`, fragmento HLS 404 → `PERMANENTE`, y el registro empieza vacío en cada `YoutubeDL`. Implementación: en `src/downloader/ytdl.py`, `YoutubeDLRegistrado` sobrescribe `urlopen`, registra código HTTP o excepción y vigila la lectura del cuerpo, relanza el error, y `crear_ydl` la usa; `descargar` y `extraer_plano` adjuntan el registro al `DownloadError` como `ultima_peticion`; `tests/support/fake_extractor.crear_ydl` usa la misma subclase; en `src/downloader/errors.py`, `UltimaPeticion` y la rama de `clasificar` para `DownloadError` sin causa (su tabla de códigos quedó sustituida por la de T037b)
- [X] T037b [US2] Tabla única de clasificación por fase y tope de errores ambiguos (research R4, FR-026, decisiones 6–8 del plan). Tests primero: en `tests/unit/test_errors.py`, cada fila de la tabla de R4 en extracción y en descarga, con la señal tomada de la cadena y del registro (transporte, timeout, conexión, cuerpo incompleto, 429, 5xx → `TEMPORAL`; 404 → `PERMANENTE`; 403/410 → `PERMANENTE` al extraer y `TEMPORAL` ambiguo al descargar), sin conexión → `TEMPORAL`, registro correcto o vacío → `PERMANENTE` salvo descargador externo (`TEMPORAL` ambiguo), y las prioridades (FATAL, cadena sobre registro, permanente reconocido sobre registro); en `tests/unit/test_retry.py`, `TopeSinAvance` (falla al tercer ambiguo seguido sin bytes nuevos, mensaje con `--cookies-de` para 403/410, el avance por archivo reinicia la racha, cortes y 5xx no cuentan); en `tests/unit/test_ytdl.py`, `hls_prefer_native=True` y la detección de fase y descargador externo en `dl`; en `tests/integration/test_registro_peticiones.py`, contra `flaky_server`: token HLS que caduca a mitad y se recupera al volver a extraer sin repetir fragmentos, 403 constante que falla tras 3 intentos sin avance, 403 con avance que no agota el tope, 429 con causa y sin causa → `TEMPORAL`, más los casos de T037a (corte, 503, fragmento 404). Implementación: dependencia `yt-dlp[default]`; en `src/downloader/errors.py`, `Fase`, `RegistroRed`, `Clasificacion` y `clasificar_detalle`; en `src/downloader/ytdl.py`, `YoutubeDLRegistrado` con `hls_prefer_native=True` y `dl` sobrescrito, y el registro adjunto como `error.registro`; en `src/downloader/retry.py`, `TopeSinAvance` y `Decision`; en `tests/support/flaky_server.py`, tokens en fragmentos HLS y `programar(caducar_tokens=True)`
- [X] T038 [US2] Ampliar `src/downloader/worker.py`: clasificar cada fallo con `errors.clasificar_detalle` (con `monitor.en_linea`) y pasarlo por un `retry.TopeSinAvance` por descarga, alimentado con el progreso de cada intento; si la decisión es `PERMANENTE`, pasar a `failed` con su mensaje; ante `TEMPORAL` pasar a `waiting_network`, calcular `proximo_intento` con `retry.espera(intentos_sin_avance)`, esperar sobre el `Event` del monitor con timeout hasta `proximo_intento`, volver a `downloading` y re-extraer (research R2); reiniciar `intentos_sin_avance` al detectar avance de bytes. Quitar el `xfail` de `tests/integration/test_network_cuts.py`. Hace pasar T016 y T037
- [X] T039 [US2] Implementar en `src/downloader/worker.py` la detección de reinicio (FR-024, research R3): por cada archivo, llevar el máximo de bytes conocido (inicializado con el tamaño del `.part` al empezar el intento y actualizado en cada evento del hook); si un evento trae `downloaded_bytes` menor que ese máximo, guardar `ultimo_aviso` y mostrarlo con `progress` (una sola vez por reinicio). Hace pasar los dos casos de T036
- [X] T040 [US2] Ampliar `src/downloader/progress.py` con la línea `#13 esperando red  reintento en 40 s  (<causa>)` (contracts/cli.md) y cuenta atrás actualizada cada segundo
- [X] T041 [US2] Integrar `network.MonitorRed` en `src/downloader/cli.py` (`ejecutar` lo inicia con `config.sondas_red` y lo detiene al salir). Hecho junto con T035: `ejecutar` necesitaba un monitor para clasificar los errores

- [X] T041a [US2] Tests del estancamiento (research R21; escritos tras la implementación y validados con el vigilante desactivado: sin él, el caso de silencio termina por timeout y no por estancamiento, y el de goteo no termina): en `tests/support/flaky_server.py`, `programar(pausar_tras_bytes=N)` (envía N bytes y deja la conexión abierta sin datos) y `programar(gotear_tras_bytes=N, intervalo=s)` (luego un byte cada `s`); `tests/integration/test_estancamiento.py` con los dos casos, que deben terminar completados tras abortar y reanudar desde el `.part` (sin aviso de reinicio, `bytes_servidos` ≤ 101 %, `intentos_sin_avance` en 0, causa "sin datos hace" / "menos de 1 KiB/s"); en `tests/unit/test_worker_espera.py`, primera espera corta, no cuenta para el tope de ambiguos ni como intento sin avance; en `tests/unit/test_errors.py`, `SinDatos` en la cadena y en el registro; en `tests/unit/test_progress.py`, la línea "sin datos hace N s, reintentando" y el recorte al ancho; `tests/unit/test_config.py`, umbrales en `config.toml` y su paso al trabajador
- [X] T041b [US2] En `src/downloader/errors.py`, `SinDatos(TransportError)` y `Clasificacion.estancamiento`; en `src/downloader/ytdl.py`, `Vigilancia`, el vigilante en `YoutubeDLRegistrado` (último byte, ventana de velocidad, `read1`, cortocircuito de `urlopen` tras estancarse), `REINTENTOS_INTERNOS = 1` y `SOCKET_TIMEOUT = 15` en las opciones; ajustar `test_opciones_fijas` a los nuevos valores
- [X] T041c [US2] En `src/downloader/worker.py`, `Tiempos.estancamiento`, `velocidad_minima` y `espera_estancamiento` (5 s), la `Vigilancia` en cada intento y el trato del estancamiento en `_Seguimiento` y `_fallo`; en `src/downloader/config.py` y `src/downloader/cli.py`, las claves `estancamiento_segundos` y `estancamiento_velocidad_minima` (data-model.md)
- [X] T041d [US2] En `src/downloader/progress.py`, "sin datos hace N s, reintentando" tras 5 s sin bytes nuevos y cada línea activa recortada al ancho de la terminal

**Checkpoint**: T016, T036–T037, T037a, T037b y T041a en verde; US1 sigue en verde.

---

## Phase 5: User Story 3 - Cola que sobrevive a cierres y reinicios (Priority: P1)

**Goal**: la cola y el progreso sobreviven a `SIGKILL`, Ctrl+C y reinicios; `ejecutar`
continúa lo pendiente al relanzarse.

**Independent Test**: escenario 4 de quickstart (`tests/integration/test_crash_recovery.py`)
y `tests/integration/test_interrupcion.py` en verde.

### Tests for User Story 3 ⚠️

- [X] T042 [P] [US3] Escribir `tests/integration/test_interrupcion.py` (FR-020, FR-021, edge case Ctrl+C), partiendo de `tests/integration/test_ctrl_c.py` (T035a): enviar `SIGINT` (en Windows, `CTRL_BREAK_EVENT` a un grupo de procesos propio) a `ejecutar` a mitad de descarga → sale con 130, la fila vuelve a `queued`, `bytes_descargados` refleja el progreso, el `.part` se conserva; relanzar termina la descarga íntegra

### Implementation for User Story 3

- [X] T043 [US3] En `src/downloader/cli.py`, llamar a `store.recuperar()` al iniciar `ejecutar`, antes de procesar (research R11). Quitar el `xfail` de `tests/integration/test_crash_recovery.py`. Hace pasar T017
- [X] T044 [US3] En `src/downloader/worker.py` y `src/downloader/cli.py`, capturar `KeyboardInterrupt`: guardar el progreso, pasar la descarga activa a `queued`, mostrar "interrumpido; progreso guardado" y salir con 130. Hace pasar T042

**Checkpoint**: T017 y T042 en verde; US1 y US2 siguen en verde.

---

## Phase 6: User Story 5 - Integridad de los archivos entregados (Priority: P1)

**Goal**: ningún archivo incompleto aparece con su nombre final (FR-013, FR-023, SC-004).

**Independent Test**: escenario 7 de quickstart (`tests/integration/test_integridad.py`) en verde.

### Tests for User Story 5 ⚠️

- [X] T045 [P] [US5] Escribir `tests/unit/test_verificacion.py` (FR-023, research R8): tamaño en disco distinto de `total_bytes` exacto → falla; con solo `total_bytes_estimate` no se compara tamaño; `@pytest.mark.ffmpeg`: un MP4 válido generado con `ffmpeg -f lavfi` pasa `ffprobe -count_packets`, el mismo truncado a la mitad falla, y un archivo sin paquetes o con duración 0 falla
- [X] T046 [P] [US5] Escribir `tests/integration/test_integridad.py` (escenario 7 de quickstart, US5, SC-004; enmendada según research R22): (a) el servidor anuncia un tamaño mayor que el servido → indistinguible de un corte repetido: la descarga sigue reintentando en `waiting_network`, nunca pasa a `failed` ni publica nada y conserva el `.part`; (a′) el servidor sirve el archivo truncado con su tamaño truncado anunciado → la descarga queda `failed` con mensaje claro, sin reintentos, el nombre final no aparece en `destino` y los archivos del intento están en `.downloader-tmp/<id>/invalidos/<fecha>/`; (b) `@pytest.mark.ffmpeg` HLS con un fragmento cortado en tránsito más veces que los reintentos de yt-dlp → pasa por `waiting_network` y termina con los mismos paquetes que el original (el fragmento truncado en origen es un límite conocido: spec, Edge Cases); (c) reencolar con `agregar` la descarga fallida de (a′) descarga de nuevo, no da por buenos los archivos de `invalidos/` y no los borra al publicar; (d) durante una descarga interrumpida, en `destino` solo existe `.downloader-tmp/`; (e) HLS con `fragmento_ausente=2` → yt-dlp no se salta el fragmento (`skip_unavailable_fragments=False`), la descarga queda `failed` con la causa y el nombre final no aparece en `destino`
- [X] T047 [P] [US5] Escribir `tests/integration/test_ffmpeg_ausente.py`: con `PATH` sin ffmpeg ni ffprobe, `ejecutar` sale con 3, mensaje claro, sin tocar la cola

### Implementation for User Story 5

- [X] T048 [US5] Implementar en `src/downloader/publish.py` la función `verificar(formatos_terminados, archivo_final)`: comparación de tamaño por formato con `total_bytes` exacto y `ffprobe -v error -count_packets -show_entries format=duration:stream=nb_read_packets` sobre el archivo final (sin errores, paquetes en todos los flujos, duración > 0); y `apartar_invalidos(directorio_trabajo)` que mueve los archivos a `invalidos/<fecha>/` sin borrar nada. Hace pasar T045
- [X] T049 [US5] En `src/downloader/worker.py`, anotar cada formato terminado con `publish.formato_terminado` en el hook, verificar antes de publicar; si falla, `apartar_invalidos`, pasar a `failed` con la causa y seguir con la siguiente descarga; tras publicar, borrar el directorio de trabajo salvo `invalidos/`. En `tests/unit/test_worker_espera.py`, la fixture sustituye `publish.verificar` (su yt-dlp falso no escribe un video) y dos tests comprueban que se verifica antes de publicar y que un fallo de verificación no publica y aparta los archivos. Hace pasar T046
- [X] T050 [US5] En `src/downloader/cli.py`, comprobar `shutil.which("ffmpeg")` y `shutil.which("ffprobe")` al iniciar `ejecutar` y salir con 3 si faltan. Hace pasar T047

**Checkpoint**: escenario 7 en verde; US1–US3 siguen en verde.

### Corrección: HTTP 416 al reanudar (añadida tras la Fase 6)

- [X] T050a Tests del 416 al reanudar (research R23), escritos y en rojo antes de T050b: `tests/integration/test_rango_no_satisfacible.py` con un `.part` dejado en el directorio de trabajo antes de `ejecutar`: (a) `.part` completo y servidor que responde 416 con `Content-Range: bytes */TOTAL` → publicado e íntegro, una sola petición de medios y `bytes_servidos` ≤ 1 %; el mismo `.part` del tamaño correcto pero dañado → `failed` por la verificación y archivos en `invalidos/`; (b) `.part` mayor que el archivo, por 50 bytes (dentro de la tolerancia de yt-dlp) y por 5000 → reinicio desde cero con aviso "el parcial … es mayor que el archivo del servidor", publicado íntegro; (c) 416 sin `Content-Range` dos veces → se reintenta como ambiguo y termina, y persistente → `failed` tras 3 intentos sin avance con "HTTP 416", conservando el `.part`. En `tests/unit/test_errors.py`, el 416 en la tabla de R4 (extracción `PERMANENTE`, descarga `TEMPORAL` ambiguo). `tests/integration/test_integridad.py` (T046 a) vuelve a terminar con el servidor corregido: la descarga se completa íntegra con `bytes_servidos` ≤ 101 %. En `tests/integration/test_ffmpeg_ausente.py`, el mensaje del código 3 indica cómo instalar ffmpeg en Windows, macOS y Linux; `tests/unit/test_config.py::test_ejecutar_pasa_los_umbrales_al_trabajador` aísla la comprobación de ffmpeg (la suite sin ffmpeg en el `PATH` da 0 fallos)
- [X] T050b En `src/downloader/ytdl.py`, `YoutubeDLRegistrado.urlopen` resuelve el 416 de la fase de descarga según R23 (`_Tras416`, `_rango_no_satisfacible`) y `Progreso.motivo_reinicio`; en `src/downloader/errors.py`, el 416 al descargar es `TEMPORAL` ambiguo; en `src/downloader/worker.py`, el aviso de reinicio con su motivo; en `src/downloader/cli.py`, instrucciones de instalación de ffmpeg por sistema en el error del código 3. Hace pasar T050a

---

## Phase 7: User Story 4 - Errores permanentes claros y URLs caducadas (Priority: P2)

**Goal**: errores permanentes sin reintentos y con causa clara; URLs caducadas renovadas sin
perder progreso; directos y DRM rechazados.

**Independent Test**: escenarios 5, 6 y 10 de quickstart en verde.

### Tests for User Story 4 ⚠️

- [X] T051 [P] [US4] Escribir `tests/integration/test_url_caducada.py` (escenario 5 de quickstart, FR-012, SC-008): con `tokens=True`, el servidor corta al 40 % e invalida el token; la descarga recibe 403, re-extrae con token nuevo, continúa el mismo `.part` (`bytes_servidos` ≤ 101 %) y termina íntegra sin intervención
- [X] T052 [P] [US4] Escribir `tests/integration/test_errores_permanentes.py` (escenario 6 de quickstart, FR-011, FR-018, FR-021, SC-005): `privado`, `borrado` y una URL no soportada quedan `failed` en < 30 s con la causa en `ultimo_error`, sin reintentos (el servidor no recibe más peticiones de medios); otra URL válida de la misma cola se completa; `ejecutar` sale con 1 y el resumen lista la causa de cada fallo; una carpeta de destino borrada tras `agregar` hace fallar solo esa descarga; un 403 constante al descargar queda `failed` tras 3 intentos sin avance con un `ultimo_error` que menciona `--cookies-de` y la cola sigue (FR-026)
- [X] T053 [P] [US4] Escribir `tests/integration/test_directo_drm.py` (escenario 10 de quickstart, FR-015, Principio V): `directo` y `drm` quedan `failed` con mensaje claro y el servidor no recibe ninguna petición de medios

### Implementation for User Story 4

- [X] T054 [US4] En `src/downloader/ytdl.py`, rechazar antes de descargar con `PERMANENTE` si `info['is_live']` es verdadero o si yt-dlp informa DRM (research R5); en `src/downloader/worker.py`, ante `PERMANENTE` pasar a `failed` con la causa filtrada y continuar con la siguiente. La clasificación de 403/410 (`PERMANENTE` al extraer; `TEMPORAL` ambiguo al descargar, con re-extracción en el siguiente intento y tope de 3 intentos sin avance, FR-026) ya está en `errors.py` y `retry.py` (T037b): aquí solo se comprueba de extremo a extremo que un 403 constante deja la fila `failed` con un `ultimo_error` que menciona `--cookies-de` y que el resto de la cola sigue. Hace pasar T051–T053
- [X] T055 [US4] En `src/downloader/cli.py`, salir con 1 cuando la cola se vacía con al menos una `failed` en esta ejecución (contracts/cli.md, FR-021). Hace pasar la parte de código de salida de T052

**Checkpoint**: escenarios 5, 6 y 10 en verde; todo lo anterior sigue en verde.

### Corrección: archivos abiertos y carpetas repetidas en Windows (añadida tras la Fase 7)

- [X] T055a Tests (research R24), escritos y en rojo antes de T055b. Origen: el CI de Windows del PR #8, donde `tests/integration/test_integridad.py::test_fragmento_hls_cortado_en_transito_se_reintenta_y_termina_integro` fallaba con `WinError 32` al renombrar el `.part` y `tests/unit/test_verificacion.py::test_apartar_dos_veces_conserva_los_anteriores` a veces con `FileExistsError`. En `tests/integration/test_integridad.py`, con el recolector de ciclos desactivado, un HLS con un fragmento cortado más veces que los reintentos de yt-dlp empieza cada intento sin ningún archivo del directorio de trabajo abierto (comprobado por `fstat` de los objetos de archivo vivos, igual en Linux y en Windows); en `tests/unit/test_verificacion.py`, `apartar_invalidos` dos veces con el reloj fijado en el mismo instante crea dos carpetas distintas
- [X] T055b En `src/downloader/worker.py`, `gc.collect()` antes de cada intento de descarga; en `src/downloader/publish.py`, `apartar_invalidos` añade `-1`, `-2`… si ya existe la carpeta de esa fecha. Hace pasar T055a

---

## Phase 8: User Story 6 - Configuración y cookies del navegador (Priority: P3)

**Goal**: carpeta de destino configurable y cookies del navegador solo con `--cookies-de`.

**Independent Test**: escenario 9 de quickstart y los tests de configuración en verde.

### Tests for User Story 6 ⚠️

- [X] T056 [P] [US6] Escribir `tests/unit/test_config.py` (FR-006): valores por defecto; `config.toml` con `carpeta_destino` y `calidad` aplicados; `--carpeta` y `--calidad` prevalecen; clave desconocida o con nombre de credencial o cookie (`cookies`, `password`, `token`) → error claro. En rojo: las claves de credenciales o cookies (el error no lo explicaba) y `carpeta_destino` que no es texto (`TypeError`). Los valores por defecto y la precedencia ya pasaban (implementados en T035): comprobados con dos mutaciones de `cli.py` (sin la configuración como valor por defecto; la configuración por encima de las opciones), que los hacen fallar
- [X] T057 [P] [US6] Escribir `tests/integration/test_cookies.py` (escenario 9 de quickstart, FR-016, FR-017, Principio VI): sin `--cookies-de` las opciones de yt-dlp no llevan `cookiesfrombrowser` ni `cookiefile`; con `agregar --cookies-de firefox` se guarda solo `navegador_cookies='firefox'` y se pasa `cookiesfrombrowser=('firefox',)`; `ejecutar --cookies-de chrome` prevalece sobre el valor de la fila; tras la descarga, ni `cola.db` ni el directorio de trabajo contienen cadenas de cookies (sustituir la lectura de cookies del navegador por una función falsa que devuelve una cookie ficticia conocida y buscarla en disco); los mensajes de error no contienen esa cookie. La lectura falsa es `tests/support/navegador_falso.py`, y `flaky_server.exigir_sesion` exige la cookie ficticia, así que el video solo se descarga si yt-dlp la envía (US6, escenario 2). La búsqueda en disco se hace durante la espera tras un corte (MP4 y HLS), justo antes de publicar y al terminar; comprobada con dos mutaciones de `ytdl.opciones` (`cookiefile` y `writeinfojson` en el directorio de trabajo), que la hacen fallar

- [X] T057a [US6] Tests de las cookies que no se pueden leer y del reencolado (research R25), escritos y en rojo antes de T058a. Antes, verificación en el yt-dlp instalado de `cookiesfrombrowser` en Windows con Chrome, Edge y Firefox (R25, hallazgos). En `tests/unit/test_ytdl.py`, con `NavegadorFalso` reproduciendo la cadena de excepciones de yt-dlp: base no encontrada, base bloqueada (Chromium abierto en Windows), fallo de DPAPI, plataforma no admitida y otro error → `CookiesIlegibles` (`PERMANENTE`) con «No se pudieron leer las cookies de NAVEGADOR: causa» y sin «ERROR:»; con las cookies leídas, un fallo de red sigue siendo `TEMPORAL`; `ytdl.NAVEGADORES` son los de yt-dlp. En `tests/integration/test_cookies.py`: cookies ilegibles de una fila → solo esa `failed`, la otra se completa, código 1 y ninguna petición de medios; de `ejecutar --cookies-de` → código 3, las dos vuelven a `queued`, y al corregirlo se completan; navegador no admitido (`netscape`, `firefox:perfil`, vacío) → código 2 en `agregar` y `ejecutar`; el nombre se normaliza; reencolar con `--cookies-de` tras un 403 guarda el navegador y descarga. En `tests/unit/test_store.py`, reencolar con navegador lo guarda, sin él conserva el anterior y una pendiente no cambia. En `tests/unit/test_flaky_server.py`, `exigir_sesion`

### Implementation for User Story 6

- [X] T058 [US6] Añadir `--cookies-de NAVEGADOR` a `agregar` y `ejecutar` en `src/downloader/cli.py`, guardar solo el nombre en `navegador_cookies`, y en `src/downloader/ytdl.py` traducirlo a `cookiesfrombrowser=(navegador,)` sin `cookiefile`; `ejecutar --cookies-de` prevalece (contracts/cli.md). Hace pasar T057
- [X] T059 [US6] Conectar `config.carpeta_destino` y `config.calidad` como valores por defecto de `--carpeta` y `--calidad` en `src/downloader/cli.py`. Hace pasar T056. Ya estaba hecho en T035; aquí, en `src/downloader/config.py`, el error que explica las claves de credenciales o cookies (`SECRETOS`) y la validación de `carpeta_destino`

- [X] T058a [US6] En `src/downloader/errors.py`, `CookiesIlegibles` y `cookies_ilegibles` (busca `CookieLoadError` en la cadena y traduce la causa); en `src/downloader/ytdl.py`, `NAVEGADORES` y la conversión en `_ejecutar`; en `src/downloader/worker.py`, las cookies ilegibles de `ejecutar --cookies-de` se tratan como `FATAL`; en `src/downloader/store.py`, `agregar` guarda el navegador al reencolar; en `src/downloader/cli.py`, validación y normalización de `--cookies-de`. Hace pasar T057a

**Checkpoint**: todas las historias en verde.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: comando `actualizar` (FR-025), casos límite transversales y validación final.

- [ ] T060 [P] Escribir `tests/integration/test_actualizar.py` (escenario 12 de quickstart, FR-025): sustituir la ejecución de `uv` por una función falsa; versión distinta tras `uv lock --upgrade-package yt-dlp` + `uv sync` → imprime `yt-dlp <anterior> → <nueva>` y sale con 0; misma versión → `yt-dlp ya está al día (<versión>)` y 0; fallo de uv o uv ausente → motivo claro y 1; fuera del proyecto (sin `pyproject.toml` del paquete) → no modifica nada e indica `uv tool upgrade downloader`
- [ ] T061 Implementar el comando `actualizar` en `src/downloader/cli.py` según research R14 y contracts/cli.md. Hace pasar T060
- [ ] T062 [P] Escribir `tests/integration/test_disco_lleno.py` (edge case "Disco lleno"): simular `OSError(ENOSPC)` en la escritura → `ejecutar` sale con 3, mensaje claro, la fila vuelve a `queued` con el `.part` intacto; relanzar sin el fallo completa la descarga
- [ ] T063 Hacer pasar T062 en `src/downloader/worker.py` tratando `FATAL` como parada ordenada (fila a `queued`, salida 3)
- [ ] T064 [P] Revisar con `grep` que ningún archivo `src/downloader/*.py` ni `tests/**/*.py` contiene cookies, tokens ni credenciales reales, y que los datos de prueba son ficticios (Principio VI)
- [ ] T065 Ejecutar `uv run pytest` completo con ffmpeg disponible, en Linux y en Windows (jobs del CI), y confirmar 0 tests omitidos y todos en verde; ejecutar los 12 escenarios de `specs/001-resilient-video-downloader/quickstart.md` y marcar cada uno
- [ ] T066 [P] Escribir `README.md` con instalación en Windows 10/11 y Linux (`uv sync`), requisito de ffmpeg (y cómo instalarlo en cada sistema), uso de `agregar`, `ejecutar` y `actualizar`, códigos de salida y ubicación de `config.toml`, enlazando a `specs/001-resilient-video-downloader/contracts/cli.md`

---

## Dependencies & Execution Order

### xfail estrictos

- `tests/integration/test_network_cuts.py` (T016): `xfail(strict=True)` desde T016 hasta T038.
- `tests/integration/test_crash_recovery.py` (T017): `xfail(strict=True)` desde T017 hasta T043.
- Entre medias, un `xpassed` rompe la suite a propósito: el test no está probando lo que debe.

### Phase Dependencies

- **Setup (Fase 1)**: sin dependencias.
- **Foundational (Fase 2)**: depende de Setup. Orden interno obligatorio: 2a → 2b (tests en
  rojo, T018) → 2c. Bloquea todas las historias.
- **US1 (Fase 3)**: depende de la Fase 2. Es el MVP.
- **US2 (Fase 4)**, **US3 (Fase 5)**, **US5 (Fase 6)**: dependen de US1 (amplían `worker.py` y
  `cli.py`). Entre sí pueden hacerse en cualquier orden, pero tocan los mismos archivos: no en
  paralelo.
- **US4 (Fase 7)**: depende de US2 (re-extracción en reintentos para URLs caducadas).
- **US6 (Fase 8)**: depende de US1.
- **Polish (Fase 9)**: depende de todas las historias; T060–T061 y T062–T063 pueden empezar
  en cuanto exista `cli.py` (US1).

### Mapa de requisitos y escenarios

| Requisito / escenario | Tareas |
|-----------------------|--------|
| FR-021 (salida de `ejecutar`) | T030, T034, T035, T042, T044, T052, T055, T062, T063 |
| FR-022 (sin sobrescribir) | T027, T032 |
| FR-023 (verificación) | T045, T046, T048, T049 |
| FR-024 (reinicio con aviso) | T036, T039, T050a, T050b |
| FR-025 (`actualizar`) | T060, T061 |
| FR-026 (tope de errores ambiguos) | T037b, T038, T050a, T050b, T052, T054 |
| Plataformas: Windows 10/11 y Linux (constitución v2.1.0) | T035a, T065 |
| Escenario 1 | T028, T034, T035 |
| Escenario 2 y 2b | T016, T037a, T037b, T038 |
| Escenario 3 | T016, T038, T041 |
| Escenario 4 | T017, T043 |
| Escenario 5 | T051, T054 |
| Escenario 6 | T052, T054, T055 |
| Escenario 7 | T045, T046, T048, T049 |
| Escenario 8 | T027, T032 |
| Escenario 9 | T057, T057a, T058, T058a |
| Escenario 10 | T053, T054 |
| Escenario 11 | T036, T039 |
| Escenario 12 | T060, T061 |

### Within Each User Story

- Tests escritos y en rojo antes de la implementación.
- `publish.py` y `progress.py` antes de `worker.py`; `worker.py` antes de `cli.py`.
- Cada fase termina con todos los tests anteriores en verde.

---

## Parallel Example: Fase 2

```text
# 2a en paralelo:
T006 tests/support/flaky_server.py
T007 tests/support/fake_extractor.py

# 2b en paralelo (tras T008):
T010 test_models.py   T011 test_store.py   T012 test_retry.py   T013 test_errors.py
T014 test_network.py  T015 test_ytdl.py    T016 test_network_cuts.py   T017 test_crash_recovery.py

# 2c en paralelo:
T019 models.py  T020 config.py  T022 retry.py  T023 errors.py  T024 network.py
```

## Parallel Example: User Story 1

```text
# Tests en paralelo:
T026 test_cli_agregar.py  T027 test_publish_nombres.py  T028 test_descarga_basica.py
T029 test_listas.py       T030 test_salida_ejecutar.py  T031 test_calidad.py

# Implementación en paralelo antes de worker.py:
T032 publish.py (publicar)  T033 progress.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Fase 1 y Fase 2 (con los tests de corte de red en rojo).
2. Fase 3 (US1).
3. **Validar**: escenario 1 de quickstart; T016 y T017 siguen en rojo, lo esperado.

### Incremental Delivery

1. US1 → MVP: descargas sin resistencia.
2. US2 → resistencia a cortes (requisito principal); T016 en verde.
3. US3 → persistencia ante cierres; T017 en verde.
4. US5 → integridad.
5. US4 → errores permanentes y URLs caducadas.
6. US6 → configuración y cookies.
7. Polish → `actualizar`, disco lleno, README, validación de los 12 escenarios.

---

## Notes

- [P] = archivos distintos, sin dependencias pendientes.
- Hacer commit al terminar cada tarea o grupo lógico; nunca con tests en rojo fuera de los
  esperados en la Fase 2.
- No omitir ni desactivar tests para pasar a verde (Principio II).
