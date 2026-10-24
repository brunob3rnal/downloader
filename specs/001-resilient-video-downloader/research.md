# Research: Gestor de descargas de video resiliente

Verificado contra yt-dlp 2026.08.19 cuando se cita su comportamiento.

## R1. yt-dlp como librería

- **Decision**: usar `yt_dlp.YoutubeDL` en proceso, con un único adaptador (`ytdl.py`) que
  construye las opciones y traduce progreso y errores. Opciones fijas:
  `continuedl=True`, `nopart=False`, `skip_unavailable_fragments=False`,
  `retries=3`, `fragment_retries=3`, `socket_timeout=20`, `noplaylist=True` al descargar,
  `quiet=True` con un `logger` propio y `progress_hooks` para el progreso. `crear_ydl` fija
  además `hls_prefer_native=True` (HLS con el descargador nativo, que pasa por `urlopen` y
  deja registro de cada petición, R4, en lugar de ffmpeg). La dependencia es
  `yt-dlp[default]`: añade `pycryptodomex` (sin ella, HLS con AES-128 se delegaría en ffmpeg),
  `requests`/`urllib3` (manejador HTTP preferido por yt-dlp), `brotli`, `certifi`,
  `websockets`, `mutagen` y `yt-dlp-ejs`. Licencias revisadas: BSD, Apache-2.0, MIT, MPL-2.0,
  Unlicense/ISC y `mutagen` GPL-2.0-or-later (a tener en cuenta si el proyecto se distribuye
  con una licencia incompatible con la GPL).
- **Rationale**: es lo pedido y evita parsear la salida de un subproceso. Los reintentos
  internos de yt-dlp se dejan bajos porque los reintentos indefinidos son nuestros (FR-010).
  `skip_unavailable_fragments` vale `True` por defecto en
  `yt_dlp/downloader/fragment.py`: con HLS/DASH se saltaría fragmentos y produciría un archivo
  incompleto, lo que viola FR-013.
- **Alternatives considered**: subproceso `yt-dlp` (rechazado por el usuario; además obliga a
  parsear texto); descargador HTTP propio (duplica lo que yt-dlp ya resuelve para HLS/DASH).

## R2. Reanudación y caducidad de URLs

- **Decision**: cada intento vuelve a extraer la información del video
  (`extract_info(url, download=True)`) y descarga en un directorio de trabajo estable por
  descarga, con plantilla de nombre fija (`descarga.%(ext)s`; antes `%(id)s.%(ext)s`, ver R7).
  yt-dlp continúa los `.part`
  existentes (`continuedl`) y los fragmentos HLS/DASH mediante su archivo `.ytdl`.
- **Rationale**: re-extraer en cada intento resuelve la caducidad de URLs temporales (FR-012)
  sin un caso especial: un 403 por URL caducada se clasifica como temporal y el siguiente
  intento obtiene una URL nueva y continúa el mismo `.part`.
- **Alternatives considered**: detectar la caducidad y re-extraer solo entonces (más código y
  más casos que probar, sin beneficio).
- **Riesgo**: si tras re-extraer cambia el formato elegido, se empieza un `.part` nuevo; el
  anterior se conserva (no se pierde, pero se desperdicia).

## R3. Servidor que no permite reanudar

- **Hallazgo**: `yt_dlp/downloader/http.py` borra el `.part` y descarga desde cero cuando el
  servidor ignora la cabecera `Range` ("resume is not possible, so wiping the local file").
- **Decision**: se acepta ese reinicio, conforme al Principio IV enmendado (v2.0.0) y a
  FR-024. No se guardan respaldos del `.part`. El reinicio se detecta sin depender del texto
  de yt-dlp: por cada archivo se lleva el máximo de bytes conocido, inicializado con el tamaño
  del `.part` al empezar el intento y actualizado con cada evento del hook de progreso; si un
  evento informa `downloaded_bytes` menor que ese máximo, la descarga se reinició. yt-dlp
  puede reiniciar dentro de una misma llamada (sus reintentos internos tras un corte), así que
  comparar solo al inicio del intento no basta. Entonces se muestra el aviso
  "#N reiniciada desde cero: el servidor no permite reanudar" y se guarda en `ultimo_aviso`.
- **Alternatives considered**: respaldar el `.part` antes de reintentar (rechazado por el
  usuario: el progreso de un servidor que no permite reanudar no es aprovechable);
  reemplazar el descargador HTTP de yt-dlp (complejidad desproporcionada).

## R4. Clasificación de errores

- **Decision**: `errors.py` clasifica cada excepción en `TEMPORAL`, `PERMANENTE` o `FATAL`, y
  marca algunos `TEMPORAL` como *ambiguos* (sujetos a un tope, FR-026). Orden de prioridad:
  1. `FATAL` (detiene `ejecutar`, FR-021): disco lleno (`ENOSPC`), archivo de estado corrupto,
     ffmpeg o ffprobe ausentes. Afectan a todo el entorno: seguir con otras descargas
     fallaría igual.
  2. Monitor de red "sin conexión" → `TEMPORAL`, sea cual sea el error.
  3. Señal de red: el código HTTP o el error de transporte se toman de la cadena de causas
     (`exc_info` / `cause` de `DownloadError` y `ExtractorError`, `__cause__`, `__context__`)
     y, si no hay, del registro de la última petición (ver abajo). Se aplica una sola tabla,
     según la fase en que ocurrió:

     | Señal | Extracción | Descarga |
     |-------|------------|----------|
     | transporte, timeout, conexión rechazada, cuerpo incompleto | `TEMPORAL` | `TEMPORAL` |
     | HTTP 429 o 5xx | `TEMPORAL` | `TEMPORAL` |
     | HTTP 404 | `PERMANENTE` | `PERMANENTE` |
     | HTTP 403 o 410 | `PERMANENTE` | `TEMPORAL` ambiguo (URL caducada: se vuelve a extraer, FR-012) |
     | otro 4xx | `PERMANENTE` | `PERMANENTE` |

     La fase es extracción si la cadena contiene un `ExtractorError` (yt-dlp envuelve en él
     todo fallo al obtener la información); si no, la que anota el registro.
  4. Sin señal de red: `UnsupportedError`, `ExtractorError(expected=True)` (video borrado,
     privado, restricción geográfica, DRM), contenido en directo y los errores propios
     (`ErrorPermanente`: carpeta de destino inexistente o sin permisos, FR-018; fallo de
     verificación) → `PERMANENTE`.
  5. Sin señal de red y registro correcto o vacío: si la descarga la hacía un programa
     externo (ffmpeg), cuyas peticiones no ve el registro → `TEMPORAL` ambiguo; en otro caso
     → `PERMANENTE`, con el mensaje original filtrado.
- **Tope de los ambiguos** (FR-026): `retry.TopeSinAvance`, una instancia por descarga.
  Si 3 intentos consecutivos terminan en un error ambiguo sin ningún byte nuevo sobre el
  máximo ya visto de cada archivo (según el hook de progreso), la descarga falla como
  `PERMANENTE`. Para 403/410 el mensaje explica que el video puede exigir una sesión y sugiere
  `--cookies-de NAVEGADOR`. Un intento que termina en un error no ambiguo (corte, timeout,
  429, 5xx) rompe la racha y no cuenta. Al reanudar HLS, yt-dlp informa `downloaded_bytes`
  acumulados desde el total anterior (verificado), así que el máximo sirve también para
  fragmentos.
- **Registro de la última petición**: cuando yt-dlp agota sus reintentos internos (cortes,
  timeouts y 5xx en descargas HTTP; cualquier error HTTP o lectura incompleta en fragmentos
  HLS/DASH) lanza un `DownloadError` con `exc_info` vacío (verificado en yt-dlp 2026.08.19:
  `RetryManager` llama a `report_error` fuera del bloque `except`). Por eso `ytdl.crear_ydl`
  usa `YoutubeDLRegistrado`, subclase de `YoutubeDL` que lleva un `RegistroRed` por intento:
  - `urlopen` sobrescrito registra el código HTTP o la excepción, y envuelve la lectura del
    cuerpo: en un corte `urlopen` responde 206 y el error llega al leer, así que una excepción
    de lectura o un cuerpo más corto que `Content-Length` se registran como error de
    transporte. El error se relanza intacto.
  - `dl` sobrescrito anota la fase de descarga y si el descargador que elige
    `get_suitable_downloader` es externo (`ExternalFD`, p. ej. `FFmpegFD`).
  - Cada intento crea su propio `YoutubeDL`, así que el registro empieza vacío. `descargar` y
    `extraer_plano` lo adjuntan al `DownloadError` como `registro`.
- **Caminos que no pasan por `ydl.urlopen`** (verificado en `yt_dlp/downloader/`): la descarga
  HTTP (`http.py`), los fragmentos HLS/DASH/ISM/F4M (`fragment.py` → `HttpQuietDownloader`),
  los manifiestos (`hls.py`, `f4m.py`), las claves AES y la extracción
  (`InfoExtractor._request_webpage`) sí pasan. Lo esquivan:
  - `FFmpegFD`: con `hls_prefer_native=True` y `pycryptodomex` instalado solo se usa para HLS
    o DASH en directo (rechazados antes, R5), HLS con cifrado distinto de AES-128 o con DRM
    (que `HlsFD` delega en ffmpeg en tiempo de ejecución, sin que `dl` lo vea), `rtmp_ffmpeg`
    y descargas por secciones (no se usan). Su fallo sin señal → `TEMPORAL` ambiguo.
  - `RtmpFD` (rtmpdump) y `websocket_frag` (librería `websockets`): RTMP y WebSocket están
    fuera de alcance (spec, Assumptions).
  - `external_downloader`: no se configura.
- **Rationale**: "sin red, es temporal" evita marcar como permanente un `ExtractorError`
  provocado por un corte. El registro recupera el tipo de error que yt-dlp descarta sin
  depender del texto de sus mensajes. Un 403/410 al descargar suele ser una URL caducada que
  la siguiente extracción resuelve, pero también puede ser un acceso que nunca se concederá;
  el tope sin avance distingue ambos casos sin reintentar para siempre.
- **Alternatives considered**: clasificar por el texto del mensaje (frágil entre versiones);
  tratar como temporal todo fallo sin causa durante la descarga (reintentaría para siempre un
  fragmento ausente); registrar solo el resultado de `urlopen` (un corte a mitad del cuerpo
  quedaría como petición correcta → `PERMANENTE`); 403 siempre permanente (rompería FR-012
  en HLS, donde caduca el token de los fragmentos); 403 siempre temporal sin tope
  (reintentaría para siempre un video que exige sesión).

## R5. DRM y directos

- **Decision**: rechazar como `PERMANENTE` si `info['is_live']` es verdadero, o si yt-dlp
  informa DRM (`_has_drm` o formatos con `has_drm`, que ya provoca
  `ExtractorError(expected=True)` "This video is DRM protected").
- **Rationale**: Principio V; no se intenta ningún formato con DRM.

## R6. Selección de calidad

- **Decision**:
  - `mejor`: `bv*+ba/b`
  - `1080p`: `bv*[height<=1080]+ba/b[height<=1080]/wv*+ba/w`
  - `720p`: `bv*[height<=720]+ba/b[height<=720]/wv*+ba/w`
  - `audio`: `ba/b` + postprocesador `FFmpegExtractAudio` (conserva el códec cuando es
    posible).
  Si la altura obtenida no coincide con la pedida, se avisa al usuario.
- **Rationale**: los filtros `<=` eligen la más cercana inferior (caso límite de la spec); la
  última alternativa evita fallar si solo existen calidades superiores.

## R7. Directorio de trabajo y publicación sin sobrescribir

- **Decision**: cada descarga trabaja en `<carpeta_destino>/.downloader-tmp/<id_descarga>/`
  (mismo sistema de archivos que el destino). Tras verificar, se publica con
  `os.link(origen, destino)` seguido de `os.unlink(origen)`: `link` falla con
  `FileExistsError` si el destino existe, y entonces se prueba `nombre (1).ext`,
  `nombre (2).ext`, etc. Si el sistema de archivos no admite enlaces duros, se usa
  `os.open(destino, O_CREAT | O_EXCL)` + copia + `fsync` + borrado del origen.
- **Rationale**: `os.rename`/`os.replace` sobrescriben en POSIX sin avisar (viola FR-022);
  `os.replace` también en Windows. El nombre final nunca aparece en la carpeta de destino hasta
  pasar la verificación (FR-013, SC-004). Al estar en el mismo sistema de archivos, la
  publicación es atómica.
- **Windows (constitución v2.1.0)**:
  - NTFS admite enlaces duros: `os.link` llama a `CreateHardLinkW`, que falla con
    `ERROR_ALREADY_EXISTS` (Python: `FileExistsError`) si el destino existe, igual que en
    Linux; `os.unlink` del origen funciona porque yt-dlp ya cerró el archivo. Lo prueban en
    `windows-latest` los tests de `tests/unit/test_publish_nombres.py` que usan `os.link`
    real.
  - FAT32 y exFAT no admiten enlaces duros. En Linux, `vfat` responde `EPERM`; en Windows el
    motivo llega en `winerror` (`ERROR_INVALID_FUNCTION`, `ERROR_NOT_SUPPORTED`,
    `ERROR_NOT_SAME_DEVICE`, `ERROR_TOO_MANY_LINKS`, `ERROR_ACCESS_DENIED`) y `errno` es un
    `EINVAL` genérico, así que `publish._sin_enlaces` mira los dos. El respaldo
    `O_CREAT | O_EXCL` + copia no depende del sistema. El CI crea un volumen FAT32 en Linux y
    uno FAT32 y otro exFAT en Windows (`DOWNLOADER_TEST_SIN_ENLACES`) y publica en ellos.
  - Nombres: `windowsfilenames=True` en las opciones de descarga, y `sanitize_filename` (que
    `nombre_publicado` ya usaba) sustituye `: ? | " < > * / \` por equivalentes de ancho
    completo en cualquier sistema. Ninguno de los dos evita los nombres reservados (`CON`,
    `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, con cualquier extensión):
    `nombre_publicado` antepone `_` si lo que queda antes del primer punto es uno de ellos.
  - Longitud: sin `LongPathsEnabled` en el registro, Windows limita la ruta a 260 caracteres
    (259 más el NUL); Linux limita cada nombre a 255 bytes. `nombre_publicado` acorta el título
    hasta que el nombre, con hueco para el sufijo más largo de `publicar` (` (9999)`), cabe en
    255 bytes UTF-8 y, en Windows, la ruta completa en 259 unidades UTF-16. El id y la
    extensión no se recortan.
  - Directorio de trabajo: los archivos se llaman `descarga.<ext>` (y yt-dlp añade
    `.f<formato>`, `.part`, `.part-Frag<N>`, `.ytdl`, `.temp`) en lugar de `<id_video>.<ext>`:
    el id podría ser largo, o un nombre reservado si lo deduce el extractor genérico de la URL
    (`…/aux.mp4`). Así la ruta de trabajo es la carpeta más unos 60 caracteres
    (`\.downloader-tmp\<id>\descarga.f137.mp4.part-Frag1234`).
  - Límite conocido: si la propia carpeta de destino supera unos 195 caracteres, los archivos de
    trabajo pueden pasar de 260 en Windows sin `LongPathsEnabled`; yt-dlp no podría crearlos y
    la descarga fallaría con el mensaje del sistema. No se valida de antemano.
- **Alternatives considered**: `.part` directamente en la carpeta de destino (mezcla parciales
  y finales; yt-dlp renombra al terminar, antes de que podamos verificar).

## R8. Verificación antes de publicar

- **Decision**:
  1. Por cada archivo de formato terminado (hook `status == 'finished'`), si `total_bytes`
     es exacto (no `total_bytes_estimate`), su tamaño en disco debe coincidir.
  2. Sobre el archivo final (tras unir audio y video), `ffprobe -v error -count_packets
     -show_entries format=duration:stream=nb_read_packets` debe terminar sin errores, leer
     paquetes en todos los flujos y devolver una duración positiva. Para HLS/DASH sin tamaño
     total es la única comprobación; en el resto, complementa la de tamaño.
  3. Si la verificación falla, la descarga pasa a `failed` y los archivos del intento se
     mueven a `<directorio de trabajo>/invalidos/<fecha>/` (no se borran). Así, si el
     usuario la vuelve a encolar, yt-dlp no da por buenos esos archivos y descarga de nuevo.
  4. Alcance real de estas comprobaciones y límites conocidos: R22.
- **Rationale**: el tamaño del archivo unido no coincide con la suma de los formatos, por lo
  que el tamaño se comprueba antes de unir. `-count_packets` lee todo el contenedor sin
  decodificar: detecta truncamientos y es rápido.
- **Alternatives considered**: `ffmpeg -f null -` (decodifica todo; demasiado lento en videos
  largos).

## R9. Monitor de conectividad

- **Decision**: hilo daemon que cada 5 s intenta una conexión TCP (timeout 3 s) a una lista de
  sondas (por defecto `1.1.1.1:443` y `8.8.8.8:443`, configurables) y expone un
  `threading.Event` "en línea". Un fallo de todas las sondas marca "sin conexión"; un acierto,
  "en línea". En los tests se inyecta un monitor falso.
- **Rationale**: simple, sin dependencias; permite despertar un reintento en cuanto vuelve la
  red en vez de esperar al final del backoff.

## R10. Reintentos

- **Decision**: espera `min(300, 2 * 2**n)` segundos con variación aleatoria de ±20 %, donde
  `n` cuenta los intentos fallidos consecutivos sin avance de bytes. Cualquier avance reinicia
  `n`. Si el monitor pasa a "en línea" durante la espera, se reintenta de inmediato.
- **Rationale**: cumple FR-010 (indefinido, tope 5 minutos). Reiniciar `n` tras progreso
  evita esperas largas en conexiones inestables pero útiles.

## R11. Persistencia (SQLite)

- **Decision**: base de datos en `typer.get_app_dir("downloader")/cola.db`, modo WAL,
  `synchronous=FULL`, una conexión por proceso. Cada transición de estado es una transacción.
  El progreso en bytes se escribe como mucho cada 2 s; la fuente de verdad de lo descargado
  son los `.part` en disco. Al iniciar `ejecutar`, las filas en `downloading` o
  `waiting_network` vuelven a `queued` (recuperación tras cierre abrupto).
- **Rationale**: WAL permite `agregar` desde otra terminal mientras `ejecutar` corre;
  `synchronous=FULL` hace que un commit sobreviva a un corte de luz.
- **Archivo corrupto**: al abrir se ejecuta `PRAGMA quick_check`; si falla o SQLite lanza
  `DatabaseError`, se copia a `cola.db.corrupta-<fecha>`, se muestra un error claro y se sale
  con código 3. No se crea una cola nueva ni se toca ningún directorio de trabajo.
- **Concurrencia de procesos**: un único `ejecutar` a la vez, garantizado con un bloqueo
  exclusivo sobre `ejecutar.lock` en el mismo directorio: `fcntl.flock` en Linux y
  `msvcrt.locking` en Windows (R17). En ambos el sistema operativo libera el bloqueo si el
  proceso muere.

## R12. Cookies del navegador

- **Decision**: `--cookies-de <navegador>` se traduce a `cookiesfrombrowser=(navegador,)`;
  nunca se pasa `cookiefile`, así yt-dlp lee las cookies en memoria y no escribe ninguna. En
  la base de datos solo se guarda el nombre del navegador (si se indicó en `agregar`), nunca
  cookies. El `logger` propio filtra cabeceras `Cookie`/`Authorization` y parámetros de URL
  con aspecto de token antes de mostrar o registrar mensajes.
- **Rationale**: Principio VI y FR-017.

## R13. Listas de reproducción

- **Decision**: `agregar` solo guarda la URL (funciona sin red). `ejecutar` extrae cada URL con
  `extract_flat='in_playlist'`; si es una lista, inserta una descarga hija por entrada (misma
  calidad, carpeta y navegador), marca la padre como `completed` con tipo `playlist` y sigue.
- **Rationale**: un único punto de extracción; las listas se benefician de los mismos
  reintentos.
- **URLs mixtas** (video dentro de una lista, p. ej. `watch?v=…&list=…`): `noplaylist=True`
  también al expandir, así que se descarga solo el video. Las URLs de lista propiamente dichas
  se expanden igual.

## R14. Comando `actualizar`

- **Decision**: `actualizar` (FR-025) actualiza yt-dlp a su última versión
  ejecutando `uv lock --upgrade-package yt-dlp` y `uv sync` en el proyecto, y muestra la
  versión anterior y la nueva. Si la herramienta no corre desde el proyecto (por ejemplo,
  instalada con `uv tool install`), indica el comando equivalente (`uv tool upgrade
  downloader`) en lugar de modificar el entorno por su cuenta.
- **Rationale**: los extractores de yt-dlp se rompen con frecuencia; actualizarlo es la
  operación de mantenimiento más habitual. Hacerlo por medio de uv mantiene `uv.lock` como
  fuente de verdad.

## R15. Tests

- **Decision**:
  - Servidor HTTP local en un hilo (`http.server`) con soporte de `Range`, y modos
    configurables: cortar la conexión tras N bytes (respuesta truncada), quedarse sin enviar
    datos hasta agotar el timeout del cliente, responder 500/503, rechazar `Range`, responder
    403 a tokens caducados, apagarse y volver a levantarse en el mismo puerto. Cubre los
    casos que exige el Principio II.
  - Un extractor de prueba (`InfoExtractor` registrado con `ydl.add_info_extractor`) para URLs
    `http://127.0.0.1:<puerto>/watch/<id>` que devuelve en cada extracción una URL de medios
    con un token nuevo: prueba la caducidad por el camino real.
  - Prueba de cierre abrupto: lanzar `ejecutar` en un subproceso (`python -m downloader`, sin
    `uv run` en medio), matarlo a mitad con `Popen.kill()` (`SIGKILL` en POSIX,
    `TerminateProcess` en Windows), relanzar y comprobar que el total de bytes servidos no
    supera el tamaño del archivo en más de un 1 % (SC-002).
  - Ctrl+C: en Windows no se puede enviar Ctrl+C a un solo proceso, así que el test lanza
    `ejecutar` con `CREATE_NEW_PROCESS_GROUP` y le envía `CTRL_BREAK_EVENT` (R17); en Linux,
    `SIGINT`.
  - Marcador `ffmpeg`: `conftest.py` omite esos tests si `shutil.which("ffmpeg")` o
    `shutil.which("ffprobe")` es `None`.
  - Sin acceso a internet en ningún test.
  - yt-dlp reintenta por su cuenta cortes y errores 5xx hasta `retries` (3). Para que un test
    pruebe nuestros reintentos, el servidor debe fallar más veces seguidas que `retries` y el
    test debe comprobar que la fila pasó por `waiting_network`.
  - Los archivos que sirve el servidor en tests de descarga son medios válidos (MP4 y HLS
    generados con ffmpeg al inicio de la sesión), porque la verificación con ffprobe (R8)
    rechazaría bytes aleatorios. Por eso los tests que descargan llevan el marcador `ffmpeg`.
- **Rationale**: Principio II; los cortes de red se prueban contra yt-dlp real, no contra un
  doble.
- **Riesgo**: si el entorno de CI no tiene ffmpeg, las pruebas de unión y verificación se
  omitirían en silencio. El CI MUST instalar ffmpeg.

## R16. Entorno con uv

- **Decision**: `uv init --package --name downloader --python 3.12` (estructura `src/` y
  script de consola), `uv add yt-dlp typer`, `uv add --dev pytest`. Todo se ejecuta con
  `uv run`.
- **Rationale**: `uv init` sin `--package` crea un `main.py` suelto sin punto de entrada
  instalable; `uv add pytest` sin `--dev` lo convertiría en dependencia de ejecución.

## R17. Plataformas: Windows 10/11 y Linux

- **Contexto**: en Windows, `uv run downloader` fallaba con `ModuleNotFoundError: No module
  named 'fcntl'` (`store.py`, línea 3). La constitución v2.1.0 fija Windows 10/11 y Linux como
  plataformas objetivo.
- **Decision (bloqueo)**: `store.py` importa `msvcrt` si `os.name == "nt"` y `fcntl` en el
  resto, y define `_bloquear`/`_desbloquear` con la misma interfaz. En Windows se bloquea el
  primer byte de `ejecutar.lock` con `msvcrt.locking(fd, LK_NBLCK, 1)` (se puede bloquear más
  allá del final del archivo; un bloqueo ocupado da `EACCES` o `EDEADLK`) y se desbloquea con
  `LK_UNLCK` antes de cerrar, porque Windows libera los bloqueos de un descriptor cerrado
  "cuando lo permiten los recursos del sistema", no necesariamente al instante.
- **Rationale**: son unas 25 líneas sin dependencias nuevas (Principio I; la constitución pide
  justificar cada dependencia). Ambos mecanismos son los que necesita R11: exclusivos entre
  procesos y entre descriptores del mismo proceso, y liberados por el sistema si el proceso
  muere (`SIGKILL`, `TerminateProcess`). `tests/unit/test_store.py` prueba la rama de Windows
  con un `msvcrt` simulado en Linux, y los tests de bloqueo corren con `msvcrt` real en
  `windows-latest`.
- **Alternatives considered**: `portalocker` (PSF-2.0, pequeña y mantenida): envuelve lo mismo
  (`msvcrt.locking` o `LockFileEx` con pywin32, y `fcntl.flock`), añade una dependencia y su
  revisión de licencia, y aporta funciones (bloqueos con espera, compartidos, Redis) que no se
  usan. `filelock`: bloqueo por existencia de archivo en algunas variantes, que no se libera
  solo si el proceso muere. SQLite como bloqueo (`BEGIN EXCLUSIVE` permanente): bloquearía
  también a `agregar`, que debe poder escribir mientras `ejecutar` corre.
- **Revisión del resto de `src/`**: no hay `os.fork`, `os.getuid`/`geteuid`, `os.O_DIRECTORY`,
  `fsync` sobre directorios, `resource`, `termios` ni señales POSIX. Se corrigió:
  - `os.access(carpeta, W_OK | X_OK)` en `cli.py` y `worker.py`: en Windows siempre responde
    que una carpeta es escribible (ignora las ACL). Se sustituye por `publish.escribible`, que
    crea un archivo temporal en la carpeta.
  - `progress._ruta_visible` construía `~/…` con `/` a mano; ahora usa `Path("~") / …`
    (`~\Downloads\…` en Windows).
  - `publish._SIN_ENLACES` solo miraba `errno` (ver R7).
  - Salida redirigida: en Windows, `sys.stdout` hacia un archivo o una tubería usa la
    codificación ANSI (cp1252) y un título con caracteres fuera de ella lanzaba
    `UnicodeEncodeError`. `cli.principal` reconfigura `stdout` y `stderr` con
    `errors="backslashreplace"`. En la consola, Python ya escribe en UTF-16 y no ocurre.
  - Nuevo `src/downloader/__main__.py` para lanzar `python -m downloader` desde los tests.
- **Ctrl+C en Windows**: Python convierte `CTRL_C_EVENT` en `SIGINT` y este en
  `KeyboardInterrupt` en el hilo principal, como en Linux; el bucle de `ejecutar` corre en el
  hilo principal y los hilos del monitor de red y del progreso son `daemon`. Diferencias:
  - Ctrl+Break (`SIGBREAK`) terminaría el proceso sin limpiar; `cli.ejecutar` lo asocia a
    `signal.default_int_handler`, así que interrumpe igual que Ctrl+C. Además es el único
    evento que se puede enviar a un proceso concreto, y lo usa el test. Solo se instala desde
    el hilo principal: `signal.signal` lanza `ValueError` en otro hilo (los tests llaman a
    `ejecutar` desde hilos), y las señales solo llegan al principal.
  - Una llamada bloqueante (`recv` sobre un socket) no se interrumpe: la señal se atiende
    cuando vuelve, como mucho tras `socket_timeout` (20 s). En Linux, `EINTR` la corta al
    instante.
  - Tras Ctrl+C, `ejecutar` guarda el último progreso, devuelve la fila a `queued`, muestra
    "interrumpido; progreso guardado" y sale con 130 (Fase 5, T042, T044).
- **Verificación**: `tests/unit/test_plataforma.py` importa todos los módulos de `downloader` y
  recorre el AST de `src/` y `tests/` para fallar ante un `import fcntl` (o `termios`,
  `msvcrt`…) o un `signal.SIGKILL`, `os.fork`, `os.getuid`… que no estén bajo
  `if os.name …`/`sys.platform …`, `hasattr(…)` o `try … except ImportError`. La verificación
  real del comportamiento en Windows es el job `windows-latest` del CI.

## R18. Bytes perdidos en un corte a mitad de bloque

- **Hallazgo** (yt-dlp 2026.08.19, urllib3 2.8.0, verificado): el descargador HTTP de yt-dlp
  lee con `read(bloque)` y agranda el bloque según la velocidad, hasta 4 MiB
  (`best_block_size`). Con el manejador `requests` (el preferido con `yt-dlp[default]`),
  `urllib3.HTTPResponse.read(amt, decode_content=True)` sigue leyendo hasta completar `amt`;
  si la conexión se corta antes, lanza `IncompleteRead` y lo ya recibido de ese bloque se queda
  en su búfer interno sin entregarse. yt-dlp no lo escribe en el `.part` y el siguiente
  `Range` lo vuelve a pedir. En los tests de T016 eso suponía un 115–122 % de bytes servidos
  (SC-002 exige ≤ 101 %).
- **Decision**: el envoltorio de `read` de `YoutubeDLRegistrado` (R4) lee de la respuesta en
  trozos de `TROZO_LECTURA` = 4 KiB hasta completar lo pedido. Si un trozo falla con un
  `TransportError` tras haber acumulado bytes, devuelve lo acumulado y guarda la excepción
  para relanzarla en la siguiente llamada a `read`; si no había bytes, la relanza al momento.
  El error se registra en el momento en que ocurre, así que la clasificación (R4) no cambia.
  Como mucho se pierden 4 KiB por corte (el trozo en curso, si la librería lo descarta).
- **Coste medido** (`ytdl.descargar` de un archivo de 200 MiB contra `flaky_server` en otro
  proceso, sin límite de velocidad, mediana de 10 descargas, dos series): antes 693–721 MiB/s,
  después 273–277 MiB/s (−60 %). Con trozos de 16 KiB serían 466 MiB/s, pero se perderían
  hasta 16 KiB por corte. Aun así, 275 MiB/s (≈ 2,3 Gbit/s) está muy por encima de una conexión
  a internet doméstica: el coste es CPU, no velocidad efectiva.
- **Alternatives considered**: (A) `buffersize=4096` y `noresizebuffer=True` en las opciones de
  yt-dlp: el mismo límite de pérdida, pero yt-dlp llamaría al hook de progreso y escribiría
  en disco cada 4 KiB; (C) recuperar el búfer interno de urllib3 tras el error: depende de
  atributos privados (`_decoded_buffer`).

## R19. Respuestas de la extracción que quedan abiertas

- **Hallazgo** (yt-dlp 2026.08.19, verificado): con una URL directa cuyo `Content-Type` es
  `video/*` (o `audio/*`), el extractor genérico (`yt_dlp/extractor/generic.py`) hace un GET
  completo, sin `Range`, para inspeccionar la URL y devuelve el resultado sin leer ni cerrar la
  respuesta. `YoutubeDL.close()` cierra el pool de conexiones, pero no las respuestas que nunca
  se liberaron, así que la conexión sigue abierta hasta que la recoge el GC: el servidor sigue
  enviando el video a un socket que nadie lee, hasta llenar el búfer de recepción de TCP (en
  una red real, varios MB). En T017 eso era ~1,25 MB de más (161 % del tamaño; SC-002 exige
  ≤ 101 %). No afecta a los extractores de sitios ni a HLS: leen sus respuestas enteras.
- **Decision**: `YoutubeDLRegistrado.urlopen` apunta con `weakref` cada respuesta abierta en la
  fase de extracción (no las de la descarga) y las cierra, tolerando que ya estén cerradas, al
  empezar `process_ie_result` (el extractor ya devolvió su resultado), al empezar `dl` y en
  `close`. `process_ie_result` se añadió porque, en un proceso recién arrancado, entre que el
  extractor devuelve y `close()` pasan ~70 ms procesando formatos (~24 KB a 400 KB/s).
  Las peticiones de la descarga (manifiesto, clave AES, fragmentos, el propio archivo) se
  hacen con la fase ya en descarga y no se tocan.
- **Queda**: con el cierre, en T017 sobran 25–27 KB (101,0–101,3 %): ~6–8 KB de los GET de
  sondeo del extractor genérico (1–2 KB cada uno antes de cerrarse) y 12–19 KB recibidos pero
  no escritos en el `.part` cuando llega el `SIGKILL` (lo que el envoltorio de lectura de R18
  acumula para el bloque de yt-dlp, que a 400 KB/s ya pide bloques de 64–370 KB, más el búfer
  de 8 KiB del archivo). Se resuelve enmendando SC-002 para las interrupciones abruptas (R20),
  sin limitar el bloque del envoltorio.
- **Alternatives considered**: forzar el GC tras extraer (no garantiza el momento y depende de
  ciclos de referencias); extraer con un `YoutubeDL` y descargar con otro (más llamadas a
  yt-dlp y la misma fuga en el primero).

## R20. Exceso admitido tras una interrupción abrupta (enmienda de SC-002)

- **Contexto**: un `SIGKILL` o un corte de luz no se pueden capturar: lo recibido que aún no
  llegó al `.part` se pierde y se vuelve a pedir al reanudar. Medido en T017 (archivo de
  ~2 MB a 400 KB/s), el exceso fue de 25–27 KB, por encima del 1 % (20,6 KB):
  - GET de sondeo del extractor genérico de yt-dlp con URLs directas: ~6–8 KB (1–2 KB cada
    uno, 3–4 por ejecución, hasta que R19 los cierra).
  - Búfer del archivo `.part` abierto por yt-dlp: hasta 8 KiB (`io.DEFAULT_BUFFER_SIZE`).
  - Bloque en vuelo: lo que el envoltorio de lectura (R18) acumula para el bloque pedido por
    yt-dlp y aún no ha entregado; yt-dlp agranda el bloque según la velocidad (64–370 KB a
    400 KB/s, hasta 4 MiB).
- **Decision**: SC-002 admite, solo tras una interrupción abrupta, el 1 % del tamaño del
  archivo más 128 KiB fijos. Los 128 KiB cubren los sondeos y el búfer del archivo con margen,
  y el bloque en vuelo cuando la interrupción llega poco después de una escritura, como en el
  test. Tras un corte de red (T016) el límite sigue siendo el 1 % estricto: ahí el envoltorio
  entrega lo acumulado antes de relanzar el error (R18) y no se pierde nada.
- **Alternatives considered**: limitar el bloque que entrega el envoltorio (p. ej. a 64 KiB)
  para escribir el `.part` más a menudo: más llamadas al hook de progreso y más CPU, y un
  `SIGKILL` seguiría perdiendo el bloque en curso y el búfer del archivo. Rechazado por el
  usuario.
- **Límite conocido**: con bloques grandes (conexiones rápidas), un `SIGKILL` en mitad de un
  bloque puede perder más de 128 KiB; sigue siendo progreso que se vuelve a descargar, no un
  archivo corrupto (el `.part` solo contiene bytes escritos en orden).

## R21. Conexión estancada sin cortarse (silencio o goteo)

- **Defecto observado** (Windows, datos móviles): la conexión sigue abierta pero no avanza, o
  avanza a goteo; la línea muestra "descargando --- MiB/s" más de 3 minutos. El monitor de
  red indica conexión, así que no es un corte.
- **Hallazgo** (yt-dlp 2026.08.19, verificado): usado como librería, `retries` y
  `fragment_retries` valen `None`, que `RetryManager` trata como 0 (los "10 por defecto" son
  solo de su línea de comandos); `socket_timeout` vale `None` y la capa de red usa
  `DEFAULT_TIMEOUT = 20` s; no hay pausa entre reintentos. Fijábamos `retries=3`,
  `fragment_retries=3` y `socket_timeout=20`. El timeout se aplica a cada lectura de socket:
  - silencio total: 20 s por intento interno, 1 + 3 intentos = ~80 s antes de que el fallo
    llegue al trabajador (por fragmento en HLS); dos rondas más el backoff superan 3 min;
  - goteo: con un byte cada menos de 20 s el timeout no salta nunca y la descarga puede
    quedarse colgada indefinidamente. Ningún ajuste de `socket_timeout` o `retries` lo evita.
  - además, con `requests`, `urllib3.HTTPResponse.read(4096)` no vuelve hasta tener los 4096
    bytes: a goteo, el envoltorio de lectura (R18) no recuperaría el control en horas.
- **Decision**:
  - `retries=1`, `fragment_retries=1` (`ytdl.REINTENTOS_INTERNOS`) y `socket_timeout=15`
    (`ytdl.SOCKET_TIMEOUT`, por defecto en `worker.Tiempos`), en `ytdl.opciones`/`_comunes`:
    el fallo sube pronto a los reintentos indefinidos del trabajador. No se fijan en
    `crear_ydl`, que los tests sustituyen por `fake_extractor.crear_ydl`.
  - Vigilante en `YoutubeDLRegistrado`, activo desde el primer `dl` del intento: anota el
    instante del último byte y una muestra de bytes acumulados como mucho cada 0,25 s. Aborta
    el intento con `errors.SinDatos` si pasan `estancamiento_segundos` (30) sin bytes
    ("sin datos hace N s") o si en los últimos 30 s llegan menos de
    `estancamiento_velocidad_minima` bytes/s (1024; "menos de 1 KiB/s durante 30 s"). Ambos
    umbrales se configuran en `config.toml`.
  - Se comprueba tras cada trozo leído y cuando una lectura o un `urlopen` fallan por
    transporte (un timeout de socket de 15 s, o dos con el reintento interno, completan los
    30 s de silencio). Cada trozo se lee con `read1` de urllib3 (`_real_read` de la respuesta
    de `requests`), que vuelve en cuanto llega algún dato, a través del `read` de yt-dlp, que
    sigue traduciendo las excepciones. Con el manejador `urllib` (si faltara `requests`) se
    sigue usando `read` y el goteo solo se detecta por timeout.
  - `SinDatos` es un `TransportError`: yt-dlp cierra el `.part` como en un corte, y se
    registra como última petición para `DownloadError` sin causa. Una vez estancado, el
    `urlopen` del mismo intento lo relanza al instante, así que el reintento interno de
    yt-dlp no vuelve a esperar 30 s. La respuesta estancada se cierra. Los bytes ya recibidos
    se entregan antes del error (R18), así que el intento siguiente abre una conexión nueva y
    reanuda desde el `.part`.
  - `clasificar_detalle`: `SinDatos` (en la cadena o en el registro) es `TEMPORAL` con
    `estancamiento=True`. En el trabajador no cuenta para el tope de errores ambiguos (ni suma
    ni rompe la racha), no cuenta como intento sin avance y lo descargado en el intento
    estancado se suma al avance del siguiente. La espera es
    `espera(n, base=espera_estancamiento)` con `n` = estancamientos seguidos − 1: unos 5 s la
    primera vez y luego el backoff habitual.
  - La línea de progreso muestra `#12 descargando  sin datos hace N s, reintentando  Título`
    cuando no llegan bytes nuevos al hook desde hace 5 s, y en la terminal cada línea activa
    se recorta al ancho (`no_wrap`, `overflow="ellipsis"`) para que un título largo no la
    parta en dos.
- **Coste medido** (como en R18, 200 MiB contra `flaky_server` en otro proceso, mediana de 10,
  tres series de cada uno, misma sesión): código anterior 183–198 MiB/s; con `read1` y
  vigilancia, 161–200 MiB/s: dentro del ruido de la medida.
- **Alternatives considered**: un hilo vigilante que cierre el socket (cerrar un descriptor
  con un `recv` bloqueado en otro hilo no lo despierta de forma fiable en todas las
  plataformas, y exige atributos privados de urllib3); bajar `socket_timeout` (no detecta el
  goteo). La opción `throttledratelimit` de yt-dlp no se evaluó.

## R22. Qué puede detectar la verificación (enmienda de R8 y de T046)

- **Hallazgos** (verificados con yt-dlp 2026.08.19 contra `flaky_server`):
  1. Un servidor que anuncia un `Content-Length` mayor del que sirve no llega a la
     verificación: el descargador HTTP de yt-dlp lanza `ContentTooShortError` ("0 bytes read,
     5000 more expected") y nunca da el archivo por terminado. Cada reanudación pide
     `Range: bytes=N-`, recibe un 206 sin cuerpo y se cierra: desde el cliente es idéntico a
     un corte repetido en el mismo byte, como los de T016 (`cortar_tras_bytes=0`). Por FR-010
     y FR-026 (los cortes no cuentan para el tope) se reintenta indefinidamente y no se publica
     nada; el `.part` se conserva.
  2. En HTTP, `total_bytes` del evento `finished` es el número de bytes recibidos, que yt-dlp
     ya obliga a coincidir con `Content-Length`; la comparación de tamaño de R8 es una
     comprobación redundante del lado de la escritura en disco. En HLS, el evento `finished`
     se refiere al TS concatenado, que después `FixupM3u8` remuxa a MP4 con otro tamaño: el
     tamaño solo se puede medir en el momento del hook (`publish.formato_terminado`), no
     después sobre el archivo final.
  3. Un fragmento HLS cortado en tránsito tampoco llega a unirse: yt-dlp lo reanuda con
     `Range` o lo reintenta, y si se agotan sus reintentos el error sube como corte.
  4. Un fragmento HLS truncado **en el origen** (servido completo según su `Content-Length`)
     no lo detecta `ffprobe -v error -count_packets`, ni sobre el MP4 remuxado ni sobre el TS:
     termina sin errores, con paquetes en todos los flujos y duración positiva. Con
     `-v warning` sobre el TS aparecen "Packet corrupt" y "PES packet size mismatch" en un
     truncamiento intermedio, pero no si se trunca el último fragmento.
  5. Lo que sí detecta: un archivo que el servidor sirve truncado con un `Content-Length`
     coherente (anuncia menos de lo que mide el archivo). yt-dlp lo da por terminado y
     `ffprobe -v error` sobre el MP4 informa errores ("Invalid NAL unit size", "partial
     file") o no encuentra el `moov`.
- **Decision** (aprobada por el usuario):
  - T046(a) pasa a comprobar que, con `Content-Length` mayor que lo servido, la descarga
    sigue pendiente reintentando, sin pasar a `failed` ni publicar nada. El caso de
    verificación fallida es (a′): `Content-Length` menor que el archivo, que ffprobe rechaza.
  - T046(b) pasa a ser un fragmento HLS cortado en tránsito, que se reintenta y termina
    íntegro. El fragmento truncado en origen queda como límite conocido (spec, Edge Cases):
    no se añade `-v warning` porque no cubre el último fragmento y los avisos de ffmpeg en
    flujos reales darían falsos positivos.
  - La verificación se mantiene como en R8: tamaño medido en el hook `finished` contra
    `total_bytes` exacto y `ffprobe -v error` sobre el archivo final. Si falla, los archivos
    del intento se mueven a `invalidos/<fecha>/` (fecha sin `:`, válida en Windows) y la
    descarga pasa a `failed` (`VerificacionFallida`, un `ErrorPermanente`). Tras una
    publicación correcta se borra el directorio de trabajo salvo `invalidos/`.
- **Alternatives considered**: tratar como error ambiguo, con el tope de 3 de FR-026, una
  respuesta 2xx que se cierra sin ningún byte (exigía enmendar FR-026 y contradice la
  semántica de corte de T016); `ffprobe -v warning` sobre el TS antes del remux (incompleto y
  con falsos positivos).
