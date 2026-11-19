"use strict";

/* ===================================================================== utilidades */
const $ = (sel, el = document) => el.querySelector(sel);
const DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const DIAS_LETRA = ["D", "L", "M", "X", "J", "V", "S"];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const FUENTES = { google: "Google Flights", ryanair: "Ryanair", wizz: "Wizz Air", aviasales: "Aviasales" };
const ORDEN_FUENTES = ["google", "ryanair", "aviasales", "wizz"];
const DURACION_ESTIMADA = new Set(["wizz"]);   // duración calculada por distancia
// El color sigue a la entidad, siempre el mismo: total = 1, Google = 2, Ryanair = 3, Aviasales = 4, Wizz = 5.
const COLOR_FUENTE = { todas: "--s1", google: "--s2", ryanair: "--s3", aviasales: "--s4", wizz: "--s5" };

function durTxt(min, estimada = false) {
  if (!min) return "";
  const h = Math.floor(min / 60), m = min % 60;
  return `${estimada ? "≈" : ""}${h ? `${h} h ${dosCifras(m)} min` : `${m} min`}`;
}
const durTramo = (t, deVuelta = false) => durTxt(deVuelta ? t.duracion_vuelta_min : t.duracion_min, DURACION_ESTIMADA.has(t.fuente));

function duracionesCombo(c) {   // [texto ida, texto vuelta]
  return c.tipo === "separados" ? [durTramo(c.tramo_ida), durTramo(c.tramo_vuelta)] : [durTramo(c.tramo_ida), durTramo(c.tramo_ida, true)];
}

const est = {
  alertas: [], sel: null, detalle: null, servidor: null,
  ultimaFin: null, estadoCargado: false, graficos: {}, elegida: null,
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fecha = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const fCorta = (iso) => { const d = fecha(iso); return `${DIAS[d.getDay()]} ${d.getDate()} ${MESES[d.getMonth()]}`; };
const dosCifras = (n) => String(n).padStart(2, "0");
const fHora = (iso) => { const d = new Date(iso); return `${d.getDate()} ${MESES[d.getMonth()]} ${dosCifras(d.getHours())}:${dosCifras(d.getMinutes())}`; };
const soloHora = (iso) => { const d = new Date(iso); return `${dosCifras(d.getHours())}:${dosCifras(d.getMinutes())}`; };
const hoyIso = () => { const d = new Date(); return `${d.getFullYear()}-${dosCifras(d.getMonth() + 1)}-${dosCifras(d.getDate())}`; };
const oscuro = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};

function euros(x) {
  if (x == null || Number.isNaN(x)) return "—";
  const entero = Math.abs(x - Math.round(x)) < 0.005;
  return new Intl.NumberFormat("es-ES", {
    style: "currency", currency: "EUR",
    minimumFractionDigits: entero ? 0 : 2, maximumFractionDigits: entero ? 0 : 2,
  }).format(x);
}

function hace(iso) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "ahora mismo";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  return `hace ${d} día${d > 1 ? "s" : ""}`;
}

async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, { headers: { "Content-Type": "application/json" }, ...opciones });
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(datos.error || `Error ${r.status}`), { codigo: datos.codigo });
  return datos;
}

/* ===================================================================== estado del servidor */
function pintarEstado(s) {
  const fichas = (s.plan_hoy || []).map((p) => {
    const h = p.hora.slice(11, 16);
    const icono = { hecha: "✓", pendiente: "◷", perdida: "✕" }[p.estado] || "";
    const texto = { hecha: "hecha", pendiente: "pendiente", perdida: "perdida (equipo apagado)" }[p.estado];
    return `<span class="ficha ${p.estado}" title="Revisión de las ${h}: ${texto}" aria-label="${h} ${texto}">${icono} ${h}</span>`;
  }).join("");
  const plan = fichas
    ? `<span class="grupo">Revisiones de hoy: ${fichas}</span>`
    : `<span class="grupo">No quedan revisiones automáticas hoy</span>`;
  const nube = s.modo === "nube";
  document.body.classList.toggle("modo-nube", nube);
  const tg = s.telegram
    ? `<span class="grupo"><span class="punto bien"></span>Telegram conectado${nube ? " en GitHub" : ""}</span>`
    : `<span class="grupo"><span class="punto aviso"></span>Telegram sin configurar${nube ? " en GitHub" : ""} · <button class="enlace-boton" type="button" data-abrir-ajustes>configurar</button></span>`;
  let prog = "";
  if (s.ocupado && nube) {
    prog = `<span class="progreso"><progress></progress>Revisión en curso en GitHub</span>`;
  } else if (s.ocupado) {
    prog = `<span class="progreso"><progress max="${s.total || 1}" value="${s.paso || 0}"></progress>${esc(s.texto)} · ${s.paso}/${s.total}${s.pendientes ? ` · ${s.pendientes} en cola` : ""}</span>`;
  }
  let gh = "";
  if (nube) {
    const ult = s.ultima_ejecucion;
    const fallo = ult && ult.resultado === "failure";
    gh = s.error
      ? `<span class="grupo"><span class="punto critico"></span>Sin conexión con GitHub: ${esc(s.error)}</span>`
      : `<span class="grupo"><span class="punto ${fallo ? "critico" : "bien"}"></span>Revisa GitHub (${esc(s.repo)})${fallo ? ` · la última ejecución falló, <a href="${esc(ult.url)}" target="_blank" rel="noopener">ver</a>` : ""}</span>`;
  }
  $("#estado").innerHTML = prog + gh + plan + tg;
  const caja = $("#progreso-detalle");
  if (caja) caja.innerHTML = progresoDetalle(s);
}

function progresoDetalle(s) {
  if (s.modo === "nube") {
    return s.ocupado
      ? `<progress style="width:240px"></progress><p class="nota">GitHub está revisando. La página se actualizará sola al terminar.</p>`
      : `<p class="nota">Pedida a GitHub: suele tardar 1–2 minutos en empezar y 2–5 en terminar. La página se actualizará sola.</p>`;
  }
  if (s.ocupado && s.alerta_id === est.sel) {
    return `<progress max="${s.total || 1}" value="${s.paso || 0}" style="width:240px"></progress>
            <p class="nota">${esc(s.texto)} · ${s.paso}/${s.total} consultas</p>`;
  }
  return `<p class="nota">En cola: empezará en cuanto termine la revisión en curso.</p>`;
}

async function refrescarEstado() {
  try {
    const s = await api("/api/estado");
    est.servidor = s;
    pintarEstado(s);
    if (est.estadoCargado && s.ultima_fin !== est.ultimaFin) {
      est.ultimaFin = s.ultima_fin;
      await cargarAlertas();
      if (est.sel) await cargarDetalle();
    }
    est.ultimaFin = s.ultima_fin;
    est.estadoCargado = true;
  } catch (e) {
    est.servidor = null;
    $("#estado").innerHTML = `<span class="grupo"><span class="punto critico"></span>Sin conexión con el buscador: ¿está abierto arrancar.bat?</span>`;
  }
}

async function sondear() {   // bucle único de consulta del estado
  await refrescarEstado();
  const rapido = est.servidor && (est.servidor.ocupado || est.servidor.pendientes);
  setTimeout(sondear, rapido ? 2500 : 15000);
}

/* ===================================================================== alertas */
async function cargarAlertas() {
  est.alertas = await api("/api/alertas");
  const ul = $("#lista-alertas");
  if (!est.alertas.length) {
    ul.innerHTML = `<li><p class="vacio">Todavía no tienes alertas. Crea la primera arriba.</p></li>`;
    return;
  }
  ul.innerHTML = est.alertas.map((a) => {
    const p = a.ultima?.mejor_precio;
    const bien = p != null && p <= a.precio_objetivo;
    const precio = p != null
      ? `<span class="precio">${bien ? '<span class="punto bien" aria-hidden="true"></span> ' : ""}${euros(p)}</span>`
      : `<span class="precio meta">${a.revisiones ? "sin vuelos" : "buscando…"}</span>`;
    return `<li><button type="button" data-id="${a.id}" aria-current="${a.id === est.sel}" class="${a.activa ? "" : "pausada"}">
      <span class="ruta"><span>${esc(etiquetaAeropuerto(a.origen, true))} → ${esc(etiquetaAeropuerto(a.destino, true))}${a.activa ? "" : " · en pausa"}</span>${precio}</span>
      <span class="meta">${fCorta(a.fecha_desde)} – ${fCorta(a.fecha_hasta)} · ${a.dias} días${a.flex ? ` ±${a.flex}` : ""} · objetivo ${euros(a.precio_objetivo)}</span>
      ${a.minimo != null ? `<span class="meta">★ Mejor hasta ahora: <b>${euros(a.minimo)}</b> el ${fHora(a.minimo_fecha)}</span>` : ""}
    </button></li>`;
  }).join("");
}

$("#lista-alertas").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-id]");
  if (b) seleccionar(Number(b.dataset.id));
});

async function seleccionar(id) {
  est.sel = id;
  est.elegida = null;
  document.querySelectorAll("#lista-alertas button[data-id]").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.id) === id)));
  await cargarDetalle();
}

async function cargarDetalle() {
  if (!est.sel) return pintarVacio();
  try {
    est.detalle = await api(`/api/alertas/${est.sel}`);
  } catch (e) {
    est.sel = null;
    return pintarVacio();
  }
  pintarDetalle();
}

function pintarVacio() {
  destruirGraficos();
  $("#detalle").innerHTML = `<div class="tarjeta estado-vacio">
    <h2>Crea una alerta para empezar</h2>
    <p>Indica destino, fechas, días y el precio que quieres pagar. El buscador consultará Google Flights, Ryanair y Wizz Air 5 veces al día, a horas al azar, y te escribirá por Telegram cuando encuentre tu precio.</p>
  </div>`;
}

/* ===================================================================== aeropuertos */
// Lista de static/aeropuertos.json (OurAirports, ver herramientas/generar_aeropuertos.py).
const AERO = { lista: [], porIata: new Map(), cargado: false };
const normal = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
  .replace(/[^a-z0-9]+/g, " ").trim();

async function cargarAeropuertos() {
  try {
    const filas = await (await fetch("/static/aeropuertos.json")).json();
    let paises = null;
    try { paises = new Intl.DisplayNames(["es"], { type: "region" }); } catch (e) { /* navegador antiguo */ }
    const nombrePais = new Map(), porCiudad = new Map();
    AERO.lista = filas.map(([iata, ciudad, nombre, pais, tam, alias, popular]) => {
      if (!nombrePais.has(pais)) {
        let n = pais;
        try { n = paises?.of(pais) || pais; } catch (e) { /* código raro */ }
        nombrePais.set(pais, n);
      }
      const k = `${ciudad}|${pais}`;   // Barcelona (España) y Barcelona (Venezuela) no cuentan como la misma
      porCiudad.set(k, (porCiudad.get(k) || 0) + 1);
      const pn = nombrePais.get(pais);
      return { iata, ciudad, nombre, pais: pn, tam, popular: Boolean(popular), k,
        _i: iata.toLowerCase(), _c: ` ${normal(ciudad)}`, _n: ` ${normal(nombre)}`, _a: ` ${normal(alias)}`, _p: ` ${normal(pn)}` };
    });
    AERO.lista.forEach((a) => { a.compartida = porCiudad.get(a.k) > 1; AERO.porIata.set(a.iata, a); });
    AERO.cargado = true;
  } catch (e) { /* sin lista: se siguen aceptando códigos de 3 letras */ }
}

// Busca por código, ciudad (en español o en inglés), nombre del aeropuerto, isla, región o país.
function buscarAeropuertos(texto, n = 8) {
  const t = normal(texto), p = ` ${t}`;
  if (!t) return [];
  const res = [];
  for (const a of AERO.lista) {
    let s = 0;
    if (a._i === t) s = 1000;
    else if (a._c.startsWith(p)) s = 600;
    else if (a._a.startsWith(p)) s = 550;   // nombre en inglés o ciudad a la que sirve («London», Girona → Barcelona)
    else if (a._c.includes(p)) s = 500;
    else if (a._a.includes(p)) s = 420;
    else if (t.length >= 3 && a._p.startsWith(p)) s = 380;
    else if (a._n.includes(p)) s = 300;
    else if (t.length >= 4 && a._n.includes(t)) s = 120;
    if (s) res.push([s - a.tam * 70 + (a.popular ? 60 : 0), a]);
  }
  res.sort((x, y) => y[0] - x[0] || x[1].ciudad.localeCompare(y[1].ciudad, "es"));
  return res.slice(0, n).map((x) => x[1]);
}

// «Lisboa (LIS)»; en corto «Lisboa», o «Londres STN» si la ciudad tiene varios aeropuertos.
function etiquetaAeropuerto(iata, corta = false) {
  const a = AERO.porIata.get(iata);
  if (!a) return iata;
  if (!corta) return `${a.ciudad} (${iata})`;
  return a.compartida ? `${a.ciudad} ${iata}` : a.ciudad;
}

// Campo de texto con sugerencias (patrón combobox de ARIA). El código va en el campo oculto.
function selectorAeropuerto(input) {
  const oculto = input.form.elements[input.dataset.campo];
  const lista = document.getElementById(input.getAttribute("aria-controls"));
  let opciones = [], activa = -1;
  const cerrar = () => {
    lista.hidden = true; activa = -1;
    input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant");
  };
  const fijar = (iata) => { oculto.value = iata || ""; input.value = iata ? etiquetaAeropuerto(iata) : ""; };
  const elegir = (a) => { fijar(a.iata); cerrar(); };
  const pintar = () => {
    lista.innerHTML = opciones.length
      ? opciones.map((a, i) => `<li id="${lista.id}-${i}" role="option" aria-selected="${i === activa}" data-i="${i}">
          <span class="combo-1"><b>${esc(a.ciudad)}</b><span class="iata">${a.iata}</span></span>
          <span class="combo-2">${esc(a.nombre)} · ${esc(a.pais)}</span></li>`).join("")
      : `<li class="combo-vacio" role="presentation">${AERO.cargado
          ? `Nada para «${esc(input.value.trim())}». Prueba con la ciudad, el país o el código de 3 letras.`
          : "Cargando aeropuertos…"}</li>`;
    lista.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (activa >= 0) {
      input.setAttribute("aria-activedescendant", `${lista.id}-${activa}`);
      lista.children[activa]?.scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  };
  const abrir = () => { opciones = buscarAeropuertos(input.value); activa = opciones.length ? 0 : -1; pintar(); };
  // Texto escrito sin elegir de la lista: vale si es un código exacto o una ciudad que no deja
  // dudas («Lisboa»; «Barcelona» = BCN y no la de Venezuela). «Londres» obliga a elegir aeropuerto.
  const resolver = () => {
    const t = input.value.trim();
    if (!t || oculto.value) return;
    const r = buscarAeropuertos(t, 20);
    const codigo = r.find((a) => a._i === normal(t));
    const exacta = r.filter((a) => a._c.trim() === normal(t));
    const habituales = exacta.filter((a) => a.popular);
    const unica = codigo || (exacta.length === 1 ? exacta[0] : habituales.length === 1 ? habituales[0] : null);
    if (unica) elegir(unica);
    else if (/^[a-z]{3}$/i.test(t) && !r.length) oculto.value = t.toUpperCase();
  };
  input.addEventListener("input", () => { oculto.value = ""; if (input.value.trim()) abrir(); else cerrar(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (lista.hidden) return abrir();
      if (!opciones.length) return;
      activa = (activa + (e.key === "ArrowDown" ? 1 : -1) + opciones.length) % opciones.length;
      pintar();
    } else if (e.key === "Enter" && !lista.hidden && opciones[activa]) {
      e.preventDefault(); elegir(opciones[activa]);
    } else if (e.key === "Escape" && !lista.hidden) {
      e.preventDefault(); cerrar();
    }
  });
  lista.addEventListener("mousedown", (e) => {
    e.preventDefault();   // que el campo no pierda el foco antes de elegir
    const li = e.target.closest("li[data-i]");
    if (li) elegir(opciones[Number(li.dataset.i)]);
  });
  input.addEventListener("focus", () => input.select());
  input.addEventListener("blur", () => { cerrar(); resolver(); });
  return { fijar, resolver, codigo: () => oculto.value };
}

const selectores = {};

/* ===================================================================== formulario */
(function prepararFormulario() {
  const f = $("#form-alerta");
  selectores.origen = selectorAeropuerto($("#origen-txt"));
  selectores.destino = selectorAeropuerto($("#destino-txt"));
  selectores.origen.fijar(f.origen.value);
  const manana = new Date(Date.now() + 86400000);
  const min = `${manana.getFullYear()}-${dosCifras(manana.getMonth() + 1)}-${dosCifras(manana.getDate())}`;
  f.fecha_desde.min = min;
  f.fecha_hasta.min = min;
  f.fecha_desde.addEventListener("change", () => { f.fecha_hasta.min = f.fecha_desde.value || min; });
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#form-error");
    selectores.origen.resolver();
    selectores.destino.resolver();
    const falta = !selectores.origen.codigo() ? "origen" : !selectores.destino.codigo() ? "destino" : null;
    if (falta) {
      err.textContent = `Elige el ${falta} en la lista de sugerencias (escribe una ciudad, isla, país o código).`;
      err.hidden = false;
      $(`#${falta}-txt`).focus();
      return;
    }
    const fd = new FormData(f);
    const datos = Object.fromEntries(fd.entries());
    datos.solo_directos = fd.get("solo_directos") === "on";
    const boton = f.querySelector("button[type=submit]");
    boton.disabled = true;
    try {
      const { id } = await api("/api/alertas", { method: "POST", body: JSON.stringify(datos) });
      err.hidden = true;
      selectores.destino.fijar("");
      await cargarAlertas();
      await seleccionar(id);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      boton.disabled = false;
    }
  });
})();

/* ===================================================================== detalle */
function destruirGraficos() {
  Object.values(est.graficos).forEach((g) => g.destroy());
  est.graficos = {};
}

function pintarDetalle() {
  destruirGraficos();
  const d = est.detalle;
  const a = d.alerta;
  const u = d.ultima;
  const partes = [htmlCabecera(a)];
  if (!u) {
    partes.push(`<section class="tarjeta"><h2>Primera búsqueda</h2>
      <p class="sub">Consultando Google Flights día a día y Ryanair y Wizz Air por meses. Suele tardar entre 2 y 5 minutos.</p>
      <div id="progreso-detalle">${est.servidor ? progresoDetalle(est.servidor) : ""}</div></section>`);
    $("#detalle").innerHTML = partes.join("");
    return;
  }
  const combo = est.elegida ? u.combos.find((c) => clave(c) === est.elegida) : null;
  partes.push(htmlCifras(d), htmlOpcion(combo || u.mejor, Boolean(combo), a), htmlHistorial(d),
    htmlAlternativas(u), htmlCalendario(d), htmlHorario(u.horario), htmlTop(u), htmlAvisos(d), htmlErrores(u));
  $("#detalle").innerHTML = partes.join("");
  graficoHistorial(d);
  graficoHorario(u.horario);
  prepararCalendario();
}

const clave = (c) => `${c.ida}|${c.dias}`;

function htmlCabecera(a) {
  const extras = [`${a.dias} días${a.flex ? ` (± ${a.flex})` : ""}`];
  if (a.solo_directos) extras.push("solo directos");
  if (a.max_horas) extras.push(`máx. ${String(a.max_horas).replace(".", ",")} h por trayecto`);
  return `<section class="tarjeta cabecera">
    <div>
      <h1>${esc(etiquetaAeropuerto(a.origen))} → ${esc(etiquetaAeropuerto(a.destino))}${a.activa ? "" : " · en pausa"}</h1>
      <p class="sub">Ida desde el ${fCorta(a.fecha_desde)} · vuelta hasta el ${fCorta(a.fecha_hasta)} · ${extras.join(" · ")} · objetivo <b>${euros(a.precio_objetivo)}</b></p>
    </div>
    <div class="botones">
      <button class="boton" type="button" data-acc="revisar">Revisar ahora</button>
      <button class="boton boton-secundario" type="button" data-acc="objetivo">Cambiar objetivo</button>
      <button class="boton boton-secundario" type="button" data-acc="horas">Límite de horas</button>
      <button class="boton boton-secundario" type="button" data-acc="pausa">${a.activa ? "Pausar" : "Reanudar"}</button>
      <button class="boton boton-secundario boton-peligro" type="button" data-acc="borrar">Borrar</button>
    </div>
  </section>`;
}

function htmlCifras(d) {
  const a = d.alerta, u = d.ultima, m = u.mejor;
  let heroe;
  if (m) {
    const dif = m.precio - a.precio_objetivo;
    const delta = dif <= 0
      ? `<span class="delta bien">✓ ${euros(-dif)} por debajo de tu objetivo</span>`
      : `<span class="delta"><span class="punto aviso" aria-hidden="true"></span>${euros(dif)} por encima de tu objetivo</span>`;
    heroe = `<div class="cifra heroe"><span class="etiqueta">Mejor precio en tus fechas</span><span class="valor">${euros(m.precio)}</span>${delta}</div>`;
  } else {
    heroe = `<div class="cifra heroe"><span class="etiqueta">Mejor precio en tus fechas</span><span class="valor">—</span><span class="delta">Sin vuelos que encajen</span></div>`;
  }
  const minimo = d.record;
  const hoy = d.historial.filter((h) => h.inicio.slice(0, 10) === hoyIso()).length;
  const fuentesTxt = u.fuentes_con_datos.map((f) => FUENTES[f]).join(", ") || "ninguna";
  return `<div class="cifras">
    ${heroe}
    <div class="cifra"><span class="etiqueta">Mejor hasta ahora</span><span class="valor">${euros(minimo?.precio)}</span><span class="delta">${minimo ? `el ${fHora(minimo.inicio)}` : ""}</span></div>
    <div class="cifra"><span class="etiqueta">Revisiones</span><span class="valor">${d.historial.length}</span><span class="delta">${hoy} hoy</span></div>
    <div class="cifra"><span class="etiqueta">Última revisión</span><span class="valor" style="font-size:20px">${hace(u.revision.inicio)}</span><span class="delta">Con datos: ${esc(fuentesTxt)}</span></div>
  </div>`;
}

function descTramo(t, duraciones) {
  const partes = [];
  if (t.aerolinea) partes.push(esc(t.aerolinea));
  partes.push(t.escalas ? `${t.escalas} escala${t.escalas > 1 ? "s" : ""}` : "directo");
  if (duraciones) partes.push(duraciones);
  return partes.join(" · ");
}

function cajaTramo(titulo, fechaTxt, t, precio, duraciones) {
  return `<div class="tramo">
    <span class="t">${titulo} · ${esc(FUENTES[t.fuente] || t.fuente)}</span>
    <span class="f">${fechaTxt}${t.hora ? ` · ${esc(t.hora)}` : ""}</span>
    <span>${descTramo(t, duraciones)} · <b>${euros(precio)}</b></span>
    ${t.enlace ? `<a href="${esc(t.enlace)}" target="_blank" rel="noopener">Ver en ${esc(FUENTES[t.fuente] || t.fuente)}</a>` : ""}
  </div>`;
}

function htmlOpcion(c, elegida, a) {
  const titulo = elegida ? "Opción seleccionada" : "La mejor opción en tus fechas";
  const volver = elegida ? `<button class="enlace-boton" type="button" data-acc="volver-mejor">Volver a la mejor</button>` : "";
  if (!c) {
    return `<section class="tarjeta opcion" id="opcion"><h2>${titulo}</h2>
      <p class="vacio">No se han encontrado vuelos que encajen en tus fechas y tu duración. Mira las alternativas y el calendario.</p></section>`;
  }
  let cuerpo;
  if (c.tipo === "separados") {
    cuerpo = `<div class="tramos">${cajaTramo("Ida", fCorta(c.ida), c.tramo_ida, c.tramo_ida.precio, durTramo(c.tramo_ida))}${cajaTramo("Vuelta", fCorta(c.vuelta), c.tramo_vuelta, c.tramo_vuelta.precio, durTramo(c.tramo_vuelta))}</div>
      <p class="nota">Son dos billetes de solo ida y se compran por separado. ¿Algo más barato como ida y vuelta? <a href="${esc(c.enlace_google_iv)}" target="_blank" rel="noopener">Compruébalo en Google Flights</a>.</p>`;
  } else {
    const [di, dv] = duracionesCombo(c);
    const tiempos = di && dv ? `ida ${di}, vuelta ${dv}` : di ? `ida ${di}` : "";
    cuerpo = `<div class="tramos">${cajaTramo("Ida y vuelta", `${fCorta(c.ida)} → ${fCorta(c.vuelta)}`, c.tramo_ida, c.precio, tiempos)}</div>
      <p class="nota">Un único billete de ida y vuelta (la hora es la de la ida).</p>`;
  }
  const fuera = c.en_rango ? "" : ` <span class="meta">· fuera de tus fechas o de tu duración</span>`;
  return `<section class="tarjeta opcion" id="opcion">
    <div class="cabecera"><h2>${titulo}: ${euros(c.precio)} · ${c.dias} días${fuera}</h2>${volver}</div>${cuerpo}</section>`;
}

function htmlAlternativas(u) {
  const base = u.mejor ? ` Se comparan con tu mejor opción (${euros(u.mejor.precio)}).` : "";
  const lista = u.alternativas;
  if (!lista.length) {
    return `<section class="tarjeta"><h2>Cómo pagar menos</h2>
      <p class="vacio">Con los precios de hoy no hay nada claramente más barato moviendo fechas, duración o escalas: tus fechas ya están bien.</p></section>`;
  }
  const items = lista.map((x) => {
    const ver = x.combo ? ` · <button class="enlace-boton" type="button" data-ver="${clave(x.combo)}">ver</button>` : "";
    let cuanto = "";
    if (x.ahorro) cuanto = `<div class="cuanto"><b>${euros(x.precio)}</b><span>−${euros(x.ahorro)}</span></div>`;
    else if (x.tipo === "dia_semana") cuanto = `<div class="cuanto"><b>${euros(x.precio)}</b><span class="meta">de media</span></div>`;
    return `<li><div class="titulo">${esc(x.titulo)}</div><div class="texto">${esc(x.texto)}${ver}</div>${cuanto}</li>`;
  }).join("");
  return `<section class="tarjeta"><h2>Cómo pagar menos</h2><p class="sub">Alternativas encontradas en la última revisión.${base}</p><ul class="alternativas">${items}</ul></section>`;
}

const escalasTxt = (t) => (t.escalas ? `${t.escalas} escala${t.escalas > 1 ? "s" : ""}` : "directo");

// «Ryanair + Wizz Air» (dos billetes) o «Iberia vía Google Flights (un billete)».
function quienVuelo(v) {
  if (v.tipo !== "separados") return `${quien(v.tramo_ida)} (un billete)`;
  const ida = quien(v.tramo_ida), vuelta = quien(v.tramo_vuelta);
  return ida === vuelta ? `${ida} (dos billetes)` : `${ida} + ${vuelta}`;
}

// El vuelo más barato visto hasta ahora en tus fechas y qué cuesta hoy.
function htmlRecord(d) {
  const r = d.record, a = d.alerta, u = d.ultima;
  if (!r) return `<p class="vacio">Todavía no se ha encontrado ningún vuelo que encaje en tus fechas.</p>`;
  const ahora = u.combos.find((c) => clave(c) === clave(r));
  let hoy;
  if (u.revision.id === r.revision_id) hoy = "Es el precio de la última revisión.";
  else if (!ahora) hoy = "En la última revisión esa combinación ya no aparece.";
  else {
    const dif = ahora.precio - r.precio;
    hoy = Math.abs(dif) < 0.5
      ? `En la última revisión seguía a ${euros(ahora.precio)}.`
      : `En la última revisión esa misma combinación costaba ${euros(ahora.precio)} (${dif > 0 ? "+" : "−"}${euros(Math.abs(dif))}).`;
  }
  const ver = ahora ? ` <button class="enlace-boton" type="button" data-ver="${clave(r)}">ver</button>` : "";
  const objetivo = r.precio <= a.precio_objetivo
    ? `<span class="bien-texto">✓ ${euros(a.precio_objetivo - r.precio)} por debajo de tu objetivo</span>`
    : `${euros(r.precio - a.precio_objetivo)} por encima de tu objetivo`;
  const [di, dv] = duracionesCombo(r);
  const tramos = r.tipo === "separados"
    ? `<span>Ida: ${quien(r.tramo_ida)} · ${escalasTxt(r.tramo_ida)}${di ? ` · ${di}` : ""} · ${euros(r.tramo_ida.precio)}</span>
       <span>Vuelta: ${quien(r.tramo_vuelta)} · ${escalasTxt(r.tramo_vuelta)}${dv ? ` · ${dv}` : ""} · ${euros(r.tramo_vuelta.precio)}</span>`
    : `<span>Un billete de ida y vuelta: ${quien(r.tramo_ida)} · ${escalasTxt(r.tramo_ida)}${di ? ` · ida ${di}` : ""}${dv ? `, vuelta ${dv}` : ""}</span>`;
  return `<div class="record">
    <div class="record-precio"><span class="etiqueta">★ Mejor precio hasta ahora</span>
      <span class="valor">${euros(r.precio)}</span><span class="nota">${objetivo}</span></div>
    <div class="record-vuelo"><b>${fCorta(r.ida)} → ${fCorta(r.vuelta)} · ${r.dias} días</b>${tramos}
      <span class="nota">Visto por primera vez el ${fHora(r.inicio)} (${hace(r.inicio)}). ${hoy}${ver}</span></div>
  </div>`;
}

function htmlHistorial(d) {
  const h = d.historial, a = d.alerta, rid = d.record?.revision_id;
  const fuentes = ORDEN_FUENTES.filter((f) => h.some((x) => x.por_fuente?.[f] != null));
  let previo = null;
  const filas = h.map((x) => {
    let cambio = `<span class="meta">—</span>`;
    if (x.mejor_precio != null && previo != null) {
      const dif = x.mejor_precio - previo;
      cambio = Math.abs(dif) < 0.5 ? `<span class="meta">=</span>`
        : dif < 0 ? `<span class="baja">▼ ${euros(-dif)}</span>` : `<span class="sube">▲ ${euros(dif)}</span>`;
    }
    if (x.mejor_precio != null) previo = x.mejor_precio;
    const v = x.vuelo;
    const vuelo = v ? `${fCorta(v.ida)} → ${fCorta(v.vuelta)} · ${v.dias} días · ${quienVuelo(v)}`
      : `<span class="meta">sin vuelos en tus fechas</span>`;
    const objetivo = x.mejor_precio != null && x.mejor_precio <= a.precio_objetivo ? " ✓" : "";
    const marca = x.id === rid ? ` <span class="chip-record">★ mejor</span>` : "";
    return `<tr class="${x.id === rid ? "fila-record" : ""}">
      <td>${fHora(x.inicio)}<span class="meta"> · ${x.motivo === "programada" ? "automática" : "manual"}</span></td>
      <td class="num"><b>${euros(x.mejor_precio)}</b>${objetivo}${marca}</td><td class="num">${cambio}</td><td>${vuelo}</td>
      ${fuentes.map((f) => `<td class="num">${euros(x.por_fuente?.[f])}</td>`).join("")}</tr>`;
  }).reverse().join("");
  return `<section class="tarjeta"><h2>Historial de precios</h2>
    <p class="sub">Mejor ida y vuelta en tus fechas en cada revisión. El gráfico separa también lo que daría cada web por sí sola.</p>
    ${htmlRecord(d)}
    <div class="lienzo"><canvas id="g-historial" role="img" aria-label="Evolución del mejor precio por revisión"></canvas></div>
    <h3 class="titulo-tabla">Todas las revisiones (${h.length}), de la más reciente a la más antigua</h3>
    <div class="historial-scroll"><table class="tabla">
      <thead><tr><th>Revisión</th><th class="num">Mejor precio</th><th class="num" title="Frente a la revisión anterior">Cambio</th><th>Vuelo</th>${fuentes.map((f) => `<th class="num">Solo ${FUENTES[f]}</th>`).join("")}</tr></thead>
      <tbody>${filas}</tbody></table></div>
    <p class="nota">✓ = por debajo de tu objetivo · «Solo …» = lo que habría costado comprando todo en esa web.</p></section>`;
}

/* ---------- calendario de precios (filas = días en destino, columnas = día de salida) */
function pasoEscala(precio, ordenados) {
  let menores = 0;
  while (menores < ordenados.length && ordenados[menores] < precio) menores++;
  const frac = menores / ordenados.length;
  return 6 - Math.min(6, Math.floor(frac * 7));   // 6 = de los más baratos
}

function htmlCalendario(d) {
  const u = d.ultima, a = d.alerta;
  if (!u.combos.length) {
    return `<section class="tarjeta"><h2>Calendario de precios</h2><p class="vacio">Sin datos en la última revisión.</p></section>`;
  }
  const idas = u.combos.map((c) => c.ida).sort();
  const primera = fecha(idas[0]), ultima = fecha(idas[idas.length - 1]);
  const columnas = [];
  for (let x = new Date(primera); x <= ultima; x.setDate(x.getDate() + 1)) {
    columnas.push(`${x.getFullYear()}-${dosCifras(x.getMonth() + 1)}-${dosCifras(x.getDate())}`);
  }
  const mapa = new Map(u.combos.map((c) => [clave(c), c]));
  const ordenados = u.combos.map((c) => c.precio).sort((x, y) => x - y);
  const esOscuro = oscuro();
  const cab = columnas.map((iso, i) => {
    const f = fecha(iso);
    const mes = i === 0 || f.getDate() === 1 ? MESES[f.getMonth()] : "";
    const finde = f.getDay() === 0 || f.getDay() === 6;
    return `<th class="dia-col${finde ? " finde" : ""}" scope="col"><span class="mes">${mes}</span>${DIAS_LETRA[f.getDay()]} ${f.getDate()}</th>`;
  }).join("");
  const filas = [];
  for (let n = u.n_min; n <= u.n_max; n++) {
    const celdas = columnas.map((iso) => {
      const c = mapa.get(`${iso}|${n}`);
      if (!c) return `<td class="vacia" aria-label="sin datos"></td>`;
      const paso = pasoEscala(c.precio, ordenados);
      const tintaBlanca = esOscuro ? paso <= 2 : paso >= 4;
      const objetivo = c.precio <= a.precio_objetivo;
      const clases = [c.en_rango ? "" : "fuera", objetivo ? "objetivo" : "", est.elegida === clave(c) ? "elegida" : ""].join(" ");
      return `<td tabindex="0" data-k="${clave(c)}" class="${clases}" style="background:var(--seq-${paso});color:${tintaBlanca ? "#ffffff" : "#0b0b0b"}"
        aria-label="Ida ${fCorta(c.ida)}, ${n} días, ${euros(c.precio)}${objetivo ? ", por debajo de tu objetivo" : ""}">${Math.round(c.precio)}${objetivo ? "✓" : ""}</td>`;
    }).join("");
    const tuya = n >= a.dias - a.flex && n <= a.dias + a.flex;
    filas.push(`<tr><th class="fila" scope="row">${tuya ? "<b>" : ""}${n} días${tuya ? "</b>" : ""}</th>${celdas}</tr>`);
  }
  const tira = [0, 1, 2, 3, 4, 5, 6].map((i) => `<i style="background:var(--seq-${i})"></i>`).join("");
  return `<section class="tarjeta"><h2>Calendario de precios</h2>
    <p class="sub">Ida y vuelta más barata según el día de salida (columnas) y los días en destino (filas), en € por persona. Pasa el ratón por una casilla para ver el detalle y pulsa para seleccionarla.</p>
    <div class="calendario-scroll"><table class="calendario"><thead><tr><th class="fila"></th>${cab}</tr></thead><tbody>${filas.join("")}</tbody></table></div>
    <div class="leyenda-escala"><span>Más caro</span><span class="tira" aria-hidden="true">${tira}</span><span>Más barato</span>
      <span>· <b>✓</b> por debajo de tu objetivo · atenuado: fuera de tus fechas o de tu duración</span></div>
  </section>`;
}

// «easyJet vía Google Flights», o solo «Ryanair» cuando la web es la propia aerolínea.
function quien(t) {
  const web = FUENTES[t.fuente] || t.fuente;
  if (!t.aerolinea) return esc(web);
  return t.aerolinea === web ? esc(web) : `${esc(t.aerolinea)} vía ${esc(web)}`;
}

function textoCombo(c) {
  const [di, dv] = duracionesCombo(c);
  const como = c.tipo === "separados"
    ? `Ida: ${quien(c.tramo_ida)} · ${euros(c.tramo_ida.precio)}${di ? ` · ${di}` : ""}<br>Vuelta: ${quien(c.tramo_vuelta)} · ${euros(c.tramo_vuelta.precio)}${dv ? ` · ${dv}` : ""}`
    : `Ida y vuelta: ${quien(c.tramo_ida)}${di ? `<br>Trayectos: ida ${di}${dv ? `, vuelta ${dv}` : ""}` : ""}`;
  return `<b>${euros(c.precio)}</b> · ${fCorta(c.ida)} → ${fCorta(c.vuelta)} · ${c.dias} días<br>${como}${c.en_rango ? "" : "<br><i>Fuera de tus fechas o de tu duración</i>"}`;
}

function prepararCalendario() {
  const tabla = $(".calendario");
  if (!tabla) return;
  const tip = $("#tooltip");
  const mapa = new Map(est.detalle.ultima.combos.map((c) => [clave(c), c]));
  const mostrar = (td, x, y) => {
    const c = mapa.get(td.dataset.k);
    if (!c) return;
    tip.innerHTML = textoCombo(c);
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(window.innerWidth - r.width - 8, x + 14)}px`;
    tip.style.top = `${Math.max(8, y - r.height - 12)}px`;
  };
  tabla.addEventListener("mousemove", (e) => {
    const td = e.target.closest("td[data-k]");
    if (td) mostrar(td, e.clientX, e.clientY); else tip.hidden = true;
  });
  tabla.addEventListener("mouseleave", () => { tip.hidden = true; });
  tabla.addEventListener("focusin", (e) => {
    const td = e.target.closest("td[data-k]");
    if (td) { const r = td.getBoundingClientRect(); mostrar(td, r.right, r.top); }
  });
  tabla.addEventListener("focusout", () => { tip.hidden = true; });
  const elegir = (td) => { tip.hidden = true; est.elegida = td.dataset.k; pintarDetalle(); $("#opcion")?.scrollIntoView({ behavior: "smooth", block: "center" }); };
  tabla.addEventListener("click", (e) => { const td = e.target.closest("td[data-k]"); if (td) elegir(td); });
  tabla.addEventListener("keydown", (e) => {
    const td = e.target.closest("td[data-k]");
    if (td && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); elegir(td); }
  });
}

function htmlHorario(h) {
  const conclusion = !h.fiable
    ? `Aún hay pocos datos para sacar conclusiones: hacen falta al menos 4 días con varias revisiones y 2 franjas con 3 o más revisiones. Llevas ${h.dias_comparables} día${h.dias_comparables === 1 ? "" : "s"} comparable${h.dias_comparables === 1 ? "" : "s"}.`
    : h.mejor_franja && h.mejor_franja.desviacion < 0
      ? `Hasta ahora, la franja más barata para buscar es la ${h.mejor_franja.nombre.toLowerCase()}: los precios salen de media un ${Math.abs(h.mejor_franja.desviacion).toLocaleString("es-ES", { maximumFractionDigits: 1 })} % por debajo de la media de ese mismo día.`
      : "Por ahora no se ve ninguna franja horaria claramente más barata.";
  const filasF = h.franjas.map((f) => `<tr><td>${f.nombre} (${f.desde}–${f.hasta} h)</td>
    <td class="num">${f.desviacion == null ? "sin datos" : `${f.desviacion.toLocaleString("es-ES", { maximumFractionDigits: 2 })} %`}</td>
    <td class="num">${f.observaciones}</td><td class="num">${f.revisiones}</td></tr>`).join("");
  const filasR = h.revisiones.slice().reverse().map((r) => `<tr><td>${fHora(r.inicio)}</td><td>${r.motivo === "programada" ? "automática" : "manual"}</td><td class="num">${euros(r.mejor_precio)}</td></tr>`).join("");
  return `<section class="tarjeta"><h2>¿Influye la hora de búsqueda?</h2>
    <p class="sub">Cada vuelo se compara con su propio precio medio de ese día; la barra es la diferencia media según la franja en que se buscó. Por debajo de 0 = más barato.</p>
    <div class="lienzo bajo"><canvas id="g-horario" role="img" aria-label="Diferencia media de precio por franja horaria de búsqueda"></canvas></div>
    <p class="nota">${conclusion}</p>
    <details class="datos"><summary>Ver los datos en tabla</summary><div class="scroll">
      <table class="tabla"><thead><tr><th>Franja</th><th class="num">Diferencia media</th><th class="num">Precios comparados</th><th class="num">Revisiones</th></tr></thead><tbody>${filasF}</tbody></table>
      <br><table class="tabla"><thead><tr><th>Revisión</th><th>Tipo</th><th class="num">Mejor precio</th></tr></thead><tbody>${filasR}</tbody></table>
    </div></details></section>`;
}

function htmlTop(u) {
  const filas = u.combos.slice(0, 10).map((c) => {
    const como = c.tipo === "separados"
      ? `2 billetes: ${quien(c.tramo_ida)} + ${quien(c.tramo_vuelta)}`
      : `1 billete de ida y vuelta: ${quien(c.tramo_ida)}`;
    const [di, dv] = duracionesCombo(c);
    return `<tr class="${c.en_rango ? "" : "fuera"}"><td>${fCorta(c.ida)}</td><td>${fCorta(c.vuelta)}</td><td class="num">${c.dias}</td>
      <td class="num"><b>${euros(c.precio)}</b></td><td>${como}</td><td class="num">${di || "—"} / ${dv || "—"}</td><td>${c.en_rango ? "✓ sí" : "no"}</td>
      <td><button class="enlace-boton" type="button" data-ver="${clave(c)}">ver</button></td></tr>`;
  }).join("");
  return `<section class="tarjeta"><h2>Las 10 combinaciones más baratas</h2>
    <p class="sub">De la última revisión, dentro y fuera de tus fechas.</p>
    <div class="tabla-scroll"><table class="tabla"><thead><tr><th>Ida</th><th>Vuelta</th><th class="num">Días</th><th class="num">Precio</th><th>Cómo</th><th class="num">Trayecto ida / vuelta</th><th>En tus fechas</th><th></th></tr></thead>
    <tbody>${filas}</tbody></table></div></section>`;
}

function htmlAvisos(d) {
  if (!d.avisos.length) return "";
  const filas = d.avisos.map((x) => `<tr><td>${fHora(x.enviado)}</td><td class="num">${euros(x.precio)}</td>
    <td><span class="punto ${x.ok ? "bien" : "aviso"}" aria-hidden="true"></span> ${esc(x.detalle)}</td></tr>`).join("");
  return `<section class="tarjeta"><h2>Avisos</h2><div class="tabla-scroll"><table class="tabla">
    <thead><tr><th>Cuándo</th><th class="num">Precio</th><th>Estado</th></tr></thead><tbody>${filas}</tbody></table></div></section>`;
}

function htmlErrores(u) {
  if (!u.revision.errores) return "";
  return `<section class="tarjeta"><h2>Incidencias de la última revisión</h2><div class="aviso-caja">${esc(u.revision.errores)}</div></section>`;
}

/* ---------- acciones del detalle */
$("#detalle").addEventListener("click", async (e) => {
  const ver = e.target.closest("[data-ver]");
  if (ver) {
    est.elegida = ver.dataset.ver;
    pintarDetalle();
    $("#opcion")?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const b = e.target.closest("[data-acc]");
  if (!b || !est.detalle) return;
  const a = est.detalle.alerta;
  try {
    if (b.dataset.acc === "revisar") {
      const r = await api(`/api/alertas/${a.id}/revisar`, { method: "POST" });
      b.textContent = r.nube ? "Pedida a GitHub (tarda unos minutos)" : "En cola…";
      b.disabled = true;
      setTimeout(refrescarEstado, r.nube ? 5000 : 300);
    } else if (b.dataset.acc === "horas") {
      const v = prompt("Máximo de horas por trayecto, con escalas (vacío = sin límite):", a.max_horas ?? "");
      if (v == null) return;
      await api(`/api/alertas/${a.id}`, { method: "PATCH", body: JSON.stringify({ max_horas: v.trim() }) });
      await cargarAlertas(); await cargarDetalle();
      refrescarEstado();
    } else if (b.dataset.acc === "pausa") {
      await api(`/api/alertas/${a.id}`, { method: "PATCH", body: JSON.stringify({ activa: !a.activa }) });
      await cargarAlertas(); await cargarDetalle();
    } else if (b.dataset.acc === "objetivo") {
      const v = prompt("Nuevo precio objetivo (€ por persona, ida y vuelta):", a.precio_objetivo);
      if (v == null) return;
      await api(`/api/alertas/${a.id}`, { method: "PATCH", body: JSON.stringify({ precio_objetivo: v }) });
      await cargarAlertas(); await cargarDetalle();
    } else if (b.dataset.acc === "borrar") {
      if (!confirm(`¿Borrar la alerta ${a.origen} → ${a.destino} y todo su historial?`)) return;
      await api(`/api/alertas/${a.id}`, { method: "DELETE" });
      est.sel = null;
      await cargarAlertas();
      pintarVacio();
    } else if (b.dataset.acc === "volver-mejor") {
      est.elegida = null;
      pintarDetalle();
    }
  } catch (ex) {
    alert(ex.message);
  }
});

/* ===================================================================== gráficos */
function baseGrafico() {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
  };
}

function ejes(t) {
  return {
    grid: { color: t.rejilla, lineWidth: 1, drawTicks: false },
    border: { color: t.eje },
    ticks: { color: t.tenue, padding: 6, font: { size: 11.5 } },
  };
}

function tokens() {
  return {
    superficie: cv("--superficie"), tinta: cv("--tinta"), tinta2: cv("--tinta-2"), tenue: cv("--tenue"),
    rejilla: cv("--rejilla"), eje: cv("--eje"), borde: cv("--borde"),
  };
}

function tooltipBase(t) {
  return {
    backgroundColor: t.superficie, titleColor: t.tinta, bodyColor: t.tinta2, borderColor: t.borde, borderWidth: 1,
    padding: 10, boxPadding: 4, boxWidth: 10, boxHeight: 10, titleFont: { weight: "600" },
    callbacks: {},
  };
}

// Línea vertical que sigue al cursor (crosshair).
const cursor = {
  id: "cursor",
  afterDatasetsDraw(chart) {
    const act = chart.tooltip?.getActiveElements?.() || [];
    if (!act.length) return;
    const x = act[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
    ctx.save(); ctx.strokeStyle = cv("--eje"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
  },
};

// Etiqueta con el último valor de la serie principal, al final de la línea.
const etiquetaFinal = {
  id: "etiquetaFinal",
  afterDatasetsDraw(chart) {
    const meta = chart.getDatasetMeta(0), datos = chart.data.datasets[0].data;
    let i = datos.length - 1;
    while (i >= 0 && datos[i] == null) i--;
    if (i < 0 || !meta.data[i]) return;
    const p = meta.data[i], ctx = chart.ctx;
    ctx.save(); ctx.fillStyle = cv("--tinta"); ctx.font = '600 12px system-ui, -apple-system, "Segoe UI", sans-serif';
    ctx.textBaseline = "middle"; ctx.fillText(euros(datos[i]), p.x + 8, p.y); ctx.restore();
  },
};

function graficoHistorial(d) {
  const lienzo = $("#g-historial");
  if (!lienzo) return;
  const t = tokens(), h = d.historial;
  const pocos = h.length <= 40;
  const linea = (color) => ({
    borderColor: color, backgroundColor: color, borderWidth: 2, tension: 0, spanGaps: true,
    pointRadius: pocos ? 4 : 0, pointHoverRadius: 5, pointBorderColor: t.superficie, pointBorderWidth: 2,
    pointBackgroundColor: color, borderJoinStyle: "round", borderCapStyle: "round",
  });
  // El mejor precio hasta ahora: punto más grande y etiqueta debajo.
  const iMin = h.findIndex((x) => x.id === d.record?.revision_id);
  const radio = (c) => (c.dataIndex === iMin ? 7 : pocos ? 4 : 0);
  const series = [{
    label: "Mejor combinando webs", data: h.map((x) => x.mejor_precio), ...linea(cv(COLOR_FUENTE.todas)),
    pointRadius: radio, pointHoverRadius: (c) => (c.dataIndex === iMin ? 8 : 5),
  }];
  const etiquetaMinimo = {
    id: "etiquetaMinimo",
    afterDatasetsDraw(chart) {
      const p = chart.getDatasetMeta(0).data[iMin];
      if (iMin < 0 || !p) return;
      const ctx = chart.ctx, area = chart.chartArea, txt = `★ Mejor: ${euros(h[iMin].mejor_precio)}`;
      ctx.save(); ctx.font = '600 12px system-ui, -apple-system, "Segoe UI", sans-serif';
      const w = ctx.measureText(txt).width;
      const x = Math.min(Math.max(p.x, area.left + w / 2 + 2), area.right - w / 2 - 2);
      const abajo = p.y + 26 <= area.bottom;
      ctx.fillStyle = cv("--tinta"); ctx.textAlign = "center"; ctx.textBaseline = abajo ? "top" : "bottom";
      ctx.fillText(txt, x, abajo ? p.y + 11 : p.y - 11); ctx.restore();
    },
  };
  ORDEN_FUENTES.forEach((f) => {
    if (h.some((x) => x.por_fuente?.[f] != null)) {
      series.push({ label: `Solo ${FUENTES[f]}`, data: h.map((x) => x.por_fuente?.[f] ?? null), ...linea(cv(COLOR_FUENTE[f])) });
    }
  });
  series.push({
    label: "Tu objetivo", data: h.map(() => d.alerta.precio_objetivo), borderColor: t.tenue, backgroundColor: t.tenue,
    borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 0, tension: 0,
  });
  const e = ejes(t);
  est.graficos.historial = new Chart(lienzo, {
    type: "line",
    data: { labels: h.map((x) => fHora(x.inicio)), datasets: series },
    options: {
      ...baseGrafico(),
      layout: { padding: { right: 56, top: 4 } },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", align: "start", labels: { color: t.tinta2, boxWidth: 16, boxHeight: 3, padding: 14 } },
        tooltip: {
          ...tooltipBase(t),
          callbacks: {
            label: (c) => ` ${c.dataset.label}: ${euros(c.parsed.y)}`,
            labelColor: (c) => ({ borderColor: c.dataset.borderColor, backgroundColor: c.dataset.borderColor, borderWidth: 0 }),
          },
        },
      },
      scales: {
        x: { ...e, grid: { display: false }, ticks: { ...e.ticks, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        y: { ...e, ticks: { ...e.ticks, callback: (v) => euros(v) } },
      },
    },
    plugins: [cursor, etiquetaFinal, etiquetaMinimo],
  });
}

// Valor en la punta de cada barra; «sin datos» donde no hay barra.
const etiquetasBarras = {
  id: "etiquetasBarras",
  afterDatasetsDraw(chart) {
    const ctx = chart.ctx, meta = chart.getDatasetMeta(0), datos = chart.data.datasets[0].data;
    const y0 = chart.scales.y.getPixelForValue(0);
    ctx.save(); ctx.textAlign = "center"; ctx.font = '600 12px system-ui, -apple-system, "Segoe UI", sans-serif';
    meta.data.forEach((barra, i) => {
      const v = datos[i];
      if (v == null) {
        ctx.fillStyle = cv("--tenue"); ctx.textBaseline = "bottom"; ctx.fillText("sin datos", barra.x, y0 - 4);
        return;
      }
      ctx.fillStyle = cv("--tinta");
      ctx.textBaseline = v >= 0 ? "bottom" : "top";
      const txt = `${v > 0 ? "+" : ""}${v.toLocaleString("es-ES", { maximumFractionDigits: 1 })} %`;
      ctx.fillText(txt, barra.x, v >= 0 ? barra.y - 4 : barra.y + 4);
    });
    ctx.restore();
  },
};

function graficoHorario(h) {
  const lienzo = $("#g-horario");
  if (!lienzo) return;
  const t = tokens(), e = ejes(t);
  const valores = h.franjas.map((f) => f.desviacion);
  const maxAbs = Math.ceil(Math.max(2, ...valores.filter((v) => v != null).map(Math.abs)) * 1.35);
  est.graficos.horario = new Chart(lienzo, {
    type: "bar",
    data: {
      labels: h.franjas.map((f) => `${f.nombre} (${f.desde}–${f.hasta} h)`),
      datasets: [{ label: "Diferencia media", data: valores, backgroundColor: cv("--s1"), maxBarThickness: 24, borderRadius: 4, borderSkipped: "start" }],
    },
    options: {
      ...baseGrafico(),
      layout: { padding: { top: 8 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...tooltipBase(t), displayColors: false,
          callbacks: {
            label: (c) => {
              const f = h.franjas[c.dataIndex];
              return ` ${c.parsed.y.toLocaleString("es-ES", { maximumFractionDigits: 2 })} % · ${f.observaciones} precios en ${f.revisiones} revisiones`;
            },
          },
        },
      },
      scales: {
        x: { ...e, grid: { display: false }, ticks: { ...e.ticks, color: t.tinta2 } },
        y: {
          ...e, min: -maxAbs, max: maxAbs,
          grid: { ...e.grid, color: (c) => (c.tick.value === 0 ? t.eje : t.rejilla) },
          ticks: { ...e.ticks, maxTicksLimit: 5, callback: (v) => `${Number(v).toLocaleString("es-ES", { maximumFractionDigits: 1 })} %` },
        },
      },
    },
    plugins: [etiquetasBarras],
  });
}

/* ===================================================================== ajustes */
const dlg = $("#dlg-ajustes");
const fa = $("#form-ajustes");
const msgAjustes = (texto, error = false) => {
  const p = $("#ajustes-msg");
  p.textContent = texto; p.hidden = !texto;
  p.className = error ? "mensaje-error" : "mensaje";
};

let githubActual = { conectado: false };
let cambiandoRepo = false;

function pintarGithub(g, cambiando = false) {
  githubActual = g || { conectado: false };
  const conectado = githubActual.conectado;
  cambiandoRepo = conectado && cambiando;
  $("#github-estado").innerHTML = !conectado
    ? "No conectado: las revisiones las hace este PC mientras está encendido."
    : cambiandoRepo
      ? `Ahora: <b>${esc(githubActual.repo)}</b>. Escribe abajo el repositorio nuevo y pega su token. El nuevo empieza sin alertas y el actual deja de revisar.`
      : `<span class="punto bien" aria-hidden="true"></span> Conectado a <b>${esc(githubActual.repo)}</b>: las revisiones las hace GitHub aunque el PC esté apagado.`;
  $("#github-pasos").hidden = conectado && !cambiandoRepo;
  $("#github-paso-final").innerHTML = cambiandoRepo
    ? "Pega aquí el repositorio y el token y pulsa <b>Conectar al repositorio nuevo</b>. Se suben el código y los datos de Telegram."
    : "Pega aquí el repositorio y el token y pulsa <b>Conectar y subir</b>. Tus alertas, tu historial y los datos de Telegram se copian a GitHub.";
  $("#github-campos").hidden = conectado && !cambiandoRepo;
  $("#btn-github-off").hidden = !conectado || cambiandoRepo;
  $("#btn-github-cambiar").hidden = !conectado;
  $("#btn-github-cambiar").textContent = cambiandoRepo ? "Cancelar" : "Cambiar a otro repositorio";
  $("#btn-github").textContent = !conectado ? "Conectar y subir"
    : cambiandoRepo ? "Conectar al repositorio nuevo" : "Subir el código otra vez";
  fa.github_repo.value = !conectado ? fa.github_repo.value : cambiandoRepo ? "" : githubActual.repo;
  fa.github_token.value = "";
  fa.revisiones_dia.max = conectado ? 8 : 24;
}

$("#btn-github-cambiar").addEventListener("click", () => {
  pintarGithub(githubActual, !cambiandoRepo);
  msgAjustes("");
  if (cambiandoRepo) fa.github_repo.focus();
});

async function abrirAjustes() {
  const a = await api("/api/ajustes");
  pintarGithub(a.github);
  fa.github_token.value = "";
  fa.telegram_token.value = ""; fa.telegram_token.placeholder = a.telegram_token || "123456789:AA…";
  fa.aviasales_token.value = ""; fa.aviasales_token.placeholder = a.aviasales_token || "opcional";
  fa.telegram_chat_id.value = a.telegram_chat_id || "";
  ["revisiones_dia", "hora_inicio", "hora_fin", "separacion_min"].forEach((k) => { fa[k].value = a[k]; });
  msgAjustes("");
  dlg.showModal();
}

async function guardarAjustes() {
  const datos = {
    telegram_token: fa.telegram_token.value.trim(), aviasales_token: fa.aviasales_token.value.trim(),
    telegram_chat_id: fa.telegram_chat_id.value.trim(),
    revisiones_dia: fa.revisiones_dia.value, hora_inicio: fa.hora_inicio.value, hora_fin: fa.hora_fin.value,
    separacion_min: fa.separacion_min.value,
  };
  await api("/api/ajustes", { method: "POST", body: JSON.stringify(datos) });
}

document.addEventListener("click", (e) => { if (e.target.closest("[data-abrir-ajustes]")) abrirAjustes(); });
$("#btn-ajustes").addEventListener("click", abrirAjustes);
$("#btn-guardar-ajustes").addEventListener("click", async () => {
  try { await guardarAjustes(); msgAjustes("Guardado."); refrescarEstado(); } catch (ex) { msgAjustes(ex.message, true); }
});
$("#btn-detectar").addEventListener("click", async () => {
  try {
    await guardarAjustes();
    const r = await api("/api/ajustes/detectar-chat", { method: "POST" });
    fa.telegram_chat_id.value = r.chat_id;
    msgAjustes("Chat detectado. Pulsa «Enviar prueba» para comprobarlo.");
  } catch (ex) { msgAjustes(ex.message, true); }
});
$("#btn-probar").addEventListener("click", async () => {
  try {
    await guardarAjustes();
    await api("/api/ajustes/probar-aviso", { method: "POST" });
    msgAjustes("Mensaje enviado: mira Telegram.");
  } catch (ex) { msgAjustes(ex.message, true); }
});
$("#btn-github").addEventListener("click", async (e) => {
  const boton = e.currentTarget;
  const datos = { repo: fa.github_repo.value.trim(), token: fa.github_token.value.trim() };
  if (cambiandoRepo && (!datos.repo || !datos.token)) {
    msgAjustes(!datos.repo ? "Escribe el repositorio nuevo como usuario/nombre." : "Pega el token del repositorio nuevo.", true);
    (!datos.repo ? fa.github_repo : fa.github_token).focus();
    return;
  }
  boton.disabled = true;
  msgAjustes("Subiendo a GitHub… (puede tardar un minuto)");
  try {
    await guardarAjustes();
    const conectar = () => api("/api/github/conectar", { method: "POST", body: JSON.stringify(datos) });
    let r;
    try {
      r = await conectar();
    } catch (ex) {
      if (ex.codigo !== "publico" || !confirm(`${ex.message}\n\nA cambio, GitHub no cobra minutos en los repositorios públicos. ¿Seguir con el repositorio público?`)) throw ex;
      datos.aceptar_publico = true;
      r = await conectar();
    }
    const partes = [`Listo: conectado a ${r.repo}.`];
    if (r.anterior) {
      partes.push("Empieza sin alertas: créalas de nuevo.");
      partes.push(r.anterior_parado
        ? `${r.anterior} ya no revisa (su flujo está desactivado); puedes borrarlo.`
        : `No he podido entrar en ${r.anterior} para pararlo: si ya lo has borrado, no hay nada más que hacer; si sigue existiendo, desactiva su flujo «Revisar vuelos» en la pestaña Actions.`);
    } else if (r.historial_subido) partes.push("Se ha copiado tu historial.");
    partes.push(r.secretos.length ? `Secretos guardados: ${r.secretos.join(", ")}.` : "Aún no hay datos de Telegram que guardar: configúralo arriba y pulsa Guardar.");
    partes.push("La primera revisión ya está en marcha en GitHub.");
    msgAjustes(partes.join(" "));
    pintarGithub({ conectado: true, repo: r.repo });
    fa.github_token.value = "";
    await cargarAlertas();
    if (est.sel) await cargarDetalle();
    refrescarEstado();
  } catch (ex) {
    msgAjustes(ex.message, true);
  } finally {
    boton.disabled = false;
  }
});
$("#btn-github-off").addEventListener("click", async () => {
  if (!confirm("¿Volver al modo local? Este PC volverá a hacer las revisiones y la página dejará de leer de GitHub. Lo que hay en GitHub se queda allí; para que deje de revisar, desactiva el flujo «Revisar vuelos» en la pestaña Actions del repositorio.")) return;
  try {
    await api("/api/github/desconectar", { method: "POST" });
    pintarGithub({ conectado: false });
    msgAjustes("Modo local activado.");
    await cargarAlertas();
    if (est.sel) await cargarDetalle();
    refrescarEstado();
  } catch (ex) { msgAjustes(ex.message, true); }
});
$("#btn-resortear").addEventListener("click", async () => {
  try {
    await guardarAjustes();
    const r = await api("/api/plan/resortear", { method: "POST" });
    const horas = r.plan_hoy.filter((p) => p.estado === "pendiente").map((p) => p.hora.slice(11, 16));
    msgAjustes(horas.length ? `Nuevas horas para hoy: ${horas.join(", ")}.` : "Hoy ya no queda hueco para más revisiones.");
    refrescarEstado();
  } catch (ex) { msgAjustes(ex.message, true); }
});

/* ===================================================================== arranque */
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (est.detalle?.ultima) pintarDetalle(); });

(async function iniciar() {
  await cargarAeropuertos();
  if (!$("#origen-txt").matches(":focus")) selectores.origen.fijar($("#form-alerta").origen.value);
  try {
    await cargarAlertas();
    const primera = est.alertas.find((a) => a.activa) || est.alertas[0];
    if (primera) await seleccionar(primera.id); else pintarVacio();
  } catch (e) {
    pintarVacio();
  }
  sondear();
})();
