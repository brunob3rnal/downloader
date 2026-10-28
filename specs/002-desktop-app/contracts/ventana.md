# Contrato: ventana y lanzador

**Feature**: [spec.md](../spec.md) | **Research**: R1, R2, R11, R12, R13, R18 |
**Maqueta**: la que adjuntó el usuario (dos páginas: ventana principal y aviso de cierre)

Es la parte exclusiva de Windows (constitución v3.1.0). La maqueta fija la disposición y el
estilo. Donde la maqueta no coincide con la spec, manda la spec; las diferencias están al
final.

## Lanzador

- `Gestor de descargas.vbs`, en la raíz del repositorio. Se abre con doble clic y no muestra
  ninguna consola (FR-001, SC-001).
- Sin `uv` en el `PATH`, muestra un cuadro de mensaje: "No se encuentra uv. Instálelo con
  `winget install --id=astral-sh.uv -e`, abra una terminal nueva, ejecute `uv sync` en la
  carpeta del repositorio y vuelva a abrir el Gestor de descargas."
- Respaldo: `.venv\Scripts\gestor-descargas.exe`, que crea `uv sync` (R11).

## Arranque

1. Si ya hay una ventana abierta, la enfoca y termina (FR-018).
2. Si falta WebView2, muestra el mensaje de R2 en un cuadro nativo y termina con el código 3.
3. Abre la ventana: título "Gestor de descargas", con el icono de descarga de la maqueta;
   tamaño inicial de 1100 × 760 y mínimo de 720 × 520.
4. Muestra el estado actual: un procesador en marcha (de la CLI o de la aplicación, FR-008 y
   FR-011) y el resultado de la última ejecución (FR-012).

## Disposición (de arriba abajo)

1. **Panel de enlaces**:
   - rótulo "Pegar enlaces, uno por línea" y un área de texto de varias líneas;
   - fila con "Calidad" (desplegable: Mejor calidad, 1080p, 720p, Solo audio), "Guardar en"
     (campo de texto con la carpeta y botón **Examinar**) y botón principal **Agregar a la
     cola**;
   - **Cookies del navegador** (añadido a la maqueta, FR-014): casilla "Usar las cookies de
     mi navegador", desmarcada al abrir, con un desplegable de navegador que solo se activa al
     marcarla y la advertencia del navegador elegido debajo, en texto de aviso;
   - tras agregar, un resumen en línea: "3 agregadas, 1 ya estaba en la cola", más la lista
     de líneas inválidas con su número ("Línea 3: no es un enlace http o https").
2. **Cabecera de la cola**:
   - título "Cola", subtítulo "N descargas · una a la vez" y el origen del procesamiento si
     no es de la ventana ("En marcha desde la terminal");
   - botones **Iniciar** y **Pausar** (Pausar es el Detener de la spec), activos según
     `puede_iniciar` y `puede_detener`, con el `motivo` como explicación al pasar el cursor y
     al pulsar.
3. **Lista**: una fila por descarga.
   - a la izquierda, el título en negrita y debajo el sitio con la ruta;
   - en el centro, la etiqueta de estado con su color (Descargando, azul; En cola, gris;
     Completada, verde; Esperando red o "Sin datos hace N s, reintentando", naranja;
     Falló, rojo), el porcentaje a la derecha, la barra de progreso y una línea de detalle:
     velocidad y "quedan …", el aviso, el destino o la causa del fallo;
   - a la derecha, **Reintentar con cookies** si `reintentar_con_cookies`. Al pulsarlo se elige
     el navegador con su advertencia y se confirma.
4. **Resultado de la última ejecución** (FR-012): una franja sobre la lista, que se puede
   cerrar, con "4 completadas, 1 fallida" y el error fatal, la parada o la terminación
   inesperada, si los hubo. Es visible hasta que se inicia otra ejecución.
5. **Pie**:
   - a la izquierda, "La cola se guarda en este equipo. Si se cierra el programa, continúa al
     abrirlo.";
   - a la derecha, el estado de la conexión: "Con conexión" (punto verde) o "Sin conexión"
     (punto rojo) mientras hay un procesador; "Sin procesamiento en marcha" si no lo hay;
   - si el registro de errores no está disponible, se dice aquí.

## Aviso al cerrar (FR-010)

Solo con un procesador de origen `app` en marcha. Es un diálogo modal dentro de la página:

- título "Hay descargas en curso";
- texto: "Una descarga está activa y otras N esperan en la cola. Puede dejarlas avanzando en
  segundo plano o pausarlas. En ambos casos se conserva lo ya descargado." (con N real, y
  "no hay más en la cola" si N = 0);
- botones **Seguir en segundo plano** (principal), **Pausar y cerrar** y **Cancelar**;
- con **Pausar y cerrar**, los botones se desactivan y aparece "Deteniendo…" hasta que la
  ventana se cierra. A los 30 s, si no ha terminado, se ofrece "Cerrar igualmente" (R18).

## Errores visibles

- Respuesta 400 de agregar: el mensaje bajo el formulario, sin borrar el texto pegado.
- Respuesta 409 de iniciar o detener: el `motivo` junto a los botones.
- Cola corrupta: un aviso que ocupa la lista, con el mensaje y la ruta de la copia (Edge
  Cases).
- Fallo de la API (la página no recibe respuesta): "Se perdió la conexión con la aplicación";
  la página reintenta cada segundo.

## Diferencias con la maqueta (manda la spec)

| Maqueta | Ventana | Motivo |
|---------|---------|--------|
| Sin opción de cookies | Casilla, navegador y advertencia en el panel de enlaces | FR-014 y la entrada del plan |
| Botón "Pausar" | Se mantiene "Pausar", con el comportamiento de Detener | FR-009; mismo término que "Pausar y cerrar" |
| Sin resultado de la última ejecución | Franja sobre la lista | FR-012 |
| Sin indicación del origen del procesamiento | Subtítulo en la cabecera de la cola | US3 escenario 1, FR-008 |
| "Con conexión" siempre visible | Solo con un procesador; si no, "Sin procesamiento en marcha" | El estado de la red lo mide el trabajador (R9); la ventana no sondea la red por su cuenta |
