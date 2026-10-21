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
  descarga, con plantilla de nombre fija (`%(id)s.%(ext)s`). yt-dlp continúa los `.part`
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
- **Rationale**: `os.rename`/`os.replace` sobrescriben en POSIX sin avisar (viola FR-022).
  El nombre final nunca aparece en la carpeta de destino hasta pasar la verificación
  (FR-013, SC-004). Al estar en el mismo sistema de archivos, la publicación es atómica.
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
- **Concurrencia de procesos**: un único `ejecutar` a la vez, garantizado con `fcntl.flock`
  sobre `ejecutar.lock` en el mismo directorio (el sistema operativo libera el bloqueo si el
  proceso muere).

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
  - Prueba de cierre abrupto: lanzar `ejecutar` en un subproceso, `SIGKILL` a mitad,
    relanzar y comprobar que el total de bytes servidos no supera el tamaño del archivo
    en más de un 1 % (SC-002).
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
