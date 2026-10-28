# Implementation Plan: Aplicación de escritorio

**Branch**: `ccr-cd6ebf2c-cwltk0` (el directorio de la feature lo fija `SPECIFY_FEATURE_DIRECTORY`, `.specify/feature.json`) | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-desktop-app/spec.md`

## Summary

Una ventana de Windows (pywebview sobre WebView2) que carga una interfaz web propia, servida
por un servidor HTTP de la biblioteca estándar que solo escucha en `127.0.0.1` y solo atiende a
la ventana: secreto por sesión en la ruta, más comprobación de `Host` y `Origin`. La ventana
no descarga nada.

**Procesamiento**: el mismo código de `ejecutar`, extraído a una función compartida, en un
subproceso que respeta `ejecutar.lock`. Sobrevive al cierre de la ventana y se detiene por el
mismo camino que Ctrl+C.

**Comunicación entre procesos**: archivos pequeños en el directorio de la app. Uno dice quién
procesa (`procesador.json`), otro lleva la velocidad y el tiempo estimado en directo, y los
otros dos llevan la petición de parada y el resultado de la última ejecución. Todo procesador
escribe los dos primeros, también un `ejecutar` de la CLI, para que la ventana pueda observarlo.

La constitución v3.1.0 admite que solo la ventana y el lanzador sean exclusivos de Windows.
Esa capa queda aislada en `src/downloader/app/ventana/`, `gestor.pyw` y
`Gestor de descargas.vbs`. Todo lo demás se prueba en Linux y en Windows con una ventana falsa.

## Technical Context

**Language/Version**: Python 3.12, gestionado con uv (como la feature 001)

**Primary Dependencies**:
- Existentes: `yt-dlp[default]` y Typer, sin cambios.
- Nueva en los dos sistemas: `psutil` 7.2.2 (BSD-3-Clause), para identificar el procesador
  por `pid` e instante de creación (R6) y para medir la memoria (R14).
- Nueva solo en Windows: `pywebview>=6.2.1; sys_platform == 'win32'` (BSD-3-Clause), que trae
  pythonnet, clr_loader, cffi, proxy_tools, bottle y typing_extensions. Todas con licencias
  permisivas (R1).
- Servidor: `http.server` de la biblioteca estándar (R3).

**Storage**:
- `cola.db` de la feature 001, sin cambios de esquema.
- Archivos JSON nuevos en el directorio de la app: `procesador.json`, `progreso-vivo.json`,
  `parada.json` y `ultima-ejecucion.json`.
- `registro/` con 4 archivos rotativos, 2 MB en total.
- Bloqueo nuevo: `ventana.lock`.

Detalle en [data-model.md](data-model.md) y [contracts/archivos.md](contracts/archivos.md).

**Testing**:
- pytest, como en 001: servidor HTTP local con cortes simulados y sin internet.
- La API se prueba con `http.client` contra el servidor real y una ventana falsa.
- Los subprocesos del trabajador se prueban de verdad: se lanzan, se paran y se matan.
- Marcadores nuevos: `solo_windows` (todo test exclusivo de Windows: la capa de la ventana y el lanzador, y las comprobaciones de R7, R8 y R11 que solo existen allí) y `pywebview_internals` (R2, R19).
- La memoria se mide con un script aparte en Windows (R14).

**Target Platform**:
- Ventana y lanzador: Windows 10/11 con WebView2.
- Servidor, procesos, archivos, registro y API: Windows 10/11 y Linux, con el CI en los dos.
- Constitución v3.1.0, sección Plataformas.

**Project Type**: aplicación de escritorio sobre la CLI existente, en el mismo paquete
(`downloader.app`)

**Performance Goals**:
- Ventana lista en 5 s o menos desde el doble clic (SC-001).
- Estado en la ventana con 2 s de retraso como máximo (SC-006).
- Parada en 10 s o menos (SC-004; riesgo en Windows con una conexión estancada, R7).
- Reconexión en 5 s o menos (SC-008).

**Constraints**:
- 300 MB de memoria privada (USS) como máximo, sumando todos los procesos de la aplicación,
  con 500 descargas en cola y una en curso (SC-005; margen estrecho, R14).
- El servidor nunca escucha fuera de `127.0.0.1`.
- Nunca dos procesadores a la vez.
- La CLI no cambia.
- Ningún secreto (cookies ni secreto de sesión) en disco ni en el registro.

**Scale/Scope**: un usuario, un equipo, colas de cientos de entradas (500 de referencia), una
descarga a la vez

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Cumplimiento en el diseño | Estado |
|-----------|---------------------------|--------|
| I. Código simple | Un subpaquete `app/` con un módulo por responsabilidad. Servidor de la biblioteca estándar, sin framework. Comunicación entre procesos con archivos JSON, sin colas ni sockets extra. Interfaz web sin framework ni compilación. Dos dependencias directas nuevas, justificadas: pywebview (solo Windows, R1) y psutil (R6). | Pasa |
| II. Tests de la lógica principal | Tests en Linux y en Windows para la API, los controles de seguridad, la detección del procesador, la parada (con datos y en `waiting_network`, contra el servidor con cortes de 001), el segundo plano, el resultado, el registro y la instancia única. La lógica del trabajador no cambia y conserva sus tests. | Pasa |
| III. Manejo claro de errores | WebView2 ausente: mensaje nativo y código 3, nunca la caída silenciosa a MSHTML (R2). Error fatal o inesperado en segundo plano: resultado y registro (R10). Errores de la API con mensaje y código HTTP. Imposible dejar el trabajador en segundo plano: error explícito (R8). | Pasa |
| IV. Nunca perder progreso reanudable | Detener y Pausar usan el mismo camino que Ctrl+C (R7). La terminación abrupta del trabajador la cubre la recuperación de 001. La aplicación nunca toma `ejecutar.lock` ni modifica la cola salvo con `Store.agregar`. | Pasa |
| V. Sin DRM | Sin cambios: el trabajador es el de 001. | Pasa |
| VI. Sin credenciales ni cookies | Cookies desactivadas por defecto y no recordadas (FR-015). La cola guarda solo el nombre del navegador, como en 001. El secreto de sesión vive solo en memoria. El registro y las respuestas pasan por `ocultar_secretos`. WebView2 en modo privado (R4, R10). | Pasa |
| Restricciones legales y de seguridad | Licencias revisadas (R1, R6). Servidor solo en `127.0.0.1` con 4 controles y tests de rechazo (R4). | Pasa |
| Plataformas (v3.1.0) | Lo exclusivo de Windows se limita a `app/ventana/`, `gestor.pyw` y `Gestor de descargas.vbs`. `test_plataforma.py` solo admite importaciones de Windows sin proteger en `app/ventana/`. El resto de `app/`, la CLI, la cola y el trabajador, en Linux y en Windows. El CI ejecuta la suite completa en los dos sistemas y los tests `solo_windows` solo en Windows, como permite la excepción. | Pasa |

**Re-check post-diseño**: pasa. No hay violaciones que justificar en Complexity Tracking. Los
puntos marcados **[a verificar]** en research no son incumplimientos, sino hipótesis con su
test o su paso manual: el objeto de trabajo de uv (R8), la señal en Windows (R7), la
precedencia de los argumentos de WebView2 (R14) y el presupuesto de memoria (R14). Si una se
refuta, la decisión cambia en research antes de implementar lo que dependa de ella.

## Project Structure

### Documentation (this feature)

```text
specs/002-desktop-app/
├── plan.md
├── research.md          # R1–R19
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── api-local.md     # API HTTP local (la usa solo la ventana)
│   ├── archivos.md      # archivos compartidos entre procesos
│   └── ventana.md       # disposición, diálogos, lanzador (capa de Windows)
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks (no lo crea este comando)
```

### Source Code (repository root)

```text
Gestor de descargas.vbs        # [Windows] lanzador sin consola (R11)
pyproject.toml                 # + psutil; + pywebview (sys_platform == 'win32');
                               #   + [project.gui-scripts] gestor-descargas
scripts/
└── medir_memoria.py           # [herramienta, Windows] SC-005 con psutil (R14)

src/downloader/
├── cli.py                     # agregar y ejecutar delegan en operaciones.py; salida idéntica
├── operaciones.py             # NUEVO: validar calidad, carpeta y navegador; agregar_urls;
│                              #   ejecutar_cola(origen, ...): bloqueo, procesador.json,
│                              #   worker.ejecutar (lo comparten la CLI y la app, R5)
├── bloqueo.py                 # NUEVO: _bloquear/_desbloquear (msvcrt | fcntl), sacado de store.py
├── excepciones.py             # NUEVO: ErrorFatal, ErrorPermanente sin yt-dlp (R15)
├── secretos.py                # NUEVO: ocultar_secretos, sin yt-dlp (R15)
├── mensajes.py                # NUEVO: CONSEJO_SESION, mensaje de lista vacía, requiere_sesion (R17)
├── models.py                  # + NAVEGADORES como constante (R15)
├── progress.py                # + Publicador (progreso-vivo.json) y Observadores (R9)
├── archivos_json.py           # NUEVO: escritura atómica y lectura tolerante de los JSON
│                              #   compartidos (contracts/archivos.md); lo usan ejecutar y la app
├── errors.py, store.py, worker.py, ytdl.py   # reexportan lo movido; sin cambios de comportamiento
└── app/
    ├── __init__.py
    ├── __main__.py            # python -m downloader.app → principal.main
    ├── gestor.pyw             # [Windows] punto de entrada de uv run --gui-script
    ├── principal.py           # arranque: instancia única, registro, servidor, ventana
    │                          #   (importa ventana/ solo en win32)
    ├── instancia.py           # ventana.lock y activar.solicitud (R12)
    ├── servidor.py            # ThreadingHTTPServer en 127.0.0.1:0; controles de R4; estáticos
    ├── api.py                 # rutas de contracts/api-local.md sobre una Ventana abstracta
    ├── vista.py               # filas, sitio, cifras con formato, ETag (R13)
    ├── procesos.py            # lanzar el trabajador (R5, R8), leer procesador.json con psutil
    │                          #   (R6), pedir la parada (R7)
    ├── procesador.py          # python -m downloader.app.procesador: vigilante de parada,
    │                          #   resultado, registro; llama a operaciones.ejecutar_cola
    ├── parada.py              # vigilante: parada.json → equivalente a Ctrl+C (R7)
    ├── resultado.py           # ultima-ejecucion.json (R10)
    ├── registro.py            # RotatingFileHandler, filtro de secretos, excepthooks (R10)
    ├── cierre.py              # decidir(estado) y flujo de Pausar y cerrar (R18)
    ├── web/
    │   ├── index.html         # maqueta (contracts/ventana.md)
    │   ├── app.css
    │   └── app.js             # consulta api/estado cada segundo, pinta y envía órdenes
    └── ventana/               # ── CAPA EXCLUSIVA DE WINDOWS (constitución v3.1.0) ──
        ├── __init__.py
        ├── webview2.py        # comprobación del motor y MessageBoxW (R2)
        └── ventana.py         # pywebview: crear, closing, diálogo de carpeta, enfocar,
                               #   evaluate_js, destruir

tests/
├── support/
│   ├── flaky_server.py        # + modo independiente (--servir, --limite) para quickstart
│   └── ventana_falsa.py       # NUEVO: implementa la Ventana abstracta para los tests
├── unit/
│   ├── test_plataforma.py     # + excepción acotada a app/ventana/
│   ├── test_app_vista.py, test_app_cierre.py, test_app_urls.py, test_mensajes.py
│   ├── test_app_archivos_json.py, test_app_registro.py
│   └── test_app_ventana_windows.py   # @solo_windows, @pywebview_internals
└── integration/
    ├── test_app_seguridad.py  # SC-007 (R4)
    ├── test_app_agregar.py, test_app_procesador.py, test_app_parada.py
    ├── test_app_segundo_plano.py (slow), test_app_vivo.py, test_app_resultado.py
    ├── test_app_cookies.py, test_app_instancia.py, test_app_sin_ytdlp.py
    └── test_lanzador.py       # @solo_windows: wscript //B con DOWNLOADER_HOME temporal (R11)
```

**Structure Decision**: un solo paquete, como en 001, con el subpaquete `downloader.app`. La
frontera de la excepción de Windows es un directorio (`app/ventana/`) más los dos archivos de
lanzamiento. Nada fuera de ellos importa pywebview, `winreg` ni `ctypes.windll`, y `principal.py`
solo los importa tras comprobar `sys.platform == "win32"`. La lógica recibe la ventana como un
objeto con cuatro operaciones, lo que permite probarla en Linux con `ventana_falsa.py`. Los
módulos nuevos fuera de `app/` existen para dos fines: compartir el código de la CLI sin
duplicarlo (`operaciones.py`, `bloqueo.py`) y que la ventana no cargue yt-dlp (`excepciones.py`,
`secretos.py`, `mensajes.py`; R15).

## Decisiones que difieren de la entrada o de la spec

1. **El trabajador no es `downloader ejecutar` literal**: es `python -m
   downloader.app.procesador`, que llama a la misma función que `ejecutar` (R5). Así se
   obtiene el resultado y la parada sin opciones ocultas en la CLI (FR-017).
2. **`procesador.json` y `progreso-vivo.json` los escribe también la CLI**: es un cambio
   interno de `ejecutar`, invisible en su salida, necesario para FR-005 y FR-008 con un
   procesador de la CLI (R6, R9).
3. **Botón "Pausar" en lugar de "Detener"**: lo dicen la entrada y la maqueta. El
   comportamiento es el Detener de la spec (FR-009), y el término coincide con "Pausar y
   cerrar".
4. **Pie "Con conexión"**: solo se muestra con un procesador en marcha, porque el estado de la
   red lo mide el trabajador. La ventana no sondea la red por su cuenta (contracts/ventana.md).
5. **Opción de cookies y franja del resultado**: se añaden a la maqueta, que no los tiene
   (FR-014, FR-012).
6. **Validación de URLs más estricta que la CLI** (solo `http` y `https` con host), como pide
   FR-002. La CLI no cambia (R16).
7. **El registro son cuatro archivos** de 500 000 bytes, dos por proceso escritor, para no
   rotar un archivo abierto por otro proceso en Windows (R10). El total sigue siendo 2 MB.

## Riesgos que el usuario debe conocer

| Riesgo | Dónde | Qué se hace |
|--------|-------|-------------|
| La memoria puede pasar de 300 MB (estimación: 205–315 MB) | R14 | Medición temprana como puerta; mitigaciones en orden; tkinter como plan B, que se consultaría antes |
| En Windows, parar con una conexión estancada puede tardar hasta 15 s (SC-004 pide 10 s) | R7 | Se mide en `test_app_parada.py`; si se confirma, el usuario decide entre enmendar SC-004 o bajar el tiempo de espera de lectura |
| uv o el lanzador del entorno virtual pueden matar el trabajador al cerrar la ventana (objeto de trabajo) | R8 | `CREATE_BREAKAWAY_FROM_JOB`, test obligatorio en Windows; si no se puede salir del objeto de trabajo, error explícito |
| VBScript se retirará de Windows | R11 | Respaldo `gestor-descargas.exe` (gui-scripts); la etapa 2 sustituye al lanzador |
| pywebview cae en silencio a MSHTML sin WebView2 | R2 | Comprobación propia antes de abrir la ventana y test de internos |

## Complexity Tracking

Sin violaciones de la constitución que justificar.
