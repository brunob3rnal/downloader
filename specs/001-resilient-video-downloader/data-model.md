# Data Model: Gestor de descargas de video resiliente

Almacenamiento: SQLite en `typer.get_app_dir("downloader")/cola.db` (ver research R11).

## Entidad: Descarga (`descargas`)

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | INTEGER PK | autoincremental; nombra el directorio de trabajo |
| `url` | TEXT | obligatoria; URL tal como la dio el usuario o la entrada de una lista |
| `calidad` | TEXT | `mejor` \| `1080p` \| `720p` \| `audio` |
| `carpeta_destino` | TEXT | ruta absoluta, fijada al agregar |
| `navegador_cookies` | TEXT NULL | solo el nombre del navegador; nunca cookies |
| `tipo` | TEXT | `video` (por defecto) \| `playlist` (padre ya expandida) |
| `padre_id` | INTEGER NULL FK → `descargas.id` | descarga de lista que la originó |
| `estado` | TEXT | ver máquina de estados y etiquetas visibles |
| `titulo` | TEXT NULL | conocido tras la primera extracción |
| `bytes_descargados` | INTEGER | informativo; la verdad está en disco |
| `bytes_totales` | INTEGER NULL | `NULL` si el servidor no lo anuncia |
| `intentos_sin_avance` | INTEGER | contador `n` del backoff (R10) |
| `proximo_intento` | TEXT NULL | ISO 8601; solo en `waiting_network` |
| `ultimo_error` | TEXT NULL | mensaje ya filtrado de secretos |
| `ultimo_aviso` | TEXT NULL | aviso no fatal, p. ej. reinicio desde cero (FR-024) o calidad inferior a la pedida |
| `archivo_final` | TEXT NULL | ruta publicada; solo en `completed` |
| `creada`, `actualizada` | TEXT | ISO 8601 UTC |

**Restricciones**

- Única entre las filas no fallidas: (`url`, `calidad`). `agregar` con una pareja que ya existe
  en `queued`/`downloading`/`waiting_network`/`completed` no crea fila y avisa; si la existente
  está en `failed`, vuelve a `queued` y conserva su directorio de trabajo.
- `archivo_final` no nulo ⇔ `estado = completed` y `tipo = video`.

## Máquina de estados

```text
               agregar
                  │
                  ▼
   ┌──────────► queued ◄───────────────── (al iniciar ejecutar: recuperación)
   │              │ el trabajador la toma
   │              ▼
   │         downloading ──── error permanente / verificación fallida ──► failed
   │           │     ▲   └─── verificada y publicada ─────────────────► completed
   │  temporal │     │ espera cumplida o vuelve la red
   │           ▼     │
   │       waiting_network
   │
   └── Ctrl+C o error FATAL (disco lleno, ffmpeg ausente): la fila vuelve a queued
```

| Desde | Hacia | Disparador |
|-------|-------|-----------|
| — | `queued` | `agregar` |
| `failed` | `queued` | `agregar` con la misma URL y calidad |
| `queued` | `downloading` | el trabajador la selecciona (orden por `id`) |
| `downloading` | `waiting_network` | error `TEMPORAL`; se fija `proximo_intento` |
| `waiting_network` | `downloading` | llega `proximo_intento` o el monitor pasa a "en línea" |
| `downloading` | `completed` | verificación correcta y publicación sin sobrescribir |
| `downloading` | `completed` (`tipo=playlist`) | la URL era una lista y se insertaron sus hijas |
| `downloading` | `failed` | error `PERMANENTE` o verificación fallida (archivos movidos a `invalidos/`, R8) |
| `downloading`, `waiting_network` | `queued` | Ctrl+C, error `FATAL` (disco lleno, ffmpeg ausente) o recuperación al iniciar |

Toda transición no listada es un error de programación y se rechaza.

**Etiquetas visibles** (spec y salida de `ejecutar`):

| Estado interno | Etiqueta |
|----------------|----------|
| `queued` | en cola |
| `downloading` | descargando |
| `waiting_network` | esperando red |
| `completed` | completada |
| `failed` | fallida |

**Pendientes** (para la condición de salida de `ejecutar`, FR-021): `queued`, `downloading`,
`waiting_network`. `ejecutar` termina cuando no queda ninguna.

## Entidad: Configuración (`config.toml` en el directorio de la app)

| Clave | Por defecto | Notas |
|-------|-------------|-------|
| `carpeta_destino` | `~/Descargas` o `~/Downloads` si existe, si no `~` | sobrescribible con `--carpeta` |
| `calidad` | `mejor` | sobrescribible con `--calidad` |
| `sondas_red` | `["1.1.1.1:443", "8.8.8.8:443"]` | monitor de conectividad (R9) |

No admite claves de credenciales ni cookies; una clave desconocida produce un error claro.

## Archivos en disco

| Ruta | Contenido | Ciclo de vida |
|------|-----------|---------------|
| `<carpeta>/.downloader-tmp/<id>/` | `.part`, `.ytdl`, formatos sueltos, resultado unido | se borra solo tras publicar correctamente; el `.part` solo se reinicia si el servidor no permite reanudar (R3) |
| `<carpeta>/.downloader-tmp/<id>/invalidos/<fecha>/` | archivos de un intento que no pasó la verificación (R8) | nunca se borra automáticamente |
| `<carpeta>/<titulo> [<id_video>].<ext>` | archivo publicado | definitivo |
