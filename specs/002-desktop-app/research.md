# Research: Aplicación de escritorio

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Fecha**: 2026-10-28

Cada decisión sigue el formato Decisión / Motivo / Alternativas. Las comprobaciones marcadas
**[verificado]** se hicieron sobre el código fuente de la versión citada; las marcadas
**[a verificar]** son hipótesis que una tarea concreta MUST confirmar en el CI de Windows o a
mano (quickstart.md) antes de dar la feature por terminada.

---

## R1. Ventana: pywebview sobre WebView2

**Decisión**: pywebview 6.2.1 con el motor `edgechromium` (WebView2), a través de su backend
WinForms y pythonnet. La ventana carga la interfaz web propia que sirve el servidor local
(R3). Dependencia declarada solo para Windows:
`pywebview>=6.2.1; sys_platform == 'win32'`.

**Motivo**: la entrada del usuario lo fija. Permite reproducir la maqueta (HTML y CSS) dentro
de una ventana nativa con barra de título propia, sin navegador externo (FR-001), y ofrece lo
que la feature necesita de la ventana: el evento `closing`, que se puede cancelar
(`should_cancel = self.closing.set()`, `platforms/winforms.py`) **[verificado, 6.2.1]**, el
diálogo nativo de carpeta (`FileDialog.FOLDER`) y la ejecución de JavaScript desde Python
(`evaluate_js`).

**Dependencias que añade en Windows** (constitución, Restricciones: revisar la licencia):

| Paquete | Versión | Licencia | Para qué |
|---------|---------|----------|----------|
| pywebview | 6.2.1 | BSD-3-Clause | ventana y WebView2 |
| pythonnet | 3.2.1 | MIT | WinForms desde Python |
| clr_loader | 0.3.1 | MIT | carga de .NET para pythonnet |
| cffi | ≥1.17 | MIT | dependencia de clr_loader |
| proxy_tools | 0.1.0 | MIT | dependencia de pywebview |
| bottle | 0.13.4 | MIT | dependencia de pywebview; la feature no usa su servidor (R3) |
| typing_extensions | — | PSF | dependencia de pywebview |

Ninguna es GPL. En Linux no se instala ninguna (marcador `sys_platform`).

**Alternativas**:
- *tkinter*: sin dependencias y con mucha menos memoria (unos 20 MB), pero reproducir la
  maqueta sería costoso y el usuario eligió una interfaz web. Queda como plan B si la medición
  de R14 no cabe en 300 MB con ninguna de las mitigaciones.
- *Electron* o *Tauri*: más memoria (Electron) o una cadena de compilación en Rust (Tauri).
  Rechazadas por el Principio I y por SC-005.
- *Edge en modo aplicación (`msedge --app=`)*: es un navegador externo, prohibido por FR-001.

## R2. Runtime de WebView2 ausente

**Hallazgo [verificado, pywebview 6.2.1, `platforms/winforms.py`]**: pywebview decide el motor
al importar su backend WinForms. `_is_chromium()` lee la versión de WebView2 en el registro
(`SOFTWARE\[WOW6432Node\]Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`,
valor `pv`, en `HKCU` y `HKLM`, más las claves de los canales beta, dev y canary) y exige
.NET Framework 4.6.2 o posterior. **Si no encuentra WebView2, no falla: cae en silencio al
motor MSHTML (Internet Explorer 11)** con un `logger.warning` que, en una aplicación sin
consola, nadie ve. Pasar `gui='edgechromium'` no lo impide: la variable `is_chromium` depende
solo del registro. Con MSHTML, la interfaz (JavaScript moderno, `fetch`) no funcionaría: el
usuario vería una ventana rota o vacía. Es justo el "cierre silencioso" que hay que evitar.

**Decisión**: antes de crear la ventana, `ventana/webview2.py` importa
`webview.platforms.winforms` y comprueba que `renderer == "edgechromium"`. Si no lo es, o si la
importación falla (pythonnet sin .NET, por ejemplo):
1. muestra un cuadro de mensaje nativo de Windows (`MessageBoxW` con `ctypes`, que no necesita
   la ventana web) con este texto: "Falta Microsoft Edge WebView2 Runtime, que la ventana
   necesita para mostrarse. Instálelo con `winget install Microsoft.EdgeWebView2Runtime` o
   desde https://developer.microsoft.com/microsoft-edge/webview2/ y vuelva a abrir la
   aplicación. La línea de comandos (`uv run downloader`) sigue funcionando.";
2. escribe el error en el registro (R10);
3. termina con el código 3, sin abrir ninguna ventana ni tocar la cola.

Cualquier otra excepción al crear o arrancar la ventana sigue el mismo camino, con su mensaje.

**Dependencia de internos**: el atributo `renderer` del módulo es interno de pywebview. Un test
marcado `pywebview_internals` (solo Windows) comprueba que existe y que vale `edgechromium` o
`mshtml`. Si una versión nueva lo cambia, el test falla antes de que el usuario vea una ventana
rota. Es el mismo criterio que `ytdlp_internals` en la feature 001.

**Contexto**: WebView2 viene con Windows 11 y Microsoft lo instaló por Windows Update en los
Windows 10 actualizados (desde 2021). Puede faltar en un Windows 10 sin actualizar o en uno
donde se desinstaló.

**Alternativas**: *leer nosotros el registro* duplicaría la lógica de pywebview y podría
discrepar de lo que pywebview acabe usando. *Dejar el respaldo MSHTML* daría una interfaz rota.

## R3. Servidor local: biblioteca estándar

**Decisión**: `http.server.ThreadingHTTPServer` de la biblioteca estándar, con
`daemon_threads = True`, en `("127.0.0.1", 0)`: el sistema elige un puerto libre y el servidor
nunca escucha en `0.0.0.0` ni en `::`. Sirve los archivos estáticos de la interfaz
(`app/web/`) y una API JSON pequeña ([contracts/api-local.md](contracts/api-local.md)). La
página consulta el estado cada segundo (`fetch`, con `ETag`) y no usa conexiones
persistentes.

Dos detalles obligatorios:
- `log_message` se anula. Con `pythonw` no existe `sys.stderr` (vale `None`) y el registro por
  defecto haría fallar cada petición. Además, la línea de petición contiene el secreto de la
  sesión (R4), que MUST NOT llegar a ningún registro.
- Las rutas estáticas se resuelven contra una lista fija de archivos (`index.html`, `app.js`,
  `app.css`), nunca contra la ruta pedida, para que no haya recorrido de directorios.

**Motivo (límite de 300 MB, SC-005)**: el servidor no añade ninguna dependencia ni proceso.
Cuesta lo que pesan los módulos `http.server`, `socketserver` y `json`, unos pocos MB dentro
del proceso de la ventana, y un hilo por petición en curso. Con una consulta por segundo,
nunca hay más de dos o tres hilos. Para medio millar de filas, la carga del servidor es
despreciable frente a WebView2 (R14).

**Alternativas**:
- *bottle* (pywebview ya lo instala en Windows): no existe en Linux, donde también hay que
  probar el servidor (constitución v3.1.0), y no aporta nada que la API necesite.
- *aiohttp, Starlette con uvicorn, Flask*: de 10 a 20 MB más y varias dependencias nuevas,
  rechazadas por el Principio I.
- *Puente `js_api` de pywebview, sin servidor*: no abre ningún puerto, así que la superficie
  de ataque sería menor. Se rechaza porque la lógica quedaría detrás de pywebview, que no
  existe en Linux: la constitución v3.1.0 exige que todo salvo la ventana y el lanzador
  funcione y tenga tests en Linux. Con el servidor, los tests ejercitan la API con un cliente
  HTTP corriente en los dos sistemas. La superficie extra queda cubierta por R4.
- *Server-Sent Events o WebSocket*: actualizan más rápido, pero mantienen hilos y conexiones
  abiertas. Consultar cada segundo cumple SC-006 (2 s) y es más simple.

## R4. Solo la ventana puede usar la interfaz (FR-013, SC-007)

**Modelo de amenazas**:
- *Otro equipo de la red*: no puede conectar, porque el servidor solo escucha en `127.0.0.1`.
- *Una página web en cualquier navegador del equipo*: puede enviar peticiones a
  `127.0.0.1:<puerto>` y adivinar el puerto. También puede usar *DNS rebinding* (un dominio
  propio que resuelve a `127.0.0.1`) para que el navegador considere la petición de su mismo
  origen.
- *Otro usuario del mismo equipo*: puede conectar a `127.0.0.1`.
- *Otro programa del mismo usuario*: lo rechaza el secreto, que nunca se guarda en disco. Un
  programa del mismo usuario con intención maliciosa podría leer la memoria del proceso o usar
  directamente la CLI y la cola: ese caso queda fuera del modelo, porque ningún mecanismo local
  lo impide.

**Decisión**: cuatro controles, todos obligatorios y comprobados en este orden:
1. **Host**: la cabecera `Host` MUST ser exactamente `127.0.0.1:<puerto>`. Si no, 403. Esto
   bloquea el DNS rebinding.
2. **Secreto de sesión**: `secrets.token_urlsafe(32)` (256 bits), generado al arrancar la
   ventana y guardado solo en memoria. Toda ruta lleva el secreto como primer segmento
   (`/<secreto>/index.html`, `/<secreto>/api/estado`). La ventana se abre en
   `http://127.0.0.1:<puerto>/<secreto>/`, y el JavaScript de la página lo toma de
   `location.pathname`. La comparación usa `hmac.compare_digest`. Con un secreto ausente o
   incorrecto, la respuesta es 403, sin cuerpo y sin revelar si la ruta existe.
3. **Origin**: si la petición trae `Origin`, MUST ser `http://127.0.0.1:<puerto>`. Las
   peticiones que cambian algo (`POST`) MUST traerlo: WebView2 lo envía en todo `fetch` con
   `POST`. Si no, 403.
4. **Cabecera propia en la API**: las rutas `api/*` exigen `X-Gestor: 1`. Una página de otro
   origen solo puede enviarla tras una petición previa `OPTIONS` (*preflight*) de CORS. El
   servidor responde 403 a todo `OPTIONS` y nunca envía cabeceras
   `Access-Control-Allow-*`, así que el navegador bloquea la petición.

Además, todas las respuestas llevan `Cache-Control: no-store` y
`X-Content-Type-Options: nosniff`. La página lleva también
`Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`. La página no contiene
enlaces externos, así que el secreto no puede salir en un `Referer`. WebView2 se arranca con
`private_mode=True` (es el valor por defecto de pywebview), que no conserva cookies ni
almacenamiento entre sesiones.

**Tests de rechazo** (SC-007, obligatorios en Linux y en Windows,
`tests/integration/test_app_seguridad.py`): sin secreto; con un secreto incorrecto; con el
secreto correcto y otro `Host` (`localhost:<p>`, `evil.example:<p>`); con otro `Origin`; un
`POST` sin `Origin`; una ruta `api/*` sin `X-Gestor`; un `OPTIONS` (403 y sin cabeceras CORS);
un intento de conexión a la IP no local del equipo (rechazado); un recorrido de directorios
(`/<secreto>/../pyproject.toml`). Cada caso comprueba además que la cola no cambió y que la
respuesta no contiene datos de la cola.

**Alternativas**: *una cookie de sesión con `SameSite=Strict`*: también funciona, pero
añade estado en WebView2 y depende de que el navegador respete `SameSite`. El secreto en la
ruta no depende del navegador. *Solo comprobar el Origin*: no protege frente a programas que no
son navegadores, porque pueden falsificarlo.

## R5. El trabajador: el mismo código que `ejecutar`, en un subproceso

**Decisión**: el cuerpo de `cli.ejecutar` pasa a una función compartida,
`operaciones.ejecutar_cola(origen, ejecucion=None, ...)`, que toma el bloqueo
`ejecutar.lock`, recupera, procesa la cola con `worker.ejecutar` y devuelve el
`worker.Resultado`. El comando `ejecutar` de la CLI la llama con `origen="cli"` y conserva
sus mensajes y códigos de salida (FR-017; los tests de la feature 001 no cambian, SC-009).
La aplicación lanza un subproceso con `python -m downloader.app.procesador --ejecucion <id>`.
Ese módulo llama a la misma función con `origen="app"`, y además:
- arranca el vigilante de parada (R7);
- escribe el resultado de la ejecución (R10), también si termina por un error fatal, por una
  parada o por una excepción inesperada;
- envía sus errores al registro (R10).

Detalles del subproceso:
- **Intérprete**: el `python.exe` del mismo entorno virtual, no `pythonw.exe` (si
  `sys.executable` termina en `pythonw.exe`, se usa el `python.exe` de su carpeta). Así
  `sys.stdout` y `sys.stderr` existen como en la CLI. En Linux, `sys.executable`.
- **Salidas**: `stdout` y `stderr` a `DEVNULL`. La aplicación no las lee: el estado llega por
  `cola.db` y `progreso-vivo.json` (R9). Los errores van al registro con `logging`. Si el
  intérprete ni siquiera arranca, la aplicación lo detecta porque el proceso termina sin dejar
  resultado, y lo anota ella en el registro con el código de salida.
- **Sin consola visible y sobreviviendo a la ventana**: ver R8.

**Motivo**: es literalmente el mismo código (FR-007), y el resultado y la parada se resuelven
sin añadir opciones ocultas a la CLI.

**Alternativas**: *lanzar `downloader ejecutar` tal cual*: para obtener el resultado habría que
analizar su salida de texto, y para la parada y el origen haría falta una opción o una variable
de entorno ocultas en la CLI. Rechazada por FR-017. *Ejecutar el trabajador en un hilo del
proceso de la ventana*: no sobreviviría al cierre de la ventana (US4) y metería yt-dlp en el
proceso de la ventana (R15).

## R6. Detectar si hay un procesador en marcha y de quién es

**Decisión**: `ejecutar.lock` sigue siendo la única garantía de exclusión. La aplicación
**nunca** lo toma, ni siquiera para comprobarlo: un intento de prueba en el momento en que
arranca un `ejecutar` de la CLI haría que este saliera con el código 2 sin motivo.

Para saber quién procesa, `ejecutar_cola` escribe, justo después de tomar el bloqueo,
`procesador.json` con `pid`, `creado` (instante de creación del proceso), `origen` (`cli` o
`app`), `ejecucion` (solo con origen `app`) e `inicio`. Lo borra al terminar de forma
ordenada. La aplicación da un procesador por vivo si el archivo existe, el proceso `pid`
existe y su instante de creación coincide con `creado`. Lo comprueba con `psutil`, que es
multiplataforma y evita confundirlo con otro proceso que haya reutilizado el `pid`. Si el
archivo existe pero el proceso no, el procesador terminó de forma abrupta (Edge Cases).

Estados posibles: *ninguno*, *de la CLI* (solo observación; Detener se rechaza con el motivo,
FR-009) y *de la aplicación* (se puede detener, también desde una sesión posterior, FR-011).

**Carrera aceptada**: entre que un `ejecutar` toma el bloqueo y escribe `procesador.json` hay
unos milisegundos. Si en ese intervalo el usuario pulsa Iniciar, el subproceso de la
aplicación no consigue el bloqueo y sale con el código 2. La aplicación lo muestra
("ya hay un procesamiento en marcha") y pasa a observar. Nunca hay dos procesadores
(SC-003), porque quien decide es el bloqueo.

**Dependencia nueva**: `psutil` (BSD-3-Clause, sin dependencias, unos 2 MB en memoria) en los
dos sistemas. Sirve también para medir la memoria (R14).

**Verificación (Fase 10, `test_app_procesador_unico.py`)**:
- la carrera aceptada, sin azar: con el bloqueo tomado por un procesador que aún no se ha
  anunciado, Iniciar lanza el trabajador (202), que sale con 2 sin resultado ni anuncio, y la
  ventana no se queda en `arrancando`;
- la carrera, con azar: 20 rondas con un `ejecutar` de la CLI y `POST api/iniciar` casi a la
  vez. Los dos no tardan lo mismo en llegar al bloqueo (en Linux, la CLI se anuncia en unos
  0,25 s y el trabajador en unos 0,07 s), así que el test mide esos tiempos y desfasa los
  arranques para barrer la ventana de la carrera. Cada ronda acaba con un solo procesador:
  el perdedor sale con 2, solo el ganador escribe `procesador.json` y nadie más puede tomar
  `ejecutar.lock`. Los tres desenlaces (gana la aplicación; gana la CLI con el trabajador ya
  lanzado; la ventana ya veía a la CLI y responde 409) salen en la misma ejecución; el test
  escribe el reparto en su salida y no exige ninguno en concreto;
- un `ejecutar` de la CLI que termina, o al que se mata, deja de verse en marcha en la
  siguiente consulta de `api/estado` (el límite son 2 s).

Reparto de la carrera en el CI 106 (commit `8c4a1e0`), 20 rondas por sistema:

| Sistema | CLI / trabajador hasta anunciarse | Gana la aplicación | Gana la CLI (trabajador lanzado, sale con 2) | La ventana ya veía la CLI (409) |
|---------|-----------------------------------|-------------------|-----------------------------------------------|---------------------------------|
| Linux | 0,24 s / 0,07 s | 6 | 10 | 4 |
| Windows | 0,30 s / 0,09 s | 0 | 10 | 10 |

En Windows, el trabajador que lanza la ventana tarda más en arrancar que en la calibración
(que lo arranca directamente, sin los indicadores de R8), así que no ganó ninguna ronda.
El caso "la aplicación tiene el bloqueo y `ejecutar` sale con 2" lo cubre en los dos sistemas
`test_con_la_aplicacion_procesando_ejecutar_sale_con_2`; en ninguna ronda hubo dos
procesadores.

**Filas a medias sin procesador**: tras un cierre abrupto, `cola.db` conserva la fila en
`downloading` o `waiting_network` hasta que el siguiente procesador la recupera. La ventana la
muestra entonces como "En cola" con su progreso (`vista`), para no mostrar nada en marcha
cuando nadie procesa (spec, Edge Cases).

**Alternativas**: *escribir el `pid` en `ejecutar.lock`*: en Windows, `msvcrt.locking` impide
que otro proceso lea el byte bloqueado, y mezclaría datos con el mecanismo de exclusión.
*Comprobar procesos por nombre*: no distingue el origen ni la cola.

## R7. Detener: el equivalente exacto de Ctrl+C

**Decisión**: la aplicación pide la parada escribiendo `parada.json` con el identificador de
la ejecución. En el subproceso, un hilo vigilante (solo con origen `app`) lo consulta cada
0,25 s. Si el identificador coincide, provoca un `KeyboardInterrupt` en el hilo principal por
la misma vía que una pulsación de Ctrl+C:
- **POSIX**: `signal.pthread_kill(main_thread_id, SIGINT)`. La señal llega al hilo principal,
  interrumpe la llamada al sistema en curso (`EINTR`) y Python lanza `KeyboardInterrupt` como
  con Ctrl+C.
- **Windows**: `signal.raise_signal(SIGINT)` desde el hilo vigilante. Llama a la función C
  `raise()`, que ejecuta el manejador que Python instaló para `SIGINT`: el mismo que usa la
  consola al pulsar Ctrl+C. Ese manejador marca la señal y activa el evento que despierta
  `time.sleep` **[verificado en Windows, CI 104]**: ver "Resultado de la medición".

A partir de ahí, el camino es el de la feature 001: `worker._interrumpida` guarda el progreso,
`ytdl` entrega lo recibido del bloque en curso (R18 de 001) y la descarga vuelve a la cola. El
subproceso escribe el resultado con tipo `detenida` y termina con 130. Una parada pedida para
otra ejecución (un `parada.json` antiguo) se ignora y se borra.

Un `ejecutar` de la CLI no tiene vigilante: la aplicación no puede detenerlo (FR-009).

**Riesgo frente a SC-004 (10 s)**: en Windows, una señal no interrumpe una lectura de socket
bloqueada. Python solo comprueba las señales cuando vuelve la lectura, y yt-dlp lee con un
tiempo de espera de 15 s (`ytdl.SOCKET_TIMEOUT`). Con datos llegando, la parada es casi
inmediata. Con una conexión estancada y sin datos, puede tardar hasta 15 s. Le pasa igual a
Ctrl+C en la CLI de Windows. El test `test_app_parada.py` mide los dos casos en los dos
sistemas. Si en Windows se confirman más de 10 s, hay dos salidas: enmendar SC-004 con esa
excepción o bajar el tiempo de espera de lectura, lo que afecta al vigilante de estancamiento
de 001 (R21). Lo decidirá el usuario.

**Alternativas**:
- *`GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)`*: es el Ctrl+Break real, pero quien lo envía
  tiene que compartir consola con el destinatario. Al reabrir la ventana (un proceso
  `pythonw` sin consola), tendría que engancharse a la consola oculta del trabajador con
  `AttachConsole`, que es frágil, y no existe en Linux.
- *`_thread.interrupt_main()`*: marca la interrupción, pero en Windows no activa el evento que
  despierta `time.sleep`.
- *`os.kill(pid, SIGINT)` desde fuera*: en Windows, `os.kill` con un valor que no es
  `CTRL_C_EVENT` termina el proceso como `TerminateProcess`, sin cierre ordenado.

### Implementación (Fase 9)

- `app/parada.py`. El vigilante consulta cada 0,25 s, la primera vez tras un intervalo, y
  **interrumpe una sola vez**: después deja de vigilar, porque una segunda interrupción a media
  limpieza cortaría el guardado del progreso.
- El procesador **reinstala el manejador de `SIGINT`** al arrancar (`asegurar_interrupcion`).
  Python solo lo instala si la señal no llega ignorada, y un proceso la hereda ignorada si
  quien lo lanza la ignora (un trabajo en segundo plano de una shell no interactiva). Sin eso,
  la interrupción no haría nada. Probado con la señal ignorada antes de arrancar.
- Una parada que se entrega cuando `ejecutar_cola` ya ha vuelto también termina en `detenida`
  con 130, no en una excepción sin resultado.
- `deteniendo` lo recuerda la sesión de la ventana que pidió la parada. Del trabajador que
  lanzó esa sesión se sigue además el proceso: `deteniendo` dura hasta que el proceso termina,
  no hasta que retira `procesador.json` (contracts/api-local.md).

### Resultado de la medición (T075; CI 104, commit `46b32f1`, 2026-11-17)

Segundos desde `POST api/detener` hasta que el proceso del trabajador termina con 130, con una
descarga de 50 MB a 2 MB/s. Una medición por sistema; SC-004 pide 10 s como máximo.

| Caso | Linux (`ubuntu-latest`) | Windows (`windows-latest`) |
|------|-------------------------|----------------------------|
| (a) con datos llegando, al 30 % | 0,15 s | 0,24 s |
| (b) esperando la red (servidor parado, `waiting_network`) | 0,15 s | 0,31 s |
| (c) conexión estancada (abierta y sin datos) | 0,13 s | **13,09 s** |

- **(b) verifica el punto pendiente**: en Windows, `raise_signal` desde el hilo vigilante
  interrumpe las esperas del hilo principal. Lo confirman además cuatro tests de unidad en un
  proceso lanzado como el trabajador (sin consola y en su propio grupo): despierta un
  `time.sleep(30)` y un bucle de `Event.wait(0,25)`, también con `SIGINT` heredada ignorada.
- **(c) supera SC-004 en Windows: 13,09 s frente a 10 s.** Es lo previsto arriba: la señal no
  interrumpe la lectura de socket bloqueada, que vuelve con su tiempo de espera de 15 s
  (`ytdl.SOCKET_TIMEOUT`). **Decisión del usuario (2026-11-17): opción 1**. SC-004 se enmienda:
  en Windows con la conexión colgada se admiten hasta 20 s, y el test usa ese límite. El tiempo
  de espera de lectura y el código de la 001 no cambian; la opción 3 queda como D4 (R21).
  En los tres casos y en los dos sistemas, todo lo recibido antes de
  la parada queda en el `.part` (en (c), los 3 000 000 bytes exactos) y la reanudación pide
  `Range: bytes=N-` desde ahí.
- En local (Linux, 8 ejecuciones): (a) 0,13–0,36 s, (b) 0,18–0,36 s, (c) 0,14–0,34 s.
- Repetición en el CI 106 (Fase 10): Linux 0,28 / 0,15 / 0,11 s; Windows 0,19 / 0,16 /
  **13,00 s**. El caso (c) en Windows se mantiene por encima de 10 s.
- Falta la medición manual de V3 en el equipo del usuario (T079).

**Parada durante la unión o la verificación** (`test_app_parada_postproceso.py`; medido en el
CI 109, commit `0aa0aa3`, con pistas de prueba repetidas hasta 310 MB):

| | Linux | Windows |
|---|---|---|
| Unión sin interrumpir (ffmpeg `-c copy`) | 1,7 s (183 MB/s, en local) | 1,33 s (233 MB/s) |
| Verificación sin interrumpir (ffprobe `-count_packets`) | 0,15 s (2 GB/s, en local) | 0,19 s (1,7 GB/s) |
| Pausar durante la unión | 0,64–0,94 s; ffmpeg muere a los 0,3–0,5 s | 2,01 s; ffmpeg acaba solo a los 1,06 s |
| Pausar durante la verificación | 0,30–0,39 s | 0,30 s |

- Se confirma lo previsto: en Windows la parada **espera a que el programa termine** (ffmpeg
  acabó por sí mismo y la parada llegó después); en Linux lo interrumpe. En los dos, reanudar
  no vuelve a pedir las pistas: no se pierde progreso.
- Con estos archivos no pasa de 20 s. Pero en Windows la espera crece con el tamaño del
  archivo: a 233 MB/s (el disco del CI), 20 s son unos **4,6 GB**; en un disco duro mecánico,
  donde la unión copia a unos 50–100 MB/s, serían unos **1–2 GB**. No medido en el equipo de
  referencia. La verificación es unas 7 veces más rápida y no llega a 20 s con tamaños
  normales.
- **Qué está terminando** (decisión del usuario, 2026-11-17): mientras se detiene, la ventana
  dice "Terminando la unión del vídeo…" o "Terminando la verificación del archivo…" en lugar
  de solo "Deteniendo…". Lo deduce de los procesos hijos del trabajador (`procesos.terminando`,
  con `psutil`), sin tocar el trabajador ni la 001. Cortar ffmpeg sigue siendo D4.
- La verificación de estas pistas dura menos que la consulta del vigilante (0,25 s): a veces
  la parada llega cuando el procesamiento ya ha terminado (código 0), que es lo que pide la
  spec en ese caso.

**Límite conocido en Windows (motivo)**: una parada pedida mientras `ffmpeg` une los
formatos, o mientras `ffprobe` verifica el archivo, espera a que ese programa termine.
`raise_signal` no llega a los procesos hijos, y Python no puede interrumpir en Windows la
espera de un subproceso (`Popen.communicate`). Con el Ctrl+C de una consola no pasa, porque la
consola lo envía también a `ffmpeg`; en POSIX tampoco, porque la espera se interrumpe y yt-dlp
mata a `ffmpeg`. Con archivos grandes, esa espera puede pasar de los 10 s de SC-004 (ver la
tabla de arriba). D4 (R21) lo resolvería.

**Opciones para SC-004 en Windows (decisión del usuario)**:
1. enmendar SC-004 con la excepción: en Windows, con la conexión estancada o durante la unión
   o la verificación, la parada puede tardar lo que tarde esa operación (hasta 15 s la lectura);
2. bajar el tiempo de espera de lectura de 15 s a menos de 10 s: cubre solo el caso (c) y
   cambia el vigilante de estancamiento de la 001 (su R21);
3. que, en Windows, el vigilante además corte la conexión en curso y termine `ffmpeg` y
   `ffprobe` al parar, como hace de hecho un Ctrl+C: dejaría la parada por debajo de 1 s en
   todos los casos, pero toca `ytdl.py` (001) y hay que probarlo en el CI de Windows.

## R8. Sin consola y sobreviviendo al cierre de la ventana (US4)

**Decisión**:
- **Windows**: `creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP |
  CREATE_BREAKAWAY_FROM_JOB`. `CREATE_NO_WINDOW` da al `python.exe` una consola sin ventana.
  `CREATE_NEW_PROCESS_GROUP` lo aísla de los eventos de consola de quien lo lanzó.
  `CREATE_BREAKAWAY_FROM_JOB` lo saca del objeto de trabajo (*job object*) al que pertenezca la
  ventana.
- **Linux**: `start_new_session=True`, para que no reciba las señales de la sesión de la
  ventana.

**Por qué hace falta salir del objeto de trabajo [verificado: diagnóstico T006 en el equipo
del usuario y T087 en el CI 110; falta V5 a mano]**: la ventana no la
lanza el sistema directamente. `uv run` (R11) lanza `pythonw.exe`, y el `pythonw.exe` de un
entorno virtual es a su vez un lanzador que arranca el intérprete real. Tanto uv como el
lanzador del entorno virtual pueden meter a sus hijos en un objeto de trabajo con
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Si el trabajador queda dentro, al cerrarse la ventana
el objeto de trabajo se cierra y el sistema mata el trabajador. No se pierde progreso
reanudable, porque es un cierre abrupto que la cola ya cubre, pero "seguir en segundo plano"
dejaría de funcionar en silencio. Si el objeto de trabajo no permite salir de él (falta
`JOB_OBJECT_LIMIT_BREAKAWAY_OK`), `CreateProcess` falla con `ERROR_ACCESS_DENIED`. La
aplicación lo trata como error explícito ("no se puede dejar el procesamiento en segundo
plano"), no lanza el trabajador sin esa garantía y lo anota en el registro.

**Verificación obligatoria**: `test_app_segundo_plano.py` (Windows, `slow`) lanza la aplicación
por la misma cadena que el lanzador (`uv run --gui-script`), inicia una descarga contra el
servidor local, termina la cadena de la ventana y comprueba que el trabajador sigue vivo y
avanzando 5 s después. En Linux, el mismo test con `start_new_session`.

**Resultado del diagnóstico (2026-11-14, `scripts/diagnostico_job.py`, opción A de la consulta
sobre la Fase 1)**. Equipo del usuario: Windows 10 19045, Python 3.12.15, uv 0.13.1. El propio
diagnóstico estaba en un objeto de trabajo.

| Camino | Escenario | Hijo en objeto de trabajo | Intermedio en objeto de trabajo | Hijo sobrevive | Intermedio sobrevive |
|--------|-----------|---------------------------|---------------------------------|----------------|----------------------|
| python directo | fin normal | no | — | sí | — |
| python directo | matar el superior | no | sí | sí | no |
| uv run | fin normal | no | — | sí | — |
| uv run | matar el superior | no | sí | sí | sí |
| uv run --gui-script | fin normal | no | sí | sí | — |
| uv run --gui-script | matar el superior | no | sí | sí | sí |

Lectura:
- en el equipo del usuario, `CREATE_BREAKAWAY_FROM_JOB` saca al hijo de todo objeto de trabajo
  por los tres caminos, y el hijo sobrevive en los seis casos;
- el intermedio, que no se separa, sí queda en un objeto de trabajo; con `python directo`
  muere al matar el lanzador del entorno virtual. Separarse es, por tanto, necesario;
- en el CI de GitHub el hijo sobrevivía pero seguía en un objeto de trabajo
  (`test_lanzar_desacoplado.py`): lo atribuye al objeto de trabajo exterior del runner, que
  no existe en el equipo del usuario. **Pendiente de la decisión del usuario** qué debe
  comprobar ese test.

**Fase 11 (segundo plano)**:
- `test_app_segundo_plano.py`, en los dos sistemas: se mata el proceso de la ventana
  (`tests/support/ventana_headless.py`, el arranque de `main()` sin la ventana real) con una
  descarga en curso; 5 s después el trabajador sigue vivo y descargando, y una ventana nueva
  lo muestra con `origen: app` y `puede_detener: true` (en local, 0,11–0,12 s desde que se
  lanza; SC-008 pide 5 s) y lo para con Pausar desde esa segunda sesión.
- `test_lanzador_segundo_plano.py`, solo en Windows (T087): la ventana arrancada con
  `uv run --gui-script`, como el lanzador; se mata toda la cadena (uv, el lanzador del
  entorno, `pythonw.exe`) salvo el trabajador, que debe seguir descargando 5 s después.
  **Verificado en el CI 110** (commit `279a0de`, `windows-latest`): el trabajador sobrevive y
  sigue descargando. Falta V5 en el equipo del usuario (T090).

## R9. Velocidad, tiempo estimado y estado de la red en directo (FR-005, SC-006)

**Hallazgo**: la feature 001 solo guarda en `cola.db` los bytes, cada 2 s
(`store.INTERVALO_PROGRESO`). La velocidad, el tiempo estimado, "sin datos hace N s" y la cuenta
atrás de un reintento solo existen en memoria, en el `Presentador` de `ejecutar`.

**Decisión**: un segundo observador del trabajador, `progress.Publicador`, recibe los mismos
eventos que el `Presentador` y escribe cada segundo `progreso-vivo.json` en el directorio de la
app. Lo escribe todo procesador, también un `ejecutar` de la CLI, porque FR-005 lo exige
también en ese caso. `worker.ejecutar` recibe ambos observadores mediante un reparto trivial
(`Observadores([Presentador(), Publicador(...)])`), y la salida de la CLI no cambia.

El archivo se escribe atómicamente (temporal más `os.replace`), sin `fsync`: es informativo y la
verdad sigue en `cola.db` y en los `.part`. En Windows, `os.replace` falla si la ventana lo
tiene abierto en ese instante; el publicador lo reintenta en el siguiente segundo. No contiene
URLs, solo identificadores, y las causas pasan por `ocultar_secretos`. Contenido en
[contracts/archivos.md](contracts/archivos.md).

**Retraso máximo**: hasta 1 s de escritura más hasta 1 s de consulta de la página, 2 s en
total, como pide SC-006.

**Si la escritura falla** (disco lleno, directorio sin permiso, error de `psutil`): ni
`progreso-vivo.json` ni `procesador.json` alteran nunca la salida ni el código de salida de
`ejecutar`, porque son informativos y la verdad sigue en `cola.db`. Pero el fallo no se
silencia (Principio III):
- se anota en el logger `downloader.vivo` al empezar a fallar y al recuperarse, no una vez por
  segundo. En el procesador de la aplicación ese logger va a `registro/procesador.log`; en la
  CLI no tiene manejador (`cli.principal` solo instala un `NullHandler`), así que su salida no
  cambia;
- la ventana no muestra datos viejos: sin un `progreso-vivo.json` válido (ausente, de otro
  proceso o de más de 5 s) mientras hay un procesador en marcha, `api/estado` devuelve
  `en_directo: false` y la fila activa muestra "Sin datos en directo" en lugar de velocidad y
  tiempo estimado.

Lo prueban `test_publicador.py` y `test_ejecutar_sin_archivos_vivos.py`, que compara la salida y
los códigos de `ejecutar` con las referencias de `test_cli_caracterizacion.py`.

**Alternativas**: *calcular la velocidad en la aplicación a partir de los bytes de `cola.db`*:
cambia de 2 s en 2 s, y con la consulta de 1 s el retraso llegaría a 3 s, por encima de SC-006.
*Escribir velocidad y tiempo estimado en `cola.db` cada segundo*: con `synchronous=FULL`,
supondría una escritura sincronizada por segundo y una migración del esquema para datos que no
necesitan sobrevivir a nada.

## R10. Resultado de la última ejecución y registro de errores (FR-012, FR-020)

**Decisión**:
- **`ultima-ejecucion.json`**: lo escribe el subproceso de la aplicación al terminar, de forma
  atómica. Contiene la ejecución, inicio y fin, completadas, fallidas (id, título o URL con
  secretos ocultos, causa), el código de salida y, si lo hubo, el error (tipo `fatal`,
  `inesperado` o `detenida`, más el mensaje). Si la ventana detecta que el procesador de su
  ejecución ya no vive y no hay resultado de esa ejecución, lo escribe ella con tipo
  `abrupto` y lo anota en el registro. La ventana muestra el resultado al abrirse hasta que se
  inicia otra ejecución (FR-012). Un `ejecutar` de la CLI no lo escribe: sus mensajes van a
  su terminal.
- **Registro de errores**: una carpeta `registro/` en el directorio de configuración de 001
  (`config.directorio_app()`), con un archivo por proceso escritor, para que nunca haya dos
  procesos rotando el mismo archivo:
  - `ventana.log`, con un respaldo `ventana.log.1`;
  - `procesador.log`, con un respaldo `procesador.log.1`.

  Cada uno usa `logging.handlers.RotatingFileHandler(maxBytes=500_000, backupCount=1)`: como
  mucho 4 × 500 000 bytes, es decir, 2 MB en total. Al llenarse, se descartan las entradas
  más antiguas (FR-020). Solo hay un escritor por archivo: una ventana a la vez (R12) y un
  procesador a la vez (bloqueo).

  Cada entrada lleva fecha, nivel, qué falló y el detalle técnico con la traza. Un `Formatter`
  propio aplica `ocultar_secretos` al mensaje y a la traza ya formateada. El secreto de sesión
  (R4) nunca se registra, porque el servidor no registra peticiones.

  `sys.excepthook` y `threading.excepthook` llevan al registro las excepciones no capturadas.
  Si el registro no se puede escribir (disco lleno, sin permisos), `handleError` activa un
  indicador que la API devuelve y la ventana muestra ("el registro de errores no está
  disponible"), y la aplicación sigue funcionando (Edge Cases).

**Alternativas**: *un único archivo con dos escritores*: en Windows, rotar un archivo que otro
proceso tiene abierto falla (`PermissionError`). *Redirigir `stderr` del subproceso a un
archivo*: no se puede acotar desde fuera y mezclaría la salida de texto de la CLI.

## R11. Lanzador sin consola

**Decisión**: `Gestor de descargas.vbs` en la raíz del repositorio. Al hacer doble clic, Windows
lo ejecuta con `wscript.exe`, que no abre consola. El script:
1. fija como directorio de trabajo la carpeta del propio `.vbs`, para que el repositorio
   pueda estar en cualquier ruta, también con espacios;
2. comprueba que `uv` está en el `PATH`. Si no, muestra un `MsgBox` con la instrucción de
   instalación del README y termina;
3. sincroniza el entorno con `uv sync`, oculto (`WScript.Shell.Run(..., 0, True)`), y espera
   a que termine. Si falla (por ejemplo, sin red tras un cambio de `uv.lock`), muestra un
   `MsgBox` con la causa probable y qué hacer, y termina sin abrir la ventana: el usuario nunca
   se queda con un doble clic sin respuesta;
4. ejecuta `uv run --gui-script src\downloader\app\gestor.pyw` con
   `WScript.Shell.Run(..., 0, False)`. El estilo 0 oculta la consola de `uv.exe`, y
   `--gui-script` hace que uv use `pythonw.exe`, que no crea consola. No queda ninguna
   ventana de consola visible en ningún momento (SC-001).

La primera apertura tras instalar o actualizar puede tardar más que los 5 s de SC-001, que se
mide con el entorno ya sincronizado (spec, Assumptions; quickstart.md V1). Con el entorno al
día, `uv sync` solo comprueba y termina enseguida.

**Alternativa añadida como respaldo**: `[project.gui-scripts] gestor-descargas =
"downloader.app.principal:main"`. `uv sync` crea `.venv\Scripts\gestor-descargas.exe`, un
lanzador del subsistema gráfico que tampoco abre consola. Sirve si VBScript está desactivado
y para anclarlo a la barra de tareas. No sustituye al `.vbs`, porque vive en `.venv`, fuera
del repositorio.

**Riesgo**: Microsoft anunció la retirada por fases de VBScript en Windows. Hoy viene
instalado y activado por defecto como característica opcional. Cuando se desactive, el
respaldo `gestor-descargas.exe` sigue funcionando, y la etapa 2 (instalador y `.exe`, spec
aparte) sustituirá al lanzador.

**Verificación**: el doble clic real se comprueba a mano (quickstart.md, V1). En el CI de
Windows, `test_lanzador.py` ejecuta `wscript.exe //B "Gestor de descargas.vbs"` con un
`DOWNLOADER_HOME` temporal y comprueba que arranca el proceso de la ventana bajo `pythonw.exe`
y que ningún proceso de la cadena tiene una ventana de consola visible.

**Resultado (V1, 2026-11-17, equipo de 4 GB)**: tres dobles clics en el `.vbs`, ninguna
consola, y la ventana lista casi al instante (sin cronómetro). Quedan por comprobar el doble
clic en `gestor-descargas.exe` y V1b (quickstart.md, "Resultados").

**Alternativas**:
- *`.cmd` o `.bat`*: abren una consola, aunque sea un instante (incumple SC-001).
- *`.pyw` suelto*: depende de que el lanzador `py` de python.org esté instalado y asociado;
  el Python que instala uv no lo registra.
- *Acceso directo `.lnk` en el repositorio*: guarda rutas absolutas y no funcionaría en otro
  equipo ni en otra carpeta.
- *`.ps1`*: el doble clic lo abre en el Bloc de notas por defecto.
- *JScript (`.js`) con `wscript`*: equivalente al `.vbs` y no está incluido en el anuncio de
  retirada de VBScript, pero los antivirus lo asocian más a descargadores maliciosos.
  Queda como opción si el `.vbs` deja de funcionar antes de la etapa 2.

## R12. Una sola ventana (FR-018)

**Decisión**: la ventana toma un bloqueo propio, `ventana.lock`, con las mismas funciones que
`ejecutar.lock` (`msvcrt.locking` o `fcntl.flock`, que pasan de `store.py` a un módulo
pequeño, `bloqueo.py`). Si una segunda apertura no lo consigue, escribe `activar.solicitud` y
termina. La ventana abierta consulta ese archivo en su ciclo de un segundo; si existe, lo borra
y llama a `ventana.enfocar()` (`restore()`, `show()` y traer al frente). El sistema libera el
bloqueo si la ventana muere, así que una ventana colgada que ya no existe no impide abrir otra.

**Limitación [a verificar en Windows; pendiente de V9 a mano]**: Windows puede negar traer una ventana al frente si la
petición no viene del proceso en primer plano; en ese caso parpadea en la barra de tareas. Se
acepta: lo importante es que no se abre una segunda ventana.

**Implementación (Fase 12)**: `app/instancia.py`. `principal.arrancar` toma el bloqueo como
primer paso; `principal.abrir` y `main()` lo comprueban además antes de abrir el registro (un
solo escritor por `ventana.log`, R10), también fuera de Windows: con una ventana abierta,
`python -m downloader.app` pide la activación y sale con 0 en Linux y en Windows. Al tomar el
bloqueo se descarta una `activar.solicitud` anterior. `test_app_instancia.py`, en los dos
sistemas y entre procesos reales: la segunda apertura termina en unos 0,15 s y la primera se
enfoca en menos de un segundo (en local, Linux). Que la ventana real pase al frente es V9.

## R13. Datos que muestra la ventana

**Decisión**: `app/vista.py` construye, para cada descarga, una fila con título (o URL con
secretos ocultos), sitio, estado, porcentaje, velocidad, tiempo estimado y último aviso o
error, a partir de `Store.leer` y de `progreso-vivo.json`:
- *sitio*: el nombre de host de la URL, sin usuario, contraseña, puerto, ruta ni consulta;
- *etiquetas*: las de `models.ETIQUETAS`, con mayúscula inicial, como en la maqueta;
- *unidades*: MB/s decimales con coma ("2,4 MB/s"), "quedan 1 min 12 s" y "sin datos hace
  14 s, reintentando", como en la maqueta y en `progress.linea_activa`;
- *listas de reproducción*: una fila "lista (N videos)" y una fila por video;
- *"requiere sesión"* (FR-016): ver R17.

`Store.leer` hace un `quick_check` y, si la base está corrupta, guarda una copia. Para no crear
una copia por segundo, la vista deja de consultar tras el primer `BaseCorrupta` y muestra el
mensaje (Edge Cases).

La API devuelve un `ETag` calculado a partir de `max(actualizada)`, del instante de
`progreso-vivo.json` y del estado del procesador. Si no cambió nada, la respuesta es 304 sin
cuerpo. La página actualiza solo las filas que cambian, por id, para que 500 filas no se
redibujen cada segundo (SC-005).

## R14. Medición de memoria (SC-005)

**Decisión**: `scripts/medir_memoria.py` (herramienta de desarrollo, no se distribuye), con
`psutil`:
- **Procesos de la aplicación**: el de la ventana y todos sus descendientes (los procesos
  `msedgewebview2.exe`); sus ascendientes de la cadena del lanzador que sigan vivos (`uv.exe`,
  el lanzador del entorno virtual); el trabajador, cuyo `pid` está en `procesador.json`, porque
  al salir del objeto de trabajo deja de ser descendiente; y los descendientes del trabajador
  (ffmpeg o ffprobe, si están en marcha).
- **Medida**: `memory_full_info().uss` (memoria privada) de cada proceso, sumada. Se toma una
  muestra cada 0,5 s durante toda la prueba y se informa del máximo de la suma, con el desglose
  por proceso en ese momento.
- **Escenario**: un `DOWNLOADER_HOME` temporal con 500 descargas en cola, una de ellas en curso
  contra el servidor local de los tests con velocidad limitada, desde que se abre la aplicación
  hasta que termina esa descarga (SC-005).

**Estimación** (verificada: ver "Resultado de la medición" al final de R14):

| Proceso | Estimación |
|---------|------------|
| Ventana (Python, pythonnet, CLR de .NET, WinForms) | 50–80 MB |
| WebView2 (proceso del navegador, GPU, renderizador, utilidades) | 100–140 MB |
| Trabajador (Python, yt-dlp, SQLite) | 50–80 MB |
| `uv.exe` y lanzador del entorno virtual | 5–15 MB |
| **Total** | **205–315 MB** |

El margen es estrecho. Por eso la medición es una tarea temprana, que actúa como puerta antes
de construir la interfaz completa. Mitigaciones, en este orden, si se pasa de 300 MB:
1. que la ventana no importe yt-dlp (R15, obligatorio de todos modos);
2. argumentos de WebView2 con `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`:
   `--renderer-process-limit=1`, `--disable-gpu` (la interfaz es estática y el renderizado por
   software basta), `--disable-extensions`. pywebview fija sus propios
   `AdditionalBrowserArguments` (`--disable-features=ElasticOverscroll`): hay que verificar
   qué valor prevalece **[verificado en T011: se suman, no se sustituyen]**;
3. que la ventana no lance `uv run` como padre persistente (usar el respaldo `gui-scripts`);
4. si nada basta, plan B con tkinter (R1), que se plantearía al usuario antes de cambiar la
   decisión.

**Resultado de la medición (2026-11-14, T011)**. Equipo de referencia del usuario: Windows 10
19045, 3,7 GB de RAM, 2 CPU lógicas, WebView2 157.0.4341.46. `scripts/medir_memoria.py` con la
ventana mínima, 500 descargas en cola y la primera descargándose (200 MB a 2 MB/s); unas 390
muestras en unos 220 s por variante. MB decimales.

| Variante | Máximo | Ventana (pythonw) | WebView2 (suma) | Trabajador (python) | uv.exe | Resultado |
|----------|--------|-------------------|-----------------|---------------------|--------|-----------|
| tal cual (`uv run --gui-script`) | 266,5 MB | 43,7 | 152,2 | 56,6 | 6,9 | dentro |
| `--sin-uv` | 253,5 MB | 43,8 | 146,5 | 56,3 | — | dentro |
| `--importar-store` | 272,2 MB | 59,6 | 143,9 | 54,8 | 6,8 | dentro |
| argumentos de WebView2 | 218,9 MB | 43,5 | 105,6 | 55,8 | 6,9 | dentro |

En todas cuentan también `conhost.exe` (unos 6 MB, la consola sin ventana del trabajador) y
dos lanzadores de menos de 1 MB.

- **Estimación de R14**: 205–315 MB; medido, 219–272 MB. La cifra queda dentro del límite en
  las cuatro variantes.
- **Coste de cargar yt-dlp en la ventana (R15)**: el proceso de la ventana pasa de 43,7 a
  59,6 MB, unos 16 MB, algo menos que los 20–30 MB estimados. R15 se mantiene.
- **Coste de `uv run` como padre**: `uv.exe` ocupa unos 7 MB; la diferencia total entre las
  dos primeras variantes (13 MB) incluye variación propia de WebView2.
- **Precedencia de `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` [verificado]**: los argumentos de la
  variable llegan **junto a** los de pywebview (`--disable-features=ElasticOverscroll` sigue
  en la línea de órdenes), no los sustituyen. Ahorran unos 47 MB en WebView2.
- **Límite de la medición**: el archivo de prueba es un único archivo directo, sin unir audio y
  video, así que no incluye el pico de un `ffmpeg` que une pistas al terminar una descarga. Con
  la configuración por defecto (266,5 MB) el margen hasta 300 MB es de 33,5 MB, y ese pico no
  está medido.

**Decisión de la puerta (T012)**: se supera; la implementación continúa. Mitigaciones
obligatorias:
- R15, la ventana sin yt-dlp (ya obligatoria en el plan);
- los argumentos de WebView2 `--renderer-process-limit=1 --disable-gpu --disable-extensions`,
  que la ventana fija al arrancar (T066). Dejan el máximo en 218,9 MB, con 81 MB de margen para
  el pico de `ffmpeg`, que la medición no cubre.

Condiciones del usuario (2026-11-15):
- los argumentos se fijan solo en el entorno del proceso de la aplicación (`os.environ` antes de
  `webview.start`), nunca como variable persistente del usuario o del sistema (`setx`, registro);
- la puerta final de memoria (T106) mide además una descarga que une audio y video, y la suma
  incluye `ffmpeg` y `ffprobe` como procesos de la aplicación;
- con `--disable-gpu`, se comprueba a mano en el equipo de 4 GB que la ventana sigue
  respondiendo con 500 filas actualizándose (quickstart.md, V11).

No es obligatorio prescindir de `uv run` como padre: R11 no cambia.

## R15. La ventana no carga yt-dlp

**Hallazgo**: `errors.py` importa `yt_dlp` en el nivel del módulo, y `store.py` importa
`errors` (por `ErrorFatal`). Abrir la cola desde la ventana cargaría yt-dlp entero: entre 20 y
30 MB de memoria y tiempo de arranque (SC-001) **[verificado en T011: unos 16 MB]**.

**Decisión**: separar lo que la ventana necesita en módulos sin yt-dlp:
- `ErrorFatal` y `ErrorPermanente` pasan a `excepciones.py`;
- `ocultar_secretos` y los mensajes de consejo (`CONSEJO_SESION`, el de FR-028) pasan a
  `secretos.py` y `mensajes.py`;
- `NAVEGADORES` se fija como constante en `models.py`, y un test comprueba que coincide con
  `yt_dlp.cookies.SUPPORTED_BROWSERS`;
- `publish.py`, que `config.py` importa, deja de cargar yt-dlp en el nivel del módulo:
  `sanitize_filename` se importa dentro de `nombre_publicado`. `escribible` y la validación de
  la plantilla siguen en `publish` con sus nombres, porque `test_cli_agregar.py` sustituye
  `publish.escribible` y los tests de 001 no se modifican;
- `operaciones.py` importa `worker`, `network` y `ytdl` dentro de `ejecutar_cola`, de modo que
  la ventana puede usar sus validaciones sin cargar yt-dlp.

`errors.py`, `ytdl.py` y `worker.py` los reexportan, así que su API no cambia. Un test
(`test_app_sin_ytdlp.py`) importa los módulos de la aplicación en un intérprete limpio y
comprueba que `yt_dlp` no está en `sys.modules`.

## R16. Qué es una URL válida en el formulario (FR-002)

**Decisión**: una línea, sin espacios al principio ni al final, es válida si
`urllib.parse.urlsplit` da el esquema `http` o `https` y un nombre de host. Las líneas vacías
se ignoran. Las demás se devuelven con su número y no se agregan, y las válidas sí se agregan.
Si la carpeta, la calidad o el navegador no son válidos, no se agrega ninguna (Edge Cases).

Es más estricto que la CLI, que acepta cualquier texto no vacío. Lo pide FR-002, y la CLI no
cambia (FR-017).

## R17. Cuándo una descarga "requiere sesión" (FR-016)

**Decisión**: una descarga fallida ofrece "Reintentar con cookies" si su `ultimo_error`
contiene uno de los consejos que la feature 001 añade cuando el fallo apunta a la sesión:
`CONSEJO_SESION` (403 y 410, FR-026 y FR-030), el mensaje de lista sin videos (FR-028) o el
de cookies ilegibles (FR-029, para probar con otro navegador). La función
`mensajes.requiere_sesion(texto)` compara con esas constantes, no con texto suelto. Un test
recorre los mensajes de la tabla de clasificación de `errors.py` y comprueba qué casos ofrecen
el reintento. Si yt-dlp informa de que el contenido exige iniciar sesión sin un 403 (por
ejemplo, `login required`), la clasificación de 001 MUST añadir `CONSEJO_SESION` **[a
verificar en la tabla de `errors.py`]**.

El reintento usa `Store.agregar` con el navegador elegido, el mismo camino que
`agregar --cookies-de`: la descarga fallida vuelve a la cola con el mismo id y ese navegador.

**Resultado (2026-11-15, T016) [verificado]**: yt-dlp lanza el contenido que exige iniciar
sesión (`InfoExtractor.raise_login_required`) como `ExtractorError(expected=True)`. La 001 lo
clasifica PERMANENTE **sin** `CONSEJO_SESION`, y guarda como `ultimo_error` el mensaje de
yt-dlp, que sugiere `--cookies-from-browser`. En lugar de cambiar el mensaje de la CLI
(FR-017), `mensajes.requiere_sesion` reconoce también esa marca de yt-dlp
(`MARCAS_YTDLP_SESION`). Lo prueba `tests/unit/test_mensajes.py`. Las cookies ilegibles se
reconocen por el comienzo de su mensaje, `COOKIES_ILEGIBLES` ("No se pudieron leer las cookies
de …"), que ahora comparten `errors` y `mensajes`.

## R18. Cierre de la ventana (FR-010)

**Decisión**: el manejador de `window.events.closing` llama a la función de lógica
`cierre.decidir(estado_procesador)`, que devuelve `cerrar` o `preguntar`. Pregunta solo si hay
un procesador de origen `app` en marcha; con uno de la CLI o sin procesador, cierra
(US4, escenario 6; Edge Cases). Si hay que preguntar, el manejador ejecuta
`mostrarDialogoCierre()` en la página y devuelve `False`, lo que cancela el cierre. El diálogo,
en HTML como en la maqueta, llama a `POST api/cerrar` con la decisión:
- `segundo_plano`: la ventana se destruye; el trabajador sigue (R8);
- `pausar`: se escribe `parada.json`; la página muestra "Deteniendo…" hasta que el procesador
  termina, y entonces la ventana se destruye. Si en 30 s no ha terminado, la página lo dice y
  ofrece cerrar igualmente (el progreso está a salvo);
- `cancelar`: no hace nada.

`cierre.decidir` y la API de cierre se prueban en los dos sistemas con una ventana falsa. Solo
el enganche al evento de pywebview es exclusivo de Windows.

## R19. Separación de la capa de Windows y tests

**Decisión**:
- Todo lo exclusivo de Windows vive en `src/downloader/app/ventana/` (pywebview, WebView2,
  `MessageBoxW`), en `src/downloader/app/gestor.pyw` y en `Gestor de descargas.vbs`. Es lo
  que admite la excepción de la constitución v3.1.0.
- El resto de `src/downloader/app/` no importa nada de `ventana/`. Recibe la ventana como un
  objeto con cuatro operaciones (`examinar_carpeta`, `enfocar`, `ejecutar_js`, `destruir`).
  En los tests, una ventana falsa las implementa. `principal.py` solo importa `ventana/`
  dentro de `if sys.platform == "win32"`.
- `tests/unit/test_plataforma.py` admite importaciones de un solo sistema sin proteger solo
  dentro de `app/ventana/`. Fuera de ahí, mantiene la regla de la feature 001.
- Marcadores nuevos de pytest:
  - `solo_windows`: solo tests de la ventana y del lanzador, la única parte que la
    constitución v3.1.0 admite exclusiva de Windows; se omite en Linux con el motivo. Un
    comportamiento de la lógica que difiere por sistema (el tiempo de parada de R7, la salida
    del objeto de trabajo de R8) se prueba con un test sin marcador y condiciones por sistema;
    lo exclusivo de la cadena del lanzador (`uv run --gui-script`) sí lleva el marcador;
  - `pywebview_internals`: comprueba los internos de pywebview (R2).
- El CI no cambia de estructura: la misma matriz Linux y Windows. En Windows se ejecutan
  además los tests `solo_windows`. Los runners de GitHub Actions no garantizan un escritorio
  interactivo, así que los tests `solo_windows` no abren una ventana real: comprueban la detección
  de WebView2, el lanzador (R11) y el enganche de eventos con objetos simulados. La ventana
  real, el doble clic y la maqueta se validan a mano (quickstart.md).
- La interfaz web (`app.js`) se mantiene delgada: pinta lo que devuelve la API y envía
  órdenes. Toda la lógica (validación, decisiones de cierre, formato de cifras, permisos de
  Detener) está en Python y tiene tests. No se añade un navegador de pruebas al CI.

## R20. Fallos intermitentes de la 001 durante la feature (investigación del 2026-11-15)

**Hallazgo**: dos tests de la feature 001 fallaron una vez en el CI tras empezar la feature 002
y pasaron al reintentarlos con el mismo código. Los dos con el mismo síntoma: el servidor de
prueba sirvió más bytes de los que exige el test al reanudar.

| Fecha | Commit | Sistema | Test | Exceso sobre el límite |
|-------|--------|---------|------|------------------------|
| 2026-11-15 | `4f0a317` (Fase 3) | Windows | `test_disco_lleno.py` | 360 448 bytes (pedidos 1 902 408; faltaban 1 541 960) |
| 2026-11-15 | `44494c2` (Fase 5) | Linux | `test_network_cuts.py::test_cortes_en_tres_momentos_distintos` | 804 947 bytes (servidos 2 889 519 > 1,01 × 2 063 943) |

**Repetición** (petición del usuario): `test_network_cuts.py` y `test_disco_lleno.py`
alternando, en el mismo contenedor Linux, el código actual (`4cb69b1`, tras la Fase 5) y el
anterior a la feature (`0d0170e`), con 30 minutos por lado (no caben 200 iteraciones):

| Código | Iteraciones | Fallos | Test | Exceso |
|--------|-------------|--------|------|--------|
| actual | 157 | 1 (iteración 121) | `test_cortes_en_tres_momentos_distintos` | 392 167 bytes (servidos 2 476 842 > 1,01 × 2 064 035) |
| anterior a la feature | 157 | 1 (iteración 51) | `test_cortes_en_tres_momentos_distintos` | 392 674 bytes (servidos 2 480 049 > 1,01 × 2 066 708) |

- **`test_disco_lleno.py` no se reproduce en Linux**: 0 fallos en 314 ejecuciones (157 por
  lado). Su único fallo fue en Windows.
- **Sin diferencia entre el código actual y el anterior**: la misma frecuencia (1 de 157) y el
  mismo exceso, en el mismo test. El fallo es anterior a la feature 002; no lo introduce
  ninguna de las fases 3, 4 o 5.
- **No es un margen demasiado justo**: el exceso no está cerca del 1 % de tolerancia, sino que
  es un bloque entero. En la repetición, unos 392 KB, casi exactamente el 20 % del archivo
  menos el 1 % de tolerancia: el trozo que el test corta tras el 20 %, servido dos veces. En el
  CI de Linux, dos bloques así. Ampliar la tolerancia no lo arreglaría salvo que fuera enorme.
  Apunta a un acontecimiento discreto: o el cliente pierde de vez en cuando el trozo recibido
  antes de un corte y lo vuelve a pedir (sería progreso perdido, Principio IV), o el servidor
  de prueba cuenta como servidos bytes que el corte de conexión descarta antes de que el
  cliente los lea (sería un artefacto del test). Con estos datos no se distingue.
- **La tolerancia no se ha cambiado.** Averiguar cuál de las dos causas es requiere una tarea
  propia sobre la feature 001, fuera de esta.

### Diagnóstico de la causa del exceso de bytes (2026-11-15)

**Método** (petición del usuario): una copia fija del código (`335186a`) en un árbol de trabajo
aparte, instrumentada y sin commits ni cambios en los tests de la 001. Cada evento va a un
JSONL con su instante (`time.monotonic`) y su test:
- servidor de prueba: los bytes que cuenta como enviados, la `Range` pedida y el corte;
- cliente: lo que lee de la red la envoltura de lectura de la 001 (`_vigilar_cuerpo`) en
  cada respuesta; y, desde la segunda tanda, cada bloque leído con su pila de llamadas;
- yt-dlp: cada apertura del `.part` (modo y tamaño antes), cada cierre (bytes escritos y
  tamaño después) y cada reintento interno;
- trabajador: el tamaño del `.part` al empezar cada intento.

`test_network_cuts.py` repetido en bucle, en Linux, de 04:05 a 05:07 (62 min de los 90
permitidos): 337 ejecuciones, **3 fallos**, todos en `test_cortes_en_tres_momentos_distintos`.

| Ejecución | Servidos / archivo | Exceso | Tramos leídos y no escritos |
|-----------|--------------------|--------|-----------------------------|
| 18 (1.ª tanda) | 2 478 661 / 2 065 551 | 413 110 | 1 tramo de 413 110 bytes |
| 141 | 2 891 759 / 2 065 543 | 826 216 | 2 tramos de 413 108 bytes |
| 282 | 2 888 207 / 2 063 005 | 825 202 | 2 tramos de 412 601 bytes |

**Datos**: en cada tramo perdido, el servidor cuenta N bytes enviados y corta; el cliente
lee de la red exactamente esos N bytes; pero yt-dlp **no abre el `.part`** en ese intento y
el `.part` sigue con el tamaño anterior, así que la reanudación vuelve a pedir el mismo
`Range`. Ejemplo (ejecución 282): intento con `.part` de 412 601 bytes, `Range:
bytes=412601-`, servidos 412 601 y corte, cliente con 412 601 leídos, ninguna apertura del
`.part`, y el siguiente intento empieza otra vez con 412 601 bytes. En la ejecución buena final,
el `.part` se abre **después** de haber leído y cerrado la respuesta entera.

**Conclusión**: el cliente leyó más de lo que quedó en el `.part`. **No es un artefacto del
servidor de prueba: es progreso perdido (Principio IV) en la 001.**

**Causa**: la pila de cada bloque leído en esos intentos termina en
`YoutubeDLRegistrado._vigilar_xml` (research R5 de la 001), en el bucle que lee el cuerpo
entero:

```python
if not xml: ...                 # no parece XML: se entrega tal cual
while not final:                # parece XML: no se entrega nada hasta tenerlo entero
    datos = leer(TROZO_LECTURA)
```

`_vigilar_xml` envuelve **todas** las respuestas, también la del archivo de video. Si el
cuerpo empieza por `<` (tras un BOM o espacios, `drm.posible_xml`), lo retiene en memoria hasta
el final para analizarlo como manifiesto. Una descarga nueva empieza por la cabecera del MP4,
pero una **reanudación** (`Range: bytes=N-`) empieza en un byte cualquiera del archivo: si es
`<` (0x3C, 1 de cada 256), el resto del archivo se retiene sin llegar a yt-dlp. Si la conexión
se corta antes del final, todo lo retenido se pierde y se vuelve a descargar. Encaja con la
frecuencia observada (el test reanuda varias veces por ejecución sobre un MP4 que ffmpeg genera
de nuevo en cada sesión, así que el byte del punto de corte cambia) y con que el exceso sea
siempre un tramo entero.

**Efectos fuera del test**:
- progreso perdido en cualquier reanudación de un archivo directo cuyo primer byte sea `<`
  (Principio IV);
- **memoria**: en esa reanudación, el resto del archivo entero se guarda en memoria del
  trabajador antes de escribirse. Con un video real de varios GB, puede agotar la memoria
  del equipo de 4 GB y rompe el presupuesto de SC-005 (la medición de la Fase 2 no lo cubre:
  su descarga no se reanudaba).

**Arreglo propuesto (no aplicado, pendiente de decisión del usuario)**, en la 001:
1. `_vigilar_xml` solo retiene cuerpos que pueden ser un manifiesto: no las respuestas 206
   (una reanudación nunca es un manifiesto que lea el extractor), ni las de la fase de
   descarga del archivo de medios, ni las de `Content-Type` de video o audio;
2. además, un límite de lo retenido (por ejemplo, 16 MB): por encima, se entrega como
   no-XML, para que ningún cuerpo pueda ocupar memoria sin límite;
3. un test nuevo de regresión (sin tocar los existentes): reanudar una descarga cuyo byte
   en el punto de corte es `<`, cortar de nuevo y comprobar que el `.part` crece y que los
   bytes servidos no pasan del 1 % de exceso. Hoy fallaría siempre; con el arreglo, el
   intermitente de `test_cortes_en_tres_momentos_distintos` desaparece sin cambiar su
   tolerancia.

La copia instrumentada se ha borrado; los registros de los tres fallos se conservan fuera del
repositorio mientras dure la sesión.

**Arreglo aplicado (2026-11-15, decisión del usuario)**: commit `44e8f18`, propio de la 001 y
separado de la 002 para poder llevarlo a `main` aparte (comprobado: se aplica sobre `main` sin
conflictos y sus tests y los de DRM pasan allí). Detalle en el research de la 001, R5,
"Corrección D2". CI 101 en verde en los dos sistemas, sin reintentos.
- Tests nuevos, vistos en rojo antes del arreglo: la regresión
  (`test_reanudar_en_byte_xml.py`, reanuda en un `<` del archivo y vuelve a cortar; fallaba
  siempre, con 413 407 bytes de más), el tope (`test_vigilar_xml.py`: un cuerpo mayor que el
  tope se entrega completo y en orden; tampoco se retienen 206, `video/*`, `audio/*` ni el
  archivo de `HttpFD`, y un F4M pedido al descargar se sigue analizando) y el aviso visible
  (`test_manifiesto_sin_analizar.py`). Los tests de DRM de la 001 no se modificaron y pasan.
- Al escribirlos apareció un segundo fallo en la misma envoltura, corregido en el mismo
  commit: tras entregar lo retenido de un cuerpo no terminado, un `read()` sin tamaño devolvía
  solo lo retenido y no el resto.
- **Repetición con el arreglo**: `test_cortes_en_tres_momentos_distintos` 300 veces en Linux,
  **0 fallos**. Con la frecuencia anterior (3 de 337) se esperaban unos 2,7; la probabilidad
  de 0 por azar con el defecto presente sería de un 7 %. La prueba fuerte es el test de
  regresión, que es determinista.
- Sin el marcador `ytdlp_internals` en los tests nuevos que dependen de yt-dlp: la lista de
  `tests/unit/test_marcadores.py` (001) es exacta, como con `slow`.

**Reintentos del CI**: desde el 2026-11-15, todo reintento se anota aquí, con test, sistema y
exceso. Fases 6 y 7: ninguno. Fase 8: ninguno (ejecución 98, verde en los dos sistemas).
Arreglo de D2 (ejecuciones 101 y 102) y resultados manuales de la Fase 8 (103): ninguno.
Fase 9: ninguno (ejecución 104, verde en los dos sistemas a la primera).
Fase 10: ninguno (ejecuciones 105 y 106, verdes en los dos sistemas a la primera).
Fase 11: ninguno (ejecuciones 109 y 110, verdes en los dos sistemas a la primera).
Fase 12, tras la corrección: CI 112 verde. "Qué está terminando" (`c4e952c`): CI 113 verde.
Fase 13: ninguno (CI 114, verde en los dos sistemas a la primera).
Fase 12: CI 111 (commit `25f6a37`) **rojo en Windows**, sin reintento. Falló
`test_app_ventana_windows.py::test_la_ventana_fija_los_argumentos_de_webview2_antes_de_arrancar`
(Fase 8, solo Windows): su ventana de pywebview simulada era un `object()` sin `events`, y la
Fase 12 engancha el aviso de cierre al crear la ventana. Error mío al cambiar la ventana real
sin poder ejecutar en local los tests `solo_windows`; se corrige el test para usar la ventana
simulada completa (`_VentanaPywebview`), sin tocar el código.
CI 108 (commit `d68cc43`, SC-004 enmendado y test de parada durante la unión): **rojo en
Linux**, sin reintento. Falló el test nuevo `test_app_parada_postproceso.py[verificacion]`: la
verificación de las pistas de prueba dura unos 0,2 s, menos que la consulta del vigilante
(0,25 s), y la parada llegó cuando el procesamiento ya había terminado (código 0). Era un
defecto del test, no del código: se corrige en el commit siguiente aceptando ese desenlace,
que es el de la spec ("Pausar cuando el procesador ya está terminando: no produce error").

**Fallo sin reintento, no detectado a tiempo** (2026-11-15): la ejecución 97 del CI, la del
commit `335186a` (solo documentación: research R20 y tasks), terminó en rojo en Linux y no lo
vi hasta después de subir la Fase 8. No hubo reintento: el siguiente commit (`387a521`, Fase 8)
pasó en los dos sistemas. Es un tercer test intermitente de la 001, distinto de los dos
anteriores:
- test: `tests/integration/test_reanudaciones_directas.py::test_corte_de_red_ctrl_c_y_cierre_abrupto_cumplen_sc002`;
- sistema: `ubuntu-latest`;
- error: `ejecutar terminó antes de reanudar tras Ctrl+C` (el segundo `ejecutar` salió con 0);
- exceso de bytes: no aplica. Lectura probable (sin repetición propia): tras el Ctrl+C, el segundo
  `ejecutar` terminó la descarga entera antes de que `esperar` viera el avance del 15 %
  pedido, es decir, una carrera del sondeo del test y no progreso perdido. No se ha cambiado.

## R21. Defectos de la 001 pendientes (no se corrigen en esta feature)

- **D1, credenciales de las URLs en la salida de la CLI** (anotado el 2026-11-15): `agregar` y
  `estado` muestran el usuario y la contraseña de una URL con `usuario:clave@`. Reproducido:
  `downloader agregar "https://usuario:clave@ejemplo.com/v/1"` imprime
  `agregada #1  https://usuario:clave@ejemplo.com/v/1  (mejor)`, y `estado` imprime
  `#1 en cola  —  https://usuario:clave@ejemplo.com/v/1`. `ocultar_secretos` oculta tokens de
  la consulta, pero no la parte `userinfo`. La ventana no lo tiene (`vista.url_visible`,
  `vista.direccion`). Se corrige después de esta feature, por decisión del usuario, con su
  test; cambia la salida de la CLI, así que también las referencias de caracterización
  (`tests/integration/referencias_cli/`) que contengan una URL con credenciales.
- **D3, la vigilancia de manifiestos HLS sin tope** (anotado el 2026-11-17; para después de la
  feature, decisión del usuario; sin corregir): `_vigilar_manifiesto` (`ytdl.py`, 001) guarda
  una copia de todo cuerpo que empieza por `#EXTM3U` sin tope de tamaño, vuelve a decodificar
  la copia entera en cada lectura (coste cuadrático con el tamaño) y no excluye las respuestas
  206 ni las de vídeo o audio, como sí hace ya `_vigilar_xml` tras D2. No pierde progreso:
  entrega los datos al momento.
- **D4, Pausar no corta la conexión en curso en Windows** (anotado el 2026-11-17; mejora para
  después de la feature, decisión del usuario): con la conexión colgada, Pausar espera a que
  la lectura vuelva por su tiempo de espera (13 s en el CI; SC-004 enmendado admite 20 s). La
  mejora: que el vigilante, además de interrumpir, cierre la conexión en curso y termine
  `ffmpeg` y `ffprobe`, como hace de hecho un Ctrl+C de consola. Toca `ytdl.py` (001) y hay que
  probarlo en el CI de Windows (R7, opción 3).
- **D2, progreso perdido y memoria sin límite al reanudar** (anotado el 2026-11-15; diagnóstico
  en R20): `_vigilar_xml` retenía entera en memoria una reanudación cuyo primer byte era `<` y,
  si la conexión se cortaba, la perdía. **Corregido** en `44e8f18` (R20, "Arreglo aplicado").
- **Revisión de otros puntos que retienen cuerpos en memoria** (2026-11-15, solo anotado, sin
  corregir, a petición del usuario):
  - `_vigilar_manifiesto` (HLS, `ytdl.py`): guarda una copia de todo cuerpo que empieza por
    `#EXTM3U` y vuelve a decodificar la copia entera en cada lectura. Entrega los datos al
    momento, así que no pierde progreso; su memoria es el tamaño del manifiesto, sin tope, y su
    coste de CPU crece con el cuadrado del tamaño. Tampoco excluye las 206 ni los cuerpos de
    vídeo (un archivo reanudado que empezara justo por `#EXTM3U` se copiaría; improbable).
  - `_vigilar_cuerpo` (envoltura de lectura): junta como mucho lo que pide yt-dlp en una lectura
    (su bloque crece hasta 4 MB). Ante un corte o un estancamiento entrega lo recibido y aplaza
    el error, así que no pierde nada.
  - `_vigilar_xml` con `read()` sin tamaño: el cuerpo ya llega entero; `drm.filtrar_xml` hace
    además una copia y el árbol de expat (unas 2 o 3 veces el manifiesto). Sin tope, porque el
    que lee ya lo tiene todo en memoria.
  - No hay `hls.py` ni `dash.py` en el código del proyecto (solo en `tests/support/`). En yt-dlp,
    `FragmentFD._read_fragment` lee cada fragmento entero en memoria (para descifrarlo o
    unirlo) y `HlsFD` lee entera la lista de medios: el tamaño de un fragmento o de una lista,
    no del archivo. En nuestro código, `publish` copia por bloques.
