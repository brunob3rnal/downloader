# Feature Specification: Gestor de descargas de video resiliente

**Feature Branch**: `main`. No existe una rama `001-resilient-video-downloader`: la feature se integra en `main` mediante PRs desde ramas de trabajo `claude/*`, y su directorio se fija con `SPECIFY_FEATURE_DIRECTORY` (`.specify/feature.json`)

**Created**: 2026-10-20

**Status**: Draft

**Input**: User description: "Un gestor de descargas de video que recibe URLs y las descarga de forma confiable, aunque la conexión se corte. Cola persistente, calidades (mejor, 1080p, 720p, solo audio), listas de reproducción, progreso, carpeta configurable, reanudación automática, reintentos, renovación de URLs caducadas, nunca entregar archivos incompletos como terminados. Fuera de alcance: DRM y transmisiones en vivo. Primera versión: solo CLI."

## Clarifications

### Session 2026-10-20

- Q: ¿Cómo se ejecuta el programa: primer plano que termina al vaciar la cola, o servicio en segundo plano? → A: Primer plano. `agregar` guarda URLs en la cola; `ejecutar` procesa la cola y solo termina cuando no queda ninguna descarga pendiente. Las descargas en "esperando red" cuentan como pendientes, por lo que el programa sigue corriendo y reintentando hasta que vuelva la conexión. Termina únicamente con la cola completada o con fallos permanentes. Tras un reinicio del equipo, el usuario relanza `ejecutar`. *(Precisado después en FR-021: también se detiene, con el progreso conservado y un código de salida propio, por interrupción del usuario (130), error fatal del entorno (3) o uso inválido u otro `ejecutar` en marcha (2).)*
- Q: ¿Qué hacer si en la carpeta de destino ya existe un archivo con el mismo nombre? → A: Conservar ambos; el archivo nuevo se publica con un sufijo numérico (`video (1).mp4`) y se avisa al usuario. Nunca se sobrescribe.
- Q: ¿Cómo comprobar que un video terminó completo antes de publicarlo? → A: El tamaño final coincide con el anunciado por el servidor (cuando lo informa) y el archivo es legible hasta el final tras unir audio y video. Si falla la comprobación, no se publica con su nombre final.
- Q: ¿Cuál es la espera máxima entre reintentos de un error temporal? → A: 5 minutos.
- Q: ¿Cómo se activa el uso de cookies del navegador? → A: Con una opción explícita que nombra el navegador (por ejemplo `--cookies-de firefox`) en `agregar` o `ejecutar`; sin ella, nunca se leen cookies.

### Session 2026-10-21

- Q: ¿Un acceso denegado (HTTP 403 o 410) es temporal o permanente? → A: Depende de la fase. Al obtener la información del video, es permanente. Durante la transferencia, se trata como URL caducada y se reintenta obteniendo una URL nueva (FR-012), pero con un tope: tras 3 intentos seguidos que terminan así sin descargar ningún byte nuevo, la descarga falla con un mensaje que sugiere `--cookies-de` (FR-026).
- Q: ¿Qué protocolos de transmisión cubre la primera versión? → A: HTTP(S) directo y HLS/DASH por HTTP. RTMP y WebSocket quedan fuera de alcance.

### Session 2026-10-22

- Q: ¿Cuánto exceso de transferencia se admite tras una interrupción abrupta del proceso (SIGKILL, corte de luz), que no se puede capturar? → A: El 1 % del tamaño del archivo más una tolerancia fija de 128 KiB (enmienda de SC-002, research R20). Tras un corte de red controlado el límite sigue siendo el 1 % estricto.

### Session 2026-10-24

- Q: ¿Cómo se evita que `actualizar` instale una versión de yt-dlp que rompa el programa? → A: Tras actualizar, ejecuta los tests que dependen del comportamiento interno de yt-dlp; si fallan, vuelve a la versión anterior y explica qué falló (enmienda de FR-025).
- Q: ¿Qué nombre llevan los archivos publicados? → A: Una plantilla configurable; por defecto, solo el título del video, sin su identificador. Dos videos con el mismo título se publican sin pisarse, con sufijo numérico (FR-022). En Windows el título se recorta para que la ruta completa no pase de 240 caracteres.
- Q: ¿Qué pasa con una lista de reproducción sin videos? → A: Falla como error permanente con un mensaje que sugiere que la URL puede no estar soportada o requerir sesión; no se da por completada.
- Q: ¿Descifrar un HLS con AES-128 es eludir una protección técnica? → A: No. El cifrado de transporte estándar (clave obtenida por HTTP(S) desde la URI del manifiesto, con `KEYFORMAT="identity"` o sin `KEYFORMAT`, y con la misma sesión del usuario) se descarga y descifra. Cualquier otra entrega de la clave (`skd://`, PSSH incrustado, otro `KEYFORMAT`) se rechaza como DRM (constitución v3.0.0, Principio V; FR-015; research R5).
- Q: ¿SC-004 admite alguna excepción? → A: Sí, la misma que ya recoge Edge Cases: un HLS con un fragmento truncado en el servidor de origen (enmienda de SC-004, research R22).
- Q: ¿El 1 % de SC-002 es por reanudación o acumulado, y sobre qué se mide? → A: Acumulado en todas las reanudaciones de una descarga y medido sobre el tamaño del archivo, para archivos de al menos 1 MB. Se conserva la tolerancia de 128 KiB para interrupciones abruptas (enmienda de SC-002).
- Q: ¿Cómo consulta el usuario el estado de la cola fuera de `ejecutar`? → A: Con un comando `estado`, de solo lectura, que no toma el bloqueo de `ejecutar` y muestra por descarga id, estado, progreso, título y último aviso o error; sale con 3 si `cola.db` está corrupta (FR-032).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Descargar videos desde una cola (Priority: P1)

El usuario agrega una o varias URLs a la cola, elige la calidad (mejor, 1080p, 720p o solo audio) y el programa las descarga una tras otra en la carpeta de destino, mostrando progreso, velocidad y tiempo estimado de cada descarga.

**Why this priority**: Es el valor central del producto. Sin descargas básicas funcionando, nada más tiene sentido.

**Independent Test**: Agregar dos URLs de videos públicos con calidad 720p y verificar que ambos archivos completos aparecen en la carpeta de destino y que durante la descarga se muestran progreso, velocidad y tiempo estimado.

**Acceptance Scenarios**:

1. **Given** una cola vacía, **When** el usuario agrega una URL válida con calidad "mejor", **Then** el video se descarga completo en la carpeta configurada.
2. **Given** varias URLs en la cola, **When** el programa las procesa, **Then** cada una se descarga y se informa su estado individual (en cola, descargando, esperando red, completada, fallida).
3. **Given** una descarga en curso, **When** el usuario consulta el estado, **Then** ve el porcentaje, la velocidad y el tiempo estimado restante de esa descarga.
4. **Given** el usuario elige "solo audio", **When** termina la descarga, **Then** el archivo resultante contiene únicamente audio.
5. **Given** la URL de una lista de reproducción en la cola, **When** se procesa, **Then** todos sus videos se incorporan a la cola como descargas individuales.
6. **Given** una cola con descargas en distintos estados, con o sin `ejecutar` en marcha, **When** el usuario ejecuta `estado`, **Then** ve cada descarga con su id, estado, progreso, título y último aviso o error, sin que la cola cambie.

---

### User Story 2 - Sobrevivir a cortes de red (Priority: P1)

Si se pierde la conexión durante una descarga, esta pasa a "esperando red" y se reanuda sola cuando la conexión vuelve, continuando desde donde quedó y no desde cero.

**Why this priority**: Es el requisito principal declarado del producto.

**Independent Test**: Iniciar la descarga de un video grande, cortar la conexión a mitad, comprobar que el estado cambia a "esperando red", restaurar la conexión y verificar que la descarga continúa desde el punto alcanzado y termina con el archivo íntegro.

**Acceptance Scenarios**:

1. **Given** una descarga al 40 %, **When** se pierde la conexión, **Then** el estado pasa a "esperando red" y se conserva el 40 % descargado.
2. **Given** una descarga en "esperando red", **When** la conexión vuelve, **Then** se reanuda automáticamente sin intervención del usuario y sin volver a descargar lo ya obtenido.
3. **Given** cortes repetidos durante la misma descarga, **When** la conexión vuelve cada vez, **Then** la descarga termina con un archivo completo e íntegro.
4. **Given** un error temporal (red, timeout), **When** ocurre, **Then** se reintenta indefinidamente con esperas cada vez mayores hasta un máximo de 5 minutos entre intentos.

---

### User Story 3 - Cola que sobrevive a cierres y reinicios (Priority: P1)

La cola y el progreso parcial se conservan si el programa se cierra, falla o el equipo se reinicia. Al abrir el programa de nuevo, las descargas pendientes continúan.

**Why this priority**: Sin persistencia, un cierre inesperado obliga a empezar de cero y rompe la promesa de confiabilidad.

**Independent Test**: Con varias descargas en cola y una en curso, terminar el proceso de forma abrupta, volver a abrir el programa y verificar que la cola se restaura y las descargas pendientes continúan desde su progreso.

**Acceptance Scenarios**:

1. **Given** una cola con descargas pendientes, **When** el programa se cierra abruptamente y se vuelve a abrir, **Then** la cola se restaura completa con su estado.
2. **Given** una descarga a medias al momento del cierre, **When** se reabre el programa, **Then** continúa desde el progreso guardado.
3. **Given** descargas ya completadas antes del cierre, **When** se reabre el programa, **Then** no se vuelven a descargar.

---

### User Story 4 - Errores permanentes claros y URLs caducadas (Priority: P2)

Los errores permanentes (video borrado, privado, sitio no soportado) hacen fallar la descarga con un mensaje claro y sin reintentos. Si la URL temporal del video caduca durante la descarga, el programa obtiene una nueva y continúa desde donde quedó.

**Why this priority**: Evita reintentos inútiles y cubre un fallo común en descargas largas, pero depende de que las descargas básicas y la reanudación ya funcionen.

**Independent Test**: Agregar una URL de un video eliminado y comprobar que falla con mensaje claro sin reintentar; simular la caducidad de la URL de medios a mitad de descarga y comprobar que continúa desde el mismo punto con una URL nueva.

**Acceptance Scenarios**:

1. **Given** una URL de un video borrado o privado, **When** se intenta descargar, **Then** la descarga se marca como fallida con un mensaje que indica la causa y no se reintenta.
2. **Given** una URL de un sitio no soportado, **When** se procesa, **Then** el usuario recibe un mensaje claro indicando que el sitio no es compatible.
3. **Given** una descarga en curso cuya URL temporal caduca, **When** el servidor rechaza la petición por esa causa, **Then** se obtiene una URL nueva y la descarga continúa desde el punto alcanzado.
4. **Given** una descarga fallida de forma permanente, **When** el usuario revisa la cola, **Then** el resto de descargas no se ven afectadas.

---

### User Story 5 - Integridad de los archivos entregados (Priority: P1)

Un archivo solo aparece como terminado en la carpeta de destino cuando está completo. Mientras la descarga está incompleta, el archivo parcial está claramente diferenciado y nunca se presenta como final.

**Why this priority**: Entregar un archivo truncado como válido destruye la confianza en el producto.

**Independent Test**: Interrumpir una descarga y comprobar que en la carpeta de destino no existe ningún archivo con el nombre final; completarla y comprobar que el archivo final aparece solo entonces.

**Acceptance Scenarios**:

1. **Given** una descarga interrumpida, **When** el usuario revisa la carpeta de destino, **Then** no hay un archivo con el nombre final, solo un parcial claramente identificado.
2. **Given** una descarga que termina, **When** se verifica su integridad, **Then** el archivo se publica con su nombre final.
3. **Given** un archivo que no supera la verificación de integridad, **When** termina la transferencia, **Then** no se publica como completo y se informa del problema.

---

### User Story 6 - Configuración y cookies del navegador (Priority: P3)

El usuario puede configurar la carpeta de destino. Opcionalmente, puede indicar que se usen las cookies de su navegador para acceder a videos que requieren su propia sesión.

**Why this priority**: Mejora la comodidad y amplía el acceso, pero el producto es útil sin ello.

**Independent Test**: Cambiar la carpeta de destino y comprobar que las nuevas descargas se guardan allí; activar el uso de cookies del navegador para un video que exige sesión y comprobar que se descarga.

**Acceptance Scenarios**:

1. **Given** una carpeta de destino configurada, **When** termina una descarga, **Then** el archivo se guarda en esa carpeta.
2. **Given** el usuario activa explícitamente el uso de cookies del navegador, **When** descarga un video que requiere su sesión, **Then** la descarga funciona con su sesión.
3. **Given** que el usuario no activa las cookies, **When** se descarga, **Then** no se leen ni usan cookies del navegador.

---

### Edge Cases

- Disco lleno durante una descarga: `ejecutar` se detiene con un mensaje claro, la descarga vuelve a la cola con su parcial intacto y continúa al relanzar `ejecutar` con espacio libre.
- Carpeta de destino inexistente o sin permisos: `agregar` la rechaza con un mensaje claro; si deja de estar disponible después, solo esa descarga falla con un mensaje claro y el resto de la cola sigue.
- La misma URL se agrega dos veces con la misma calidad: si ya está pendiente o completada, no se crea otra descarga y se avisa; si falló, vuelve a la cola.
- Un archivo con el mismo nombre ya existe en la carpeta de destino: se conservan ambos, el nuevo se publica con un sufijo numérico y se avisa al usuario; nunca se sobrescribe.
- El servidor no permite reanudar: esa descarga se reinicia desde cero, se avisa claramente al usuario de que se reinició y por qué, y las demás no se ven afectadas.
- El archivo de estado de la cola está corrupto: `ejecutar` y `agregar` se detienen con un mensaje claro, se conserva una copia del archivo y no se tocan las descargas parciales existentes.
- Lista de reproducción con videos no disponibles: se descargan los disponibles y se reportan los que fallaron.
- Lista de reproducción sin ningún video (a menudo una URL no soportada o que exige sesión): falla como permanente con el mensaje "no se encontraron videos; la URL puede no estar soportada o requerir sesión (ver --cookies-de)" y no se intenta descargar nada.
- El servidor deniega el acceso (403 o 410) al obtener la información del video: falla como permanente, sin reintentos, con un mensaje que sugiere `--cookies-de NAVEGADOR`.
- La calidad pedida no existe para un video: se elige la más cercana inferior y se avisa al usuario. Si no hay ninguna inferior, se elige la menor de las superiores y también se avisa (research R6).
- El usuario intenta descargar contenido con DRM o una transmisión en vivo: se rechaza con un mensaje claro. Un HLS cifrado con clave `identity` obtenida por HTTP(S) desde la URI del manifiesto no es DRM y se descarga; cualquier otra entrega de la clave (`skd://`, PSSH incrustado, otro `KEYFORMAT`) sí lo es y se rechaza sin pedir la clave (FR-015).
- Cierre ordenado del programa (Ctrl+C) durante una descarga: el progreso se guarda antes de salir.
- El servidor deniega el acceso (403 o 410) durante la transferencia de forma persistente, por ejemplo porque el video exige una sesión iniciada: se reintenta obteniendo una URL nueva; tras 3 intentos seguidos sin ningún byte nuevo, la descarga falla con un mensaje que lo explica y sugiere `--cookies-de NAVEGADOR`. Los cortes de red, los timeouts y los errores 429 y 5xx no cuentan para ese tope.
- Las cookies del navegador pedido no se pueden leer (el navegador está abierto y bloquea sus cookies, las cifra de una forma que no se puede descifrar o no tiene perfil): el error nombra el navegador y la causa y dice qué hacer. Si se pidieron al agregar la URL, solo falla esa descarga; si se pidieron para toda la ejecución, se detiene sin dar ninguna por fallida y sin perder progreso.
- Una descarga que hace un programa externo (ffmpeg) falla sin causa conocida: se reintenta como temporal con el mismo tope de 3 intentos seguidos sin avanzar.
- El servidor anuncia un tamaño mayor del que llega a enviar: es indistinguible de un corte repetido en el mismo punto, así que se reintenta como cualquier corte (FR-010) y no se publica nada. Si el servidor envía un archivo truncado anunciando su tamaño truncado, la verificación lo rechaza: la descarga falla y sus archivos se apartan sin borrarse (FR-023).
- Límite conocido: un fragmento HLS truncado en el servidor de origen (servido completo según su tamaño anunciado) no lo detecta la verificación, y el video puede publicarse con un salto en la imagen o el sonido. Un fragmento cortado durante la transferencia sí se detecta y se vuelve a pedir (research R22).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema MUST permitir agregar una o varias URLs a una cola de descargas.
- **FR-002**: El sistema MUST permitir elegir la calidad por descarga: mejor, 1080p, 720p o solo audio.
- **FR-003**: El sistema MUST soportar los sitios que soporte yt-dlp, limitado a videos accesibles sin DRM.
- **FR-004**: El sistema MUST aceptar URLs de listas de reproducción y agregar cada uno de sus videos a la cola.
- **FR-005**: El sistema MUST mostrar, por cada descarga, su estado, progreso, velocidad y tiempo estimado restante.
- **FR-006**: El sistema MUST guardar los archivos en una carpeta de destino configurable.
- **FR-007**: El sistema MUST pasar una descarga al estado "esperando red" cuando se pierde la conexión, y reanudarla automáticamente al recuperarse.
- **FR-008**: El sistema MUST reanudar desde el último punto válido y no desde cero siempre que el servidor lo permita.
- **FR-009**: El sistema MUST persistir la cola y el progreso de forma que sobrevivan a cierres, fallos del programa y reinicios del equipo, y MUST continuar las descargas pendientes al abrirse de nuevo.
- **FR-010**: El sistema MUST reintentar indefinidamente los errores temporales (red, timeouts) con una espera creciente entre intentos que MUST NOT superar los 5 minutos, salvo el tope de FR-026 para los errores ambiguos.
- **FR-011**: El sistema MUST clasificar como permanentes los errores de video borrado, privado o sitio no soportado, MUST NOT reintentarlos y MUST mostrar un mensaje claro de la causa.
- **FR-012**: Si la URL temporal del video caduca, el sistema MUST obtener una nueva y continuar desde el punto alcanzado.
- **FR-013**: El sistema MUST NOT presentar un archivo incompleto como terminado; los parciales MUST estar claramente diferenciados hasta verificar su integridad.
- **FR-014**: El sistema MUST NOT sobrescribir ni borrar progreso parcial sin una acción explícita del usuario, salvo el reinicio descrito en FR-024.
- **FR-015** *(enmendado 2026-10-24)*: El sistema MUST rechazar con un mensaje claro el contenido con DRM y las transmisiones en vivo, sin intentar eludir ninguna protección técnica. Un HLS cifrado MUST descargarse solo si la clave se obtiene por HTTP(S) desde la URI que declara el manifiesto, con `KEYFORMAT="identity"` (o sin `KEYFORMAT`) y con la misma sesión del usuario; cualquier otra entrega de la clave (URI `skd://`, PSSH o clave incrustados, otro `KEYFORMAT`) MUST rechazarse como DRM, como error permanente y sin pedir la clave (constitución v3.0.0, Principio V).
- **FR-016**: El sistema MUST ofrecer, de forma opcional y solo mediante una opción explícita que nombre el navegador (por ejemplo `--cookies-de firefox`), el uso de cookies del navegador del usuario para videos que requieren su propia sesión.
- **FR-017**: El sistema MUST NOT guardar credenciales ni cookies en el repositorio ni en los archivos de estado de la cola, y MUST NOT incluirlas en mensajes de error ni registros.
- **FR-018**: El fallo de una descarga MUST NOT detener ni afectar a las demás descargas de la cola.
- **FR-019**: El sistema MUST ofrecerse en su primera versión como herramienta de línea de comandos, sin servicio en segundo plano: un comando `agregar` guarda URLs en la cola, un comando `ejecutar` procesa la cola en primer plano, un comando `estado` la muestra sin modificarla (FR-032) y un comando `actualizar` actualiza yt-dlp (FR-025).
- **FR-020**: El sistema MUST guardar el progreso al recibir una señal de cierre ordenado.
- **FR-021**: El comando `ejecutar` MUST terminar solo cuando no queden descargas pendientes; las descargas en "esperando red" cuentan como pendientes. Al vaciarse la cola, su código de salida MUST distinguir si todas se completaron o si alguna falló de forma permanente. Las únicas excepciones son la interrupción por el usuario y un error fatal del entorno (disco lleno, archivo de estado corrupto, ffmpeg ausente), que detienen `ejecutar` con un código de salida propio y el progreso conservado.
- **FR-022**: Al publicar un archivo terminado cuyo nombre ya existe en la carpeta de destino, el sistema MUST conservar el existente, publicar el nuevo con un sufijo numérico y avisar al usuario; MUST NOT sobrescribir archivos existentes.
- **FR-023**: Antes de publicar un archivo con su nombre final, el sistema MUST verificar que su tamaño coincide con el anunciado por el servidor (cuando este lo informa) y que el archivo es legible hasta el final tras unir audio y video; si la verificación falla, MUST NOT publicarlo como completo e informar del problema.
- **FR-024**: Si el servidor no permite reanudar una descarga, el sistema MUST reiniciarla desde cero y MUST avisar claramente al usuario de que se reinició y por qué.
- **FR-025** *(enmendado 2026-10-24)*: El comando `actualizar` MUST actualizar yt-dlp a su última versión dentro del proyecto, mostrar la versión anterior y la nueva, e informar con claridad si la actualización falla. Antes de dar por buena una versión nueva, MUST ejecutar los tests que dependen del comportamiento interno de yt-dlp; si fallan o no se pueden ejecutar, MUST restaurar la versión anterior, explicar qué falló e indicar que se conserva la versión anterior.
- **FR-026**: Un acceso denegado (HTTP 403 o 410) durante la transferencia y un fallo sin causa conocida de una descarga hecha por un programa externo (ffmpeg) MUST reintentarse como temporales, obteniendo una URL nueva en cada intento (FR-012). Si 3 intentos seguidos terminan en uno de esos errores sin ningún byte nuevo sobre el máximo ya descargado, el sistema MUST marcar la descarga como fallida con un mensaje claro de la causa que, para 403 y 410, sugiera usar `--cookies-de NAVEGADOR`. Los cortes de red, timeouts y errores 429 y 5xx MUST NOT contar para este tope.
- **FR-027**: El nombre de cada archivo publicado MUST salir de una plantilla configurable (`plantilla_nombre`, campos `{titulo}` e `{id}`; por defecto `{titulo}`). El sistema MUST rechazar al cargar la configuración una plantilla inválida (campo desconocido, sin ningún campo, llaves sin cerrar, `/` o `\`). El nombre MUST ser válido en Windows y caber en 255 bytes; en Windows, la ruta completa, con hueco para el sufijo de FR-022, MUST NOT pasar de 240 caracteres, recortando el título y, si no queda sitio para él, usando el id del video.
- **FR-028**: Una lista de reproducción sin ningún video MUST fallar como error permanente con el mensaje "no se encontraron videos; la URL puede no estar soportada o requerir sesión (ver --cookies-de)", sin crear descargas hijas ni intentar descargar nada.
- **FR-029**: Si las cookies del navegador pedido no se pueden leer, el error MUST nombrar el navegador y la causa y decir qué hacer. Si se pidieron con `agregar --cookies-de`, MUST fallar solo esa descarga; si se pidieron con `ejecutar --cookies-de`, `ejecutar` MUST detenerse como error fatal sin dar ninguna descarga por fallida y sin perder progreso.
- **FR-030**: Un acceso denegado (HTTP 403 o 410) al obtener la información del video MUST tratarse como error permanente, sin reintentos, con un mensaje que sugiera `--cookies-de NAVEGADOR`.
- **FR-031**: Una transferencia que sigue abierta sin recibir datos durante `estancamiento_segundos` (por defecto 30), o que recibe menos de `estancamiento_velocidad_minima` bytes/s (por defecto 1024) sostenidos durante ese tiempo, MUST abortarse y reintentarse como error temporal, reanudando desde lo ya descargado. Ese reintento MUST NOT contar para el tope de FR-026 ni como intento sin avance.
- **FR-032**: El comando `estado` MUST mostrar cada descarga de la cola con su id, estado, progreso, título y último aviso o error (con los secretos ocultos, FR-017). MUST ser de solo lectura: MUST NOT modificar la cola ni tomar el bloqueo de `ejecutar`, y MUST funcionar mientras `ejecutar` corre. Si `cola.db` no existe, MUST indicar que la cola está vacía sin crearla. Si `cola.db` está corrupta, MUST salir con el código 3 y un mensaje claro, como `agregar` y `ejecutar`.

### Key Entities

- **Descarga**: un video concreto a obtener. Atributos: URL de origen, calidad elegida, estado (en cola, descargando, esperando red, completada, fallida; las tres primeras cuentan como pendientes), progreso, causa de fallo, archivo parcial y archivo final.
- **Cola**: conjunto ordenado de descargas con su estado persistente; sobrevive a cierres y reinicios.
- **Lista de reproducción**: URL que se expande en varias descargas individuales.
- **Configuración**: carpeta de destino, calidad por defecto, plantilla del nombre de los archivos publicados (por defecto, el título sin el identificador del video; FR-027), sondas de conectividad (`sondas_red`) y umbrales del vigilante de estancamiento (`estancamiento_segundos`, `estancamiento_velocidad_minima`; FR-031). El uso de cookies del navegador no se configura aquí: solo se activa con la opción explícita de FR-016.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tras un corte de red de hasta 24 horas, el 100 % de las descargas afectadas se reanuda sola al volver la conexión, sin intervención del usuario.
- **SC-002** *(enmendado 2026-10-24)*: El total servido para una descarga, acumulado en todas sus reanudaciones, no excede en más de un 1 % el tamaño del archivo (siempre que el servidor permita reanudar; se asumen archivos de al menos 1 MB). Si alguna de las interrupciones es abrupta (cierre forzado, `SIGKILL`, corte de luz), el límite es el 1 % del tamaño del archivo más 128 KiB.
- **SC-003**: Tras un cierre abrupto o reinicio del equipo, el 100 % de las descargas pendientes reaparece en la cola y continúa desde su progreso guardado.
- **SC-004** *(enmendado 2026-10-24)*: En 0 casos un archivo incompleto aparece en la carpeta de destino con su nombre final, salvo el límite conocido de un HLS con un fragmento truncado en el servidor de origen y servido completo según su tamaño anunciado, que la verificación no detecta (Edge Cases, research R22).
- **SC-005**: Los errores permanentes se notifican al usuario en menos de 30 segundos desde el primer intento, con la causa indicada, y no se reintentan.
- **SC-006**: El usuario puede agregar una URL y empezar a descargar con dos comandos (`agregar` y `ejecutar`).
- **SC-007**: Cada descarga activa muestra progreso, velocidad y tiempo estimado, actualizados al menos una vez por segundo.
- **SC-008**: Una descarga cuya URL caduca a mitad de transferencia termina con el archivo íntegro sin intervención del usuario.

## Assumptions

- La compatibilidad de sitios se apoya en el conjunto de sitios que soporta yt-dlp, por lo que el producto depende de esa herramienta externa.
- La primera versión es solo de línea de comandos; no hay interfaz gráfica ni web ni servicio en segundo plano. Tras un reinicio del equipo, el usuario relanza `ejecutar` para continuar.
- La primera versión procesa una sola descarga a la vez. La concurrencia configurable queda para una feature futura.
- El servidor de origen debe permitir reanudar peticiones parciales para reanudar sin repetir datos; si no, solo esa descarga se reinicia con aviso (FR-024).
- Plataformas objetivo: Windows 10/11 y Linux (constitución v2.1.0, sección Plataformas). El programa funciona en ambas con el mismo comportamiento: un único `ejecutar` a la vez, publicación sin sobrescribir (también en unidades FAT32/exFAT sin enlaces duros), nombres de archivo válidos en Windows y Ctrl+C. macOS no es plataforma objetivo: el código no lo excluye, pero no se verifica.
- El usuario tiene derecho legítimo a descargar el contenido que solicita.
- El almacenamiento local es suficiente y escribible en la carpeta de destino.
- Las cookies del navegador solo se leen en el momento de la descarga y no se persisten en la cola ni en el repositorio.
- Fuera de alcance: contenido con DRM, transmisiones en vivo, protocolos RTMP y WebSocket, interfaces gráficas, programación horaria de descargas y descargas simultáneas (feature futura).
