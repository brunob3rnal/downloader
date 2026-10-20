# Feature Specification: Gestor de descargas de video resiliente

**Feature Branch**: `001-resilient-video-downloader`

**Created**: 2026-10-20

**Status**: Draft

**Input**: User description: "Un gestor de descargas de video que recibe URLs y las descarga de forma confiable, aunque la conexión se corte. Cola persistente, calidades (mejor, 1080p, 720p, solo audio), listas de reproducción, progreso, carpeta configurable, reanudación automática, reintentos, renovación de URLs caducadas, nunca entregar archivos incompletos como terminados. Fuera de alcance: DRM y transmisiones en vivo. Primera versión: solo CLI."

## Clarifications

### Session 2026-10-20

- Q: ¿Cuál es la espera máxima entre reintentos de un error temporal? → A: 5 minutos.
- Q: ¿Cómo comprobar que un video terminó completo antes de publicarlo? → A: El tamaño final coincide con el anunciado por el servidor (cuando lo informa) y el archivo es legible hasta el final tras unir audio y video. Si falla la comprobación, no se publica con su nombre final.
- Q: ¿Qué hacer si en la carpeta de destino ya existe un archivo con el mismo nombre? → A: Conservar ambos; el archivo nuevo se publica con un sufijo numérico (`video (1).mp4`) y se avisa al usuario. Nunca se sobrescribe.
- Q: ¿Cómo se ejecuta el programa: primer plano que termina al vaciar la cola, o servicio en segundo plano? → A: Primer plano. `agregar` guarda URLs en la cola; `ejecutar` procesa la cola y solo termina cuando no queda ninguna descarga pendiente. Las descargas en "esperando red" cuentan como pendientes, por lo que el programa sigue corriendo y reintentando hasta que vuelva la conexión. Termina únicamente con la cola completada o con fallos permanentes. Tras un reinicio del equipo, el usuario relanza `ejecutar`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Descargar videos desde una cola (Priority: P1)

El usuario agrega una o varias URLs a la cola, elige la calidad (mejor, 1080p, 720p o solo audio) y el programa las descarga una tras otra en la carpeta de destino, mostrando progreso, velocidad y tiempo estimado de cada descarga.

**Why this priority**: Es el valor central del producto. Sin descargas básicas funcionando, nada más tiene sentido.

**Independent Test**: Agregar dos URLs de videos públicos con calidad 720p y verificar que ambos archivos completos aparecen en la carpeta de destino y que durante la descarga se muestran progreso, velocidad y tiempo estimado.

**Acceptance Scenarios**:

1. **Given** una cola vacía, **When** el usuario agrega una URL válida con calidad "mejor", **Then** el video se descarga completo en la carpeta configurada.
2. **Given** varias URLs en la cola, **When** el programa las procesa, **Then** cada una se descarga y se informa su estado individual (pendiente, descargando, completada, fallida).
3. **Given** una descarga en curso, **When** el usuario consulta el estado, **Then** ve el porcentaje, la velocidad y el tiempo estimado restante de esa descarga.
4. **Given** el usuario elige "solo audio", **When** termina la descarga, **Then** el archivo resultante contiene únicamente audio.
5. **Given** la URL de una lista de reproducción, **When** el usuario la agrega, **Then** todos sus videos se incorporan a la cola como descargas individuales.

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
2. **Given** una URL de un sitio no soportado, **When** se agrega, **Then** el usuario recibe un mensaje claro indicando que el sitio no es compatible.
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

- Disco lleno durante una descarga: se informa claramente, se conserva el parcial y la descarga queda pausada sin perder progreso.
- Carpeta de destino inexistente o sin permisos: se informa antes de iniciar y no se pierde la cola.
- La misma URL se agrega dos veces con la misma calidad: no se descarga dos veces en paralelo; se avisa al usuario.
- Un archivo con el mismo nombre ya existe en la carpeta de destino: se conservan ambos, el nuevo se publica con un sufijo numérico y se avisa al usuario; nunca se sobrescribe.
- El servidor no permite reanudar parcialmente: se informa y se reinicia solo esa descarga, sin afectar a las demás.
- El archivo de estado de la cola está corrupto: se informa, se conserva una copia y no se descartan descargas parciales existentes.
- Lista de reproducción con videos no disponibles: se descargan los disponibles y se reportan los que fallaron.
- La calidad pedida no existe para un video: se elige la más cercana inferior y se avisa al usuario.
- El usuario intenta descargar contenido con DRM o una transmisión en vivo: se rechaza con un mensaje claro.
- Cierre ordenado del programa (Ctrl+C) durante una descarga: el progreso se guarda antes de salir.

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
- **FR-010**: El sistema MUST reintentar indefinidamente los errores temporales (red, timeouts) con una espera creciente entre intentos que MUST NOT superar los 5 minutos.
- **FR-011**: El sistema MUST clasificar como permanentes los errores de video borrado, privado o sitio no soportado, MUST NOT reintentarlos y MUST mostrar un mensaje claro de la causa.
- **FR-012**: El sistema MUST detectar la caducidad de la URL temporal del video, obtener una nueva y continuar desde el punto alcanzado.
- **FR-013**: El sistema MUST NOT presentar un archivo incompleto como terminado; los parciales MUST estar claramente diferenciados hasta verificar su integridad.
- **FR-023**: Antes de publicar un archivo con su nombre final, el sistema MUST verificar que su tamaño coincide con el anunciado por el servidor (cuando este lo informa) y que el archivo es legible hasta el final tras unir audio y video; si la verificación falla, MUST NOT publicarlo como completo e informar del problema.
- **FR-014**: El sistema MUST NOT sobrescribir ni borrar progreso parcial sin una acción explícita del usuario.
- **FR-022**: Al publicar un archivo terminado cuyo nombre ya existe en la carpeta de destino, el sistema MUST conservar el existente, publicar el nuevo con un sufijo numérico y avisar al usuario; MUST NOT sobrescribir archivos existentes.
- **FR-015**: El sistema MUST rechazar con un mensaje claro el contenido con DRM y las transmisiones en vivo, sin intentar eludir ninguna protección técnica.
- **FR-016**: El sistema MUST ofrecer, de forma opcional y solo por activación explícita, el uso de cookies del navegador del usuario para videos que requieren su propia sesión.
- **FR-017**: El sistema MUST NOT guardar credenciales ni cookies en el repositorio ni en los archivos de estado de la cola, y MUST NOT incluirlas en mensajes de error ni registros.
- **FR-018**: El fallo de una descarga MUST NOT detener ni afectar a las demás descargas de la cola.
- **FR-019**: El sistema MUST ofrecerse en su primera versión como herramienta de línea de comandos, sin servicio en segundo plano: un comando `agregar` guarda URLs en la cola y un comando `ejecutar` procesa la cola en primer plano.
- **FR-021**: El comando `ejecutar` MUST terminar solo cuando no queden descargas pendientes; las descargas en "esperando red" cuentan como pendientes. MUST terminar con la cola completada o con descargas fallidas de forma permanente, y su código de salida MUST distinguir ambos resultados.
- **FR-020**: El sistema MUST guardar el progreso al recibir una señal de cierre ordenado.

### Key Entities

- **Descarga**: un video concreto a obtener. Atributos: URL de origen, calidad elegida, estado (pendiente, descargando, esperando red, completada, fallida), progreso, causa de fallo, archivo parcial y archivo final.
- **Cola**: conjunto ordenado de descargas con su estado persistente; sobrevive a cierres y reinicios.
- **Lista de reproducción**: URL que se expande en varias descargas individuales.
- **Configuración**: carpeta de destino, calidad por defecto y activación opcional de cookies del navegador.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tras un corte de red de hasta 24 horas, el 100 % de las descargas afectadas se reanuda sola al volver la conexión, sin intervención del usuario.
- **SC-002**: Tras un corte, la descarga reanudada no vuelve a transferir más del 1 % del contenido ya obtenido (siempre que el servidor permita reanudar).
- **SC-003**: Tras un cierre abrupto o reinicio del equipo, el 100 % de las descargas pendientes reaparece en la cola y continúa desde su progreso guardado.
- **SC-004**: En 0 casos un archivo incompleto aparece en la carpeta de destino con su nombre final.
- **SC-005**: Los errores permanentes se notifican al usuario en menos de 30 segundos desde el primer intento, con la causa indicada, y no se reintentan.
- **SC-006**: El usuario puede agregar una URL y empezar a descargar con dos comandos (`agregar` y `ejecutar`).
- **SC-007**: Cada descarga activa muestra progreso, velocidad y tiempo estimado, actualizados al menos una vez por segundo.
- **SC-008**: Una descarga cuya URL caduca a mitad de transferencia termina con el archivo íntegro sin intervención del usuario.

## Assumptions

- La compatibilidad de sitios se apoya en el conjunto de sitios que soporta yt-dlp, por lo que el producto depende de esa herramienta externa.
- La primera versión es solo de línea de comandos; no hay interfaz gráfica ni web ni servicio en segundo plano. Tras un reinicio del equipo, el usuario relanza `ejecutar` para continuar.
- Las descargas se procesan con una concurrencia limitada y configurable, por defecto una a la vez.
- El servidor de origen debe permitir reanudar peticiones parciales para reanudar sin repetir datos; si no, solo esa descarga se reinicia.
- El usuario tiene derecho legítimo a descargar el contenido que solicita.
- El almacenamiento local es suficiente y escribible en la carpeta de destino.
- Las cookies del navegador solo se leen en el momento de la descarga y no se persisten en la cola ni en el repositorio.
- Fuera de alcance: contenido con DRM, transmisiones en vivo, interfaces gráficas y programación horaria de descargas.
