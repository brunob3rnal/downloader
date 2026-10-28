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
uv run pytest -m ventana -rs                   # capa de Windows; en Linux, omitidos con motivo
uv run pytest tests/integration/test_app_seguridad.py   # rechazos de FR-013 (SC-007)
```

Resultado esperado:
- todo pasa en los dos sistemas;
- en Linux, los tests `ventana` y `pywebview_internals` aparecen como omitidos ("solo
  Windows");
- los tests de la feature 001 pasan sin modificaciones (SC-009).

Qué cubren, por escenario de la spec:

| Escenario | Test | Comprueba |
|-----------|------|-----------|
| US1 agregar | `test_app_agregar.py` | líneas vacías ignoradas, inválidas con su número, duplicados como `agregar`, carpeta inválida rechaza todo |
| US1 y US3 un procesador | `test_app_procesador.py` | Iniciar con un `ejecutar` de la CLI en marcha da 409; con la app procesando, `ejecutar` sale con 2; la carrera por el bloqueo (R6) |
| US2 detener | `test_app_parada.py` | parada con datos llegando y en `waiting_network`: termina con 130, progreso guardado, reanuda sin empezar de cero; mide el tiempo frente a SC-004 |
| US3 escenario 5 | `test_app_procesador.py` | detener un `ejecutar` de la CLI da 409 con el motivo |
| US4 segundo plano | `test_app_segundo_plano.py` (`slow`) | el trabajador sigue vivo al terminar la cadena de la ventana (R8); al reabrir, `api/estado` lo muestra y `api/detener` funciona |
| US4 cierre | `test_app_cierre.py` | `cierre.decidir` y `api/cerrar` con una ventana falsa |
| US5 cookies | `test_app_cookies.py` | opción desactivada por defecto, reintento con navegador (navegador falso de 001), nada de cookies en disco |
| FR-005, SC-006 | `test_app_vivo.py` | velocidad y tiempo estimado también con un `ejecutar` de la CLI; retraso ≤ 2 s |
| FR-012, FR-020 | `test_app_resultado.py`, `test_app_registro.py` | resultado tras un fatal, una parada, una excepción y un `kill`; registro ≤ 2 MB y sin secretos |
| FR-013, SC-007 | `test_app_seguridad.py` | todos los rechazos de research R4 |
| FR-018 | `test_app_instancia.py` | la segunda apertura escribe `activar.solicitud` y termina |
| R15 | `test_app_sin_ytdlp.py` | los módulos de la ventana no cargan yt-dlp |

## B. Escenarios manuales **[manual, Windows]**

Prepare una cola de prueba con el servidor local de los tests (su modo independiente,
`--servir`, se añade en esta feature; ver plan.md):

```powershell
uv run python -m tests.support.flaky_server --servir 8765 --limite 2000000   # 2 MB/s
```

### V1. Doble clic sin consola (FR-001, SC-001)

1. Con la aplicación cerrada, doble clic en `Gestor de descargas.vbs` desde el Explorador.
2. Mida desde el doble clic hasta que se puede escribir en el área de enlaces.

Esperado:
- no aparece ninguna consola en ningún momento;
- la ventana coincide con la maqueta (contracts/ventana.md);
- con el entorno ya sincronizado, tarda 5 s o menos.

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

Repita con el servidor parado (sin datos): anote el tiempo; es el caso de riesgo de R7.

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

## C. Memoria (SC-005) **[manual, Windows, equipo de referencia de 4 GB]**

```powershell
uv run python scripts/medir_memoria.py --cola-de-prueba 500 --limite 300
```

El script prepara un `DOWNLOADER_HOME` temporal con 500 descargas en cola, abre la aplicación
por el lanzador, inicia el procesamiento contra el servidor local limitado y muestrea cada
0,5 s la memoria privada (USS) sumada de todos los procesos de la aplicación (research R14)
hasta que termina la primera descarga.

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
