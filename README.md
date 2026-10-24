# downloader

Gestor de descargas de video por línea de comandos, basado en
[yt-dlp](https://github.com/yt-dlp/yt-dlp). Guarda las URLs en una cola persistente y las
descarga una a una, reanudando tras cortes de red, cierres del programa o reinicios del equipo.
Un archivo solo aparece con su nombre final en la carpeta de destino cuando está completo y
verificado.

## Instalación

Requisitos:

- Windows 10/11 o Linux (macOS no se verifica).
- [uv](https://docs.astral.sh/uv/), que instala Python 3.12 si hace falta.
- ffmpeg y ffprobe en el `PATH`: unen audio y video y verifican cada archivo antes de
  publicarlo. Sin ellos, `ejecutar` sale con el código 3 sin tocar la cola.
- git, para clonar el repositorio.

### Windows

En PowerShell:

```powershell
winget install --id=astral-sh.uv -e
winget install Gyan.FFmpeg          # o: choco install ffmpeg
git clone https://github.com/brunob3rnal/downloader.git
cd downloader
uv sync
```

Abra una terminal nueva después de instalar ffmpeg para que el `PATH` lo incluya, y compruebe
`ffmpeg -version` y `ffprobe -version`.

### Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install ffmpeg             # Debian, Ubuntu; en otras, el gestor de la distribución
git clone https://github.com/brunob3rnal/downloader.git
cd downloader
uv sync
```

## Uso

Los comandos se ejecutan desde el directorio del repositorio con `uv run downloader`. El
contrato completo (salidas y códigos) está en
[specs/001-resilient-video-downloader/contracts/cli.md](specs/001-resilient-video-downloader/contracts/cli.md).

### `agregar`

```bash
uv run downloader agregar URL [URL...] [--calidad mejor|1080p|720p|audio] [--carpeta RUTA] [--cookies-de NAVEGADOR]
```

Guarda las URLs en la cola; no necesita red. Una lista de reproducción se expande en sus
videos al ejecutar. Una URL que ya está en cola no se duplica; una que falló vuelve a la cola.
Sale con 0, o con 2 si la calidad, la carpeta o el navegador no son válidos.

### `ejecutar`

```bash
uv run downloader ejecutar [--cookies-de NAVEGADOR]
```

Procesa la cola en primer plano y termina cuando no queda nada pendiente. Las descargas que
esperan a que vuelva la red cuentan como pendientes: se reintenta indefinidamente, con esperas
de hasta 5 minutos. Recoge las URLs que se agreguen desde otra terminal mientras corre. Tras un
reinicio del equipo, vuelva a lanzarlo y continúa donde se quedó.

| Código | Significado |
|--------|-------------|
| 0 | todas completadas |
| 1 | la cola se vació con alguna descarga fallida (el resumen dice por qué) |
| 2 | uso inválido u otro `ejecutar` ya en marcha |
| 3 | error fatal: disco lleno, cola corrupta, ffmpeg ausente o cookies de `--cookies-de` ilegibles; el progreso se conserva |
| 130 | interrumpido con Ctrl+C (en Windows, también Ctrl+Break); el progreso se conserva |

### `estado`

```bash
uv run downloader estado
```

Muestra la cola sin modificarla, también mientras otro `ejecutar` está en marcha: una línea por
descarga con su número, su estado, el progreso, el título (o la URL) y el último aviso o error,
con los tokens y firmas ocultos. El progreso es el que `ejecutar` guarda cada 2 s. Sale con 0,
o con 3 si la cola está corrupta (se guarda una copia, como en `agregar`).

```text
#12 descargando  43.1%  Título del video
#13 esperando red  12.0%  Otro video  (conexión rechazada)
#15 fallida  —  https://…/watch/abc  Video privado: el propietario restringió el acceso
```

### `actualizar`

```bash
uv run downloader actualizar
```

Actualiza yt-dlp a su última versión (`uv lock --upgrade-package yt-dlp` y `uv sync`) y la
comprueba con los tests que dependen del comportamiento interno de yt-dlp
(`pytest -m ytdlp_internals`, unos 25 s). Si fallan, restaura el `uv.lock` anterior, vuelve a
sincronizar y conserva la versión anterior. Necesita red, uv y ffmpeg. Muestra
`yt-dlp 2026.8.19 → 2026.10.21` o `yt-dlp ya está al día (…)` y sale con 0; si algo falla, el
motivo y 1. Si instaló el programa con `uv tool install`, actualícelo con
`uv tool upgrade downloader`.

### Configuración

`config.toml`, en `%APPDATA%\downloader\` en Windows y en `~/.config/downloader/` en Linux (o
en el directorio de la variable `DOWNLOADER_HOME`). Todas las claves son opcionales:

```toml
carpeta_destino = '~/Videos'        # por defecto ~/Descargas o ~/Downloads, si existen
calidad = '720p'                    # mejor (por defecto), 1080p, 720p o audio
plantilla_nombre = '{titulo}'       # campos {titulo} e {id}; p. ej. '{titulo} [{id}]'
sondas_red = ["1.1.1.1:443", "8.8.8.8:443"]
estancamiento_segundos = 30         # sin datos (o por debajo de la velocidad mínima)
estancamiento_velocidad_minima = 1024   # bytes/s
```

`--carpeta` y `--calidad` prevalecen sobre la configuración. No admite credenciales ni cookies.
Los archivos a medias están en `<carpeta>/.downloader-tmp/`. Si ya existe un archivo con el
mismo nombre, el nuevo se publica con ` (1)`, ` (2)`… y nunca se sobrescribe. En Windows el
título se recorta para que la ruta completa no pase de 240 caracteres.

## Cookies del navegador

Para videos que exigen su sesión, `--cookies-de NAVEGADOR` (en `agregar` o en `ejecutar`) hace
que yt-dlp lea las cookies de ese navegador en memoria al descargar. Nunca se guardan: la cola
solo guarda el nombre del navegador. Sin la opción no se lee ninguna cookie.

| Navegador | En Windows |
|-----------|------------|
| Firefox | Fiable, abierto o cerrado. Una sesión recién iniciada puede no verse hasta cerrar Firefox. |
| Edge | Solo con Edge cerrado del todo, también en segundo plano. |
| Chrome 127 o posterior | No funciona, ni cerrado: cifra las cookies con una clave ligada a la aplicación que yt-dlp no sabe leer. |

Brave, Chromium, Opera, Vivaldi y Whale usan el mismo código que Chrome y Edge; Safari solo
existe en macOS. Si las cookies no se pueden leer, el error nombra el navegador y la causa.

## Tests

Ningún test usa internet: todos van contra un servidor local.

```bash
uv run pytest                      # todos (unos 2 min en Linux)
uv run pytest -m "not slow"        # los rápidos
uv run pytest -m slow              # los lentos, de más de unos 5 s cada uno
uv run pytest -m ytdlp_internals   # los que ejecuta `actualizar`
uv run pytest -rs                  # con el motivo de cada test omitido
```

Los tests que descargan necesitan ffmpeg y se omiten si falta. El de publicación en un volumen
FAT32 o exFAT solo corre si la variable `DOWNLOADER_TEST_SIN_ENLACES` apunta a uno (el CI los
crea en Linux y en Windows).

## Límites conocidos

- Un fragmento HLS truncado en el servidor de origen (servido completo según su tamaño
  anunciado) no lo detecta la verificación: el video puede publicarse como completo con un
  salto en la imagen o el sonido. Un fragmento cortado durante la transferencia sí se detecta y
  se vuelve a pedir.
- Se rechazan las transmisiones en directo y el contenido con DRM: todo lo que dependa de un
  sistema de licencias (FairPlay, PlayReady, Widevine, Adobe Access, una URI `skd://`, una caja
  PSSH o cualquier `KEYFORMAT` distinto de `identity`), sin llegar a pedir la clave. Un HLS
  cifrado con `AES-128` sin licencias sí se descarga, venga la clave por HTTP(S) (con las
  cookies de `--cookies-de`, si se usan), en una URI `data:` o del propio sitio.
- Un HLS cifrado con `SAMPLE-AES`, `SAMPLE-AES-CTR` u otro método distinto de `AES-128` falla
  como no soportado: solo ffmpeg sabe descargarlo, y ffmpeg no permite reanudar de forma
  fiable. RTMP y WebSocket quedan fuera de alcance.
- La detección de DRM (HLS, DASH, ISM y F4M) solo se ha comprobado con manifiestos de prueba,
  no con manifiestos reales de sitios. La descarga completa de un ISM (Smooth Streaming) sin
  protección no tiene test.
- Una sola descarga a la vez.
- El vigilante de estancamiento solo actúa durante la descarga de los medios, no durante la
  extracción de la información del video: ahí una conexión colgada solo se corta por el
  timeout de 15 s sin datos, y un goteo lento no se detecta.
- La URL que da el usuario se guarda tal cual en la cola, porque hace falta para descargarla:
  si lleva un token, queda en `cola.db`. Los mensajes la muestran con los tokens y firmas
  ocultos.
- En Windows sin rutas largas habilitadas, una carpeta de destino de más de unos 195
  caracteres puede dejar los archivos de trabajo por encima del límite de 260 de Windows.

## Licencia

El código de este repositorio se distribuye bajo la licencia MIT ([LICENSE](LICENSE)).

`yt-dlp[default]` instala como dependencia `mutagen`, con licencia GPL-2.0-or-later. No se
incluye en el repositorio: uv lo descarga al instalar. Distribuir el programa empaquetado junto
con sus dependencias puede tener implicaciones de licencia. Es una decisión del propietario del
repositorio; esta nota no es asesoría legal.

## Documentación

- Especificación: [spec.md](specs/001-resilient-video-downloader/spec.md)
- Plan e investigación: [plan.md](specs/001-resilient-video-downloader/plan.md),
  [research.md](specs/001-resilient-video-downloader/research.md)
- Validación: [quickstart.md](specs/001-resilient-video-downloader/quickstart.md)
