# Data Model: Aplicación de escritorio

**Feature**: [spec.md](spec.md) | **Research**: [research.md](research.md)

La feature **no cambia el esquema de `cola.db`**: la cola y la entidad Descarga son las de
[001/data-model.md](../001-resilient-video-downloader/data-model.md), compartidas sin cambios
entre la CLI y la aplicación (FR-007, FR-017). Lo nuevo son archivos pequeños en el directorio
de la app (`config.directorio_app()`: `%APPDATA%\downloader\`, `~/.config/downloader/` o
`DOWNLOADER_HOME`) y datos que solo viven en memoria. El formato exacto de cada archivo está en
[contracts/archivos.md](contracts/archivos.md).

## Entidades

### Procesador (`procesador.json`)

La ejecución que procesa la cola: un `ejecutar` de la CLI o un subproceso de la aplicación.
Como mucho hay uno a la vez, y lo garantiza `ejecutar.lock` (R6).

| Campo | Tipo | Regla |
|-------|------|-------|
| `pid` | entero | proceso que tiene el bloqueo |
| `creado` | número (s desde la época) | instante de creación del proceso; con `pid`, lo identifica sin ambigüedad |
| `origen` | `cli` \| `app` | solo `app` admite Detener desde la ventana (FR-009) |
| `ejecucion` | texto \| null | identificador de la ejecución; solo con origen `app` |
| `inicio` | fecha ISO 8601 UTC | |

- Lo escribe `operaciones.ejecutar_cola` justo después de tomar el bloqueo y lo borra al
  terminar de forma ordenada (completada, con fallidas, fatal o detenida).
- **Vivo** si el archivo existe y `psutil` encuentra el proceso `pid` con el mismo instante de
  creación. Si el archivo existe y el proceso no, el procesador terminó de forma abrupta.

**Estado visto por la ventana** (derivado; no se guarda):

```text
ninguno ──Iniciar──▶ arrancando ──(procesador.json de su ejecución)──▶ en_marcha(app)
   ▲                     │                                              │
   │                     └─(sale con 2: otro ya en marcha)─▶ observando ◀┘ (si es de la CLI)
   │                                                                    │
   │                              Detener / Pausar y cerrar ──▶ deteniendo
   │                                                                    │
   └────────── el proceso termina (ordenado o abrupto) ◀────────────────┘
```

- `en_marcha(cli)`, al que la ventana llama `observando`: se muestra en directo, Iniciar y
  Detener se rechazan con su motivo (FR-008, FR-009).
- `en_marcha(app)`: se puede detener, también desde una sesión posterior de la ventana
  (FR-011).
- `deteniendo`: desde que se escribe `parada.json` hasta que el proceso termina (FR-009).

### Ejecución de la aplicación

Cada vez que la aplicación inicia el procesamiento. Identificador: 16 caracteres hexadecimales
aleatorios (`secrets.token_hex(8)`), que enlazan `procesador.json`, `parada.json` y
`ultima-ejecucion.json`.

### Progreso en directo (`progreso-vivo.json`)

Lo que hace falta para FR-005 y SC-006 y que `cola.db` no guarda (R9). Lo escribe cada
segundo cualquier procesador, también el de la CLI. Es informativo y desechable.

| Campo | Tipo | Regla |
|-------|------|-------|
| `pid`, `creado` | | los del procesador que lo escribe; uno de otro procesador se ignora |
| `actualizado` | fecha ISO 8601 UTC | si tiene más de 5 s, la ventana no muestra velocidad ni tiempo estimado |
| `en_linea` | booleano | estado de la red según el monitor del trabajador (pie de la ventana) |
| `activa` | objeto \| null | la descarga en curso |
| `activa.id` | entero | id en `cola.db` |
| `activa.bytes`, `activa.total` | entero \| null | |
| `activa.velocidad` | número \| null | bytes/s |
| `activa.eta` | número \| null | s |
| `activa.sin_datos` | número \| null | s sin bytes nuevos, a partir de 5 s (`progress.SIN_DATOS`) |
| `activa.espera` | objeto \| null | en `waiting_network`: `causa` (con secretos ocultos) y `reintento_en` (s, o null si espera a la red) |

No contiene URLs ni títulos: la ventana los toma de `cola.db`.

### Solicitud de parada (`parada.json`)

| Campo | Tipo | Regla |
|-------|------|-------|
| `ejecucion` | texto | debe coincidir con la del procesador; si no, el vigilante la ignora y la borra |
| `pedida` | fecha ISO 8601 UTC | |

La escribe solo la ventana, y solo para un procesador de origen `app` (FR-009). La lee y la
borra el vigilante del subproceso (R7).

### Resultado de la ejecución (`ultima-ejecucion.json`)

Spec, Key Entities y FR-012. Persiste con la ventana cerrada y se reemplaza al iniciar otra
ejecución.

| Campo | Tipo | Regla |
|-------|------|-------|
| `ejecucion` | texto | |
| `inicio`, `fin` | fecha ISO 8601 UTC | |
| `completadas` | entero | |
| `fallidas` | lista de `{id, nombre, causa}` | `nombre`: título o URL; todo con secretos ocultos |
| `codigo` | entero | los de `ejecutar`: 0, 1, 3, 130; o null si `abrupto` |
| `error` | objeto \| null | `tipo`: `fatal` \| `inesperado` \| `detenida` \| `abrupto`; `mensaje`: con secretos ocultos |

- Lo escribe el subproceso al terminar. Si muere sin escribirlo, la ventana lo escribe con
  `tipo = abrupto` al detectarlo (R10).
- `fatal`: el mismo mensaje que daría `ejecutar` (FR-012). `inesperado`: el tipo y el mensaje
  de la excepción; la traza completa va al registro.

### Registro de errores (`registro/`)

Spec, Key Entities y FR-020. Archivos `ventana.log`, `ventana.log.1`, `procesador.log` y
`procesador.log.1`, de 500 000 bytes como máximo cada uno: 2 MB en total. Una entrada por
línea de cabecera (`fecha nivel origen: qué falló`), seguida del detalle o la traza, con los
secretos ocultos. Un solo proceso escribe en cada par de archivos (R10).

### Solicitud de activación (`activar.solicitud`)

Archivo vacío. Lo escribe una segunda apertura que no consigue `ventana.lock`; lo borra la
ventana abierta al enfocarse (FR-018, R12).

### Sesión de la ventana (solo en memoria)

| Campo | Regla |
|-------|-------|
| `puerto` | lo asigna el sistema al escuchar en `127.0.0.1:0` |
| `secreto` | `secrets.token_urlsafe(32)`; nunca se escribe en disco ni en el registro (R4) |
| `cookies_activadas`, `navegador` | opción de cookies del formulario; empieza desactivada en cada sesión y no se guarda (FR-014, FR-015) |

### Envío de URLs (spec, Key Entities)

Lo que llega en `POST api/agregar`: texto pegado, calidad, carpeta y navegador (o null).

Reglas de validación, que se aplican en este orden:
1. calidad entre `mejor`, `1080p`, `720p` y `audio`;
2. navegador null o uno de `models.NAVEGADORES`;
3. carpeta existente y escribible, como en `agregar` de la CLI.

Si alguna falla, no se agrega nada. Si pasan, cada línea no vacía se valida como URL `http` o
`https` con host (R16). Las válidas se agregan con `Store.agregar`, con el mismo resultado que
`agregar` (FR-004); las inválidas se devuelven con su número de línea.

### Fila de la vista (derivada; no se guarda)

Por cada descarga de `cola.db`, combinada con `progreso-vivo.json` si es la activa (R13):

| Campo | Origen |
|-------|--------|
| `id` | Descarga |
| `titulo` | título o URL, con secretos ocultos |
| `sitio` | host de la URL |
| `estado`, `etiqueta` | `Estado` y `ETIQUETAS` |
| `porcentaje` | vivo si la descarga es la activa; si no, `bytes_descargados / bytes_totales` de la cola |
| `velocidad`, `eta`, `sin_datos`, `espera` | vivo, solo para la descarga activa |
| `aviso` | último error si está fallida o esperando la red; si no, archivo publicado y último aviso |
| `reintentar_con_cookies` | `estado = failed` y `mensajes.requiere_sesion(ultimo_error)` (R17) |
| `hijas` | número de videos, si es una lista |

## Archivos nuevos en el directorio de la app

| Archivo | Escribe | Lee | Persistencia |
|---------|---------|-----|--------------|
| `procesador.json` | procesador (CLI o app) | ventana | mientras dura el procesador |
| `progreso-vivo.json` | procesador (CLI o app) | ventana | informativo, se sobrescribe cada segundo |
| `parada.json` | ventana | subproceso de la app | hasta que se atiende |
| `ultima-ejecucion.json` | subproceso de la app (o la ventana, si murió) | ventana | hasta la siguiente ejecución |
| `registro/*.log*` | ventana y subproceso de la app | usuario | rotación, 2 MB en total |
| `ventana.lock` | ventana | segunda apertura | bloqueo del sistema |
| `activar.solicitud` | segunda apertura | ventana | hasta que se atiende |

Ninguno contiene cookies, credenciales ni el secreto de la sesión (Principio VI, FR-015).
