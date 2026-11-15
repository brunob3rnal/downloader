// Gestor de descargas: la página de la ventana (contracts/ventana.md, contracts/api-local.md).
// Sin dependencias ni recursos de otros orígenes: la CSP solo permite 'self'.
"use strict";

(function () {
  // El secreto de la sesión es el primer segmento de la ruta: "/<secreto>/".
  const BASE = location.pathname.slice(0, location.pathname.indexOf("/", 1) + 1);
  const INTERVALO = 1000; // ms entre consultas de api/estado (R13)
  const PAUSAR_DISPONIBLE = false; // Pausar llega con la parada ordenada (Fase 9)
  const TONOS = {
    downloading: "azul", queued: "gris", completed: "verde",
    waiting_network: "naranja", failed: "rojo",
  };
  const DETALLE_POR_ESTADO = { queued: "Esperando su turno" };

  const $ = (id) => document.getElementById(id);
  let etiquetaEstado = null;
  let ultimoEstado = null;
  let resultadoCerrado = null; // ejecución cuyo resultado cerró el usuario
  const filas = new Map(); // id → {elemento, clave}

  // --- API ---------------------------------------------------------------------------

  async function api(metodo, ruta, cuerpo, cabeceras) {
    const opciones = {
      method: metodo,
      cache: "no-store",
      headers: Object.assign({ "X-Gestor": "1" }, cabeceras || {}),
    };
    if (cuerpo !== undefined) {
      opciones.headers["Content-Type"] = "application/json";
      opciones.body = JSON.stringify(cuerpo);
    }
    const respuesta = await fetch(BASE + "api/" + ruta, opciones);
    let datos = null;
    if (respuesta.status !== 304 && respuesta.status !== 204) {
      try { datos = await respuesta.json(); } catch (e) { datos = null; }
    }
    return { estado: respuesta.status, datos: datos, cabeceras: respuesta.headers };
  }

  function mostrar(elemento, texto) {
    elemento.textContent = texto || "";
    elemento.hidden = !texto;
  }

  // --- Formulario ----------------------------------------------------------------------

  async function cargarOpciones() {
    let r;
    try { r = await api("GET", "opciones"); } catch (e) { setTimeout(cargarOpciones, INTERVALO); return; }
    if (r.estado !== 200 || !r.datos) { setTimeout(cargarOpciones, INTERVALO); return; }
    const calidad = $("calidad");
    calidad.textContent = "";
    for (const c of r.datos.calidades) {
      const opcion = document.createElement("option");
      opcion.value = c.id;
      opcion.textContent = c.nombre;
      calidad.appendChild(opcion);
    }
    calidad.value = r.datos.calidad_inicial;
    $("carpeta").value = r.datos.carpeta_inicial;
    mostrar($("aviso-config"), r.datos.aviso_config
      ? "config.toml no es válido; se usan los valores por defecto: " + r.datos.aviso_config : "");
    const examinar = $("examinar");
    examinar.hidden = !r.datos.examinar_disponible;
  }

  async function examinar() {
    const boton = $("examinar");
    boton.disabled = true;
    try {
      const r = await api("POST", "examinar");
      if (r.estado === 200 && r.datos && r.datos.carpeta) $("carpeta").value = r.datos.carpeta;
    } catch (e) {
      // la consulta periódica muestra la pérdida de conexión
    } finally {
      boton.disabled = false;
    }
  }

  function plural(n, uno, varios) { return n + " " + (n === 1 ? uno : varios); }

  function resumir(datos) {
    const partes = [];
    if (datos.agregadas.length) partes.push(plural(datos.agregadas.length, "agregada", "agregadas"));
    if (datos.reencoladas.length) partes.push(plural(datos.reencoladas.length, "vuelve a la cola", "vuelven a la cola"));
    if (datos.ya_en_cola.length) partes.push(datos.ya_en_cola.length === 1
      ? "1 ya estaba en la cola" : datos.ya_en_cola.length + " ya estaban en la cola");
    const resumen = $("resumen");
    resumen.textContent = partes.join(", ");
    if (datos.lineas_invalidas.length) {
      const lista = document.createElement("ul");
      for (const l of datos.lineas_invalidas) {
        const item = document.createElement("li");
        item.textContent = "Línea " + l.linea + ": no es un enlace http o https (" + l.texto + ")";
        lista.appendChild(item);
      }
      resumen.appendChild(lista);
    }
    resumen.hidden = false;
  }

  async function agregar() {
    const boton = $("agregar");
    const texto = $("enlaces").value;
    mostrar($("error-agregar"), "");
    $("resumen").hidden = true;
    boton.disabled = true;
    try {
      const r = await api("POST", "agregar", {
        texto: texto, calidad: $("calidad").value, carpeta: $("carpeta").value, navegador: null,
      });
      if (r.estado === 200 && r.datos) {
        resumir(r.datos);
        // Quedan en el área solo las líneas que no se pudieron agregar.
        const lineas = texto.split(/\r?\n/);
        $("enlaces").value = r.datos.lineas_invalidas.map((l) => lineas[l.linea - 1]).join("\n");
        etiquetaEstado = null;
        consultar();
      } else {
        // 400: el mensaje bajo el formulario, sin borrar el texto pegado.
        mostrar($("error-agregar"), (r.datos && (r.datos.error || r.datos.motivo)) || "No se pudo agregar");
      }
    } catch (e) {
      mostrar($("error-agregar"), "Se perdió la conexión con la aplicación");
    } finally {
      boton.disabled = false;
    }
  }

  // --- Iniciar y Pausar ----------------------------------------------------------------

  function activar(boton, activo, motivo) {
    boton.setAttribute("aria-disabled", activo ? "false" : "true");
    boton.classList.toggle("inactivo", !activo);
    boton.disabled = false;
    boton.title = activo ? "" : (motivo || "");
    boton.dataset.motivo = activo ? "" : (motivo || "");
  }

  async function iniciar() {
    const boton = $("iniciar");
    if (boton.getAttribute("aria-disabled") === "true") {
      mostrar($("motivo"), boton.dataset.motivo);
      return;
    }
    mostrar($("motivo"), "");
    activar(boton, false, "");
    try {
      const r = await api("POST", "iniciar");
      if (r.estado === 409) mostrar($("motivo"), r.datos && r.datos.motivo);
      else if (r.estado !== 202) mostrar($("motivo"), (r.datos && r.datos.error) || "No se pudo iniciar");
      else resultadoCerrado = null;
    } catch (e) {
      mostrar($("motivo"), "Se perdió la conexión con la aplicación");
    }
    etiquetaEstado = null;
    consultar();
  }

  function pausar() {
    const boton = $("pausar");
    if (boton.getAttribute("aria-disabled") === "true") mostrar($("motivo"), boton.dataset.motivo);
  }

  // --- Cola --------------------------------------------------------------------------

  function tono(fila) {
    if (fila.estado === "downloading" && fila.sin_datos) return "naranja";
    if (fila.espera) return "naranja";
    return TONOS[fila.estado] || "gris";
  }

  function etiqueta(fila) {
    if (fila.estado === "failed") {
      return fila.reintentar_con_cookies ? "Falló: requiere iniciar sesión" : "Falló";
    }
    return fila.etiqueta;
  }

  function detalle(fila) {
    if (fila.detalle) return fila.detalle;
    if (fila.aviso) return fila.aviso;
    return DETALLE_POR_ESTADO[fila.estado] || "";
  }

  function crearFila() {
    const li = document.createElement("li");
    li.className = "fila";
    li.innerHTML =
      '<div class="identidad"><div class="titulo"></div><div class="direccion"></div></div>' +
      '<div class="progreso"><div class="cabeza"><span class="etiqueta"></span>' +
      '<span class="porcentaje"></span></div><div class="barra"><span></span></div>' +
      '<div class="detalle"></div></div><div class="lateral"></div>';
    return li;
  }

  function pintarFila(li, fila) {
    li.className = "fila tono-" + tono(fila);
    li.querySelector(".titulo").textContent = fila.titulo;
    li.querySelector(".titulo").title = fila.titulo;
    li.querySelector(".direccion").textContent = fila.direccion || fila.sitio || "";
    li.querySelector(".etiqueta").textContent = etiqueta(fila);
    const porcentaje = fila.porcentaje;
    li.querySelector(".porcentaje").textContent =
      porcentaje === null || porcentaje === undefined ? "" : Math.floor(porcentaje) + " %";
    li.querySelector(".barra > span").style.width = (porcentaje || 0) + "%";
    const texto = detalle(fila);
    li.querySelector(".detalle").textContent = texto;
    li.querySelector(".detalle").title = texto;
  }

  function pintarLista(datos) {
    const lista = $("lista");
    const vistos = new Set();
    let anterior = null;
    for (const fila of datos.cola.filas) {
      vistos.add(fila.id);
      let entrada = filas.get(fila.id);
      if (!entrada) {
        entrada = { elemento: crearFila(), clave: null };
        filas.set(fila.id, entrada);
      }
      const clave = JSON.stringify(fila);
      if (entrada.clave !== clave) { // solo se repintan las filas que cambian
        pintarFila(entrada.elemento, fila);
        entrada.clave = clave;
      }
      const siguiente = anterior ? anterior.nextSibling : lista.firstChild;
      if (siguiente !== entrada.elemento) lista.insertBefore(entrada.elemento, siguiente);
      anterior = entrada.elemento;
    }
    for (const [id, entrada] of filas) {
      if (!vistos.has(id)) { entrada.elemento.remove(); filas.delete(id); }
    }
    const corrupta = datos.avisos.cola_corrupta;
    mostrar($("cola-corrupta"), corrupta);
    lista.hidden = Boolean(corrupta);
    $("vacia").hidden = Boolean(corrupta) || datos.cola.filas.length > 0;
  }

  function pintarCabecera(datos) {
    const total = datos.cola.total;
    $("subtitulo").textContent = plural(total, "descarga", "descargas") + " · una a la vez";
    const p = datos.procesador;
    const desdeTerminal = p.origen === "cli" && p.estado !== "ninguno";
    mostrar($("origen"), desdeTerminal ? "En marcha desde la terminal" : "");
    activar($("iniciar"), p.puede_iniciar, p.motivo);
    activar($("pausar"), PAUSAR_DISPONIBLE && p.puede_detener,
      PAUSAR_DISPONIBLE ? p.motivo : "Pausar todavía no está disponible en esta versión.");
    if (p.puede_iniciar && $("motivo").textContent === p.motivo) mostrar($("motivo"), "");
  }

  function pintarResultado(datos) {
    const ultima = datos.ultima_ejecucion;
    const p = datos.procesador;
    const enCurso = p.origen === "app" && p.estado !== "ninguno";
    const franja = $("resultado");
    if (!ultima || enCurso || resultadoCerrado === ultima.ejecucion) {
      franja.hidden = true;
      return;
    }
    const fallidas = (ultima.fallidas || []).length;
    let texto = plural(ultima.completadas || 0, "completada", "completadas") + ", " +
      plural(fallidas, "fallida", "fallidas");
    if (ultima.error && ultima.error.mensaje) texto += ". " + ultima.error.mensaje;
    $("resultado-texto").textContent = "Última ejecución: " + texto;
    franja.classList.toggle("con-error", Boolean(ultima.error) || fallidas > 0);
    franja.dataset.ejecucion = ultima.ejecucion;
    franja.hidden = false;
  }

  function pintarPie(datos) {
    const conexion = $("conexion");
    const enMarcha = datos.procesador.estado !== "ninguno";
    let texto = "Sin procesamiento en marcha";
    let clase = "";
    if (enMarcha && datos.en_linea === true) { texto = "Con conexión"; clase = "con"; }
    else if (enMarcha && datos.en_linea === false) { texto = "Sin conexión"; clase = "sin"; }
    else if (enMarcha && datos.en_directo === false) { texto = "Sin datos en directo"; }
    else if (enMarcha) { texto = "Procesamiento en marcha"; }
    conexion.className = "conexion " + clase;
    $("conexion-texto").textContent = texto;
    $("registro-no-disponible").hidden = !datos.avisos.registro_no_disponible;
  }

  function pintar(datos) {
    ultimoEstado = datos;
    pintarCabecera(datos);
    pintarResultado(datos);
    pintarLista(datos);
    pintarPie(datos);
  }

  let temporizador = null;

  async function consultar() {
    clearTimeout(temporizador);
    try {
      const cabeceras = etiquetaEstado ? { "If-None-Match": etiquetaEstado } : {};
      const r = await api("GET", "estado", undefined, cabeceras);
      $("sin-conexion-app").hidden = true;
      if (r.estado === 200 && r.datos) {
        etiquetaEstado = r.cabeceras.get("ETag");
        pintar(r.datos);
      } else if (r.estado !== 304) {
        etiquetaEstado = null;
      }
    } catch (e) {
      $("sin-conexion-app").hidden = false; // la página reintenta cada segundo
    }
    temporizador = setTimeout(consultar, INTERVALO);
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("examinar").addEventListener("click", examinar);
    $("agregar").addEventListener("click", agregar);
    $("iniciar").addEventListener("click", iniciar);
    $("pausar").addEventListener("click", pausar);
    $("cerrar-resultado").addEventListener("click", function () {
      resultadoCerrado = $("resultado").dataset.ejecucion;
      $("resultado").hidden = true;
    });
    $("enlaces").addEventListener("keydown", function (evento) {
      if (evento.key === "Enter" && evento.ctrlKey) agregar();
    });
    cargarOpciones();
    consultar();
  });

  window.gestor = { ultimoEstado: function () { return ultimoEstado; } };
})();
