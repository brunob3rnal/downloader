---

description: "Lista de tareas de la aplicación de escritorio"
---

# Tasks: Aplicación de escritorio

**Input**: Documentos de diseño en `/specs/002-desktop-app/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R19), data-model.md,
contracts/api-local.md, contracts/archivos.md, contracts/ventana.md, quickstart.md

**Tests**: obligatorios (Principio II y regla 2 del usuario). Van primero: cada test se
escribe y se ejecuta en rojo antes de la tarea que lo hace pasar.

**Reglas del usuario para esta feature**:
1. La primera fase tras la preparación es la puerta de memoria (Fase 2). Si se superan los
   300 MB, se para y se consulta.
2. Tests antes que implementación.
3. Los cambios internos de `ejecutar` van en su propia fase (Fase 5), con escritura atómica,
   frecuencia acotada y los tests de 001 intactos.
4. Cada punto "[a verificar]" de research tiene su tarea; la tabla está al final.
5. Los tests exclusivos de Windows llevan el marcador `solo_windows`; los demás corren en Linux
   y en Windows.
6. Fases pequeñas, cada una con su criterio de aceptación y su commit.

**Organization**: después de la preparación, la puerta de memoria y la base (Fases 1–6), una
fase por historia en orden de prioridad. US1 se divide en dos fases: la lógica sin ventana y la
capa de Windows. US4 también: segundo plano, y cierre con instancia única.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: puede ejecutarse en paralelo (archivo distinto, sin dependencias pendientes)
- **[Story]**: historia de usuario de spec.md (US1…US5)
- Rutas relativas a la raíz del repositorio. Comandos con `uv run`.

## Convenciones para todos los tests

- Sin internet. Todo va contra `tests/support/flaky_server.py` (fixture `servidor`), con
  `app_home` (`DOWNLOADER_HOME` temporal) y `destino`, como en 001.
- Los tests de la API usan el servidor real de `src/downloader/app/servidor.py` con
  `tests/support/ventana_falsa.py`, a través de `tests/support/cliente_app.py`. El cliente
  añade el secreto, `Host: 127.0.0.1:<p>`, `Origin` y `X-Gestor: 1`, y permite quitar o
  falsear cada uno.
- Los subprocesos del trabajador se lanzan de verdad (`python -m downloader.app.procesador` o
  `python -m downloader ejecutar`) y se paran o se matan de verdad.
  - Interrupción de la CLI: el helper `interrumpir` de `tests/integration/test_ctrl_c.py`.
  - Muerte abrupta: `Popen.kill()`.
- `@pytest.mark.solo_windows`: exclusivo de Windows; en Linux se omite con el motivo "solo
  Windows (constitución v3.1.0)". `@pytest.mark.pywebview_internals`: depende de internos de
  pywebview (R2). Cualquier otro test corre en los dos sistemas.
- Los archivos de test de la feature 001 existentes **no se modifican**, salvo
  `tests/unit/test_plataforma.py`, cuyo cambio pide el plan (T004). Cada fase lo comprueba en
  su criterio de aceptación con
  `git diff --name-only --diff-filter=M HEAD -- tests/unit tests/integration`, que debe salir
  vacío.
- Al cerrar cada fase: `uv run pytest` en verde (en Linux en local y en Linux y Windows en el
  CI), y un commit con el mensaje indicado.

---

## Phase 1: Setup

**Purpose**: dependencias, marcadores de pytest y soporte de tests.

- [ ] T001 Añadir `psutil>=7.2.2` a `dependencies` y `pywebview>=6.2.1; sys_platform == 'win32'` en `pyproject.toml` con `uv add`. Comprobar en `uv.lock` que pywebview, pythonnet, clr_loader, cffi, proxy_tools, bottle y typing_extensions solo se resuelven para `win32`, y que `uv sync` en Linux no instala pywebview (research R1, R6)
- [ ] T002 Registrar en `pyproject.toml` (`[tool.pytest.ini_options].markers`) estos dos marcadores:
  - `solo_windows: exclusivo de Windows (ventana, lanzador, R7, R8, R11); se omite en Linux`;
  - `pywebview_internals: depende de internos de pywebview; ver research R2`.

  En `tests/conftest.py` (`pytest_runtest_setup`), omitir los `solo_windows` si `sys.platform != "win32"` con el motivo "solo Windows (constitución v3.1.0)"
- [ ] T003 [P] Crear `tests/support/ventana_falsa.py` con la clase `VentanaFalsa`. Implementa `examinar_carpeta()` (devuelve la ruta fijada en el test), `enfocar()`, `ejecutar_js(codigo)` y `destruir()`, y registra cada llamada en `self.llamadas` (research R19)
- [ ] T004 [P] Ampliar `tests/unit/test_plataforma.py`: las importaciones y atributos de un solo sistema sin proteger solo se admiten dentro de `src/downloader/app/ventana/`. Añadir un caso que falle si aparecen fuera, en un archivo temporal bajo `app/` (plan, Constitution Check, Plataformas)
- [ ] T005 [P] Añadir a `tests/support/flaky_server.py` un modo independiente: `python -m tests.support.flaky_server --servir PUERTO --limite BYTES_POR_S --archivo NOMBRE --tamano BYTES`. Sirve un archivo generado a esa velocidad hasta Ctrl+C. Lo usan quickstart.md y `scripts/medir_memoria.py`
- [ ] T006 Commit `002 Fase 1: dependencias, marcadores y soporte de tests`

**Aceptación**:
- `uv run pytest` pasa en Linux y en Windows;
- `uv run pytest -m solo_windows -rs` en Linux lista los omitidos con su motivo;
- `uv tree` en Linux no muestra pywebview.

---

## Phase 2: Puerta de memoria (SC-005) — BLOQUEA TODO LO DEMÁS

**Purpose**: medir, antes de construir ninguna pantalla, si una ventana mínima de pywebview y
el trabajador caben en 300 MB de memoria privada (USS) (regla 1; research R14).

**⚠️ PUERTA**: si el máximo medido supera los 300 MB con las mitigaciones de R14 aplicadas,
**parar y consultar al usuario** con las cifras antes de empezar la Fase 3.

- [ ] T007 [P] Escribir `tests/unit/test_medir_memoria.py`. Prueba las funciones puras de `scripts/medir_memoria.py` (cargado con `importlib` desde su ruta) con objetos de proceso falsos:
  - `procesos_de_la_aplicacion(ventana, trabajador)` incluye el proceso de la ventana, sus descendientes, sus ascendientes de la cadena del lanzador (`uv.exe` y el lanzador del entorno virtual, por nombre de ejecutable), el trabajador y sus descendientes; excluye los demás;
  - `suma_uss(procesos)` ignora los procesos que terminan durante la muestra (`psutil.NoSuchProcess`);
  - `maximo(muestras)` devuelve el máximo y el desglose en ese instante.

  Corre en Linux y en Windows
- [ ] T008 [P] Crear `scripts/ventana_minima.py` y `scripts/ventana_minima.html` (solo Windows, herramienta de medición). La ventana se abre con pywebview `gui="edgechromium"` y `private_mode=True`, y carga la página desde un `ThreadingHTTPServer` en `127.0.0.1:0`. La página tiene 500 filas con la estructura de contracts/ventana.md (título, sitio, etiqueta, barra, cifras), sin la interfaz real. Cada segundo pide un JSON estático de 500 filas y actualiza las que cambian
- [ ] T009 Crear `scripts/medir_memoria.py` con estas opciones: `--cola-de-prueba N` (500 por defecto), `--limite MB` (300), `--ventana minima|app`, `--sin-uv` y `--importar-store`. El script:
  1. crea un `DOWNLOADER_HOME` temporal con N descargas en cola que apuntan al servidor independiente de T005: un archivo de 200 MB a 2 MB/s;
  2. arranca la ventana por la misma cadena que el lanzador (`uv run --gui-script scripts/ventana_minima.py`), o directamente con `pythonw` si se pasa `--sin-uv`;
  3. arranca el trabajador con `python -m downloader ejecutar` y `creationflags=CREATE_NO_WINDOW`, que en esta fase es la CLI;
  4. muestrea la USS de `procesos_de_la_aplicacion` cada 0,5 s hasta que termina la primera descarga;
  5. imprime el máximo, el desglose por proceso en ese instante y la línea de órdenes de los procesos `msedgewebview2.exe`;
  6. sale con 0 si el máximo es de `--limite` o menos, y con 1 si se pasa.

  Con `--importar-store`, la ventana mínima además importa `downloader.store`, que hoy carga yt-dlp, para medir su coste (R15)
- [ ] T010 **[manual, Windows, equipo de 4 GB]** Ejecutar `uv run python scripts/medir_memoria.py` en cuatro variantes: tal cual, con `--sin-uv`, con `--importar-store`, y con `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--renderer-process-limit=1 --disable-gpu --disable-extensions"`. En la última, comprobar en la línea de órdenes impresa de `msedgewebview2.exe` si esos argumentos llegan junto a `--disable-features=ElasticOverscroll` de pywebview o lo sustituyen (precedencia, R14 [a verificar]).

  Anotar en `specs/002-desktop-app/research.md` (R14, nuevo apartado "Resultado de la medición") la fecha, el equipo, el máximo y el desglose de cada variante, el coste de importar `downloader.store` (verifica la estimación de R15) y la precedencia observada
- [ ] T011 **PUERTA**: con los resultados de T010, decidir.
  - Si alguna variante viable queda en 300 MB o menos: anotar en research R14 qué mitigaciones son obligatorias (por ejemplo, los argumentos de WebView2) y añadirlas como requisito a T059, `ventana/ventana.py`.
  - Si ninguna lo consigue: **detener la implementación** y presentar al usuario las cifras, las mitigaciones probadas y el plan B (tkinter, R1). No continuar sin su respuesta.
- [ ] T012 Commit `002 Fase 2: medición de memoria con ventana mínima (SC-005)`, con los scripts, el test y los resultados en research.md

**Aceptación**:
- `test_medir_memoria.py` pasa en Linux y en Windows;
- research R14 tiene las cifras reales;
- la puerta está decidida y anotada.

---

## Phase 3: Base — módulos sin yt-dlp (R15, R17)

**Purpose**: que la ventana pueda abrir la cola y mostrar mensajes sin cargar yt-dlp, sin
cambiar el comportamiento de la CLI.

- [ ] T013 [P] Escribir `tests/unit/test_app_sin_ytdlp.py`. Lanza un intérprete limpio con `subprocess.run([sys.executable, "-c", ...])`, que importa `downloader.models`, `downloader.config`, `downloader.store`, `downloader.bloqueo`, `downloader.excepciones`, `downloader.secretos` y `downloader.mensajes`, y comprueba que `"yt_dlp" not in sys.modules`. Debe fallar (rojo): hoy `store` importa `errors`, que importa yt-dlp
- [ ] T014 [P] Escribir `tests/unit/test_mensajes.py` con estos casos:
  - `mensajes.requiere_sesion(texto)` devuelve `True` si el texto contiene `CONSEJO_SESION`, `SIN_VIDEOS` (el mensaje de FR-028) o `CONSEJO_OTRO_NAVEGADOR` (el de cookies ilegibles);
  - devuelve `False` con los mensajes de DRM, de video privado, de sitio no soportado y de un 404;
  - `models.NAVEGADORES == frozenset(yt_dlp.cookies.SUPPORTED_BROWSERS)`;
  - `secretos.ocultar_secretos` se comporta igual que `errors.ocultar_secretos` con los casos de `tests/unit/test_errors.py`, recorridos por parametrización sin modificar ese archivo
- [ ] T015 **[a verificar R17]** Averiguar cómo clasifica la feature 001 un "contenido que exige iniciar sesión" sin 403. Escribir en `tests/unit/test_mensajes.py` un caso con `tests/support/fake_extractor.py` que lance el `ExtractorError` de yt-dlp para login (`self.raise_login_required()`) y ejecute `errors.clasificar_detalle`. Anotar en research R17 el mensaje resultante.
  - Si no incluye `CONSEJO_SESION`, `mensajes.requiere_sesion` MUST reconocer también el texto que yt-dlp pone en esos errores (las constantes de `raise_login_required`) **sin cambiar el mensaje de la CLI** (FR-017).
  - El caso debe acabar en `requiere_sesion(...) is True`
- [ ] T016 Crear `src/downloader/excepciones.py` con `ErrorFatal` y `ErrorPermanente`, que se mueven de `errors.py`. Crear `src/downloader/secretos.py` con `ocultar_secretos`, `_parametro_secreto` y `_SUFIJOS_SECRETOS`. Crear `src/downloader/mensajes.py` con `CONSEJO_SESION`, `SIN_VIDEOS` (de `worker.py`), `CONSEJO_OTRO_NAVEGADOR` (de `errors.py`) y `requiere_sesion`. Ninguno de los tres importa yt-dlp
- [ ] T017 Hacer que `errors.py`, `worker.py` y `ytdl.py` importen y reexporten desde esos módulos, con los mismos nombres (`errors.ErrorFatal`, `errors.ocultar_secretos`, `errors.CONSEJO_SESION`…). Cambiar `store.py` para que importe `ErrorFatal` de `excepciones.py`. Añadir `NAVEGADORES` como `frozenset` en `src/downloader/models.py` y hacer que `ytdl.NAVEGADORES` la reexporte
- [ ] T018 [P] Mover `_bloquear` y `_desbloquear` (con su import condicional `msvcrt`/`fcntl`) de `src/downloader/store.py` a `src/downloader/bloqueo.py`, con las funciones públicas `bloquear(descriptor) -> bool` y `desbloquear(descriptor)`. `store.py` las usa desde ahí, sin cambiar su comportamiento (R12)
- [ ] T019 Commit `002 Fase 3: módulos sin yt-dlp para la ventana (R15, R17)`

**Aceptación**:
- `test_app_sin_ytdlp.py` y `test_mensajes.py` en verde;
- la suite de 001 en verde, sin archivos de test modificados (comando de las convenciones).

---

## Phase 4: Base — código compartido entre la CLI y la aplicación (R5)

**Purpose**: extraer de `cli.py` la validación, el agregado y el cuerpo de `ejecutar` a
`operaciones.py`, para que la aplicación use el mismo código (FR-007), con la salida de la CLI
idéntica (FR-017).

- [ ] T020 [P] Escribir `tests/unit/test_operaciones.py` con estos casos:
  - `validar_calidad`, `validar_navegador` y `validar_carpeta` lanzan `ValueError` con los mismos textos que hoy imprime la CLI ("calidad desconocida «x»; admitidas: …", "navegador no admitido en --cookies-de: «x»; admitidos: …", "la carpeta de destino X no existe", "sin permiso de escritura en la carpeta de destino X");
  - `agregar_urls(store, urls, calidad, carpeta, navegador)` devuelve la lista de `ResultadoAgregar` con las acciones `agregada`, `ya_en_cola` y `reencolada`;
  - `ejecutar_cola(origen="cli", ...)` devuelve el `worker.Resultado` y lanza `EjecucionEnCurso` si `ejecutar.lock` está tomado.

  Debe fallar (rojo)
- [ ] T021 Crear `src/downloader/operaciones.py` con `validar_calidad`, `validar_navegador`, `validar_carpeta`, `agregar_urls` y `ejecutar_cola(origen, ejecucion=None, navegador=None, presentador=None)`. `ejecutar_cola` toma el bloqueo, llama a `recuperar()`, arranca `MonitorRed`, llama a `worker.ejecutar` y devuelve el `Resultado`. La comprobación de ffmpeg y la configuración siguen siendo responsabilidad del llamador, como hoy
- [ ] T022 Cambiar `src/downloader/cli.py` para que `agregar` y `ejecutar` deleguen en `operaciones`: convertir `ValueError` en `_error(..., USO_INVALIDO)` y `EjecucionEnCurso` en código 2, y conservar los mensajes, el orden de las comprobaciones y los códigos 0, 1, 2, 3 y 130
- [ ] T023 Commit `002 Fase 4: operaciones compartidas entre la CLI y la aplicación`

**Aceptación**:
- `test_operaciones.py` en verde;
- toda la suite de 001 en verde sin tocar sus tests, en especial `test_cli_agregar.py`, `test_salida_ejecutar.py`, `test_ctrl_c.py` y `test_ffmpeg_ausente.py` (SC-009).

---

## Phase 5: Cambios internos en `ejecutar` — `procesador.json` y `progreso-vivo.json` (regla 3)

**Purpose**: que todo procesador, también un `ejecutar` de la CLI, publique quién es y su
progreso en directo, sin cambiar nada visible de la CLI (R6, R9; contracts/archivos.md).

- [ ] T024 [P] Escribir `tests/unit/test_archivos_json.py` con estos casos:
  - `escribir_atomico(ruta, datos)` deja el archivo completo o el anterior, nunca a medias: un lector en otro hilo lee 500 veces mientras se escribe 500 veces y nunca obtiene JSON inválido;
  - con `os.replace` parcheado para lanzar `PermissionError` una vez, `escribir_atomico(..., reintentar=True)` reintenta tras 50 ms, y `escribir_atomico(..., reintentar=False)` devuelve `False` sin lanzar; no quedan temporales;
  - `leer(ruta)` devuelve `None` si el archivo no existe, si está vacío o si su JSON no es válido.

  Debe fallar (rojo)
- [ ] T025 [P] Escribir `tests/unit/test_publicador.py` para `progress.Publicador` con un reloj falso:
  - escribe como mucho una vez por segundo aunque reciba 100 eventos de progreso por segundo (frecuencia acotada);
  - contiene `pid`, `creado`, `actualizado`, `en_linea` y `activa` con `id`, `bytes`, `total`, `velocidad`, `eta`, `sin_datos` (null hasta 5 s sin bytes nuevos) y `espera` (`causa` con los secretos ocultos y `reintento_en`);
  - `activa` vale null entre descargas;
  - no contiene ninguna URL ni título;
  - `detener()` borra el archivo.

  También para `progress.Observadores([a, b])`, que reparte cada evento a los dos. Debe fallar
  (rojo)
- [ ] T026 [P] Escribir `tests/integration/test_ejecutar_archivos.py`, que lanza `python -m downloader ejecutar` contra el fixture `servidor`, con un archivo de 20 MB a 2 MB/s:
  - durante la descarga, `procesador.json` tiene `origen: "cli"`, `ejecucion: null`, `pid == proceso.pid` y un `creado` que coincide con `psutil.Process(pid).create_time()`;
  - `progreso-vivo.json` se actualiza al menos cada 2 s, con `velocidad` y `eta` no nulas y un `actualizado` de hace 2 s o menos;
  - al terminar con 0, con 1 y con 130 (`interrumpir`), los dos archivos ya no existen;
  - tras `Popen.kill()`, `procesador.json` sigue ahí con un `pid` muerto, y un `ejecutar` posterior lo sobrescribe;
  - la salida estándar de `ejecutar` es idéntica a la de una ejecución sin estos archivos: comparar las líneas con el patrón de `test_salida_ejecutar.py`.

  Debe fallar (rojo)
- [ ] T027 Crear `src/downloader/archivos_json.py` con `escribir_atomico(ruta, datos, reintentar=False) -> bool` (temporal en el mismo directorio, `json.dumps(..., ensure_ascii=False)`, `os.replace`, sin `fsync`, un reintento a los 50 ms con `PermissionError` si `reintentar`) y `leer(ruta) -> dict | None` (contracts/archivos.md)
- [ ] T028 Añadir a `src/downloader/progress.py` las clases `Publicador(directorio, pid, creado, reloj=time.monotonic)` y `Observadores(lista)`. `Publicador` implementa los mismos métodos que `Presentador` (`iniciar`, `detener`, `inicio`, `titulo`, `progreso`, `esperando`, `completada`, `fallida`, `aviso`, `mensaje`, `resumen`) y escribe `progreso-vivo.json` desde un hilo a 1 Hz con `escribir_atomico(reintentar=False)`: si Windows lo impide, lo intenta en el siguiente segundo. Recibe `en_linea` del monitor
- [ ] T029 En `operaciones.ejecutar_cola`:
  - escribir `procesador.json` (`pid`, `creado` de `psutil.Process().create_time()`, `origen`, `ejecucion`, `inicio`) justo después de `bloquear_ejecucion()`;
  - pasar a `worker.ejecutar` `Observadores([presentador, Publicador(...)])`;
  - borrar `procesador.json` en un `finally` también con `ErrorFatal` y `KeyboardInterrupt`; el `Publicador` borra el suyo al detenerse.

  El `Presentador` de la CLI no cambia
- [ ] T030 Commit `002 Fase 5: ejecutar publica procesador.json y progreso-vivo.json`

**Aceptación**:
- T024–T026 en verde en Linux y en Windows;
- `git diff --name-only --diff-filter=M HEAD -- tests/unit tests/integration` vacío;
- la suite completa de 001 en verde sin modificar ninguno de sus tests (regla 3).

---

## Phase 6: Base — infraestructura de la aplicación y seguridad (FR-013, FR-020, R4, R6, R10)

**Purpose**: servidor local seguro, registro, resultado y detección básica del procesador.
Todas las historias dependen de esta fase.

- [ ] T031 [P] Crear `tests/support/cliente_app.py` (cliente `http.client` que añade el secreto, `Host`, `Origin` y `X-Gestor`, cada uno con opción de quitarlo o falsearlo). En `tests/integration/conftest.py`, crear el fixture `app_local`, que arranca `principal.arrancar_servidor(VentanaFalsa())` en un hilo con `app_home` y lo cierra al terminar
- [ ] T032 [P] Escribir `tests/integration/test_app_seguridad.py` (SC-007, R4). Debe responder 403, sin cuerpo y sin cabeceras `Access-Control-*`, en cada uno de estos casos:
  - sin secreto;
  - con un secreto incorrecto de la misma longitud;
  - con el secreto correcto y `Host` igual a `localhost:<p>`, a `evil.example:<p>` o ausente;
  - con `Origin: http://evil.example`;
  - un `POST` sin `Origin`;
  - una ruta `api/*` sin `X-Gestor`;
  - un `OPTIONS`;
  - `/<secreto>/../pyproject.toml` y `/<secreto>/%2e%2e/pyproject.toml`.

  En cada caso, comprobar además que `cola.db` no cambió y que el cuerpo no contiene datos de la cola. Comprobar también que el servidor escucha en `127.0.0.1` (`server_address[0]`) y que conectar a la IP no local del equipo (obtenida con un socket UDP hacia `192.0.2.1`) se rechaza; si el equipo no tiene IP no local, el caso se omite con el motivo. Con todo correcto, `GET /<secreto>/` responde 200 con `Content-Security-Policy`. Debe fallar (rojo)
- [ ] T033 [P] Escribir `tests/unit/test_app_registro.py` (FR-020, R10):
  - tras escribir 5 MB de errores en `ventana` y en `procesador`, existen como mucho `ventana.log`, `ventana.log.1`, `procesador.log` y `procesador.log.1`, cada uno de 500 000 bytes o menos y 2 000 000 bytes en total;
  - un mensaje y una traza con `?token=abc&sig=def` y una cookie ficticia quedan con los secretos ocultos;
  - con la carpeta `registro/` sin permiso de escritura, `registro.disponible()` devuelve `False` y no se lanza nada;
  - `sys.excepthook` y `threading.excepthook` escriben la traza.

  Debe fallar (rojo)
- [ ] T034 [P] Escribir `tests/unit/test_app_resultado.py`: `resultado.escribir(...)` y `resultado.leer(...)` con el formato de contracts/archivos.md (`codigo` de 0, 1, 3 o 130, o null; `error.tipo` de `fatal`, `inesperado`, `detenida` o `abrupto`), con los nombres y causas de `fallidas` pasados por `ocultar_secretos`. Debe fallar (rojo)
- [ ] T035 [P] Escribir `tests/unit/test_app_procesos_estado.py` para `procesos.estado_procesador(directorio)`:
  - sin `procesador.json` → `ninguno`;
  - con el `pid` del propio test y su `creado` real → `en_marcha` con el origen del archivo;
  - con un `pid` vivo y un `creado` distinto (pid reutilizado) → `ninguno`;
  - con un `pid` muerto → `ninguno` y la marca `abrupto=True`;
  - con JSON inválido → `ninguno`.

  Debe fallar (rojo)
- [ ] T036 Ampliar `tests/unit/test_app_sin_ytdlp.py` con `downloader.app.servidor`, `downloader.app.api`, `downloader.app.vista`, `downloader.app.registro`, `downloader.app.resultado`, `downloader.app.procesos` y `downloader.app.principal` (R15)
- [ ] T037 Crear `src/downloader/app/registro.py`:
  - `configurar(origen)`, con `origen` igual a `ventana` o `procesador`: `RotatingFileHandler(registro/<origen>.log, maxBytes=500_000, backupCount=1, encoding="utf-8")`;
  - un `Formatter` que aplica `ocultar_secretos` al mensaje y a la traza ya formateada, con el formato de contracts/archivos.md;
  - `handleError` que activa el indicador de no disponible;
  - `disponible()`;
  - la instalación de `sys.excepthook` y `threading.excepthook`.
- [ ] T038 [P] Crear `src/downloader/app/resultado.py` (`escribir`, `leer` y `escribir_abrupto(ejecucion)`) con `archivos_json`
- [ ] T039 [P] Crear `src/downloader/app/procesos.py` con `estado_procesador(directorio)` (lee `procesador.json` con `archivos_json.leer` y comprueba el proceso y su creación con `psutil`) y el tipo `EstadoProcesador(estado, origen, ejecucion, pid, abrupto)` (R6)
- [ ] T040 Crear `src/downloader/app/servidor.py`:
  - `ThreadingHTTPServer` en `("127.0.0.1", 0)` con `daemon_threads = True`;
  - un manejador con los 5 controles de contracts/api-local.md, en orden, con `hmac.compare_digest`;
  - `log_message` vacío;
  - las cabeceras comunes y la CSP en `index.html`;
  - los estáticos por lista blanca (`index.html`, `app.js`, `app.css` de `src/downloader/app/web/`);
  - el despacho de `api/*` a `api.py`.

  Crear también `src/downloader/app/api.py` con el protocolo `Ventana` (`examinar_carpeta`, `enfocar`, `ejecutar_js`, `destruir`) y el enrutado vacío, más `src/downloader/app/web/index.html` provisional
- [ ] T041 Crear `src/downloader/app/principal.py` con:
  - `arrancar_servidor(ventana) -> (servidor, url)`: secreto `secrets.token_urlsafe(32)`, guardado solo en memoria;
  - `main()`: configura el registro de `ventana`; importa `downloader.app.ventana` dentro de la función y solo con `sys.platform == "win32"`, nunca en el nivel del módulo;
  - `src/downloader/app/__init__.py` y `src/downloader/app/__main__.py`.
- [ ] T042 Commit `002 Fase 6: servidor local seguro, registro, resultado y estado del procesador`

**Aceptación**:
- T032–T036 en verde en Linux y en Windows (SC-007 automatizado);
- `test_plataforma.py` en verde.

---

## Phase 7: User Story 1 — Agregar e iniciar, sin ventana (Priority: P1) 🎯 MVP (1/2)

**Goal**: la API agrega URLs, inicia el procesamiento en un subproceso con el mismo código que
`ejecutar` y muestra la cola con estado, porcentaje, velocidad, tiempo estimado y aviso.

**Independent Test**: con `app_local`, agregar dos URLs del `servidor`, iniciar y ver en
`api/estado` cómo avanzan hasta que `ultima_ejecucion` dice 2 completadas y los archivos están
en `destino`.

### Tests for User Story 1 (primero, en rojo)

- [ ] T043 [P] [US1] Escribir `tests/unit/test_app_urls.py` (R16):
  - líneas vacías y de solo espacios ignoradas;
  - `http` y `https` con host válidas;
  - `ftp://x`, `hola`, `https://` sin host y `javascript:alert(1)` inválidas, con su número de línea (empezando en 1, contando las vacías);
  - espacios alrededor recortados.
- [ ] T044 [P] [US1] Escribir `tests/unit/test_app_vista.py` (R13, data-model "Fila de la vista"):
  - `sitio` es solo el host (sin usuario, contraseña, puerto, ruta ni consulta);
  - `porcentaje` sale de `progreso-vivo.json` para la descarga activa y de la cola para las demás;
  - cifras: "2,4 MB/s", "quedan 1 min 12 s", "Sin datos hace 14 s, reintentando", "Esperando red · reintento en 40 s";
  - etiquetas con mayúscula inicial;
  - un vivo de otro `pid` o con más de 5 s se ignora;
  - las listas se muestran como "lista (N videos)";
  - `aviso` según el estado;
  - los secretos de URL, título y error quedan ocultos;
  - el `ETag` cambia si cambia `actualizada`, el vivo o el estado del procesador, y no cambia si nada cambia.
- [ ] T045 [P] [US1] Escribir `tests/integration/test_app_agregar.py` (contracts/api-local.md, `POST api/agregar` y `GET api/opciones`):
  - las líneas válidas se agregan y las inválidas se devuelven con su número;
  - el resultado en `cola.db` es igual que con `downloader agregar` con las mismas URLs y opciones en otro `app_home` (comparar url, calidad, carpeta y estado);
  - duplicados en `ya_en_cola` y fallidas en `reencoladas`;
  - carpeta inexistente, carpeta sin permiso, calidad desconocida o navegador desconocido → 400 con el mensaje de la CLI y nada agregado;
  - texto vacío → 400 "Pegue al menos un enlace";
  - `api/opciones` toma calidad y carpeta de `config.toml`, y con un `config.toml` inválido devuelve los valores por defecto y `aviso_config`.
- [ ] T046 [P] [US1] Escribir `tests/integration/test_app_iniciar.py` (`@pytest.mark.ffmpeg`), con `app_local`, `servidor` y archivos de 5 MB a 1 MB/s:
  - `POST api/iniciar` → 202 con `ejecucion`;
  - mientras dura, `api/estado` da `procesador.estado == "en_marcha"`, `origen == "app"` y una fila con `velocidad` y `eta`;
  - una URL agregada durante el procesamiento se procesa sin volver a iniciar (US1, escenario 4);
  - al final, `ultima_ejecucion` dice `completadas: 3`, `codigo: 0` y `error: null`, con los archivos en `destino`;
  - con una URL que da 404, `codigo: 1` y una entrada en `fallidas` con la causa;
  - sin ffmpeg en el `PATH` del subproceso, `error.tipo == "fatal"` con el mismo mensaje que `ejecutar` y el código 3 (FR-012).
- [ ] T047 [P] [US1] Escribir `tests/unit/test_app_procesador.py`. Llama a `procesador.main(["--ejecucion", "x"])` con `operaciones.ejecutar_cola` parcheado para lanzar `RuntimeError("boom")`. Comprueba que `ultima-ejecucion.json` tiene `error.tipo == "inesperado"` con "RuntimeError: boom", que `registro/procesador.log` contiene la traza y que el código de salida no es 0
- [ ] T048 [P] [US1] Escribir `tests/integration/test_app_vivo.py` (FR-005, SC-006):
  - con un procesamiento de la aplicación y con un `ejecutar` de la CLI, `api/estado` muestra `velocidad` y `eta` de la descarga activa;
  - el `porcentaje` de la API no va más de 2 s por detrás de los bytes que el servidor ha enviado: comparar la marca temporal de `bytes_servidos` del `servidor` con la de la consulta.

### Implementation for User Story 1

- [ ] T049 [US1] Crear `src/downloader/app/urls.py` con `separar(texto) -> (validas, invalidas[(linea, texto)])` (R16)
- [ ] T050 [US1] Crear `src/downloader/app/vista.py` con `filas(descargas, vivo, estado)`, los formatos de cifras y `etag(...)`. Tras el primer `BaseCorrupta`, la vista deja de leer y devuelve el aviso `cola_corrupta` (R13)
- [ ] T051 [US1] Añadir a `src/downloader/app/procesos.py` la función `lanzar_trabajador(directorio, ejecucion) -> Popen` (R5, R8):
  - intérprete: `python.exe` junto a `pythonw.exe` si `sys.executable` es `pythonw`;
  - orden: `[python, "-m", "downloader.app.procesador", "--ejecucion", id]`;
  - `stdout`/`stderr` a `DEVNULL`;
  - en Windows, `creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`, protegido por una comprobación de plataforma (test_plataforma);
  - en Linux, `start_new_session=True`;
  - si `CreateProcess` falla con `ERROR_ACCESS_DENIED` por el objeto de trabajo, lanza `SinSegundoPlano` con el mensaje de R8 y lo anota en el registro, sin reintentar sin el indicador.
- [ ] T052 [US1] Crear `src/downloader/app/procesador.py` (`python -m downloader.app.procesador --ejecucion ID`):
  1. configura el registro `procesador`;
  2. comprueba ffmpeg con la misma función que la CLI y carga la configuración;
  3. llama a `operaciones.ejecutar_cola(origen="app", ejecucion=ID)`;
  4. escribe `ultima-ejecucion.json`: completadas y fallidas (con su causa), y `codigo` 0 o 1; con `ErrorFatal`, `fatal` y 3; con `KeyboardInterrupt`, `detenida` y 130; con cualquier otra excepción, `inesperado` (traza al registro);
  5. con `EjecucionEnCurso`, sale con 2 **sin** escribir resultado.
- [ ] T053 [US1] Implementar en `src/downloader/app/api.py` las rutas `GET api/opciones`, `GET api/estado` (con `If-None-Match` y 304), `POST api/agregar` (con `operaciones.validar_*` y `agregar_urls`) y `POST api/iniciar` (202 o 500 con el error en el registro), según contracts/api-local.md. `POST api/iniciar` lanza el trabajador y guarda la ejecución en curso para mostrar el estado `arrancando`
- [ ] T054 [US1] Commit `002 Fase 7: API de agregar, estado e iniciar con el trabajador en subproceso (US1)`

**Aceptación**:
- T043–T048 en verde en Linux y en Windows;
- la suite de 001 en verde sin tocar sus tests.

---

## Phase 8: User Story 1 — Ventana, interfaz y lanzador en Windows (Priority: P1) 🎯 MVP (2/2)

**Goal**: el doble clic abre la ventana de la maqueta sin consola, y si falta WebView2 da un
mensaje claro con el código 3.

**Independent Test**: quickstart V1, V2 y V7.

### Tests for User Story 1 (primero, en rojo)

- [ ] T055 [P] [US1] Escribir `tests/unit/test_app_ventana_windows.py` (`@pytest.mark.solo_windows`) con estos casos, comprobando cada uno de ellos que no se crea ninguna ventana:
  - con `webview.platforms.winforms.renderer` parcheado a `"mshtml"`, `ventana.webview2.comprobar()` devuelve `False`; `principal.main()` llama a `MessageBoxW` (parcheado) con el texto exacto de R2, escribe una entrada en `registro/ventana.log` y termina con el código 3 **[a verificar R2: mensaje y código 3]**;
  - con la importación de `webview.platforms.winforms` parcheada para lanzar `Exception`, el mismo mensaje con la causa y el código 3;
  - con `@pytest.mark.pywebview_internals`, el atributo `renderer` existe y vale `edgechromium`, `mshtml` o `cef`.
- [ ] T056 [P] [US1] Escribir `tests/integration/test_lanzador.py` (`@pytest.mark.solo_windows`, `slow`) (R11):
  1. ejecuta `wscript.exe //B "Gestor de descargas.vbs"` con `DOWNLOADER_HOME` temporal;
  2. espera hasta 30 s un proceso `pythonw.exe` cuya línea de órdenes contenga `gestor.pyw`;
  3. comprueba con `EnumWindows` (por `ctypes`) que ningún proceso de la cadena (`wscript`, `uv`, `pythonw` y sus hijos) tiene una ventana visible de clase `ConsoleWindowClass`;
  4. termina la cadena.

### Implementation for User Story 1

- [ ] T057 [P] [US1] Crear `src/downloader/app/web/index.html`, `app.css` y `app.js` según contracts/ventana.md y la maqueta:
  - panel de enlaces (área de texto, Calidad, Guardar en con Examinar, Agregar a la cola, resumen y líneas inválidas);
  - cabecera Cola con su subtítulo, Iniciar y Pausar (Pausar desactivado hasta la Fase 9);
  - filas con título, sitio, etiqueta coloreada, porcentaje, barra y detalle;
  - franja del resultado;
  - pie.

  `app.js` toma el secreto de `location.pathname`, envía `X-Gestor: 1`, consulta `api/estado` cada segundo con `If-None-Match` y actualiza solo las filas que cambian, por id. Sin dependencias externas ni recursos de otros orígenes (CSP)
- [ ] T058 [US1] Crear `src/downloader/app/ventana/webview2.py`:
  - `comprobar() -> str | None` (None si el motor es `edgechromium`; si no, la causa);
  - `mostrar_error(texto)` con `ctypes.windll.user32.MessageBoxW`;
  - el texto de R2.
- [ ] T059 [US1] Crear `src/downloader/app/ventana/ventana.py`, una implementación de `Ventana` con pywebview:
  - `webview.create_window("Gestor de descargas", url, width=1100, height=760, min_size=(720, 520))`;
  - `webview.start(gui="edgechromium", private_mode=True)`;
  - `examinar_carpeta` con `create_file_dialog(webview.FileDialog.FOLDER)`;
  - `enfocar` con `restore`, `show` y traer al frente;
  - `ejecutar_js` con `evaluate_js`;
  - `destruir`;
  - las mitigaciones de memoria que T011 haya hecho obligatorias.

  Y `src/downloader/app/ventana/__init__.py`
- [ ] T060 [US1] Completar `principal.main()` para Windows: comprobar WebView2 (si falla: mensaje, registro y código 3), arrancar el servidor y abrir la ventana. Implementar `POST api/examinar` (501 sin ventana real)
- [ ] T061 [P] [US1] Crear `src/downloader/app/gestor.pyw`, que llama a `downloader.app.principal.main()`, y `Gestor de descargas.vbs` (R11). El `.vbs`:
  - fija el directorio de trabajo en su propia carpeta;
  - comprueba `uv` en el `PATH`; si falta, muestra un `MsgBox` con el texto de contracts/ventana.md;
  - ejecuta `uv run --gui-script src\downloader\app\gestor.pyw` con `WScript.Shell.Run(..., 0, False)`.

  Añadir `[project.gui-scripts] gestor-descargas = "downloader.app.principal:main"` en `pyproject.toml`
- [ ] T062 [US1] **[manual, Windows]** Quickstart V1: con el entorno sincronizado, doble clic desde el Explorador. Comprobar que no aparece ninguna consola y medir el tiempo hasta la ventana lista (SC-001: 5 s o menos). Comprobar que `.venv\Scripts\gestor-descargas.exe` también abre la ventana sin consola. Anotar los resultados en quickstart.md (nuevo apartado "Resultados")
- [ ] T063 [US1] **[manual, Windows]** Quickstart V2 y comparación con la maqueta (contracts/ventana.md, incluida la tabla de diferencias)
- [ ] T064 [US1] **[manual, Windows] [a verificar R2]** Quickstart V7: en una máquina virtual sin WebView2, comprobar el cuadro de mensaje de R2, el código 3 (`echo %ERRORLEVEL%` tras `start /wait`) y la entrada en `registro\ventana.log`. Anotar el resultado en research R2
- [ ] T065 [US1] Commit `002 Fase 8: ventana, interfaz y lanzador en Windows (US1)`

**Aceptación**:
- T055–T056 en verde en el CI de Windows y omitidos con su motivo en Linux;
- V1, V2 y V7 hechos y anotados.

---

## Phase 9: User Story 2 — Pausar y reanudar (Priority: P1)

**Goal**: Pausar detiene un procesamiento de la aplicación igual que Ctrl+C, y se reanuda
desde la aplicación o desde la CLI.

**Independent Test**: `test_app_parada.py` y quickstart V3.

### Tests for User Story 2 (primero, en rojo)

- [ ] T066 [P] [US2] Escribir `tests/unit/test_app_parada.py` para `parada.Vigilante`, con la función que provoca la interrupción parcheada:
  - con un `parada.json` de su ejecución, la llama una vez y borra el archivo;
  - con uno de otra ejecución, no la llama y borra el archivo;
  - sin archivo, no hace nada.

  Comprobar que consulta cada 0,25 s
- [ ] T067 [P] [US2] Escribir `tests/integration/test_app_parada.py` (`@pytest.mark.ffmpeg`), con `app_local` y una descarga de 50 MB a 2 MB/s. Medir el tiempo de cada parada:
  - (a) **con datos llegando**: al 30 %, `POST api/detener` → 202; `api/estado` pasa por `deteniendo`. El proceso sale con 130 en 10 s o menos (assert en los dos sistemas). `ultima_ejecucion.error.tipo == "detenida"`. La descarga vuelve a `queued` con `bytes_descargados > 0`. Al reanudar con `POST api/iniciar`, la primera petición del archivo trae `Range: bytes=N-` con N > 0 (`servidor.rangos`);
  - (b) **esperando la red**: `servidor.detener()` hasta que la fila está en `waiting_network`; después, parada en 10 s o menos (assert en los dos sistemas; verifica que `raise_signal` despierta las esperas en Windows, R7 **[a verificar]**);
  - (c) **conexión estancada** (`pausar_tras_bytes`): en Linux, assert de 10 s o menos. En Windows, la variante `@pytest.mark.solo_windows` **solo mide**: guarda la duración con `record_property("parada_estancada_s", t)` y la escribe en la salida con `-rs`; solo falla si pasa de `ytdl.SOCKET_TIMEOUT + 10` s (que no se cuelgue) (SC-004, regla 4);
  - (d) reanudar con `downloader ejecutar` en lugar de la aplicación continúa desde el progreso guardado;
  - (e) un segundo `api/detener` durante `deteniendo` responde 202 sin efecto; `api/detener` sin procesador responde 409.

### Implementation for User Story 2

- [ ] T068 [US2] Crear `src/downloader/app/parada.py` con:
  - `pedir(directorio, ejecucion)`, que escribe `parada.json` con `escribir_atomico(reintentar=True)`;
  - `Vigilante(directorio, ejecucion, interrumpir)`, un hilo daemon que consulta cada 0,25 s;
  - `interrumpir_principal()`: en POSIX, `signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)`; en Windows, `signal.raise_signal(signal.SIGINT)`, cada uno protegido por una comprobación de plataforma (R7).
- [ ] T069 [US2] Arrancar el `Vigilante` en `src/downloader/app/procesador.py` antes de `ejecutar_cola` y detenerlo al terminar. Implementar `POST api/detener` en `src/downloader/app/api.py` (202, 409 sin procesador) y el estado `deteniendo` hasta que el proceso termina
- [ ] T070 [US2] Activar Pausar en `src/downloader/app/web/app.js` según `puede_detener`, con "Deteniendo…" mientras dura
- [ ] T071 [US2] **[manual, Windows]** Quickstart V3, incluido el caso con el servidor parado. Anotar en research R7 (nuevo apartado "Resultado de la medición") los tiempos de parada de T067 (a), (b) y (c) en los dos sistemas, tomados del CI y de V3. **No enmendar SC-004**: si (c) pasa de 10 s en Windows, comunicarlo al usuario con la cifra (regla 4)
- [ ] T072 [US2] Commit `002 Fase 9: Pausar equivalente a Ctrl+C (US2)`

**Aceptación**:
- T066–T067 en verde en Linux y en Windows ((c) en Windows solo mide);
- los tiempos anotados en research R7.

---

## Phase 10: User Story 3 — Un único procesador (Priority: P1)

**Goal**: la ventana observa un `ejecutar` de la CLI sin lanzar otro ni detenerlo, y nunca hay
dos procesadores.

**Independent Test**: `test_app_procesador_unico.py` y quickstart V4.

### Tests for User Story 3 (primero, en rojo)

- [ ] T073 [P] [US3] Escribir `tests/integration/test_app_procesador_unico.py` (`@pytest.mark.ffmpeg`):
  - con `downloader ejecutar` en marcha, `api/estado` da `origen: "cli"`, `puede_iniciar: false` y `puede_detener: false`, con el `motivo` exacto de contracts/api-local.md, y muestra la cola en directo;
  - `api/iniciar` y `api/detener` → 409 con su motivo (FR-008, FR-009, US3 escenario 5);
  - con la aplicación procesando, `downloader ejecutar` sale con 2 y no procesa nada;
  - cuando el `ejecutar` de la CLI termina, `api/estado` da `ninguno` en 2 s o menos (US3, escenario 4);
  - tras `Popen.kill()` del `ejecutar` de la CLI, también en 2 s o menos (Edge Cases);
  - **carrera**, 20 repeticiones: `downloader ejecutar` y `POST api/iniciar` a la vez. En ningún momento hay dos procesos con `ejecutar.lock` (comprobar que exactamente uno escribió `procesador.json` en cada ronda), y si el subproceso de la aplicación sale con 2, `ultima_ejecucion` no cambia y la ventana pasa a observar (R6, SC-003).

### Implementation for User Story 3

- [ ] T074 [US3] Completar `src/downloader/app/procesos.py` con el seguimiento del subproceso lanzado: estado `arrancando` hasta que aparece `procesador.json` con su ejecución o el proceso termina; si sale con 2, pasar a observar sin tocar el resultado. Completar `api/estado`, `api/iniciar` y `api/detener` con `puede_iniciar`, `puede_detener` y los motivos de contracts/api-local.md
- [ ] T075 [US3] Mostrar en `src/downloader/app/web/app.js` el origen ("En marcha desde la terminal") y los motivos de los botones desactivados
- [ ] T076 [US3] **[manual, Windows]** Quickstart V4
- [ ] T077 [US3] Commit `002 Fase 10: un único procesador y modo observación (US3)`

**Aceptación**: T073 en verde en Linux y en Windows; V4 hecho.

---

## Phase 11: User Story 4 — Segundo plano y reconexión (Priority: P2) (1/2)

**Goal**: el trabajador sobrevive al cierre de la ventana; al reabrir, se ve y se puede
detener; una muerte abrupta queda en el resultado y el registro.

**Independent Test**: `test_app_segundo_plano.py` y quickstart V5 (primera parte) y V6.

### Tests for User Story 4 (primero, en rojo)

- [ ] T078 [P] [US4] Crear `tests/support/ventana_headless.py`, un programa que arranca `principal.arrancar_servidor(VentanaFalsa())` y escribe en su salida estándar el puerto y el secreto (solo para tests). Escribir `tests/integration/test_app_segundo_plano.py` (`slow`, `@pytest.mark.ffmpeg`), en los dos sistemas:
  1. lanza `ventana_headless.py` como proceso y llama a `api/iniciar` (descarga de 50 MB a 2 MB/s);
  2. mata el proceso de la ventana;
  3. 5 s después, el trabajador sigue vivo y `bytes_descargados` ha crecido;
  4. un segundo `ventana_headless.py` muestra en 5 s o menos (SC-008) el procesador con `origen: "app"` y `puede_detener: true`;
  5. `api/detener` lo para con 130.
- [ ] T079 [P] [US4] **[a verificar R8]** Añadir a `tests/integration/test_app_segundo_plano.py` la variante `@pytest.mark.solo_windows`: lanza `tests/support/ventana_headless.py` por la misma cadena que el lanzador (`uv run --gui-script`, con el `.py` copiado como `.pyw` en un directorio temporal), inicia, termina el proceso `uv.exe` y su árbol de la ventana (sin el trabajador), y comprueba que el trabajador sigue vivo y avanzando 5 s después (objetos de trabajo de uv y del lanzador del entorno virtual)
- [ ] T080 [P] [US4] Escribir `tests/unit/test_app_lanzar.py`: con `subprocess.Popen` parcheado para lanzar `OSError(winerror=5)` cuando se pide `CREATE_BREAKAWAY_FROM_JOB`, `lanzar_trabajador` lanza `SinSegundoPlano` con el mensaje de R8, no reintenta sin el indicador y lo anota en el registro. `api/iniciar` responde 409 con ese motivo. Corre en los dos sistemas, porque simula los indicadores
- [ ] T081 [P] [US4] Escribir `tests/integration/test_app_resultado.py`:
  - tras `Popen.kill()` del trabajador de la aplicación, la siguiente consulta a `api/estado` escribe `ultima-ejecucion.json` con `error.tipo == "abrupto"` y `codigo: null`, y lo anota en `registro/ventana.log`;
  - con un resultado ya escrito de esa ejecución, no lo sobrescribe;
  - el resultado se sigue mostrando al reabrir, hasta el siguiente `api/iniciar` (FR-012).

### Implementation for User Story 4

- [ ] T082 [US4] En `src/downloader/app/procesos.py` y `api.py`: detectar la terminación abrupta del procesador propio (`EstadoProcesador.abrupto` con origen `app` y sin resultado de su ejecución), escribir el resultado `abrupto` con `resultado.escribir_abrupto` y registrarla. Al arrancar, `principal` reconecta con un procesador de origen `app` vivo (FR-011)
- [ ] T083 [US4] **[manual, Windows]** Quickstart V5 (seguir en segundo plano, reabrir y Pausar desde la segunda sesión) y V6 (terminar el trabajador desde el Administrador de tareas). Anotar en research R8 si el trabajador sobrevivió con `uv run` (resultado de T079 en el CI y de V5)
- [ ] T084 [US4] Commit `002 Fase 11: segundo plano, reconexión y terminación abrupta (US4)`

**Aceptación**:
- T078–T081 en verde (T079 en el CI de Windows);
- research R8 actualizado con el resultado verificado.

---

## Phase 12: User Story 4 — Cierre de la ventana e instancia única (Priority: P2) (2/2)

**Goal**: cerrar con un procesamiento propio en marcha pregunta (Seguir en segundo plano,
Pausar y cerrar, Cancelar); una segunda apertura no abre otra ventana.

**Independent Test**: `test_app_cierre.py`, `test_app_instancia.py` y quickstart V5 (resto) y V9.

### Tests for User Story 4 (primero, en rojo)

- [ ] T085 [P] [US4] Escribir `tests/unit/test_app_cierre.py` para `cierre.decidir(estado)`:
  - origen `app` en `en_marcha`, `arrancando` o `deteniendo` → `preguntar`;
  - origen `cli` → `cerrar`;
  - `ninguno` → `cerrar`;
  - el texto del diálogo da N = pendientes menos 1, con la variante para N = 0 de contracts/ventana.md.
- [ ] T086 [P] [US4] Escribir `tests/integration/test_app_cierre.py` con `app_local` y `VentanaFalsa`:
  - `api/cerrar` con `segundo_plano` → `destruir` llamado y el trabajador sigue vivo;
  - con `pausar` → `parada.json` escrito, y `destruir` llamado solo cuando el proceso ha terminado (130);
  - con el plazo de 30 s inyectado a 1 s y un trabajador que no para (vigilante parcheado), `api/estado` ofrece `cerrar_igualmente`;
  - con `cancelar` → nada;
  - con `pausar` y un procesador de la CLI → 409.
- [ ] T087 [P] [US4] Escribir `tests/integration/test_app_instancia.py`. Con un primer proceso que tiene `ventana.lock` (`ventana_headless.py`), un segundo `python -m downloader.app` con la ventana parcheada termina con 0 en 3 s o menos, y deja `activar.solicitud`. El primero lo borra y llama a `VentanaFalsa.enfocar` en 2 s o menos. Si el primero muere, el bloqueo se libera y una nueva apertura arranca normalmente (FR-018, R12)
- [ ] T088 [P] [US4] Añadir a `tests/unit/test_app_ventana_windows.py` (`@pytest.mark.solo_windows`), con objetos simulados de pywebview: si `cierre.decidir` devuelve `preguntar`, el manejador de `events.closing` devuelve `False` y llama a `evaluate_js("mostrarDialogoCierre(...)")`; con `cerrar`, devuelve `True` sin llamar a nada

### Implementation for User Story 4

- [ ] T089 [US4] Crear `src/downloader/app/cierre.py` (`decidir`, el texto del diálogo y el flujo de Pausar y cerrar con el plazo de 30 s) e implementar `POST api/cerrar` en `src/downloader/app/api.py`
- [ ] T090 [US4] Crear `src/downloader/app/instancia.py` (`ventana.lock` con `downloader.bloqueo` y `activar.solicitud`). Integrarlo en `principal.main` y en el ciclo de un segundo del servidor, que llama a `ventana.enfocar()`
- [ ] T091 [US4] En `src/downloader/app/ventana/ventana.py`, enganchar `window.events.closing` a `cierre.decidir`. Añadir en `src/downloader/app/web/` el diálogo modal de la segunda página de la maqueta (título, texto con N, tres botones, "Deteniendo…" y "Cerrar igualmente")
- [ ] T092 [US4] **[manual, Windows] [a verificar R12]** Quickstart V5 (Pausar y cerrar, Cancelar) y V9. Anotar en research R12 si la ventana pasa al frente o solo parpadea en la barra de tareas
- [ ] T093 [US4] Commit `002 Fase 12: diálogo de cierre e instancia única (US4)`

**Aceptación**:
- T085–T088 en verde (T088 en el CI de Windows);
- V5 completo y V9 hechos y anotados.

---

## Phase 13: User Story 5 — Cookies del navegador y reintento con sesión (Priority: P3)

**Goal**: opción explícita de cookies, desactivada por defecto, con la advertencia de cada
navegador, y "Reintentar con cookies" en las descargas que requieren sesión.

**Independent Test**: `test_app_cookies.py` y quickstart V8.

### Tests for User Story 5 (primero, en rojo)

- [ ] T094 [P] [US5] Escribir `tests/integration/test_app_cookies.py` (`@pytest.mark.ffmpeg`), con `servidor.exigir_sesion` y `tests/support/navegador_falso.py` de 001:
  - `api/opciones` lista los navegadores de `models.NAVEGADORES` con los avisos exactos de contracts/api-local.md;
  - agregar con `navegador: "firefox"` guarda `navegador_cookies = "firefox"` en la fila;
  - una descarga que falla con 403 por sesión tiene `reintentar_con_cookies: true`, y una 404 tiene `false`;
  - `api/reintentar` con `navegador` → 200 `reencolada`, mismo id, y la descarga se completa con la cookie ficticia del navegador falso;
  - `api/reintentar` sobre una descarga no fallida → 409, y con un navegador desconocido → 400;
  - tras todo, el valor de la cookie ficticia no aparece en ningún archivo de `app_home` (incluidos `registro/*`, `*.json` y `cola.db`) ni en ninguna respuesta de la API (FR-015, Principio VI);
  - ningún archivo guarda si la opción estaba activada.

### Implementation for User Story 5

- [ ] T095 [US5] Implementar `POST api/reintentar` en `src/downloader/app/api.py` con `operaciones.validar_navegador`, `Store.agregar` y `mensajes.requiere_sesion` (R17). Completar `reintentar_con_cookies` en `src/downloader/app/vista.py`
- [ ] T096 [US5] En `src/downloader/app/web/`: casilla "Usar las cookies de mi navegador" desmarcada al cargar, desplegable de navegador solo activo si está marcada y el aviso del navegador elegido. Botón "Reintentar con cookies" en las filas, con la elección de navegador, su aviso y la confirmación (contracts/ventana.md)
- [ ] T097 [US5] **[manual, Windows]** Quickstart V8
- [ ] T098 [US5] Commit `002 Fase 13: cookies del navegador y reintento con sesión (US5)`

**Aceptación**: T094 en verde en Linux y en Windows; V8 hecho.

---

## Phase 14: Polish & Cross-Cutting Concerns

- [ ] T099 **[manual, Windows, equipo de 4 GB] PUERTA FINAL SC-005**: `uv run python scripts/medir_memoria.py --ventana app` con 500 descargas en cola y la aplicación real (quickstart C). Anotar el resultado en research R14. Si pasa de 300 MB, **parar y consultar al usuario** antes de dar la feature por terminada
- [ ] T100 [P] Añadir al `README.md` la sección "Aplicación de escritorio (Windows)":
  - requisitos (WebView2, cómo instalarlo si falta);
  - el lanzador `Gestor de descargas.vbs` y su respaldo `gestor-descargas.exe`;
  - qué hace Pausar;
  - el segundo plano y la reconexión;
  - el registro de errores en `%APPDATA%\downloader\registro\`;
  - en "Límites conocidos": el tiempo de parada medido en Windows con una conexión estancada (R7) y la retirada de VBScript (R11).
- [ ] T101 [P] Ejecutar quickstart.md completo en Windows (A, B, D) y en Linux (A, D), y anotar en su apartado "Resultados" la fecha, los sistemas y cualquier desviación
- [ ] T102 Comprobar SC-009 sobre toda la feature: `git diff --name-only --diff-filter=M <base de la feature> -- tests/unit tests/integration` solo debe listar `tests/unit/test_plataforma.py` (T004). El CI en verde en `ubuntu-latest` y `windows-latest`, con los tests `solo_windows` ejecutados en Windows y omitidos con su motivo en Linux
- [ ] T103 Revisar que todos los puntos "[a verificar]" de research.md tienen su resultado anotado, según la tabla de este documento, y marcar cada uno como **[verificado]** o como refutado, con la decisión que se tomó
- [ ] T104 Commit `002 Fase 14: medición final, README y validación`

**Aceptación**: puerta final de memoria superada, quickstart con resultados, CI en verde en los
dos sistemas y research sin puntos pendientes.

---

## Puntos "[a verificar]" de research.md → tareas (regla 4)

| Punto | Qué se verifica | Tarea |
|-------|-----------------|-------|
| R2 | mensaje claro y código 3 si falta WebView2 | T055 (test), T064 (manual V7) |
| R7 | `raise_signal` despierta las esperas en Windows | T067 (b) |
| R7 / SC-004 | tiempo de parada en Windows con conexión estancada (solo medir) | T067 (c), T071 |
| R8 | el trabajador sobrevive al cierre de la cadena `uv run` (objetos de trabajo) | T079 (test), T083 (manual V5) |
| R12 | traer la ventana al frente en Windows | T092 (manual V9) |
| R14 | presupuesto de memoria | T010–T011 (puerta), T099 (puerta final) |
| R14 | precedencia de `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` | T010 |
| R15 | coste de cargar yt-dlp en la ventana | T010 (`--importar-store`), T013 |
| R17 | "requiere iniciar sesión" sin 403 | T015 |
| R11 | doble clic sin consola | T056 (test), T062 (manual V1) |

---

## Dependencies & Execution Order

### Orden de fases

- **Fase 1** (preparación) → **Fase 2** (puerta de memoria): bloquea todo lo demás. Si la
  puerta no se supera, se para.
- **Fases 3 → 4 → 5 → 6** (base), en ese orden: cada una usa lo anterior.
  - Fase 4 usa los módulos ligeros de la Fase 3.
  - Fase 5 modifica `operaciones.ejecutar_cola` de la Fase 4.
  - Fase 6 usa `archivos_json` de la Fase 5.
- **Fase 7** (US1, sin ventana) tras la 6. **Fase 8** (US1, ventana) tras la 7.
- **Fase 9** (US2) tras la 7: necesita `procesador.py`. Puede hacerse en paralelo con la 8;
  su paso manual V3 necesita la 8.
- **Fase 10** (US3) tras la 7. Es independiente de la 9.
- **Fase 11** (US4, segundo plano) tras la 9, porque usa `api/detener`.
- **Fase 12** (US4, cierre) tras la 11 y la 8, porque usa el evento de pywebview.
- **Fase 13** (US5) tras la 7; su interfaz, tras la 8.
- **Fase 14** al final.

### Dentro de cada fase

Tests en rojo → implementación → tareas manuales → commit. Las tareas [P] de tests de una
misma fase se escriben en paralelo.

## Parallel Example: Fase 6

```text
T031 tests/support/cliente_app.py + fixture app_local
T032 tests/integration/test_app_seguridad.py
T033 tests/unit/test_app_registro.py
T034 tests/unit/test_app_resultado.py
T035 tests/unit/test_app_procesos_estado.py
# y después, en paralelo: T038 resultado.py, T039 procesos.py
```

## Parallel Example: User Story 1 (Fase 7)

```text
T043 test_app_urls.py   T044 test_app_vista.py   T045 test_app_agregar.py
T046 test_app_iniciar.py   T047 test_app_procesador.py   T048 test_app_vivo.py
```

## Implementation Strategy

### MVP

Fases 1 a 8: puerta de memoria, base y US1 completa. Con eso, el usuario abre la aplicación
con doble clic, agrega URLs y descarga viendo el progreso. Pausar llega en la Fase 9.
Conviene no publicar el MVP sin la Fase 9, porque hasta entonces la única forma de parar es
cerrar el proceso, aunque el progreso esté protegido por la recuperación de 001.

### Entrega incremental

Cada fase termina con la suite en verde y un commit:
1. Fases 1–2: dependencias y puerta de memoria.
2. Fases 3–6: la base no cambia nada visible de la CLI (SC-009 en cada una).
3. Fases 7–8: US1, el MVP.
4. Fase 9 (US2), Fase 10 (US3), Fases 11–12 (US4), Fase 13 (US5).
5. Fase 14: puerta final de memoria y documentación.

## Notes

- Las tareas manuales están marcadas **[manual, Windows]**. Sus resultados se anotan en
  research.md o en el apartado "Resultados" de quickstart.md, nunca solo en la conversación.
- Ninguna tarea enmienda la spec. Si una medición contradice un criterio (SC-004, SC-005), se
  comunica al usuario con la cifra y se espera su decisión.
- Los archivos de test de la feature 001 no se modifican, salvo `test_plataforma.py` (T004).
