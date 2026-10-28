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
  `time.sleep` **[a verificar en Windows]**.

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

## R8. Sin consola y sobreviviendo al cierre de la ventana (US4)

**Decisión**:
- **Windows**: `creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP |
  CREATE_BREAKAWAY_FROM_JOB`. `CREATE_NO_WINDOW` da al `python.exe` una consola sin ventana.
  `CREATE_NEW_PROCESS_GROUP` lo aísla de los eventos de consola de quien lo lanzó.
  `CREATE_BREAKAWAY_FROM_JOB` lo saca del objeto de trabajo (*job object*) al que pertenezca la
  ventana.
- **Linux**: `start_new_session=True`, para que no reciba las señales de la sesión de la
  ventana.

**Por qué hace falta salir del objeto de trabajo [a verificar en Windows]**: la ventana no la
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
3. ejecuta `uv run --gui-script src\downloader\app\gestor.pyw` con
   `WScript.Shell.Run(..., 0, False)`. El estilo 0 oculta la consola de `uv.exe`, y
   `--gui-script` hace que uv use `pythonw.exe`, que no crea consola. No queda ninguna
   ventana de consola visible en ningún momento (SC-001).

`uv run` sincroniza el entorno si `uv.lock` cambió, como en la CLI. La primera apertura tras
un `git pull` puede tardar más que los 5 s de SC-001, que se mide con el entorno ya
sincronizado (quickstart.md).

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

**Limitación [a verificar en Windows]**: Windows puede negar traer una ventana al frente si la
petición no viene del proceso en primer plano; en ese caso parpadea en la barra de tareas. Se
acepta: lo importante es que no se abre una segunda ventana.

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

**Estimación [a verificar]**:

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
   qué valor prevalece **[a verificar]**;
3. que la ventana no lance `uv run` como padre persistente (usar el respaldo `gui-scripts`);
4. si nada basta, plan B con tkinter (R1), que se plantearía al usuario antes de cambiar la
   decisión.

## R15. La ventana no carga yt-dlp

**Hallazgo**: `errors.py` importa `yt_dlp` en el nivel del módulo, y `store.py` importa
`errors` (por `ErrorFatal`). Abrir la cola desde la ventana cargaría yt-dlp entero: entre 20 y
30 MB de memoria y tiempo de arranque (SC-001) **[a verificar]**.

**Decisión**: separar lo que la ventana necesita en módulos sin yt-dlp:
- `ErrorFatal` y `ErrorPermanente` pasan a `excepciones.py`;
- `ocultar_secretos` y los mensajes de consejo (`CONSEJO_SESION`, el de FR-028) pasan a
  `secretos.py` y `mensajes.py`;
- `NAVEGADORES` se fija como constante en `models.py`, y un test comprueba que coincide con
  `yt_dlp.cookies.SUPPORTED_BROWSERS`.

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
  - `ventana`: tests de la capa de Windows, que se omiten en Linux con el motivo;
  - `pywebview_internals`: comprueba los internos de pywebview (R2).
- El CI no cambia de estructura: la misma matriz Linux y Windows. En Windows se ejecutan
  además los tests `ventana`. Los runners de GitHub Actions no garantizan un escritorio
  interactivo, así que los tests `ventana` no abren una ventana real: comprueban la detección
  de WebView2, el lanzador (R11) y el enganche de eventos con objetos simulados. La ventana
  real, el doble clic y la maqueta se validan a mano (quickstart.md).
- La interfaz web (`app.js`) se mantiene delgada: pinta lo que devuelve la API y envía
  órdenes. Toda la lógica (validación, decisiones de cierre, formato de cifras, permisos de
  Detener) está en Python y tiene tests. No se añade un navegador de pruebas al CI.
