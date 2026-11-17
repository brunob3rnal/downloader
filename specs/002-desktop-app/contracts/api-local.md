# Contrato: API local de la ventana

**Feature**: [spec.md](../spec.md) | **Research**: R3, R4, R13, R16, R17, R18

El servidor escucha solo en `127.0.0.1` y en un puerto que asigna el sistema. Lo usa
únicamente la página de la ventana. Este contrato lo prueban los tests de integración en Linux
y en Windows, con una ventana falsa.

## Controles comunes (FR-013, R4)

Se aplican a toda petición, en este orden. El primero que falla responde **403**, sin cuerpo y
sin cabeceras CORS, y no ejecuta nada:

1. `Host` es exactamente `127.0.0.1:<puerto>`.
2. El primer segmento de la ruta es el secreto de la sesión (`/<secreto>/...`).
3. Si hay `Origin`, es `http://127.0.0.1:<puerto>`. Todo `POST` lo lleva.
4. Las rutas `api/*` llevan `X-Gestor: 1`.
5. El método es `GET` o `POST`. `OPTIONS` y los demás responden 403.

Toda respuesta lleva `Cache-Control: no-store` y `X-Content-Type-Options: nosniff`. Los
cuerpos son JSON en UTF-8. Ninguna respuesta contiene cookies, credenciales ni el secreto.
Todo texto que venga de la cola pasa por `ocultar_secretos`.

## Archivos estáticos

| Ruta | Respuesta |
|------|-----------|
| `GET /<secreto>/` | `index.html`, con `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'` |
| `GET /<secreto>/app.js`, `/<secreto>/app.css` | el archivo |
| cualquier otra | 404 |

## `GET api/opciones`

Valores iniciales del formulario (FR-003, FR-014).

```json
{
  "calidades": [{"id": "mejor", "nombre": "Mejor calidad"}, {"id": "1080p", "nombre": "1080p"},
                {"id": "720p", "nombre": "720p"}, {"id": "audio", "nombre": "Solo audio"}],
  "calidad_inicial": "mejor",
  "carpeta_inicial": "C:\\Users\\Usuario\\Downloads",
  "navegadores": [
    {"id": "firefox", "nombre": "Firefox", "aviso": "Fiable, abierto o cerrado."},
    {"id": "edge", "nombre": "Edge", "aviso": "Solo con Edge cerrado del todo, también en segundo plano."},
    {"id": "chrome", "nombre": "Chrome", "aviso": "Chrome 127 o posterior no funciona, ni cerrado."}
  ],
  "examinar_disponible": true
}
```

- `calidad_inicial` y `carpeta_inicial` salen de `config.toml` o de los valores por defecto de
  la CLI. Si `config.toml` no es válido, la respuesta es 200 con los valores por defecto y un
  campo `aviso_config` con el error, para que la ventana lo muestre.
- `navegadores` lista todos los de `models.NAVEGADORES`. Los que no son Firefox, Edge ni
  Chrome llevan el aviso común de la documentación: "usa el mismo código que Chrome y Edge".
- `examinar_disponible` vale `false` sin ventana real (tests en Linux).

## `GET api/estado`

Estado de la cola y del procesador. La página lo consulta cada segundo con `If-None-Match`;
si nada cambió, la respuesta es **304** sin cuerpo (R13).

```json
{
  "procesador": {
    "estado": "ninguno | arrancando | en_marcha | deteniendo",
    "origen": "cli | app | null",
    "puede_iniciar": true,
    "puede_detener": false,
    "motivo": "texto que explica por qué no se puede iniciar o detener, o null",
    "terminando": "qué está terminando mientras se detiene, o null"
  },
  "en_linea": true,
  "en_directo": true,
  "cola": {
    "total": 5,
    "filas": [
      {
        "id": 12, "titulo": "Entrevista completa, parte 1", "sitio": "ejemplo.com",
        "direccion": "ejemplo.com/video/entrevista-1",
        "estado": "downloading", "etiqueta": "Descargando",
        "porcentaje": 43.1, "velocidad": 2400000, "eta": 72,
        "sin_datos": null, "espera": null,
        "detalle": "2,4 MB/s · quedan 1 min 12 s",
        "aviso": "texto o null", "reintentar_con_cookies": false, "hijas": null
      }
    ]
  },
  "ultima_ejecucion": "el contenido de ultima-ejecucion.json, o null",
  "avisos": {
    "registro_no_disponible": false,
    "cola_corrupta": "mensaje de BaseCorrupta, o null"
  },
  "cierre": "null, o {\"estado\": \"deteniendo\", \"cerrar_igualmente\": false} (POST api/cerrar)"
}
```

- `detalle` es el texto ya formateado de la línea bajo la barra ("2,4 MB/s · quedan 1 min 12 s",
  "Esperando red · reintento en 40 s", "lista (2 videos)", "Sin datos en directo",
  "Continúa desde el 14 %, no empieza de cero" en una fila en cola con progreso), o null; el
  título muestra la URL sin usuario ni contraseña si no hay título (research R13).
- `direccion` es la línea bajo el título de la maqueta: sitio y ruta, sin esquema, usuario,
  contraseña, puerto ni consulta.
- Las filas van en el orden de `cola.db` (id ascendente). Con una cola corrupta, `filas`
  viene vacío y `cola_corrupta` lleva el mensaje (Edge Cases).
- Sin un procesador en marcha, una fila que un cierre abrupto dejó en `downloading` o
  `waiting_network` se muestra como `queued` ("En cola"), con su progreso: es lo que será al
  iniciar, y así nada se muestra como en marcha cuando nadie procesa (Edge Cases; `recuperar`,
  R11 de 001). `cola.db` no se toca.
- `en_linea` es null si no hay un procesador en marcha o si `progreso-vivo.json` tiene más de
  5 s.
- `en_directo` es `false` si hay un procesador en marcha y no hay un `progreso-vivo.json` válido
  (ausente, de otro proceso o de más de 5 s). Entonces la fila activa no lleva `velocidad`,
  `eta` ni el porcentaje del vivo: nunca se muestran datos viejos (R9). Sin procesador, `null`.
- `puede_detener` solo es verdadero con origen `app` y estado `en_marcha`. Con origen `cli`,
  `motivo` dice: "Este procesamiento se lanzó desde la terminal; deténgalo allí con Ctrl+C."
  (FR-009, US3 escenario 5).
- `deteniendo`: desde que se acepta `api/detener` hasta que el procesamiento termina, con el
  motivo "El procesamiento se está deteniendo." (FR-009). Si mientras tanto el trabajador
  espera a ffmpeg o ffprobe (en Windows, la parada no los interrumpe; research R7), el campo
  `terminando` dice cuál: "Terminando la unión del vídeo…" o "Terminando la verificación del
  archivo…"; si no, y en cualquier otro estado, es null. La ventana lo muestra junto a los
  botones y en el aviso de cierre, en lugar de solo "Deteniendo…". Lo recuerda la sesión de la ventana
  que pidió la parada; una ventana abierta después ve `en_marcha` hasta que termina.
- Del trabajador que lanzó esta sesión se sabe además si su proceso sigue vivo. Está
  `arrancando` ("El procesamiento está arrancando.") hasta que escribe `procesador.json`. Y
  entre que lo retira y el proceso termina no pasa a `ninguno`: sigue `en_marcha`, con
  `puede_detener: false` y el motivo "El procesamiento está terminando." (o `deteniendo`, si se
  pidió la parada). Así, Iniciar no se ofrece antes de que suelte el bloqueo de la cola.

## `POST api/agregar`

```json
{"texto": "https://a.example/v/1\n\nno es una url\nhttps://b.example/v/2",
 "calidad": "720p", "carpeta": "C:\\Videos", "navegador": null}
```

- **200**:
  ```json
  {"agregadas": [{"id": 31, "url": "https://a.example/v/1"}],
   "ya_en_cola": [{"id": 7, "url": "…", "estado": "queued"}],
   "reencoladas": [{"id": 4, "url": "…"}],
   "lineas_invalidas": [{"linea": 3, "texto": "no es una url"}]}
  ```
  Las líneas vacías no cuentan como inválidas. Las URLs van con los secretos ocultos.
- **400** `{"error": "..."}` si la calidad, el navegador o la carpeta no son válidos. Los
  mensajes son los mismos que en `agregar`: "la carpeta de destino X no existe", "sin permiso
  de escritura en la carpeta de destino X", "navegador no admitido…". No se agrega ninguna
  URL.
- **400** si no hay ninguna línea no vacía: "Pegue al menos un enlace".
- Funciona con o sin procesador en marcha. Lo que se agrega con un procesador en marcha entra
  en la misma ejecución (US1, escenario 4).

## `POST api/iniciar`

Sin cuerpo.

- **202** `{"ejecucion": "9f2c…"}`: el subproceso se ha lanzado (estado `arrancando`).
- **409** `{"motivo": "..."}`: ya hay un procesador (de la CLI o de la aplicación), o no se pudo
  dejar el trabajador fuera del objeto de trabajo (R8).
- **500** `{"error": "..."}`: no se pudo lanzar el proceso; queda anotado en el registro.

Si el subproceso sale con el código 2 (perdió la carrera por el bloqueo, R6), `api/estado`
pasa a mostrar el procesador ajeno, y `ultima_ejecucion` no se reemplaza. Mientras arranca,
el estado es `arrancando`; si termina sin llegar a anunciarse, vuelve a `ninguno`.

El motivo del 409 es el de `api/estado`: con un `ejecutar` de la CLI, "Este procesamiento se
lanzó desde la terminal; deténgalo allí con Ctrl+C."; con uno de la aplicación, "Ya hay un
procesamiento en marcha.", o el de `arrancando`, `deteniendo` o terminando.

## `POST api/detener`

Sin cuerpo.

- **202** `{"estado": "deteniendo"}`: se ha escrito `parada.json`.
- **409** `{"motivo": "..."}`: no hay procesador ("No hay ningún procesamiento en marcha."),
  es de la CLI (FR-009) o todavía está arrancando. No se escribe nada.
- **500** `{"error": "No se pudo pedir la parada; vuelva a intentarlo."}`: no se pudo escribir
  `parada.json`; queda anotado en el registro y el estado no cambia.
- Repetirlo mientras se detiene responde 202 sin efecto: no vuelve a escribir `parada.json`
  (Edge Cases).

## `POST api/reintentar`

```json
{"id": 15, "navegador": "firefox"}
```

- **200** `{"accion": "reencolada", "id": 15}`: la descarga fallida vuelve a la cola con ese
  navegador, como `agregar --cookies-de` (FR-016).
- **400**: navegador ausente o no admitido, o cuerpo inválido.
- **409** `{"motivo": "Solo se puede reintentar con cookies una descarga que falló por requerir
  una sesión iniciada."}`: la descarga no está fallida, o no ofrece el reintento (R17).
- **404**: no existe.
- La fila vuelve a la cola con el mismo id y su directorio de trabajo; el procesamiento no se
  inicia solo (como `agregar`).

## `POST api/examinar`

Sin cuerpo. Abre el diálogo nativo de carpeta.

- **200** `{"carpeta": "C:\\Videos"}`, o `{"carpeta": null}` si el usuario canceló.
- **501** sin ventana real.

## `POST api/cerrar`

```json
{"decision": "segundo_plano | pausar | cancelar | cerrar_igualmente"}
```

Respuesta al diálogo de cierre (FR-010, R18).

- **202**: con `segundo_plano`, la ventana se destruye; con `pausar`, se pide la parada (como
  `api/detener`) y la ventana se destruye cuando el procesador termina; si ya no hay
  procesador, se destruye enseguida; con `cancelar`, no hace nada.
- Mientras dura "Pausar y cerrar", `api/estado` lleva `"cierre": {"estado": "deteniendo",
  "cerrar_igualmente": false}`; pasado el plazo (30 s), `cerrar_igualmente` vale `true` y la
  página ofrece el botón. Con la decisión `cerrar_igualmente`, la ventana se destruye sin
  esperar; no se mata nada y el progreso está a salvo. Sin "Pausar y cerrar", `cierre` es
  `null`.
- **409** con `pausar` si el procesador no es de la aplicación (con el motivo de
  `api/detener`), y con `cerrar_igualmente` si no se está cerrando ("No se está cerrando la
  ventana.").
- **400** con una decisión desconocida.

El diálogo lo abre la ventana real al pulsar su X: si `cierre.decidir` da `preguntar` (un
procesamiento de la aplicación en marcha, arrancando o deteniéndose), cancela el cierre y
ejecuta `mostrarDialogoCierre(texto)` en la página. Con uno de la CLI o sin procesamiento, se
cierra sin preguntar.
