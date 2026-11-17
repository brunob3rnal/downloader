# Quickstart: validar la aplicación de escritorio

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Escenarios ejecutables que prueban la feature de principio a fin. Los automáticos corren en
Linux y en Windows; los manuales (marcados **[manual, Windows]**) cubren lo que el CI no puede
comprobar sin un escritorio interactivo (research R19).

## Requisitos

- Repositorio clonado y `uv sync` hecho (README). ffmpeg y ffprobe en el `PATH`.
- Windows 10/11 con WebView2 para los escenarios manuales.
- Ningún escenario necesita internet: todos usan el servidor local de los tests
  (`tests/support/flaky_server.py`).
- Un `DOWNLOADER_HOME` temporal para no tocar la cola real:

  ```powershell
  $env:DOWNLOADER_HOME = "$env:TEMP\gestor-prueba"   # PowerShell
  ```
  ```bash
  export DOWNLOADER_HOME=$(mktemp -d)               # Linux
  ```

## A. Tests automáticos (Linux y Windows)

```bash
uv run pytest                                  # suite completa: CLI de 001 sin cambios + app
uv run pytest tests -k "app_"                  # solo los de la aplicación
uv run pytest -m solo_windows -rs              # exclusivos de Windows; en Linux, omitidos con motivo
uv run pytest tests/integration/test_app_seguridad.py   # rechazos de FR-013 (SC-007)
```

Resultado esperado:
- todo pasa en los dos sistemas;
- en Linux, los tests `solo_windows` y `pywebview_internals` aparecen como omitidos ("solo
  Windows");
- los tests de la feature 001 pasan sin modificaciones (SC-009).

Qué cubren, por escenario de la spec:

| Escenario | Test | Comprueba |
|-----------|------|-----------|
| US1 agregar | `test_app_agregar.py` | líneas vacías ignoradas, inválidas con su número, duplicados como `agregar`, carpeta inválida rechaza todo |
| US1 y US3 un procesador | `test_app_procesador.py` | Iniciar con un `ejecutar` de la CLI en marcha da 409; con la app procesando, `ejecutar` sale con 2; la carrera por el bloqueo (R6) |
| US2 Pausar | `test_app_parada.py` | parada con datos llegando, en `waiting_network` y con la conexión estancada: termina con 130, progreso guardado, reanuda sin empezar de cero; con la conexión estancada, Linux ≤ 10 s y Windows registra el tiempo y falla solo por encima de 25 s (SC-004) |
| US3 escenario 5 | `test_app_procesador.py` | detener un `ejecutar` de la CLI da 409 con el motivo |
| US4 segundo plano | `test_app_segundo_plano.py` (`slow`), `test_lanzador_segundo_plano.py` (`solo_windows`) | el trabajador sigue vivo al terminar el proceso de la ventana y, en Windows, la cadena del lanzador (R8); al reabrir, `api/estado` lo muestra y `api/detener` funciona |
| US4 cierre | `test_app_cierre.py` | `cierre.decidir` y `api/cerrar` con una ventana falsa |
| US5 cookies | `test_app_cookies.py` | opción desactivada por defecto, reintento con navegador (navegador falso de 001), nada de cookies en disco |
| FR-005, SC-006 | `test_app_vivo.py` | velocidad y tiempo estimado también con un `ejecutar` de la CLI; retraso ≤ 2 s |
| FR-012, FR-020 | `test_app_resultado.py`, `test_app_registro.py` | resultado tras un fatal, una parada, una excepción y un `kill`; registro ≤ 2 MB y sin secretos |
| FR-013, SC-007 | `test_app_seguridad.py` | todos los rechazos de research R4 |
| FR-018 | `test_app_instancia.py` | la segunda apertura escribe `activar.solicitud` y termina |
| R15 | `test_app_sin_ytdlp.py` | los módulos de la ventana no cargan yt-dlp |
| FR-003 | `test_app_examinar.py` | `api/examinar` con la ventana falsa: ruta elegida, cancelado y 501 sin diálogo |
| FR-017, SC-009 | `test_cli_caracterizacion.py`, `test_ejecutar_sin_archivos_vivos.py` | salida y códigos de `agregar`, `ejecutar` y `estado` iguales a las referencias tomadas antes de cambiar la CLI, también si fallan los archivos informativos |
| R8 | `test_lanzar_desacoplado.py` | un hijo lanzado fuera del objeto de trabajo (Windows) o en otra sesión (Linux) sobrevive a su padre bajo `uv run pytest` |

## B. Escenarios manuales **[manual, Windows]**

Prepare una cola de prueba con el servidor local de los tests (su modo independiente,
`--servir`, se añade en esta feature; ver plan.md):

```powershell
# Un MP4 real de unos 60 MB (2 min a 4 Mb/s): ffprobe lo verifica y la descarga se completa.
ffmpeg -f lavfi -i testsrc=size=1280x720:rate=30 -f lavfi -i sine -t 120 -c:v libx264 -preset ultrafast -b:v 4M -c:a aac -shortest $env:TEMP\prueba.mp4
uv run python -m tests.support.flaky_server --servir 8765 --limite 2000000 --fuente $env:TEMP\prueba.mp4   # 2 MB/s
```

Los enlaces son `http://127.0.0.1:8765/video.mp4?n=1`, `?n=2`, …: la consulta distingue las
descargas y el servidor la ignora. Sin `--fuente`, sirve ceros, que la verificación con
ffprobe rechaza (sirve para medir memoria, no para completar descargas).

### V1. Doble clic sin consola (FR-001, SC-001)

1. Con la aplicación cerrada, doble clic en `Gestor de descargas.vbs` desde el Explorador.
2. Mida desde el doble clic hasta que se puede escribir en el área de enlaces.

Esperado:
- no aparece ninguna consola en ningún momento;
- la ventana coincide con la maqueta (contracts/ventana.md);
- con el entorno ya sincronizado, tarda 5 s o menos.

**V1b. Sincronización fallida (spec, Assumptions)**: con un cambio pendiente en `uv.lock` (por
ejemplo, tras un `git pull`) y sin red, doble clic en el lanzador. Esperado: el cuadro de
mensaje de contracts/ventana.md ("No se pudo preparar el entorno…"), sin ventana a medias y
nunca un doble clic sin respuesta.

### V2. Agregar, iniciar y ver el progreso (US1)

1. Pegue dos enlaces del servidor local y una línea con `hola`, elija 720p y pulse
   **Agregar a la cola**.
2. Pulse **Iniciar**.

Esperado:
- dos agregadas y "Línea 3: no es un enlace http o https";
- las filas muestran estado, porcentaje, "2,0 MB/s · quedan …" y avanzan cada segundo;
- al terminar, la franja dice "2 completadas, 0 fallidas".

### V3. Pausar y reanudar (US2, SC-004)

1. A mitad de una descarga, pulse **Pausar** y cronometre hasta que **Iniciar** vuelve a
   estar activo.
2. Pulse **Iniciar**.

Esperado:
- se detiene en 10 s o menos;
- la fila vuelve a "En cola" con su porcentaje;
- al iniciar, continúa desde ese porcentaje.

Repita con la red cortada, de dos formas, y anote los tiempos:
- con el servidor de prueba parado (Ctrl+C en su terminal): la fila pasa a "Esperando red" y
  la parada es igual de rápida;
- con la conexión estancada, que sigue abierta pero sin datos: es el caso de riesgo de R7. Con
  un video real en descarga, active el modo avión y pulse **Pausar** a los 5 s. En Windows
  puede tardar hasta 15 s (13,09 s en el CI; research R7).

### V4. Procesador de la CLI (US3)

1. En una terminal, `uv run downloader ejecutar` con descargas en cola.
2. Abra la aplicación.

Esperado:
- "En marcha desde la terminal";
- progreso en directo;
- **Iniciar** desactivado;
- **Pausar** explica que se detiene con Ctrl+C en la terminal.

### V5. Cerrar y seguir en segundo plano (US4)

1. Con una descarga de la aplicación en curso, cierre la ventana: aparece el aviso de la
   maqueta.
2. Pulse **Seguir en segundo plano**.
3. Compruebe en el Administrador de tareas que el `python.exe` del trabajador sigue y que no
   hay ninguna consola.
4. Abra de nuevo con doble clic.

Esperado: muestra la descarga en directo, y **Pausar** la detiene.

Repita con **Pausar y cerrar** (se detiene y se cierra) y con **Cancelar** (no pasa nada).

### V6. Errores en segundo plano (FR-012, FR-020)

1. Inicie el procesamiento, ciérrelo en segundo plano y termine el `python.exe` del trabajador
   desde el Administrador de tareas.
2. Abra la aplicación.

Esperado:
- "el procesamiento terminó de forma inesperada";
- una entrada en `%APPDATA%\downloader\registro\ventana.log`.

### V7. WebView2 ausente (R2)

En una máquina virtual con Windows 10 sin WebView2, o desinstalándolo en una de pruebas, abra
la aplicación.

Esperado:
- el cuadro de mensaje de R2;
- código de salida 3;
- ninguna ventana rota;
- una entrada en el registro.

### V8. Cookies (US5)

1. Marque la casilla de cookies y elija Edge y Chrome: aparecen sus advertencias.
2. Una descarga del servidor local que responde 403 falla con **Reintentar con cookies**.
3. Reintente con Firefox.

Esperado: vuelve a la cola. Al cerrar y abrir, la casilla está desmarcada.

### V9. Una sola ventana (FR-018)

Con la ventana abierta, doble clic otra vez en el lanzador.

Esperado: no se abre otra; la existente pasa al frente o parpadea en la barra de tareas
(R12).

### V10. Agregar y empezar a descargar sin terminal (SC-002)

Una persona que no use la terminal, con la ventana abierta y una URL del servidor local
copiada, agrega la URL y empieza a descargarla. Cronometre y cuente las acciones.

Esperado: menos de 1 minuto y 3 acciones como máximo (pegar, **Agregar a la cola**,
**Iniciar**).

### V11. La ventana responde con `--disable-gpu` (research R14) **[equipo de 4 GB]**

Con la aplicación arrancada normalmente (fija `--disable-gpu` en su propio proceso) y 500
descargas en cola, una de ellas en curso:
1. desplácese por la lista de arriba abajo y de vuelta;
2. escriba en el área de enlaces y cambie la calidad;
3. pulse **Pausar** e **Iniciar**.

Esperado: la lista se desplaza sin saltos de más de un instante, el texto aparece al
escribir, los botones responden en menos de 1 s y la fila activa sigue actualizándose cada
segundo. Anote en "Resultados" cualquier retraso apreciable.

## C. Memoria (SC-005) **[manual, Windows, equipo de referencia de 4 GB]**

```powershell
uv run python scripts/medir_memoria.py --ventana app --cola-de-prueba 500 --limite 300
uv run python scripts/medir_memoria.py --ventana app --union --cola-de-prueba 500 --limite 300
```

El script prepara un `DOWNLOADER_HOME` temporal con 500 descargas en cola, abre la aplicación
por el lanzador, inicia el procesamiento contra el servidor local limitado y muestrea cada
0,5 s la memoria privada (USS) sumada de todos los procesos de la aplicación (research R14)
hasta que termina la primera descarga. Con `--union`, la descarga une audio y video con
ffmpeg, y `ffmpeg` y `ffprobe` cuentan en la suma (research R14, condición del usuario).

Esperado:
- informa del máximo y del desglose por proceso en ese instante;
- termina con 0 si el máximo es de 300 MB o menos, y con 1 si se pasa.

Esta medición se hace pronto, antes de construir toda la interfaz, y se repite al final.

## D. Seguridad a mano (complemento de SC-007)

Con la aplicación abierta, busque el puerto con `netstat -ano | findstr <pid>` (Windows) o
`ss -ltnp` (Linux).

Esperado:
- escucha en `127.0.0.1:<puerto>` y nunca en `0.0.0.0`;
- `curl http://127.0.0.1:<puerto>/` responde 403;
- desde otro equipo de la red, la conexión se rechaza.

## Resultados

Lo que se ha ejecutado a mano, por fecha. Cada fila dice lo que se observó; lo que no se llegó
a observar queda como pendiente, no como bueno. Un escenario que no aparece aquí no se ha hecho.

### 2026-11-17: Fase 8, en el equipo de referencia de 4 GB

Comunicados por el usuario. No consta el commit exacto; como mínimo `930d73e`, porque las
descargas de V2 se completaron y eso necesita `--fuente`.

| Escenario | Qué se observó | Queda pendiente |
|-----------|----------------|-----------------|
| V1, doble clic en `Gestor de descargas.vbs`, 3 veces | Ninguna consola. Se pudo escribir "de forma casi instantánea", claramente por debajo de los 5 s de SC-001 (sin cronómetro: no hay cifra) | — |
| V1, respaldo `.venv\Scripts\gestor-descargas.exe` | Abierto desde PowerShell: la ventana se abrió bien | El doble clic desde el Explorador: abrirlo desde una terminal no dice si aparecería una consola |
| V1b, sincronización fallida | No hecho | Todo |
| V2, servidor de prueba | Las dos descargas terminaron en "Completada". La línea `hola` "dio error al agregar" | No se anotó el texto de ese error (se entiende que es el aviso esperado de la línea 3, porque los otros dos enlaces se agregaron y se completaron) ni si la franja final decía "2 completadas, 0 fallidas" |
| Comparación con la maqueta | Ninguna diferencia, aparte de las ya previstas: **Pausar** desactivado (Fase 9), sin cookies ni "Reintentar con cookies" (Fase 13), y la franja del resultado y el pie, que añade la spec | — |
| V7, WebView2 ausente | No hecho: el usuario no tiene una máquina virtual sin WebView2 (dicho el 2026-11-15) | Todo |
| V10, sin terminal | No hecho | Todo |
| V11, 500 descargas en cola con `--disable-gpu` | El desplazamiento, la escritura, el cambio de calidad y **Examinar** respondieron en menos de 1 s | La fila activa: "creo que siguió actualizándose", sin fijarse con detalle; queda sin confirmar. El paso 3 (**Pausar**) no existe hasta la Fase 9: se hace al repetir el quickstart completo (T108) |
| Fuera de quickstart: un video real, desde la ventana | Se completó; la fila mostró porcentaje y velocidad | — |

Con estos datos no se cierra ninguna tarea manual de la Fase 8:
- T069: faltan V1b y el doble clic en `gestor-descargas.exe`;
- T070: falta confirmar la franja final de V2 y que la fila activa de V11 se actualiza cada
  segundo con las 500 filas;
- T071 (V10) y T072 (V7): sin hacer.
