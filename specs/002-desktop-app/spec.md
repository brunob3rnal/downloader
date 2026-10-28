# Feature Specification: Aplicación de escritorio

**Feature Branch**: `ccr-cd6ebf2c-cwltk0`. El directorio de la feature se fija con `SPECIFY_FEATURE_DIRECTORY` (`.specify/feature.json`), como en la feature 001

**Created**: 2026-10-27

**Status**: Draft

**Input**: User description: "Aplicación de escritorio para Windows 10/11 sobre el gestor de descargas existente, como segunda feature (la CLI no cambia y sigue funcionando igual). Se abre con doble clic en un lanzador incluido en el repositorio (sin consola visible) y muestra una ventana propia, sin terminal ni navegador externo. Funciones: pegar una o varias URLs (una por línea), elegir calidad (mejor, 1080p, 720p, solo audio) y carpeta de destino; ver la cola con estado, porcentaje, velocidad, ETA y último aviso o error; iniciar el procesamiento y detenerlo. Detener equivale al Ctrl+C actual: termina de forma ordenada y la cola queda lista para reanudar. No reimplementa la lógica: usa la misma cola y el mismo trabajador que la CLI. Nunca puede haber dos procesadores de la cola a la vez. Si ya hay uno en marcha (lanzado desde la CLI o desde una sesión anterior de la aplicación), la aplicación lo detecta, muestra la cola en directo y no lanza otro. Al cerrar la ventana con descargas en curso, pregunta si seguir en segundo plano, pausar o cancelar el cierre. Pausar deja la cola lista para reanudar. Si se eligió seguir en segundo plano, al volver a abrir la aplicación esta se reconecta a esa ejecución y permite verla y detenerla. Cookies del navegador: opción explícita y desactivada por defecto, con selección de navegador y la advertencia actual (Firefox fiable, Edge solo cerrado, Chrome 127+ no funciona). No se guardan. Una descarga fallida por requerir sesión ofrece reintentar con cookies. Seguridad: la interfaz solo acepta conexiones locales del propio equipo, nunca de la red. Rendimiento: debe funcionar con poca memoria (equipo de 4 GB de RAM); fijar un criterio medible en la fase de clarificación. Fuera de alcance: acceso remoto, varios usuarios, descargas simultáneas, instalador y .exe (etapa 2, spec aparte)."

## Clarifications

### Session 2026-10-27

- Q: ¿La aplicación puede detener un `ejecutar` lanzado desde una terminal? → A: No. Solo detiene los procesamientos que lanzó ella misma, también los que dejó en segundo plano. Un `ejecutar` de la CLI se observa en directo desde la ventana y se detiene desde su terminal (FR-009, US3).
- Q: ¿La aplicación debe funcionar también en Linux? → A: Solo la ventana y el lanzador son exclusivos de Windows 10/11, mediante una enmienda de la sección Plataformas de la constitución limitada a ellos. La lógica de la aplicación sigue exigiendo funcionar y probarse en Linux y en Windows (FR-019).
- Q: ¿Cuál es el criterio de memoria? → A: 300 MB como máximo con 500 descargas en cola y una en curso, sumando todos los procesos de la aplicación: la ventana, lo que necesita para mostrarse y el procesador de la cola (SC-005).

### Session 2026-10-28

- Q: ¿La interfaz debe aceptar órdenes solo de la propia ventana de la aplicación, o de cualquier programa del mismo equipo? → A: Solo de la propia ventana. Cualquier otro programa del equipo, incluidas las páginas web abiertas en un navegador, se rechaza tanto al consultar como al dar órdenes, y ese rechazo se comprueba con tests (FR-013, SC-007).
- Q: Cuando las descargas siguen en segundo plano sin ventana, ¿cómo se entera el usuario de un error fatal o de un fallo inesperado del procesamiento? → A: Se guarda el resultado de la última ejecución lanzada por la aplicación (resumen y, si lo hubo, el error fatal o inesperado con su mensaje) y la ventana lo muestra al abrirse. Además, la aplicación escribe un registro de errores en la carpeta de configuración, de tamaño acotado y con los secretos ocultos (FR-012, FR-020).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agregar URLs y descargar desde una ventana (Priority: P1)

El usuario hace doble clic en el lanzador del repositorio y se abre una ventana propia, sin consola ni navegador. Pega una o varias URLs, una por línea, elige la calidad y la carpeta de destino, las agrega a la cola y pulsa Iniciar. La ventana muestra cada descarga con su estado, porcentaje, velocidad, tiempo estimado y último aviso o error, hasta que la cola se vacía.

**Why this priority**: Es el valor central de la feature: usar el gestor de descargas sin abrir una terminal.

**Independent Test**: Con la cola vacía, abrir la aplicación con doble clic, pegar dos URLs de un servidor de prueba local con calidad 720p, agregarlas e iniciar; comprobar que no aparece ninguna consola, que la ventana muestra el progreso de cada una y que ambos archivos completos aparecen en la carpeta elegida.

**Acceptance Scenarios**:

1. **Given** la aplicación cerrada, **When** el usuario hace doble clic en el lanzador, **Then** se abre la ventana de la aplicación y no aparece ninguna ventana de consola ni de navegador.
2. **Given** la ventana abierta, **When** el usuario pega tres URLs en líneas distintas (con líneas vacías entre ellas), elige "solo audio" y una carpeta, y pulsa Agregar, **Then** las tres aparecen en la cola con esa calidad y esa carpeta, y las líneas vacías se ignoran.
3. **Given** URLs en cola y ningún procesador en marcha, **When** el usuario pulsa Iniciar, **Then** las descargas avanzan una a una y cada una muestra estado, porcentaje, velocidad, tiempo estimado y último aviso o error.
4. **Given** un procesamiento en marcha, **When** el usuario agrega más URLs, **Then** se incorporan a la cola y se procesan en la misma ejecución, sin pulsar Iniciar otra vez.
5. **Given** un procesamiento en marcha, **When** la cola se vacía, **Then** el procesamiento termina y la ventana muestra el resultado: cuántas se completaron y cuántas fallaron, con la causa de cada fallo.
6. **Given** una URL que ya está en la cola, **When** el usuario la vuelve a agregar, **Then** no se duplica, igual que con `agregar` en la CLI.

---

### User Story 2 - Detener y reanudar (Priority: P1)

El usuario pulsa Detener durante una descarga. El procesamiento termina de forma ordenada, como con Ctrl+C en la CLI: se conserva el progreso y la cola queda lista para reanudar. Al pulsar Iniciar de nuevo, o al lanzar `ejecutar` desde la CLI, la descarga continúa donde se quedó.

**Why this priority**: Sin una forma segura de parar, el usuario cerraría el proceso a la fuerza; el Principio IV de la constitución exige no perder progreso reanudable.

**Independent Test**: Iniciar una descarga grande desde la aplicación, pulsar Detener a mitad, comprobar que el procesamiento termina y que el progreso guardado se conserva; pulsar Iniciar y comprobar que reanuda desde ese punto y no desde cero.

**Acceptance Scenarios**:

1. **Given** una descarga al 40 %, **When** el usuario pulsa Detener, **Then** la ventana indica que se está deteniendo, el procesamiento termina sin perder progreso y la descarga queda pendiente en la cola.
2. **Given** una descarga detenida desde la aplicación, **When** el usuario pulsa Iniciar o lanza `ejecutar` en la CLI, **Then** la descarga se reanuda desde el progreso guardado.
3. **Given** una descarga en "esperando red", **When** el usuario pulsa Detener, **Then** el procesamiento termina sin esperar a que vuelva la conexión.

---

### User Story 3 - Un único procesador de la cola (Priority: P1)

Si ya hay un procesador de la cola en marcha, lanzado desde la CLI o por una sesión anterior de la aplicación, la aplicación lo detecta al abrirse, muestra la cola en directo y no lanza otro. Mientras la aplicación procesa la cola, un `ejecutar` lanzado desde la CLI se niega como hoy.

**Why this priority**: Dos procesadores sobre la misma cola corromperían las descargas en curso; es una garantía que la CLI ya ofrece y que la aplicación no puede romper.

**Independent Test**: Lanzar `ejecutar` en una terminal con descargas en cola, abrir la aplicación y comprobar que muestra el progreso en directo, que Iniciar no lanza un segundo procesador y que el progreso avanza igual que en la terminal.

**Acceptance Scenarios**:

1. **Given** un `ejecutar` de la CLI en marcha, **When** el usuario abre la aplicación, **Then** la ventana indica que hay un procesamiento en marcha, de dónde viene (CLI o aplicación) y muestra la cola actualizándose en directo.
2. **Given** un procesador ajeno en marcha, **When** el usuario intenta iniciar el procesamiento, **Then** no se lanza otro y la ventana explica por qué.
3. **Given** el procesamiento iniciado desde la aplicación, **When** el usuario lanza `ejecutar` en una terminal, **Then** `ejecutar` sale con el código 2 sin procesar nada, igual que hoy.
4. **Given** un procesador ajeno que termina mientras la ventana está abierta, **When** termina, **Then** la ventana lo refleja en 2 segundos como máximo y vuelve a permitir Iniciar.
5. **Given** un `ejecutar` de la CLI en marcha y la aplicación abierta, **When** el usuario pulsa Detener, **Then** la aplicación no lo detiene y explica que un procesamiento de la CLI se detiene desde su terminal con Ctrl+C.

---

### User Story 4 - Cerrar la ventana y seguir en segundo plano (Priority: P2)

Al cerrar la ventana con un procesamiento lanzado por la aplicación en marcha, se pregunta qué hacer: seguir en segundo plano, pausar o cancelar el cierre. Si elige seguir, las descargas continúan sin ninguna ventana visible; al abrir la aplicación de nuevo, esta se reconecta a esa ejecución y permite verla y detenerla.

**Why this priority**: Permite dejar descargas largas en marcha sin ocupar la pantalla, pero el uso básico (US1-US3) funciona sin ello.

**Independent Test**: Iniciar una descarga, cerrar la ventana eligiendo seguir en segundo plano, comprobar que la descarga avanza sin ventana ni consola visibles; abrir la aplicación con doble clic y comprobar que muestra esa ejecución en directo y que Detener la termina de forma ordenada.

**Acceptance Scenarios**:

1. **Given** un procesamiento lanzado por la aplicación en marcha, **When** el usuario cierra la ventana, **Then** se le pregunta si seguir en segundo plano, pausar o cancelar el cierre.
2. **Given** ese diálogo, **When** elige seguir en segundo plano, **Then** la ventana se cierra, las descargas continúan y no queda visible ninguna ventana ni consola.
3. **Given** ese diálogo, **When** elige pausar, **Then** el procesamiento se detiene como en US2 y la ventana se cierra cuando ha terminado de detenerse, con la cola lista para reanudar.
4. **Given** ese diálogo, **When** elige cancelar el cierre, **Then** la ventana sigue abierta y el procesamiento no se altera.
5. **Given** un procesamiento en segundo plano, **When** el usuario abre la aplicación, **Then** esta se reconecta, muestra la cola en directo y permite detenerlo.
6. **Given** la aplicación sin procesamiento en marcha, **When** el usuario cierra la ventana, **Then** se cierra sin preguntar.
7. **Given** un procesamiento en segundo plano que vacía la cola con la ventana cerrada, **When** el usuario abre la aplicación, **Then** ve el estado final de cada descarga y el resultado de esa ejecución, y puede iniciar de nuevo.
8. **Given** un procesamiento en segundo plano que se detiene por un error fatal (por ejemplo, disco lleno) o por un fallo inesperado del programa, **When** el usuario abre la aplicación, **Then** ve el mensaje de ese error, y el error consta en el registro de errores.

---

### User Story 5 - Cookies del navegador y reintento con sesión (Priority: P3)

Para videos que exigen la sesión del usuario, este activa de forma explícita el uso de cookies y elige el navegador. La ventana muestra la misma advertencia que la documentación de la CLI. Si una descarga falla porque requiere sesión, la ventana ofrece reintentarla con cookies.

**Why this priority**: Solo hace falta para una parte de los sitios; el resto de la aplicación funciona sin ello.

**Independent Test**: Con un servidor de prueba que exige una cookie, agregar la URL sin cookies, comprobar que falla con la oferta de reintentar con cookies; reintentar con un navegador de prueba y comprobar que se descarga, y que ninguna cookie queda guardada en disco por la aplicación.

**Acceptance Scenarios**:

1. **Given** la ventana recién abierta, **When** el usuario mira las opciones, **Then** el uso de cookies está desactivado.
2. **Given** el uso de cookies activado, **When** el usuario elige el navegador, **Then** ve la advertencia: Firefox es fiable; Edge solo funciona cerrado del todo; Chrome 127 o posterior no funciona.
3. **Given** cookies activadas con Firefox, **When** el usuario agrega URLs, **Then** esas descargas usan las cookies de Firefox, leídas en el momento de descargar, igual que `agregar --cookies-de firefox`.
4. **Given** una descarga fallida por requerir sesión, **When** el usuario la mira en la cola, **Then** se le ofrece reintentarla con cookies eligiendo el navegador, y al aceptar vuelve a la cola con ese navegador.
5. **Given** las cookies de un navegador que no se pueden leer, **When** se intenta la descarga, **Then** falla solo esa descarga con el mensaje que nombra el navegador, la causa y qué hacer (FR-029 de la feature 001).
6. **Given** cookies activadas en una sesión, **When** el usuario cierra y vuelve a abrir la aplicación, **Then** el uso de cookies vuelve a estar desactivado.

---

### Edge Cases

- **Texto pegado que no es una URL**: cada línea que no sea una URL válida se señala con su número de línea y no se agrega; las demás sí se agregan.
- **Carpeta de destino inexistente o sin permiso de escritura**: se rechaza al agregar con un mensaje claro, como `agregar` con una carpeta no válida; no se agrega ninguna URL de ese envío.
- **ffmpeg o ffprobe ausentes, disco lleno o cola corrupta**: el procesamiento no arranca o se detiene con el mismo mensaje que da la CLI (código 3), conservando el progreso; en la cola corrupta se guarda la copia como hoy. Si ocurre en segundo plano, el mensaje se ve al abrir la aplicación (FR-012).
- **Fallo inesperado del programa sin ventana abierta**: no se pierde; queda en el resultado de la última ejecución y en el registro de errores (FR-012, FR-020).
- **Registro de errores que no se puede escribir** (disco lleno, sin permiso): la aplicación sigue funcionando y la ventana avisa de que el registro no está disponible.
- **Procesador terminado de forma abrupta** (cierre forzado del proceso, corte de luz): la aplicación deja de mostrarlo como en marcha en 2 segundos como máximo; al iniciar de nuevo, la cola se recupera igual que tras un cierre abrupto de `ejecutar`.
- **Reinicio del equipo con un procesamiento en segundo plano**: el procesamiento no se relanza solo; al abrir la aplicación, la cola muestra las descargas pendientes y el usuario pulsa Iniciar.
- **Segundo doble clic con la ventana ya abierta**: no se abre una segunda ventana independiente; se muestra la existente.
- **Cerrar la ventana mientras procesa un `ejecutar` de la CLI**: se cierra sin preguntar; la ejecución de la CLI no se ve afectada.
- **Detener cuando el procesador ya está terminando o terminó**: no produce error; la ventana refleja el estado real.
- **Cola con cientos de descargas** (por ejemplo, una lista de reproducción grande): la ventana sigue respondiendo y dentro del límite de memoria de SC-005.
- **URL con tokens o firmas**: la ventana la muestra con los secretos ocultos, igual que `estado`.
- **Lista de reproducción**: se expande en sus videos al procesarse, como en la CLI; la ventana muestra cada video como una descarga.
- **Un programa de otro equipo intenta conectarse a la interfaz**: la conexión se rechaza siempre, también con el cortafuegos de Windows desactivado (FR-013).
- **Otro programa del propio equipo o una página web abierta en el navegador envía peticiones a la interfaz**: se rechazan sin agregar URLs, iniciar, detener ni mostrar la cola (FR-013).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La aplicación MUST abrirse con doble clic en un lanzador incluido en el repositorio, sin mostrar ninguna ventana de consola, y MUST mostrar su interfaz en una ventana propia, sin terminal ni navegador externo.
- **FR-002**: La aplicación MUST permitir pegar una o varias URLs, una por línea, ignorar las líneas vacías, señalar con su número cada línea que no sea una URL válida y agregar las demás a la cola.
- **FR-003**: La aplicación MUST permitir elegir la calidad (mejor, 1080p, 720p o solo audio) y la carpeta de destino de las URLs que se agregan. Los valores iniciales MUST ser los de la configuración de la feature 001 (`config.toml`) o, sin ella, los mismos valores por defecto que la CLI.
- **FR-004**: Agregar desde la aplicación MUST producir en la cola el mismo resultado que `agregar` con las mismas URLs y opciones: sin duplicar URLs que ya están en cola y devolviendo a la cola las que fallaron.
- **FR-005**: La aplicación MUST mostrar cada descarga de la cola con su estado, porcentaje, velocidad, tiempo estimado restante y último aviso o error, con los secretos ocultos (FR-017 de la feature 001), actualizados al menos cada 2 segundos, también cuando el procesador lo lanzó la CLI.
- **FR-006**: La aplicación MUST permitir iniciar el procesamiento de la cola cuando no hay ningún procesador en marcha. El procesamiento MUST comportarse como `ejecutar`: procesa una descarga a la vez, recoge las URLs que se agreguen mientras corre y termina cuando no quedan pendientes.
- **FR-007**: La aplicación MUST NOT reimplementar la lógica de descarga: MUST usar la misma cola persistente y el mismo trabajador que la CLI, de modo que todo lo garantizado por la feature 001 (reanudación, reintentos, verificación, publicación, rechazo de DRM) valga igual.
- **FR-008**: Nunca MUST haber dos procesadores de la misma cola a la vez, sea cual sea su origen (CLI, aplicación o una sesión anterior de la aplicación). Al abrirse y antes de iniciar, la aplicación MUST detectar un procesador en marcha; si lo hay, MUST mostrar la cola en directo e indicar su origen, y MUST NOT lanzar otro.
- **FR-009**: Detener MUST equivaler al Ctrl+C de `ejecutar`: termina de forma ordenada, conserva el progreso y deja la cola lista para reanudar desde la aplicación o desde la CLI. La ventana MUST indicar que se está deteniendo hasta que el procesamiento haya terminado. La aplicación MUST permitir detener solo los procesamientos que lanzó ella misma, también desde una sesión anterior; MUST NOT detener un `ejecutar` lanzado desde la CLI, y MUST indicar que ese se detiene desde su terminal.
- **FR-010**: Al cerrar la ventana con un procesamiento lanzado por la aplicación en marcha, la aplicación MUST preguntar si seguir en segundo plano, pausar o cancelar el cierre. Seguir MUST dejar el procesamiento en marcha sin ventana ni consola visibles; pausar MUST detenerlo como en FR-009 y cerrar la ventana al terminar; cancelar MUST dejarlo todo como estaba. Sin procesamiento de la aplicación en marcha, la ventana MUST cerrarse sin preguntar.
- **FR-011**: Al abrirse con un procesamiento en segundo plano en marcha, la aplicación MUST reconectarse a él, mostrar la cola en directo y permitir detenerlo.
- **FR-012**: Al terminar un procesamiento, la ventana MUST mostrar cuántas descargas se completaron y cuántas fallaron, con la causa de cada fallo, y los errores fatales con el mismo mensaje que da la CLI. El resultado de la última ejecución lanzada por la aplicación (ese resumen y, si lo hubo, el error fatal o inesperado con su mensaje) MUST conservarse aunque la ventana esté cerrada, y la ventana MUST mostrarlo al abrirse hasta que se inicie otra ejecución.
- **FR-013**: La interfaz MUST aceptar solo conexiones del propio equipo y MUST rechazar cualquier conexión desde la red, en todas las configuraciones de red del equipo. Dentro del propio equipo, MUST atender solo a la ventana de la aplicación: MUST rechazar, sin ejecutar ninguna orden ni revelar el estado de la cola, cualquier petición de otro programa, incluidas las páginas web abiertas en un navegador.
- **FR-014**: El uso de cookies del navegador MUST ser una opción explícita, desactivada al abrir la aplicación, que obliga a elegir un navegador y muestra la advertencia de la documentación de la CLI (Firefox fiable; Edge solo cerrado del todo; Chrome 127 o posterior no funciona). Las URLs agregadas con la opción activa MUST comportarse como `agregar --cookies-de NAVEGADOR`.
- **FR-015**: La aplicación MUST NOT guardar cookies ni recordar entre sesiones si el uso de cookies estaba activado; la cola guarda solo el nombre del navegador de cada descarga, como hoy (FR-017 de la feature 001).
- **FR-016**: Una descarga fallida por requerir sesión (acceso denegado, contenido que exige iniciar sesión, o la lista de reproducción sin videos de FR-028 de la feature 001) MUST ofrecer reintentarla con cookies eligiendo el navegador; al aceptar, MUST volver a la cola con ese navegador.
- **FR-017**: La CLI MUST seguir funcionando igual: mismos comandos, opciones, salidas y códigos de salida. La aplicación MUST funcionar sobre la misma cola que la CLI, de modo que lo agregado desde una se vea y se procese desde la otra.
- **FR-018**: Una segunda apertura de la aplicación con la ventana ya abierta MUST mostrar la ventana existente en lugar de abrir otra independiente.
- **FR-019**: La ventana y el lanzador sin consola MUST funcionar en Windows 10/11; en Linux no se exigen. El resto de la aplicación (agregar, iniciar, detener, detección del procesador en marcha, segundo plano y reconexión, cookies, rechazo de conexiones desde la red) MUST funcionar y tener tests automatizados en Linux y en Windows, sin depender de la ventana. Esta excepción se apoya en la sección Plataformas de la constitución v3.1.0 (enmendada el 2026-10-28), que la limita a la ventana y el lanzador.
- **FR-020**: La aplicación MUST escribir un registro de errores en la carpeta de configuración de la feature 001 con los errores fatales y los fallos inesperados de la ventana y del procesamiento que lanza, con fecha, qué falló y el detalle técnico. MUST ocultar en él tokens, firmas y cookies (FR-017 de la feature 001) y MUST NOT ocupar más de 2 MB en total: al llenarse, se descartan las entradas más antiguas.

### Key Entities

- **Cola**: la de la feature 001, compartida sin cambios entre la CLI y la aplicación.
- **Descarga**: la de la feature 001; la aplicación muestra además su velocidad y tiempo estimado, también cuando la procesa la CLI.
- **Procesador**: la ejecución que procesa la cola (un `ejecutar` de la CLI o uno lanzado por la aplicación). Atributos visibles: origen (CLI o aplicación), en marcha, deteniéndose o terminado. Como mucho hay uno a la vez.
- **Resultado de la ejecución**: resumen de la última ejecución lanzada por la aplicación (completadas, fallidas y, si lo hubo, el error fatal o inesperado con su mensaje); persiste con la ventana cerrada.
- **Registro de errores**: archivo en la carpeta de configuración con los errores fatales e inesperados, acotado a 2 MB y sin secretos.
- **Envío de URLs**: el texto pegado más la calidad, la carpeta y, si se activó, el navegador de cookies; produce el mismo efecto que un `agregar`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Desde el doble clic en el lanzador hasta la ventana lista para pegar URLs pasan como mucho 5 segundos en el equipo de referencia (4 GB de RAM, Windows 10/11), sin que aparezca ninguna consola en ningún momento.
- **SC-002**: Un usuario sin conocimientos de terminal agrega una URL y empieza a descargarla en menos de 1 minuto y con 3 acciones como máximo (pegar, agregar, iniciar).
- **SC-003**: En 0 casos hay dos procesadores de la cola a la vez, en cualquier combinación de aperturas de la aplicación, cierres en segundo plano y `ejecutar` de la CLI.
- **SC-004**: Tras Detener o Pausar, el procesamiento termina en 10 segundos como máximo, y el 100 % de las descargas detenidas se reanuda desde su progreso guardado, dentro de los límites de SC-002 de la feature 001 para Ctrl+C.
- **SC-005**: Con 500 descargas en cola y una en curso, la memoria usada por todos los procesos de la aplicación juntos (la ventana, lo que necesita para mostrarse y el procesador de la cola) no supera en ningún momento 300 MB en el equipo de referencia de 4 GB de RAM.
- **SC-006**: El estado mostrado en la ventana (porcentaje, velocidad, tiempo estimado, estado) no va más de 2 segundos por detrás del procesador, sea de la aplicación o de la CLI.
- **SC-007**: El 100 % de los intentos de acceso a la interfaz desde otro equipo, desde otro programa del propio equipo o desde una página web abierta en el navegador se rechazan sin efecto sobre la cola ni revelar su contenido.
- **SC-008**: Tras reabrir la aplicación con un procesamiento en segundo plano, la ventana lo muestra en directo en 5 segundos como máximo, sin acción del usuario.
- **SC-009**: Los tests de la CLI de la feature 001 pasan sin modificaciones.

## Assumptions

- La sección Plataformas de la constitución v3.1.0 (enmendada el 2026-10-28) admite la ventana y el lanzador solo en Windows (FR-019); el CI de Linux sigue ejecutando los tests de la CLI y de la lógica de la aplicación.

- Los usuarios trabajan en un solo equipo y con una sola cuenta: acceso remoto y varios usuarios están fuera de alcance.
- El lanzador se ejecuta desde un clon del repositorio ya instalado según el README (uv, ffmpeg y ffprobe); instalador y ejecutable propio quedan para la etapa 2, con su propia especificación.
- Se procesa una sola descarga a la vez, como en la CLI; las descargas simultáneas quedan fuera de alcance.
- El procesamiento en segundo plano no sobrevive a un reinicio del equipo ni se relanza solo al iniciar sesión; el usuario abre la aplicación y pulsa Iniciar, igual que relanza `ejecutar` en la CLI.
- Para mostrar velocidad y tiempo estimado de un procesador lanzado desde la CLI, el trabajador puede necesitar publicar esos datos además del progreso; eso es un cambio interno que no altera el comportamiento visible de la CLI (FR-017).
- El límite de 2 MB del registro de errores (FR-020) es un valor por defecto razonable para un equipo de 4 GB; no es configurable.
- La ventana de la aplicación sigue el idioma de la CLI (español).
- Mientras la ventana está cerrada no hay icono en la bandeja del sistema ni notificaciones; reabrir la aplicación es la forma de ver el estado.
- Borrar descargas de la cola, reordenarla o cambiar la calidad de una ya agregada quedan fuera de alcance: la CLI tampoco lo permite.
- La actualización de yt-dlp sigue haciéndose con `actualizar` de la CLI.
