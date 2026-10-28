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
    "motivo": "texto que explica por qué no se puede iniciar o detener, o null"
  },
  "en_linea": true,
  "cola": {
    "total": 5,
    "filas": [
      {
        "id": 12, "titulo": "Entrevista completa, parte 1", "sitio": "ejemplo.com",
        "estado": "downloading", "etiqueta": "Descargando",
        "porcentaje": 43.1, "velocidad": 2400000, "eta": 72,
        "sin_datos": null, "espera": null,
        "aviso": "texto o null", "reintentar_con_cookies": false, "hijas": null
      }
    ]
  },
  "ultima_ejecucion": "el contenido de ultima-ejecucion.json, o null",
  "avisos": {
    "registro_no_disponible": false,
    "cola_corrupta": "mensaje de BaseCorrupta, o null"
  }
}
```

- Las filas van en el orden de `cola.db` (id ascendente). Con una cola corrupta, `filas`
  viene vacío y `cola_corrupta` lleva el mensaje (Edge Cases).
- `en_linea` es null si no hay un procesador en marcha o si `progreso-vivo.json` tiene más de
  5 s.
- `puede_detener` solo es verdadero con origen `app` y estado `en_marcha`. Con origen `cli`,
  `motivo` dice: "Este procesamiento se lanzó desde la terminal; deténgalo allí con Ctrl+C."
  (FR-009, US3 escenario 5).

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
pasa a mostrar el procesador ajeno, y `ultima_ejecucion` no se reemplaza.

## `POST api/detener`

Sin cuerpo.

- **202**: se ha escrito `parada.json` (estado `deteniendo`).
- **409** `{"motivo": "..."}`: no hay procesador, o es de la CLI (FR-009).
- Repetirlo mientras se detiene responde 202 sin efecto (Edge Cases).

## `POST api/reintentar`

```json
{"id": 15, "navegador": "firefox"}
```

- **200** `{"accion": "reencolada", "id": 15}`: la descarga fallida vuelve a la cola con ese
  navegador, como `agregar --cookies-de` (FR-016).
- **400**: navegador no admitido.
- **409**: la descarga no está fallida, o no ofrece el reintento (R17).
- **404**: no existe.

## `POST api/examinar`

Sin cuerpo. Abre el diálogo nativo de carpeta.

- **200** `{"carpeta": "C:\\Videos"}`, o `{"carpeta": null}` si el usuario canceló.
- **501** sin ventana real.

## `POST api/cerrar`

```json
{"decision": "segundo_plano | pausar | cancelar"}
```

Respuesta al diálogo de cierre (FR-010, R18).

- **202**: con `segundo_plano`, la ventana se destruye; con `pausar`, se pide la parada y la
  ventana se destruye cuando el procesador termina; con `cancelar`, no hace nada.
- **409** con `pausar` si el procesador no es de la aplicación.
