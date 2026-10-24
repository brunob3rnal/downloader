# Research: Gestor de descargas de video resiliente

Verificado contra yt-dlp 2026.08.19 cuando se cita su comportamiento. Desde el 2026-10-24,
`uv.lock` fija yt-dlp 2026.10.21: `actualizar` la aceptó tras pasar los tests marcados
`ytdlp_internals` (R14), que cubren lo citado de R1, R3, R4, R19, R21, R23, R24 y R25. El resto
de citas no se ha vuelto a revisar a mano en esa versión, salvo R5 y la lista de caminos que
esquivan `ydl.urlopen` de R4, verificados en 2026.10.21.

## R1. yt-dlp como librería

- **Decision**: usar `yt_dlp.YoutubeDL` en proceso, con un único adaptador (`ytdl.py`) que
  construye las opciones y traduce progreso y errores. Opciones fijas:
  `continuedl=True`, `nopart=False`, `skip_unavailable_fragments=False`,
  `retries=1`, `fragment_retries=1`, `socket_timeout=15` (antes 3, 3 y 20; cambiados por R21),
  `noplaylist=True` al descargar,
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
     | HTTP 416 que llega como error (sin `Content-Range */TOTAL` utilizable, R23) | `PERMANENTE` | `TEMPORAL` ambiguo |
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
    o DASH en directo (rechazados antes, R5), HLS con un `METHOD` distinto de `NONE` y
    `AES-128` que yt-dlp no reconoce como DRM, por ejemplo `SAMPLE-AES` con clave `identity`
    o con un `KEYFORMAT` de Widevine (que `HlsFD` delega en ffmpeg en tiempo de ejecución, sin
    que `dl` lo vea; desde R5 todos se rechazan antes de llegar ahí), `rtmp_ffmpeg` y descargas
    por secciones (no se usan). Su fallo sin señal → `TEMPORAL` ambiguo. El HLS que yt-dlp sí
    reconoce como DRM (`HlsFD._has_drm`: URI `skd://`, `KEYFORMAT` de FairPlay o PlayReady,
    `#EXT-X-FAXS-CM`) no se delega: `HlsFD` falla con "This format is DRM protected"
    (verificado en 2026.10.21, `yt_dlp/downloader/hls.py`; antes este texto decía, por error,
    que también se delegaba).
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

- **Decision (directos)**: rechazar como `PERMANENTE` si `info['is_live']` es verdadero.
  `YoutubeDLRegistrado.process_info` lanza `ErrorPermanente`: es el paso por el que yt-dlp
  entrega cada video resuelto a la descarga, antes de elegir el descargador (FFmpegFD para HLS
  en directo) y de pedir ningún medio. No se hace en `dl`, que debe seguir anotando la fase y
  el descargador externo de cualquier info (R4).
- **Regla (DRM, constitución v3.0.0, Principio V; FR-015)**: el criterio es el sistema de
  licencias, no el transporte de la clave. Es DRM todo lo que dependa de uno: URI `skd://`,
  FairPlay, PlayReady, Widevine, un PSSH y cualquier `KEYFORMAT` distinto de `identity`. Se
  acepta `METHOD=AES-128` con `KEYFORMAT` ausente (RFC 8216 §4.3.2.4: equivale a `identity`) o
  `identity`, con la clave por HTTP(S) desde la URI del manifiesto (con la misma sesión del
  usuario), en una URI `data:` o en `info['hls_aes']` del extractor.
- **Hallazgos** (yt-dlp 2026.10.21, verificado con `HlsFD.can_download` y `HlsFD._has_drm`
  sobre manifiestos de prueba, y leyendo `yt_dlp/downloader/hls.py`, `fragment.py` y
  `extractor/common.py`):

  | `METHOD` | Clave | Qué hace yt-dlp | Decisión |
  |----------|-------|-----------------|----------|
  | `NONE` | — | descargador nativo | se descarga |
  | `AES-128` | URI http(s), sin `KEYFORMAT` o `identity` | nativo; pide la clave con `ydl.urlopen` (mismas cookies y cabeceras) y descifra con pycryptodomex | se descarga |
  | `AES-128` | URI `data:` con la clave, `identity` | nativo; el manejador `urllib` admite `data:` y descifra con esa clave | se descarga |
  | `AES-128` | `info['hls_aes']` (clave, URI o IV del extractor) | nativo; sustituye a la URI del manifiesto | se descarga si el manifiesto no declara DRM |
  | `AES-128` | `KEYFORMAT` Widevine (`urn:uuid:edef8ba9-…`) u otro distinto de `identity` | nativo; intenta usar como clave lo que devuelva la URI | **DRM** (yt-dlp no lo rechaza) |
  | `AES-128` | URI `data:` con un PSSH | nativo; usaría el PSSH como clave | **DRM** (yt-dlp no lo rechaza) |
  | `AES-128` o `SAMPLE-AES` | URI `skd://` o `KEYFORMAT` de FairPlay o PlayReady | `_has_drm`: la extracción marca el formato `has_drm` y lo descarta; si llega a descargarse, `HlsFD` falla con "This format is DRM protected" sin delegar | DRM (ya lo rechaza yt-dlp; también nuestro código) |
  | `SAMPLE-AES`, `SAMPLE-AES-CTR` | `KEYFORMAT` Widevine | delega en ffmpeg, que falla | **DRM** (yt-dlp no lo rechaza) |
  | `SAMPLE-AES`, `SAMPLE-AES-CTR` u otro | URI http(s), `identity` | delega en ffmpeg, que descifra | **no soportado** (ver abajo) |
  | cualquiera | atributos con `URI` antes que `METHOD` | la expresión `#EXT-X-KEY:METHOD=(?!NONE\|AES-128)` no casa: descargador nativo, que solo descifra `AES-128` y **guarda cifrados** los fragmentos de otro método | según sus atributos, igual que con el orden habitual |

  Además, `#EXT-X-SESSION-KEY` del manifiesto maestro solo cuenta para `_has_drm`. Lo marcado en
  negrita no lo detecta yt-dlp, y lo que sí detecta depende del orden de los atributos.
- **Decision (DRM y métodos no soportados)**:
  - La detección la hace nuestro código con un analizador propio de atributos (módulo
    `src/downloader/drm.py`, funciones puras; plan, decisión 30), no con la expresión de
    yt-dlp: separa la lista de atributos por comas fuera de comillas, admite cualquier orden,
    compara `METHOD` y `KEYFORMAT` sin distinguir mayúsculas y quita las comillas de `URI` y
    `KEYFORMAT`. Se aplica a cada `#EXT-X-KEY` y `#EXT-X-SESSION-KEY` de cada manifiesto.
  - Reglas, en este orden, para cada etiqueta con `METHOD` distinto de `NONE`:
    1. **DRM** si la URI empieza por `skd://`; si `KEYFORMAT` existe y no es `identity` (cubre
       FairPlay `com.apple.streamingkeydelivery`, PlayReady `com.microsoft.playready`, Widevine
       `urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed` y cualquier otro); o si la URI es `data:`
       y su contenido decodificado es una caja PSSH (bytes 4–8 = `pssh`). También si el
       manifiesto tiene `#EXT-X-FAXS-CM`. Mensaje: "contenido con DRM (<sistema>): no se puede
       descargar".
    2. **No soportado** si `METHOD` no es `AES-128` (`SAMPLE-AES`, `SAMPLE-AES-CTR` o
       desconocido). Mensaje: "no soportado: el cifrado SAMPLE-AES solo se puede descargar a
       través de ffmpeg, que no permite reanudar de forma fiable". Motivo: yt-dlp delega en
       `FFmpegFD`, que no continúa un `.part` ni fragmentos (cada intento empezaría de cero, en
       contra de FR-008) y cuyas peticiones no ve el registro de R4.
    3. En otro caso se acepta: `AES-128` con la clave por URI http(s), `data:` o `hls_aes`.
    Los dos rechazos son `ErrorPermanente`, sin reintentos (R4, regla 4).
  - Dónde se aplica, siempre antes de que yt-dlp pida la clave o un fragmento: sobre cada
    cuerpo que empiece por `#EXTM3U` leído por `YoutubeDLRegistrado.urlopen` (R4), en las dos
    fases, analizando en cada lectura las líneas completas recibidas: al extraer, el manifiesto
    maestro, antes de que el extractor lo analice; al descargar, antes de entregarlo a `HlsFD`
    (que, si no, pasaría a ffmpeg los métodos que no sabe descifrar). En `process_info` (como
    `is_live`), sobre `info['hls_media_playlist_data']` y la URI de `info['hls_aes']`, que la
    descarga usaría sin pedirlos; ahí también se vuelve a lanzar el primer rechazo de la
    extracción, por si el extractor capturó el error y siguió con otros formatos. El error se
    registra como última petición para que la clasificación no dependa de cómo yt-dlp lo
    envuelva: `errors.clasificar_detalle` trata como permanente un error permanente registrado.
  - Una URI `data:` cuyo contenido no se puede decodificar no es una caja PSSH: el analizador
    no lanza y la acepta (regla 3); yt-dlp no podrá leer la clave y la descarga fallará como
    permanente por su cuenta.
  - Cobertura (Fase 10 de tasks.md): `tests/unit/test_drm.py` (analizador y cada fila de la
    tabla), `test_directo_drm.py` (rechazos, sin peticiones de clave ni de fragmentos y sin
    lanzar ffmpeg) y `test_hls_aes.py` (aceptados y descifrados). DASH, ISM y F4M: T095 y T096
    (Fase 11).
- **Regla (DASH, ISM y F4M; FR-015, hallazgo G1 de `/speckit-analyze`)**: cualquier indicador de
  protección rechaza la descarga como DRM permanente (`ErrorPermanente`, sin reintentos), antes
  de pedir ningún segmento y aunque el manifiesto tenga también representaciones sin protección.
  Indicadores: un `ContentProtection` hijo de un `AdaptationSet` o de una `Representation` del
  MPD, sea cual sea su `schemeIdUri` (también `urn:mpeg:dash:mp4protection:2011` solo, que no
  nombra ningún sistema de licencias); un `Protection` hijo de `SmoothStreamingMedia` en ISM; y
  en F4M, un `drmAdditionalHeader` o `drmAdditionalHeaderSet`, o un `media` con
  `drmAdditionalHeaderId` o `drmAdditionalHeaderSetId`. Mensaje: "contenido con DRM
  (DASH|ISM|F4M): no se puede descargar". Es el mismo criterio que en HLS, donde un DRM en el
  manifiesto maestro rechaza todas sus variantes. Sin esta regla, yt-dlp elige en silencio lo
  que queda sin proteger: otra calidad, o solo el audio.
- **Hallazgos (DASH, ISM y F4M)**: verificados el 2026-10-24 con el yt-dlp instalado
  (2026.10.21), con manifiestos servidos por un servidor HTTP local y el extractor genérico; la
  elección de formato, con `ytdl.crear_ydl` y los cuatro selectores de R6 (`download=False`); las
  peticiones de medios, con `extract_info(download=True)` de `YoutubeDL` sin nuestra subclase.
  También leyendo `_parse_mpd_formats_and_subtitles`, `_parse_ism_formats_and_subtitles` y
  `_parse_f4m_formats` (`extractor/common.py`), `remove_encrypted_media` y `F4mFD`
  (`downloader/f4m.py`) y `process_video_result` (`YoutubeDL.py`). El script no está en el
  repositorio.

  | Manifiesto | Qué hace yt-dlp | Peticiones de medios |
  |------------|-----------------|----------------------|
  | MPD con `ContentProtection` Widevine y `cenc:pssh` en el `AdaptationSet` de video; audio sin proteger | marca `has_drm=True` el video y lo descarta; con los cuatro selectores elige **solo el audio**, sin aviso | las del audio |
  | MPD con solo `urn:mpeg:dash:mp4protection:2011` (con y sin `cenc:default_KID`), en el `AdaptationSet` o en la `Representation` | igual: cuenta cualquier `ContentProtection` hijo directo, sin mirar `schemeIdUri` (elección verificada solo con `mejor`) | las del audio |
  | MPD mixto: 1080p con `ContentProtection` y 720p sin él, en `AdaptationSet` distintos o en el mismo | descarta la 1080p y elige `720p+audio`, sin aviso, con `mejor`, `1080p` y `720p` | las de la 720p y el audio |
  | MPD con video y audio protegidos | descarta todo y falla al extraer con "This video is DRM protected" (`ExtractorError`, `expected=True`), antes de `process_info` | ninguna |
  | MPD con `ContentProtection` solo como hijo de `Period` o de `MPD` | no lo marca: lo descarga como si no estuviera protegido | todas |
  | ISM con `<Protection>` (PlayReady) | marca `has_drm=True` todos los formatos (el elemento vale para todo el manifiesto) y falla al extraer con "This video is DRM protected" | ninguna |
  | F4M con un `media` con `drmAdditionalHeaderId` | **no marca `has_drm`**: descarta el `media` al extraer; sin ninguno más, falla con "No video formats found!" (`expected=False`), sin nombrar el DRM | ninguna |
  | F4M con un `media` cifrado y otro sin cifrar | descarta el cifrado en silencio y elige el otro; `_has_drm` vacío | no comprobado (el `bootstrapInfo` de prueba no era válido y la descarga falló antes de pedir fragmentos) |
  | F4M con un `drmAdditionalHeader` sin `id` | la extracción lo acepta; al descargar, `F4mFD` falla con "Missing ID in f4m DRM" | ninguna |

  - En DASH e ISM, cuando quedan formatos sin proteger, `info['_has_drm']` sigue en `True` al
    llegar a `process_info`; cuando no queda ninguno, yt-dlp falla antes, con su mensaje. En F4M
    yt-dlp no marca nada, así que, como en HLS, el indicador hay que leerlo en el manifiesto.
  - `has_drm='maybe'` solo lo usa el extractor de YouTube, para formatos HLS: yt-dlp los
    conserva, no los cuenta en `_has_drm` y los prueba descargando un fragmento antes de
    elegirlos (`_check_formats`). Quedan fuera de esta regla porque son HLS y pasan por el
    analizador de `drm.py`.
  - Límite conocido: un `ContentProtection` hijo de `Period` o de `MPD` no lo marca yt-dlp y
    queda fuera de la regla.
  - No comprobado: manifiestos reales de sitios (los tests no usan internet); si el esquema de
    DASH admite `ContentProtection` como hijo de `Period` o de `MPD`, y si aparece así en sitios
    reales; un `ContentProtection` en `SubRepresentation` o `ContentComponent`; un
    `ContentProtection` con `ref` a uno del nivel `MPD` (por lectura del código, yt-dlp lo
    contaría si está en el `AdaptationSet` o la `Representation`; no lo ejecuté); el error
    "Unsupported DRM" de `F4mFD`, porque con todos los `media` cifrados la extracción falla antes;
    y los formatos `maybe` de YouTube. El ISM de prueba sin protección se extrajo sin `has_drm`
    (control); su descarga no terminó, porque los fragmentos de prueba no eran válidos.
- **Rationale**: Principio V; ninguna clave de un sistema de licencias se llega a pedir. El
  cifrado que solo ffmpeg sabe descargar se rechaza por FR-008, no por el Principio V.
- **Alternatives considered**: confiar solo en `_has_drm` (deja pasar Widevine y el PSSH, y
  depende del orden de los atributos); usar `yt_dlp.utils.parse_m3u8_attributes` (interno de
  yt-dlp y sin la garantía de los tests `ytdlp_internals`); aceptar `SAMPLE-AES` `identity` a
  través de ffmpeg (sin reanudación ni registro de peticiones); rechazar todo HLS cifrado
  (también el `AES-128` sin licencias, que el Principio V v3.0.0 permite).

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
    255 bytes UTF-8 y, en Windows, la ruta completa en 240 unidades UTF-16 (antes 259; plan,
    decisión 22: margen para programas que añaden algo a la ruta al abrirla). La extensión no se
    recorta; si no queda sitio para el título, el nombre es el id.
  - Nombre (enmienda del 2026-10-24): `plantilla_nombre` de `config.toml`, con los campos
    `{titulo}` e `{id}`; por defecto `{titulo}`, sin el id, porque las colisiones ya se
    resuelven con sufijo (FR-022). Antes era siempre `<titulo> [<id>]`. La plantilla se valida
    al cargar la configuración: campos conocidos, al menos uno, llaves cerradas y sin `/` ni `\`
    (no crea subcarpetas). `sanitize_filename` se aplica al nombre ya compuesto.
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
  con aspecto de token antes de mostrar o registrar mensajes. Navegadores admitidos, fiabilidad
  en Windows y errores de lectura: R25.
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
- **Enmienda (2026-10-24, FR-025)**: el programa depende de detalles internos de yt-dlp que
  ninguna versión garantiza (el registro de peticiones de R4, `_real_read` de R21, la petición
  repetida tras un 416 de R23, el `.part` abierto de R24…). Tras `uv sync`, `actualizar` ejecuta
  `uv run pytest -m ytdlp_internals` en el proyecto (unos 25 s en Linux). Si fallan, restaura el
  contenido anterior de `uv.lock` byte a byte, vuelve a ejecutar `uv sync`, sale con 1 y el
  mensaje lleva las líneas `FAILED` de pytest y "se conserva yt-dlp <anterior>"; si la
  restauración del entorno también falla, lo dice y pide `uv sync`. Si `uv sync` falla tras
  `uv lock`, también se restaura. Con la misma versión no sincroniza ni ejecuta tests. Exige
  ffmpeg y ffprobe antes de modificar nada: los tests marcados que descargan se omitirían sin
  ellos y una versión rota se aceptaría. La versión se lee del `uv.lock`, no del entorno
  cargado. Primera ejecución real: yt-dlp 2026.8.19 → 2026.10.21, con los 40 tests marcados en
  verde.
- **Marcados `ytdlp_internals`**: los que prueban R1 (HLS nativo y AES), R4 (registro de
  peticiones), R19 (respuestas de la extracción), R23 (416), R24 (`.part` abierto), y además
  R3 (borrado del `.part` sin `Range`), R21 (`_real_read`/`read1`) y R25 (cadena de
  `CookieLoadError`), que también dependen de detalles internos. La lista exacta la fija
  `tests/unit/test_marcadores.py`.

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
    supera el tamaño del archivo en más de un 1 % más 128 KiB (SC-002 para interrupciones
    abruptas, R20).
  - Ctrl+C: en Windows no se puede enviar Ctrl+C a un solo proceso, así que el test lanza
    `ejecutar` con `CREATE_NEW_PROCESS_GROUP` y le envía `CTRL_BREAK_EVENT` (R17); en Linux,
    `SIGINT`.
  - Marcador `ffmpeg`: `conftest.py` omite esos tests si `shutil.which("ffmpeg")` o
    `shutil.which("ffprobe")` es `None`.
  - Sin acceso a internet en ningún test.
  - yt-dlp reintenta por su cuenta cortes y errores 5xx hasta `retries` (1 desde R21; antes 3). Para que un test
    pruebe nuestros reintentos, el servidor debe fallar más veces seguidas que `retries` y el
    test debe comprobar que la fila pasó por `waiting_network`.
  - Los archivos que sirve el servidor en tests de descarga son medios válidos (MP4 y HLS
    generados con ffmpeg al inicio de la sesión), porque la verificación con ffprobe (R8)
    rechazaría bytes aleatorios. Por eso los tests que descargan llevan el marcador `ffmpeg`.
- **Rationale**: Principio II; los cortes de red se prueban contra yt-dlp real, no contra un
  doble.
- **Tiempos comprimidos frente a 24 horas sin red (SC-001)**: el trabajador no tiene ningún
  límite de tiempo total ni de número de intentos para los errores temporales: solo
  `intentos_sin_avance`, que fija la espera con un tope (R10), y `proximo_intento`. Un corte de
  24 horas es la misma rama del bucle (`waiting_network` → espera → nuevo intento) repetida unas
  290 veces con la espera en su tope de 300 s; con `tiempos_rapidos` (tope 0,5 s) se recorre la
  misma rama, solo que más deprisa. Lo que cambia con el tiempo real fuera del programa (la URL
  de medios caduca) lo cubre la re-extracción de cada intento (R2) y T051. No se prueba la
  duración en sí: un `ejecutar` de 24 horas no aporta más casos.
- **`Popen.kill()` frente a un corte de luz o un reinicio (SC-003)**: `SIGKILL` en Linux y
  `TerminateProcess` en Windows no dejan ejecutar nada al proceso, como un corte de luz: ni
  manejadores de señales, ni `finally`, ni el guardado de progreso de Ctrl+C. Lo que queda es lo
  que el proceso ya había entregado al sistema operativo: las transacciones de SQLite
  confirmadas y lo escrito en el `.part`. Para la cola, la equivalencia es completa porque
  `synchronous=FULL` (R11) hace `fsync` en cada commit: lo confirmado sobrevive también a un
  corte de luz, y al relanzar se recupera igual (filas en `downloading` → `queued`). Un
  reinicio ordenado del equipo es un caso más suave: el sistema vacía su caché al apagarse.
- **Corte de luz (límite conocido; fuera de SC-002 y excepción de SC-004 desde el 2026-10-24)**: `Popen.kill()` no
  simula la pérdida de la caché de páginas del sistema, así que no equivale a un corte de luz.
  - Se garantiza: la cola tal como quedó en la última transacción confirmada, porque
    `synchronous=FULL` hace `fsync` en cada commit (R11), y al relanzar la misma recuperación
    que tras `SIGKILL`; la verificación final de R8 antes de publicar, también cuando la
    descarga se reanuda tras el corte, porque ningún camino publica sin ella; y que lo que no la
    pase se aparta en `invalidos/<fecha>/` sin borrarse (R8, R22).
  - No se garantiza: el contenido del `.part` que el sistema no había volcado a disco (en Linux,
    por defecto, hasta unos 30 s de escrituras). Se pierde y se vuelve a descargar; el exceso no
    está acotado por SC-002. Como no se garantiza, el corte de luz es también la segunda excepción
    de SC-004, junto al HLS truncado en origen (R22).
  - Hipótesis no verificada (zona vacía): en sistemas de archivos que no ordenan las escrituras
    de datos y de metadatos, el `.part` podría conservar un tamaño cuyos últimos bloques no
    contienen los bytes recibidos (ceros o datos anteriores). yt-dlp reanudaría tras esa zona y
    el archivo final la contendría; solo la verificación podría detectarlo, si ffprobe encuentra
    el daño, y si no lo encuentra el archivo se publicaría incompleto. Con `ext4` en modo
    `data=ordered` (el habitual) no debería ocurrir; en NTFS no se ha comprobado. No hay test que
    lo cubra: exigiría cortar la alimentación o simular la caché de páginas.
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
    cuando vuelve, como mucho tras `socket_timeout` (15 s desde R21). En Linux, `EINTR` la corta al
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

- **Contexto** (el corte de luz salió de SC-002 el 2026-10-24; ver R15): un `SIGKILL` o un
  corte de luz no se pueden capturar: lo recibido que aún no
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

## R23. HTTP 416 al reanudar un parcial completo o que no encaja

- **Hallazgos** (verificados en yt-dlp 2026.08.19, `yt_dlp/downloader/http.py`):
  1. Al reanudar, yt-dlp pide `Range: bytes=N-` con N = tamaño del `.part`. Si el `.part` ya
     tiene todo el archivo (cierre abrupto justo tras el último byte, o un servidor que
     anunciaba de más y se corrige, R22), el servidor responde 416 con
     `Content-Range: bytes */TOTAL`.
  2. Ante un 416, yt-dlp repite la petición "sin la cabecera Range" para comparar el
     `Content-Length` del archivo entero con el `.part`, pero la repite **con** `Range`: el
     setter de `Request.headers` guarda el mismo `HTTPHeaderDict` que recibe, así que
     `request.headers['Range'] = …` modifica también el `headers` compartido con la petición
     repetida. Recibe otro 416 y lo lanza: `HTTP Error 416`, que R4 clasificaba como "otro 4xx"
     → `PERMANENTE`. La descarga fallaba con el archivo entero en disco.
  3. Aunque la petición repetida saliera sin `Range`, la comparación de yt-dlp no sirve aquí:
     acepta como completo un `.part` que difiera menos de 100 bytes (issue #175 de
     youtube-dl), no cierra la respuesta de ese GET completo (el servidor empieza a enviar el
     archivo aunque nadie lo lea) y emite `finished` con `total_bytes` = tamaño del `.part`.
- **Decision**: `YoutubeDLRegistrado.urlopen` resuelve el 416 de la fase de descarga con
  comparación exacta, sin depender de que yt-dlp corrija su error:
  - `Content-Range: bytes */TOTAL` con TOTAL == N: el `.part` está completo. Se relanza el
    416 y la petición repetida de yt-dlp recibe, sin red, una respuesta vacía con
    `Content-Length: TOTAL`; yt-dlp lo da por terminado (`finished` con `total_bytes` = N =
    TOTAL). Después la verificación de R8 (tamaño y ffprobe) es la misma de siempre: un
    `.part` del tamaño correcto pero dañado no se publica.
  - TOTAL < N: el `.part` no encaja con el archivo del servidor. En lugar del 416 se devuelve
    el GET del archivo entero sin `Range`; yt-dlp ve un 200 sin `Content-Range`, borra el
    `.part` y descarga desde cero (FR-024, R3) aunque la diferencia sea menor de 100 bytes. El
    aviso dice el motivo: "reiniciada desde cero: el parcial (N bytes) es mayor que el archivo
    del servidor (TOTAL bytes)". El motivo llega al trabajador en `Progreso.motivo_reinicio`
    a través del parámetro `downloader_reinicios`.
  - Sin `Content-Range` (o TOTAL > N, una respuesta incoherente): la petición repetida recibe
    el mismo 416 sin red, y en la fase de descarga el 416 es `TEMPORAL` ambiguo
    (`motivo="HTTP 416"`): se reintenta con el tope de 3 intentos sin avance de FR-026, nunca
    `PERMANENTE` de entrada. Al extraer sigue siendo `PERMANENTE` (otro 4xx).
- **Alternatives considered**: quitar solo `Range` de la petición repetida (conserva la
  tolerancia de ±100 bytes y el GET completo sin cerrar); copiar las cabeceras en cada
  `Request` sobrescribiendo métodos del descargador HTTP de yt-dlp (mucho más acoplamiento);
  clasificar todo 416 como ambiguo (un `.part` completo agotaría el tope y fallaría).

## R24. Archivos que siguen abiertos en Windows tras un intento fallido

- **Hallazgos** (CI de Windows del PR #8; verificados en yt-dlp 2026.08.19 y CPython 3.12):
  1. Cuando un fragmento HLS/DASH agota sus reintentos, el error sube como `DownloadError`
     por `except DownloadError: if fatal: raise` (`yt_dlp/downloader/fragment.py`), que no
     pasa por el `error_callback` que cierra `ctx['dest_stream']`. El `.part` queda abierto,
     retenido por los marcos de la traza en un ciclo de referencias, hasta que actúa el
     recolector de ciclos de Python.
  2. En Linux eso no impide nada. En Windows, Python abre los archivos sin
     `FILE_SHARE_DELETE`: el intento siguiente puede seguir escribiendo en el mismo `.part`,
     pero al terminar no puede renombrarlo (`WinError 32`), yt-dlp lo da por error y la
     descarga quedaba `failed` (`PERMANENTE`, sin señal de red). Que el recolector actúe
     entre dos intentos depende de las reservas de memoria anteriores: en `main` el test
     pasaba, y con los tests de la fase 7, que se ejecutan antes, fallaba en 2 de 2.
  3. `apartar_invalidos` nombra la carpeta con la fecha en microsegundos, pero en Windows el
     reloj de Python 3.12 avanza a saltos (0,5 ms en el runner del CI, hasta 16 ms): dos
     llamadas seguidas daban el mismo nombre y `mkdir` fallaba con `FileExistsError`.
- **Decision**: el trabajador llama a `gc.collect()` antes de cada intento de descarga, cuando
  el error del intento anterior ya no está referenciado; cerrar ese archivo no es posible
  desde fuera de yt-dlp sin recorrer sus marcos. `apartar_invalidos` añade `-1`, `-2`… si la
  carpeta de esa fecha ya existe. Los tests lo comprueban sin depender del sistema ni del
  reloj: con el recolector desactivado, ningún archivo del directorio de trabajo sigue
  abierto al empezar un intento (por `fstat` de los objetos de archivo vivos), y con el reloj
  fijado, dos llamadas crean dos carpetas.
- **Alternatives considered**: clasificar `WinError 32` como temporal (el siguiente intento
  volvería a fallar mientras el archivo siga abierto); vaciar los marcos de la traza con
  `traceback.clear_frames` (depende de qué variables retiene yt-dlp en cada versión); un
  contador o un UUID en lugar de la fecha (se pierde el orden legible de los intentos).

## R25. Cookies del navegador en Windows y errores al leerlas

- **Hallazgos** (verificados en el código de `yt_dlp/cookies.py` y `YoutubeDL.py` de yt-dlp
  2026.08.19; las ramas de Windows, emuladas en Linux con la misma cadena de excepciones; el
  WAL de Firefox, probado con SQLite real):
  1. yt-dlp lee las cookies en la primera petición de cada `YoutubeDL` (`cookiejar`, propiedad
     perezosa) y solo en memoria: copia la base de datos del navegador a un directorio
     temporal propio y la borra al terminar. Sin `cookiefile` no escribe ninguna cookie.
  2. Cualquier fallo de la lectura se envuelve en `CookieLoadError("failed to load cookies")`
     con la excepción original como `__context__`, y llega como `DownloadError`. En el código
     de cookies, `logger.error` (`report_error`) ya lanza la excepción: un solo fallo aborta
     toda la lectura, no solo la de esa cookie.
  3. Chrome y Edge (y los demás basados en Chromium) en Windows: mientras el navegador está
     abierto, la base `Cookies` está bloqueada; la copia falla con `PermissionError` 13 y el
     mensaje de yt-dlp es "Could not copy Chrome cookie database. See …/issues/7271", que dice
     "Chrome" también para Edge y no explica que hay que cerrarlo.
  4. Cifrado en Windows: `WindowsChromeCookieDecryptor` solo descifra valores `v10` (AES-GCM
     con la clave de `Local State` protegida con DPAPI); cualquier otro prefijo lo pasa a
     DPAPI. Los valores `v20` de Chrome 127 y posteriores (cifrado ligado a la aplicación,
     *App-Bound Encryption*) no son de DPAPI: `CryptUnprotectData` falla y la lectura entera
     se aborta con "Failed to decrypt with DPAPI. See …/issues/10927", sin nombrar el
     navegador. Esta versión de yt-dlp no tiene soporte para `v20` (la incidencia sigue
     abierta). Edge usa el mismo código: funciona con el navegador cerrado mientras sus
     cookies sean `v10`; si adopta el cifrado ligado a la aplicación, fallará como Chrome. No
     se ha podido comprobar en un Windows real qué versión de Edge cifra así.
  5. Firefox: `cookies.sqlite` no está cifrado, y yt-dlp no tiene ninguna rama que dependa de
     que Firefox esté cerrado: la copia funciona con Firefox abierto porque SQLite abre sus
     archivos compartidos para lectura en Windows (según el código de SQLite; no comprobado en
     un Windows real). Pero Firefox usa WAL y yt-dlp
     copia solo el archivo principal: una cookie confirmada en `cookies.sqlite-wal` y aún no
     integrada no se ve (comprobado: no aparece con la conexión abierta y aparece al cerrarla).
     Una sesión recién iniciada puede no llegar hasta que Firefox se cierre o integre el WAL.
     Con varios perfiles se usa el `cookies.sqlite` modificado más recientemente.
  6. `safari` solo existe en macOS; en Windows y Linux falla con "unsupported platform".
- **Fiabilidad en Windows**: Firefox es el único fiable (abierto o cerrado, con la salvedad
  del WAL). Edge, solo cerrado y mientras no cifre con clave ligada a la aplicación. Chrome
  127+ no funciona con esta versión de yt-dlp, ni cerrado.
- **Decision**:
  - `--cookies-de` admite los nombres de `yt_dlp.cookies.SUPPORTED_BROWSERS` (sin perfil),
    en minúsculas; otro valor sale con 2 y lista los admitidos. No se restringe a Firefox:
    el mensaje de error explica el fallo cuando ocurre.
  - Un fallo de lectura se convierte en `CookiesIlegibles` (`ErrorPermanente`) con el
    mensaje «No se pudieron leer las cookies de NAVEGADOR: causa», con la causa traducida
    para la base bloqueada (cerrar el navegador), el fallo de DPAPI (usar otro navegador) y
    la base no encontrada; en otro caso, el mensaje original de yt-dlp sin sus «ERROR: ».
  - Alcance: las cookies de `agregar --cookies-de` son de esa descarga, que queda `failed`
    y la cola sigue (código 1). Las de `ejecutar --cookies-de` valen para todas las
    descargas de la ejecución, que fallarían igual: se detiene como error FATAL (código 3),
    la descarga vuelve a `queued` y ninguna se da por fallida.
  - Reencolar con `agregar --cookies-de` una descarga `failed` guarda el navegador nuevo; es
    lo que pide el mensaje de FR-026 («vuelva a agregarlo con --cookies-de NAVEGADOR»). Sin
    la opción se conserva el anterior; una descarga pendiente no cambia.
  - `config.toml` rechaza las claves con nombre de credencial o cookie con un mensaje que lo
    explica y remite a `--cookies-de`, sin repetir el valor.
- **Rationale**: Principio III (qué falló, en qué recurso y qué hacer) y FR-017. Con FATAL
  para las cookies de una fila, una causa que no se corrige (Chrome 127+) dejaría la cola
  atascada en esa fila; con PERMANENTE para las de `ejecutar`, un navegador abierto daría por
  fallida toda la cola y habría que volver a agregar cada URL.
- **Alternatives considered**: admitir solo `firefox` (el contrato promete los navegadores de
  yt-dlp, y Edge funciona cerrado); leer las cookies una vez al iniciar `ejecutar` para
  comprobarlas (una lectura más por ejecución, y no cubre las de cada fila); reintentar el
  fallo como TEMPORAL (un navegador abierto no se cierra solo, y Chrome 127+ no se arregla
  esperando).

## R26. Archivos en uso en Windows al publicar

- **Contexto**: en Windows, el antivirus o el indexador abren los archivos recién escritos para
  examinarlos. Mientras, enlazar, copiar o borrar ese archivo falla con `PermissionError` y
  `winerror` 32 (ERROR_SHARING_VIOLATION) o 5 (ERROR_ACCESS_DENIED). Se libera en fracciones de
  segundo. No se ha podido reproducir en el CI; la decisión se prueba con errores simulados.
- **Decision**: `publish.publicar` reintenta cada paso por separado (`os.link`, la copia
  exclusiva de FAT32/exFAT y el `os.unlink` del origen) hasta 3 veces, tras 0,2, 0,5 y 1 s. Solo
  esos dos `winerror`: cualquier otro error, o un `PermissionError` sin `winerror` (Linux), sale
  al instante. Un `winerror` 5 de `os.link` que persiste tras los reintentos sigue llevando a la
  copia, como antes (R7). Si tras publicar el origen no se puede borrar, la publicación vale y se
  avisa con la ruta del archivo que queda; `worker._limpiar_trabajo` tampoco convierte en fallo
  un archivo que no puede borrar. Antes, una excepción ahí llegaba a `_fallo` con la descarga ya
  `completed` y `completed → failed` detenía `ejecutar` con una traza.
- `apartar_invalidos` ya creaba la carpeta con `mkdir(exist_ok=False)` dentro del bucle de
  sufijos, sin comprobar antes con `exists()` (R24); solo se hace explícito `exist_ok=False`.
- **Alternatives considered**: reintentar la publicación entera (tras enlazar crearía una
  segunda copia con ` (1)`); esperas aleatorias (no se ganaba nada y los tests serían menos
  deterministas).

## R27. Secretos en las URLs y en `cola.db`

- **Hallazgo**: `ocultar_secretos` solo ocultaba los parámetros llamados exactamente `token`,
  `sig`, `signature`, `key` o `auth`. Una URL prefirmada (`X-Amz-Signature`,
  `X-Amz-Security-Token`, `Key-Pair-Id`, `Policy`, `lsig`, `access_token`, `hdnts`) pasaba
  entera a la salida y a `ultimo_error`, por ejemplo en "Unsupported URL: …".
- **Decision**: el nombre del parámetro se divide en palabras por `-`, `_` y `.`, y se oculta el
  valor si alguna es `token`, `sig`, `signature`, `key`, `auth`, `credential`, `policy`, `hmac`,
  `hdnts`, `password` o `secret`, o si el nombre termina en `token`, `sig` o `signature`.
  `monkey`, `itag` o `X-Amz-Date` se conservan.
- **Alcance**: los tokens y firmas que se obtienen al extraer nunca se guardan. La URL que da el
  usuario se guarda tal cual en la columna `url` de `cola.db`, porque hace falta para
  descargarla; si lleva un token, queda ahí (README, límites conocidos). En los mensajes, la
  salida y las demás columnas aparece con los secretos ocultos. Lo comprueba
  `tests/integration/test_secretos.py` con la cookie ficticia, un token y tres firmas por
  extracción y una URL prefirmada del usuario.
