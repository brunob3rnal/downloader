# Contrato: archivos compartidos entre procesos

**Feature**: [spec.md](../spec.md) | **Data model**: [data-model.md](../data-model.md) |
**Research**: R6, R7, R9, R10, R12

Todos viven en el directorio de la app (`config.directorio_app()`). Los JSON se escriben de
forma atómica: un temporal en el mismo directorio y después `os.replace`. Si `os.replace`
falla en Windows porque otro proceso tiene el destino abierto, se reintenta en la siguiente
escritura, o tras 50 ms en las escrituras únicas. El lector trata un archivo ausente, a medio
escribir o con JSON inválido como "sin información" y vuelve a intentarlo en la siguiente
consulta. Fechas en ISO 8601, UTC, con milisegundos, como en `cola.db`.

## `procesador.json`

```json
{"pid": 4321, "creado": 1761650000.25, "origen": "app",
 "ejecucion": "9f2c4b1a7d3e5f60", "inicio": "2026-10-28T10:15:02.123+00:00"}
```

- `origen` es `cli` o `app`. `ejecucion` es null con `cli`.
- `creado` es `psutil.Process(pid).create_time()` del propio proceso.
- Se escribe tras tomar `ejecutar.lock` y se borra al terminar de forma ordenada. Si sigue
  ahí tras una terminación abrupta, el siguiente procesador lo sobrescribe.

## `progreso-vivo.json`

```json
{"pid": 4321, "creado": 1761650000.25, "actualizado": "2026-10-28T10:16:40.001+00:00",
 "en_linea": true,
 "activa": {"id": 12, "bytes": 52428800, "total": 121634816, "velocidad": 2400000.0,
            "eta": 72, "sin_datos": null, "espera": null}}
```

- Se escribe como mucho una vez por segundo mientras haya un procesador. `activa` es null
  entre descargas.
- En `waiting_network`: `"espera": {"causa": "conexión rechazada", "reintento_en": 40}`, o
  `"reintento_en": null` si espera a que vuelva la red.
- Se borra al terminar de forma ordenada. Un archivo cuyo `pid` y `creado` no coinciden con
  `procesador.json`, o que tiene más de 5 s, se ignora.

## `parada.json`

```json
{"ejecucion": "9f2c4b1a7d3e5f60", "pedida": "2026-10-28T10:20:00.000+00:00"}
```

- Lo escribe solo la ventana, y solo si `procesador.json` tiene `origen: app` y la misma
  `ejecucion`.
- El vigilante lo consulta cada 0,25 s. Si coincide, lo borra y provoca el equivalente a
  Ctrl+C (R7). Si no coincide, lo borra sin más.

## `ultima-ejecucion.json`

```json
{"ejecucion": "9f2c4b1a7d3e5f60",
 "inicio": "2026-10-28T10:15:02.123+00:00", "fin": "2026-10-28T11:02:47.900+00:00",
 "completadas": 4,
 "fallidas": [{"id": 15, "nombre": "https://ejemplo.com/video/privado",
               "causa": "HTTP 403 al obtener la información del video. Puede que el video exija una sesión iniciada: …"}],
 "codigo": 1,
 "error": null}
```

- `codigo` es el de `ejecutar` (0, 1, 3 o 130), o null con `error.tipo = abrupto`.
- `error` puede ser:
  - `{"tipo": "fatal", "mensaje": "<el mismo texto que imprime ejecutar>"}`;
  - `{"tipo": "detenida", "mensaje": "interrumpido; progreso guardado"}`;
  - `{"tipo": "inesperado", "mensaje": "<Tipo>: <mensaje>; detalle en el registro de errores"}`;
  - `{"tipo": "abrupto", "mensaje": "el procesamiento terminó de forma inesperada; el progreso guardado se conserva"}`.
- Lo escribe el subproceso de la aplicación al terminar, o la ventana si el subproceso murió
  sin escribirlo. Un `ejecutar` de la CLI no lo toca.

## `registro/`

```text
registro/ventana.log      registro/ventana.log.1
registro/procesador.log   registro/procesador.log.1
```

- `RotatingFileHandler(maxBytes=500_000, backupCount=1, encoding="utf-8")`, con un solo
  proceso escritor por par de archivos: 2 MB en total como máximo (FR-020).
- Formato de cada entrada:

  ```text
  2026-10-28T10:20:01.512+00:00 ERROR procesador: error fatal: no queda espacio en el disco (…)
  Traceback (most recent call last):
    …
  ```

- Mensaje y traza pasan por `ocultar_secretos` antes de escribirse. Nunca se escriben las
  peticiones al servidor local ni el secreto de la sesión.

## `ventana.lock` y `activar.solicitud`

- `ventana.lock`: bloqueo exclusivo del primer byte, igual que `ejecutar.lock`. Lo tiene la
  ventana abierta mientras vive.
- `activar.solicitud`: archivo vacío. Lo crea una segunda apertura que no consigue
  `ventana.lock`, que después termina. La ventana abierta lo borra y se enfoca.

## Compatibilidad con la CLI

- `ejecutar` escribe `procesador.json` y `progreso-vivo.json` y los borra al terminar. No
  cambia ninguna salida, opción ni código de salida (FR-017).
- `agregar` y `estado` no leen ni escriben ninguno de estos archivos.
