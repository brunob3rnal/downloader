<!--
Sync Impact Report (2026-10-24)
- Versión: 2.1.0 → 3.0.0 (MAJOR)
- Principio modificado: V. "Sin elusión de DRM ni protecciones técnicas" → "Sin elusión de DRM
  ni sistemas de licencias".
- Ajuste del mismo día, antes de fusionarse en `main`: el criterio de rechazo pasa a ser el
  sistema de licencias, no el transporte de la clave. La clave `identity` en URI `data:` o en
  `hls_aes` del extractor se acepta (la primera redacción la rechazaba); el PSSH y Widevine se
  nombran como DRM. Sigue siendo 3.0.0: la 3.0.0 no llegó a ratificarse en `main`, y frente a
  la 2.1.0 ambas redacciones son el mismo cambio MAJOR.
- Motivo: la redacción anterior prohibía "descifrar ... cifrado de contenido" sin excepciones,
  y el diseño descifra HLS AES-128 (research R1, R5; test_hls_aes.py). Hallazgo C1 de
  /speckit-analyze.
- Por qué MAJOR: redefine el principio de forma incompatible. El mismo código recibe veredictos
  distintos con una versión y otra: el descifrado de HLS con clave `identity` estaba prohibido
  y ahora se permite, y los sistemas de licencias que yt-dlp no reconoce como DRM (Widevine,
  PSSH, otro `KEYFORMAT`), que el texto anterior no obligaba a detectar, ahora MUST rechazarse.
  Mismo criterio que la enmienda del Principio IV (v2.0.0).
- Plan de migración: research R5 (comportamiento verificado de yt-dlp 2026.10.21) y tareas de
  la Fase 10 de specs/001-resilient-video-downloader/tasks.md (tests de DRM `skd://`, de
  AES-128 aceptado y de los sistemas de licencias que yt-dlp no rechaza, con un analizador de
  atributos del manifiesto propio).
- Plantillas: sin cambios necesarios (.specify/templates no citan el Principio V).
-->
# Downloader Constitution

## Core Principles

### I. Código simple y legible
El código MUST priorizar la claridad sobre la ingeniosidad. Cada módulo MUST tener una
responsabilidad única y nombres que expliquen su intención. Las abstracciones,
dependencias y configuración MUST NOT añadirse para necesidades hipotéticas (YAGNI); toda complejidad
añadida MUST justificarse por escrito en el plan.

Justificación: un descargador que otros puedan leer y auditar es más fácil de mantener y de
confiar.

### II. Tests para la lógica principal
La lógica principal (planificación de descargas, reanudación, reintentos, escritura de
progreso) MUST tener tests automatizados. Los tests MUST incluir simulación de cortes de red:
conexión interrumpida a mitad de transferencia, timeouts, respuestas truncadas y errores del
servidor. Los tests MUST ejecutarse sin acceso a internet real, usando servidores o
transportes simulados. Un cambio en esa lógica sin tests MUST NOT fusionarse.

Justificación: los fallos de red son el caso normal de un descargador, no la excepción.

### III. Manejo claro de errores
Todo error MUST ser explícito: las excepciones MUST NOT silenciarse ni los resultados
parciales devolverse como si fueran completos. Los mensajes al usuario MUST indicar qué falló, en qué
recurso y qué puede hacer (reintentar, comprobar la red, etc.). Los errores recuperables
(red, timeouts) MUST distinguirse de los fatales (URL inválida, permisos, disco lleno) y
tratarse de forma distinta. Los códigos de salida MUST reflejar el resultado.

### IV. Nunca perder progreso reanudable (NON-NEGOTIABLE)
Nunca se pierde progreso reanudable. Los datos ya descargados MUST conservarse ante cualquier
interrupción (corte de red, cierre del proceso, error) y las descargas MUST reanudarse desde
el último byte válido cuando el servidor lo permita. Si el servidor no permite reanudar, la
descarga MUST reiniciarse desde cero y el usuario MUST recibir un aviso claro de que se
reinició y por qué. Fuera de ese caso, un archivo parcial MUST NOT borrarse ni sobrescribirse
automáticamente. Un archivo parcial MUST permanecer claramente distinguido del archivo
completo (por ejemplo, con un sufijo temporal) hasta que su integridad se verifique.

### V. Sin elusión de DRM ni sistemas de licencias
El proyecto MUST NOT eludir, descifrar ni saltarse DRM, sistemas de licencias, controles de
acceso ni otras medidas técnicas de protección. MUST descargar solo contenido al que el
usuario tenga acceso legítimo por medios estándar. Las contribuciones que añadan
funcionalidad de elusión MUST rechazarse.

El cifrado sin sistema de licencias no es una medida técnica de protección: un HLS con
`METHOD=AES-128` y `KEYFORMAT="identity"` (o sin `KEYFORMAT`, que según RFC 8216 equivale a
`identity`) MAY descargarse y descifrarse, tanto si la clave llega por HTTP(S) desde la URI
del manifiesto, con la misma sesión del usuario, como si viene incrustada en una URI `data:`
o la entrega el extractor (`hls_aes`). Todo lo que dependa de un sistema de licencias MUST
tratarse como DRM: URI `skd://`, FairPlay, PlayReady, Widevine, un PSSH y cualquier `KEYFORMAT`
distinto de `identity`. La descarga MUST rechazarse con un mensaje claro y sin pedir la clave.

Justificación: lo que distingue el DRM es el sistema de licencias, que entrega la clave solo a
módulos de descifrado autorizados; obtenerla por otra vía sería eludirlo. Una clave `identity`
no pasa por ningún sistema de licencias, venga por donde venga.

### VI. Sin credenciales ni cookies en el repositorio
Credenciales, tokens, cookies y cualquier secreto de sesión MUST NOT guardarse en el
repositorio, ni en el código, ni en tests, fixtures, logs o historial de git. Los secretos
MUST proporcionarse en tiempo de ejecución (variables de entorno, archivos locales ignorados
por git o entrada del usuario). Los logs y mensajes de error MUST NOT incluirlos. Los datos de
prueba MUST usar valores ficticios.

## Restricciones Legales y de Seguridad

- Los archivos locales con secretos MUST estar listados en `.gitignore`.
- Los mensajes y logs MUST ocultar tokens y cookies presentes en URLs o cabeceras.
- Las dependencias nuevas MUST justificarse conforme al Principio I y revisar su licencia.

## Plataformas

- El programa MUST funcionar en Windows 10/11 y en Linux. Una funcionalidad que no funcione en
  una de las dos plataformas MUST NOT fusionarse.
- El código MUST NOT depender sin alternativa de APIs de un solo sistema (`fcntl`, señales
  POSIX como `SIGKILL`, `os.fork`, `os.getuid`…): cada uso MUST ir protegido por una
  comprobación de plataforma y tener su equivalente en la otra.
- Las rutas MUST construirse con `pathlib` u `os.path`, nunca concatenando `/` a mano, y los
  nombres de archivo que se creen MUST ser válidos en Windows (caracteres, nombres reservados
  y longitud de ruta).
- El CI MUST ejecutar la suite completa en Linux y en Windows, con ffmpeg instalado en ambos.

Justificación: el usuario principal trabaja en Windows; un descargador que solo funciona en
Linux no cumple su propósito.

## Flujo de Desarrollo

- Cada cambio MUST pasar por la especificación, el plan y las tareas de Spec Kit antes de
  implementarse.
- Los tests MUST pasar antes de fusionar cualquier cambio.
- Toda revisión MUST verificar el cumplimiento de los seis principios; la sección
  "Constitution Check" de cada plan MUST usarse como puerta de calidad.

## Governance

Esta constitución prevalece sobre cualquier otra práctica del proyecto. Las enmiendas MUST
documentarse en un cambio propio que explique el motivo e incluya, si procede, un plan de
migración del código afectado. El versionado sigue SemVer: MAJOR para eliminar o redefinir
principios de forma incompatible, MINOR para añadir principios o ampliar guía de forma
material, PATCH para aclaraciones y correcciones de redacción. Toda revisión de código MUST
comprobar el cumplimiento; las desviaciones MUST justificarse en la sección de complejidad
del plan.

**Version**: 3.0.0 | **Ratified**: 2026-10-20 | **Last Amended**: 2026-10-24
