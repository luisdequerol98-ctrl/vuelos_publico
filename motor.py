"""Motor de revisiones: consulta las fuentes, combina ida y vuelta, propone
alternativas más baratas, analiza la hora de búsqueda y decide si avisar."""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean

import avisos
import db
import fuentes

log = logging.getLogger("buscador.motor")

EXTENSION = 5   # días antes y después de tu rango que también se exploran
FLEX_ALT = 2    # días de duración de más y de menos que también se exploran
RT_TOP = 12     # combinaciones que se comprueban como billete de ida y vuelta en Google
CORTE_GOOGLE = 8   # fallos seguidos (cada uno ya con sus reintentos) tras los que se deja Google
FRANJAS = [("Madrugada", 0, 6), ("Mañana", 6, 12), ("Tarde", 12, 18), ("Noche", 18, 24)]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

ESTADO = {"ocupado": False, "texto": "", "paso": 0, "total": 0, "alerta_id": None,
          "ultima_fin": None}
_cola: "queue.Queue[tuple[int | None, str]]" = queue.Queue()


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- alcance de búsqueda

def alcance(a: dict) -> dict | None:
    """Fechas que se consultan: tu rango ± EXTENSION días y duraciones ± FLEX_ALT."""
    manana = date.today() + timedelta(days=1)
    desde, hasta = _d(a["fecha_desde"]), _d(a["fecha_hasta"])
    n_min = max(1, a["dias"] - a["flex"] - FLEX_ALT)
    n_max = a["dias"] + a["flex"] + FLEX_ALT
    ida_desde = max(manana, desde - timedelta(days=EXTENSION))
    ida_hasta = hasta + timedelta(days=EXTENSION - n_min)
    vuelta_desde = ida_desde + timedelta(days=n_min)
    vuelta_hasta = hasta + timedelta(days=EXTENSION)
    if ida_hasta < ida_desde:
        return None
    return {"n_min": n_min, "n_max": n_max, "ida_desde": ida_desde, "ida_hasta": ida_hasta,
            "vuelta_desde": vuelta_desde, "vuelta_hasta": vuelta_hasta}


def caducada(a: dict) -> bool:
    ultima_salida = _d(a["fecha_hasta"]) - timedelta(days=max(1, a["dias"] - a["flex"]))
    return ultima_salida <= date.today()


def _rango(desde: date, hasta: date) -> list[str]:
    return [(desde + timedelta(days=i)).isoformat() for i in range((hasta - desde).days + 1)]


# ---------------------------------------------------------------- combinaciones

def max_minutos(a: dict) -> int | None:
    return int(round(a["max_horas"] * 60)) if a.get("max_horas") else None


def personas_de(a: dict) -> int:
    """Adultos que viajan. Los precios se comparan y guardan siempre por persona."""
    return max(1, int(a.get("personas") or 1))


def _supera(minutos, limite) -> bool:
    """Si no se conoce la duración (algunas tarifas de Aviasales), no se descarta."""
    return bool(limite and minutos and minutos > limite)


def combinaciones(a: dict, al: dict, tarifas: list[dict], fuentes_ok: set[str] | None = None,
                  solo_directos: bool = False, max_min: int | None = None) -> list[dict]:
    """Todas las parejas ida/vuelta posibles con su precio más barato.

    Para cada pareja se queda con lo más barato entre sumar dos billetes de solo ida
    (pueden ser de webs distintas) o un billete de ida y vuelta. Con max_min se descartan
    los trayectos que tardan más de ese tiempo (con escalas incluidas).
    """
    ida, vuelta, iv = {}, {}, {}

    def guardar(dic, clave, t):
        if clave not in dic or t["precio"] < dic[clave]["precio"]:
            dic[clave] = t

    for t in tarifas:
        if fuentes_ok and t["fuente"] not in fuentes_ok:
            continue
        if solo_directos and (t.get("escalas") or 0) > 0:
            continue
        if _supera(t.get("duracion_min"), max_min) or _supera(t.get("duracion_vuelta_min"), max_min):
            continue
        if t["tramo"] == "ida":
            guardar(ida, t["fecha"], t)
        elif t["tramo"] == "vuelta":
            guardar(vuelta, t["fecha"], t)
        elif t.get("fecha_vuelta"):
            guardar(iv, (t["fecha"], t["fecha_vuelta"]), t)

    pares = set()
    for di in ida:
        for n in range(al["n_min"], al["n_max"] + 1):
            r = (_d(di) + timedelta(days=n)).isoformat()
            if r in vuelta:
                pares.add((di, r))
    pares |= {k for k in iv if al["n_min"] <= (_d(k[1]) - _d(k[0])).days <= al["n_max"]}

    lo, hi = a["dias"] - a["flex"], a["dias"] + a["flex"]
    salida = []
    for di, r in pares:
        opciones = []
        if di in ida and r in vuelta:
            opciones.append({"tipo": "separados",
                             "precio": round(ida[di]["precio"] + vuelta[r]["precio"], 2),
                             "tramo_ida": ida[di], "tramo_vuelta": vuelta[r]})
        if (di, r) in iv:
            opciones.append({"tipo": "ida_vuelta", "precio": iv[(di, r)]["precio"],
                             "tramo_ida": iv[(di, r)], "tramo_vuelta": None})
        mejor = min(opciones, key=lambda o: o["precio"])
        n = (_d(r) - _d(di)).days
        mejor.update(ida=di, vuelta=r, dias=n,
                     en_rango=a["fecha_desde"] <= di and r <= a["fecha_hasta"] and lo <= n <= hi)
        salida.append(mejor)
    salida.sort(key=lambda c: (c["precio"], c["ida"], c["dias"]))
    return salida


def _mas_barata(combos: list[dict], filtro=lambda c: True) -> dict | None:
    candidatos = [c for c in combos if filtro(c)]
    return min(candidatos, key=lambda c: c["precio"]) if candidatos else None


def analizar(a: dict, al: dict, tarifas: list[dict]) -> dict:
    directos, limite = bool(a["solo_directos"]), max_minutos(a)
    combos = combinaciones(a, al, tarifas, solo_directos=directos, max_min=limite)
    mejor = _mas_barata(combos, lambda c: c["en_rango"])
    por_fuente = {}
    for f in fuentes.NOMBRES:
        m = _mas_barata(combinaciones(a, al, tarifas, {f}, directos, limite), lambda c: c["en_rango"])
        if m:
            por_fuente[f] = m["precio"]
    con_escalas = combinaciones(a, al, tarifas, solo_directos=False, max_min=limite) if directos else None
    sin_limite = combinaciones(a, al, tarifas, solo_directos=directos) if limite else None
    return {"combos": combos, "mejor": mejor, "por_fuente": por_fuente, "con_escalas": con_escalas,
            "sin_limite": sin_limite}


def duraciones(c: dict) -> tuple[int | None, int | None]:
    """(minutos de la ida, minutos de la vuelta) de una combinación."""
    if c["tipo"] == "separados":
        return c["tramo_ida"].get("duracion_min"), c["tramo_vuelta"].get("duracion_min")
    return c["tramo_ida"].get("duracion_min"), c["tramo_ida"].get("duracion_vuelta_min")


# ---------------------------------------------------------------- alternativas

def _texto_fechas(c: dict) -> str:
    return f"{avisos.fecha_corta(c['ida'])} → {avisos.fecha_corta(c['vuelta'])} · {c['dias']} días"


def alternativas(a: dict, res: dict, horario: dict | None = None) -> list[dict]:
    combos, mejor = res["combos"], res["mejor"]
    if not combos:
        return []
    base = mejor["precio"] if mejor else None
    umbral = max(5.0, base * 0.03) if base else 0

    def ahorra(c):
        return c is not None and (base is None or base - c["precio"] >= umbral)

    def alt(tipo, titulo, c, texto=None):
        return {"tipo": tipo, "titulo": titulo, "precio": c["precio"],
                "ahorro": round(base - c["precio"], 2) if base else None,
                "texto": texto or _texto_fechas(c), "combo": c}

    desde, hasta = a["fecha_desde"], a["fecha_hasta"]
    lo, hi = a["dias"] - a["flex"], a["dias"] + a["flex"]
    en_fechas = lambda c: desde <= c["ida"] and c["vuelta"] <= hasta
    en_duracion = lambda c: lo <= c["dias"] <= hi
    salida = []

    # 1. Otra duración, dentro de tus fechas
    for filtro in (lambda c: c["dias"] < lo, lambda c: c["dias"] > hi):
        c = _mas_barata(combos, lambda c, f=filtro: en_fechas(c) and f(c))
        if ahorra(c):
            salida.append(alt("duracion", f"Quedarte {c['dias']} días en vez de {a['dias']}", c))

    # 2. Mover el viaje unos días, con tu duración
    c = _mas_barata(combos, lambda c: en_duracion(c) and c["ida"] < desde)
    if ahorra(c):
        salida.append(alt("fechas", f"Salir {(_d(desde) - _d(c['ida'])).days} días antes de tu rango", c))
    c = _mas_barata(combos, lambda c: en_duracion(c) and c["vuelta"] > hasta)
    if ahorra(c):
        salida.append(alt("fechas", f"Volver {(_d(c['vuelta']) - _d(hasta)).days} días después de tu rango", c))

    # 3. Las dos cosas a la vez
    c = _mas_barata(combos)
    if ahorra(c) and not c["en_rango"] and all(c["precio"] < s["precio"] - 1 for s in salida):
        salida.append(alt("libre", "Moviendo fechas y duración a la vez", c))

    # 4. Aceptar escalas
    if res.get("con_escalas"):
        c = _mas_barata(res["con_escalas"], lambda c: c["en_rango"])
        if ahorra(c):
            salida.append(alt("escalas", "Aceptar vuelos con escala", c))

    # 5. Aceptar trayectos más largos que tu límite de horas
    if res.get("sin_limite"):
        c = _mas_barata(res["sin_limite"], lambda c: c["en_rango"])
        if ahorra(c):
            mas_largo = max((m for m in duraciones(c) if m), default=None)
            horas = math.ceil(mas_largo / 60) if mas_largo else None
            titulo = (f"Aceptar trayectos de hasta {horas} h (tu límite: {a['max_horas']:g} h)"
                      if horas else "Quitar el límite de horas")
            salida.append(alt("horas", titulo, c))

    # 6. Día de la semana de salida
    en = [c for c in combos if c["en_rango"]]
    por_dia = defaultdict(list)
    for c in en:
        por_dia[_d(c["ida"]).weekday()].append(c["precio"])
    if len(por_dia) >= 3 and base:
        medias = {d: mean(v) for d, v in por_dia.items() if len(v) >= 2}
        dia, media_dia = min(medias.items(), key=lambda x: x[1]) if len(medias) >= 2 else (None, None)
        media_total = mean(c["precio"] for c in en)
        if dia is not None and media_total - media_dia >= umbral:
            ejemplo = _mas_barata(en, lambda c: _d(c["ida"]).weekday() == dia)
            salida.append({"tipo": "dia_semana", "titulo": f"Salir en {DIAS_SEMANA[dia]}",
                           "precio": round(media_dia, 2), "ahorro": None,
                           "texto": f"Saliendo en {DIAS_SEMANA[dia]} pagas de media "
                                    f"{avisos.euros(round(media_dia))}, frente a "
                                    f"{avisos.euros(round(media_total))} de media en tus fechas.",
                           "combo": ejemplo})

    # 7. Hora de búsqueda
    if horario and horario.get("fiable") and horario.get("mejor_franja"):
        f = horario["mejor_franja"]
        if f["desviacion"] <= -1.5:
            pct = f"{abs(f['desviacion']):.1f}".replace(".", ",")
            salida.append({"tipo": "hora", "titulo": f"Buscar por la {f['nombre'].lower()}",
                           "precio": None, "ahorro": None, "combo": None,
                           "texto": f"En tus revisiones, los precios de la {f['nombre'].lower()} han "
                                    f"salido un {pct} % por debajo de la media de ese mismo día "
                                    f"({f['revisiones']} revisiones)."})

    salida.sort(key=lambda s: -(s["ahorro"] or 0))
    return salida


# ---------------------------------------------------------------- hora de búsqueda

def analisis_horario(alerta_id: int) -> dict:
    """¿Cambia el precio según la hora a la que se busca?

    Para cada vuelo concreto (fuente + tramo + fecha) se compara su precio en cada
    revisión con la media de ese mismo vuelo en las revisiones del mismo día. Así
    se separa el efecto de la hora del de la tendencia de fondo.
    """
    filas = db.q(
        "SELECT r.id AS rev, r.inicio, t.fuente, t.tramo, t.fecha, IFNULL(t.fecha_vuelta,'') AS fv, "
        "MIN(t.precio) AS precio FROM tarifas t JOIN revisiones r ON r.id = t.revision_id "
        "WHERE r.alerta_id = ? AND r.fin IS NOT NULL "
        "GROUP BY r.id, t.fuente, t.tramo, t.fecha, t.fecha_vuelta", (alerta_id,))
    por_dia_clave = defaultdict(list)
    for f in filas:
        por_dia_clave[(f["inicio"][:10], f["fuente"], f["tramo"], f["fecha"], f["fv"])].append(f)

    desv = defaultdict(list)   # franja -> desviaciones %
    revs_franja = defaultdict(set)
    dias = set()
    for (dia, *_), obs in por_dia_clave.items():
        if len({o["rev"] for o in obs}) < 2:
            continue
        m = mean(o["precio"] for o in obs)
        if m <= 0:
            continue
        dias.add(dia)
        for o in obs:
            hora = int(o["inicio"][11:13])
            nombre = next(n for n, h0, h1 in FRANJAS if h0 <= hora < h1)
            desv[nombre].append((o["precio"] / m - 1) * 100)
            revs_franja[nombre].add(o["rev"])

    franjas = []
    for nombre, h0, h1 in FRANJAS:
        v = desv.get(nombre, [])
        franjas.append({"nombre": nombre, "desde": h0, "hasta": h1,
                        "desviacion": round(mean(v), 2) if v else None,
                        "observaciones": len(v), "revisiones": len(revs_franja.get(nombre, ()))})

    revisiones = db.q("SELECT id, inicio, motivo, mejor_precio FROM revisiones "
                      "WHERE alerta_id=? AND fin IS NOT NULL ORDER BY inicio", (alerta_id,))
    con_datos = [f for f in franjas if f["desviacion"] is not None and f["revisiones"] >= 3]
    fiable = len(dias) >= 4 and len(con_datos) >= 2
    mejor = min(con_datos, key=lambda f: f["desviacion"]) if con_datos else None
    return {"franjas": franjas, "dias_comparables": len(dias), "fiable": fiable,
            "mejor_franja": mejor,
            "revisiones": [{"inicio": r["inicio"], "motivo": r["motivo"],
                            "mejor_precio": r["mejor_precio"]} for r in revisiones]}


# ---------------------------------------------------------------- revisión

def _paso(texto: str | None = None) -> None:
    ESTADO["paso"] += 1
    if texto:
        ESTADO["texto"] = texto


def revisar_alerta(a: dict, motivo: str) -> int | None:
    if caducada(a):
        db.ex("UPDATE alertas SET activa=0 WHERE id=?", (a["id"],))
        log.info("Alerta %s caducada: se desactiva", a["id"])
        return None
    al = alcance(a)
    if not al:
        return None
    o, dst = a["origen"], a["destino"]
    fechas_ida = _rango(al["ida_desde"], al["ida_hasta"])
    fechas_vuelta = _rango(al["vuelta_desde"], al["vuelta_hasta"])
    token_av = db.ajuste("aviasales_token", "")
    limite = max_minutos(a)
    personas = personas_de(a)

    ESTADO.update(ocupado=True, alerta_id=a["id"], paso=0,
                  total=4 + (2 if token_av else 0) + len(fechas_ida) + len(fechas_vuelta) + RT_TOP,
                  texto=f"{o} → {dst}: empezando")
    rev_id = db.ex("INSERT INTO revisiones(alerta_id, inicio, motivo) VALUES(?,?,?)",
                   (a["id"], ahora_iso(), motivo))
    tarifas: list[dict] = []
    errores: list[str] = []

    # Ryanair: una consulta por mes y tramo
    for tramo, (oo, dd, f1, f2) in (("ida", (o, dst, al["ida_desde"], al["ida_hasta"])),
                                     ("vuelta", (dst, o, al["vuelta_desde"], al["vuelta_hasta"]))):
        _paso(f"{o} → {dst}: Ryanair ({tramo})")
        try:
            tarifas += fuentes.ryanair_tramo(oo, dd, f1, f2, tramo, personas)
        except Exception as e:
            errores.append(f"Ryanair ({tramo}): {e}")

    # Wizz Air: una consulta por bloque de 30 días y tramo
    for tramo, (oo, dd, f1, f2) in (("ida", (o, dst, al["ida_desde"], al["ida_hasta"])),
                                     ("vuelta", (dst, o, al["vuelta_desde"], al["vuelta_hasta"]))):
        _paso(f"{o} → {dst}: Wizz Air ({tramo})")
        try:
            tarifas += fuentes.wizz_tramo(oo, dd, f1, f2, tramo, personas)
        except Exception as e:
            errores.append(f"Wizz Air ({tramo}): {e}")

    # Aviasales (si hay token)
    if token_av:
        _paso(f"{o} → {dst}: Aviasales (solo ida)")
        try:
            tarifas += fuentes.aviasales_tramo(token_av, o, dst, al["ida_desde"], al["ida_hasta"], "ida", limite)
            tarifas += fuentes.aviasales_tramo(token_av, dst, o, al["vuelta_desde"], al["vuelta_hasta"], "vuelta", limite)
        except Exception as e:
            errores.append(f"Aviasales (solo ida): {e}")
        _paso(f"{o} → {dst}: Aviasales (ida y vuelta)")
        try:
            tarifas += fuentes.aviasales_ida_vuelta(token_av, o, dst, al["ida_desde"], al["ida_hasta"],
                                                    al["n_min"], al["n_max"])
        except Exception as e:
            errores.append(f"Aviasales (ida y vuelta): {e}")

    # Google Flights: una consulta por día y tramo
    fallos, seguidos, ultimo_error, hechas = 0, 0, "", 0
    for tramo, oo, dd, fechas in (("ida", o, dst, fechas_ida), ("vuelta", dst, o, fechas_vuelta)):
        for f in fechas:
            _paso(f"{o} → {dst}: Google Flights ({tramo} {avisos.fecha_corta(f)})")
            if seguidos >= CORTE_GOOGLE:
                continue
            try:
                tarifas += fuentes.google_tramo(oo, dd, f, tramo, limite, personas)
                seguidos = 0
            except Exception as e:
                fallos, seguidos, ultimo_error = fallos + 1, seguidos + 1, str(e)[:200]
            hechas += 1
            fuentes.pausa()
    if fallos:
        corte = f" Se paró tras {CORTE_GOOGLE} fallos seguidos." if seguidos >= CORTE_GOOGLE else ""
        errores.append(f"Google Flights: fallaron {fallos} de {hechas} consultas.{corte} "
                       f"Último error: {ultimo_error}")

    # Google Flights: las combinaciones más prometedoras, como billete de ida y vuelta
    previas = combinaciones(a, al, tarifas, max_min=limite) or combinaciones(a, al, tarifas)
    elegidas = [c for c in previas if c["en_rango"]][:8]
    elegidas += [c for c in previas if c not in elegidas][:RT_TOP - len(elegidas)]
    fallos_rt = 0
    for i, c in enumerate(elegidas):
        _paso(f"{o} → {dst}: Google Flights (ida y vuelta {i + 1}/{len(elegidas)})")
        try:
            tarifas += fuentes.google_ida_vuelta(o, dst, c["ida"], c["vuelta"], bool(a["solo_directos"]),
                                                 limite, personas)
        except Exception as e:
            fallos_rt += 1
            ultimo_error = str(e)[:200]
        fuentes.pausa()
    ESTADO["paso"] = ESTADO["total"]
    if fallos_rt:
        errores.append(f"Google Flights (ida y vuelta): fallaron {fallos_rt} de {len(elegidas)}. "
                       f"Último error: {ultimo_error}")

    db.exmany("INSERT INTO tarifas(revision_id, fuente, tramo, fecha, fecha_vuelta, precio, "
              "aerolinea, escalas, hora, enlace, duracion_min, duracion_vuelta_min) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              [(rev_id, t["fuente"], t["tramo"], t["fecha"], t["fecha_vuelta"], t["precio"],
                t["aerolinea"], t["escalas"], t["hora"], t["enlace"], t.get("duracion_min"),
                t.get("duracion_vuelta_min")) for t in tarifas])

    res = analizar(a, al, tarifas)
    m = res["mejor"]
    db.ex("UPDATE revisiones SET fin=?, mejor_precio=?, mejor_json=?, por_fuente_json=?, "
          "n_tarifas=?, errores=? WHERE id=?",
          (ahora_iso(), m["precio"] if m else None, json.dumps(m, ensure_ascii=False) if m else None,
           json.dumps(res["por_fuente"]), len(tarifas), "\n".join(errores) or None, rev_id))
    log.info("Revisión %s de la alerta %s: %s tarifas, mejor %s, errores: %s",
             rev_id, a["id"], len(tarifas), m["precio"] if m else None, errores or "ninguno")

    try:
        comprobar_aviso(a, res)
    except Exception:
        log.exception("Error al comprobar el aviso")
    return rev_id


def enlace_personal(chat: str | None) -> str | None:
    """Enlace a la página pública de esa persona (solo en GitHub, donde están el repositorio y la llave)."""
    import os
    import nube
    repo, clave = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("CLAVE_DATOS")
    destino = chat or db.ajuste("telegram_chat_id", "")
    return nube.enlace_personal(repo, clave, destino) if repo and clave and destino else None


def comprobar_aviso(a: dict, res: dict) -> None:
    m = res["mejor"]
    if not m:
        return
    if m["precio"] > a["precio_objetivo"]:
        if a["ultimo_aviso_precio"] is not None:   # volvió a subir: el próximo cruce avisará
            db.ex("UPDATE alertas SET ultimo_aviso_precio=NULL WHERE id=?", (a["id"],))
        return
    if a["ultimo_aviso_precio"] is not None and m["precio"] >= a["ultimo_aviso_precio"] - 0.5:
        return   # ya avisado de este precio o de uno mejor
    alts = alternativas(a, res, analisis_horario(a["id"]))
    chat = a.get("chat_id") or None          # sin chat: la alerta es tuya
    texto = avisos.mensaje(a, m, alts, enlace_personal(chat))
    ok, detalle = avisos.enviar(texto, chat)
    if not ok:
        # No se marca como avisado: en cuanto Telegram funcione, la siguiente revisión lo enviará.
        previo = db.uno("SELECT ok, precio FROM avisos WHERE alerta_id=? ORDER BY id DESC LIMIT 1", (a["id"],))
        if previo and not previo["ok"] and previo["precio"] == m["precio"]:
            return   # mismo fallo y mismo precio: no se repite en el registro
    db.ex("INSERT INTO avisos(alerta_id, enviado, precio, mensaje, ok, detalle) VALUES(?,?,?,?,?,?)",
          (a["id"], ahora_iso(), m["precio"], texto, int(ok), detalle))
    if ok:
        db.ex("UPDATE alertas SET ultimo_aviso_precio=? WHERE id=?", (m["precio"], a["id"]))


# ---------------------------------------------------------------- resultados para la página

def detalle_ultima(a: dict) -> dict | None:
    rev = db.uno("SELECT * FROM revisiones WHERE alerta_id=? AND fin IS NOT NULL "
                 "ORDER BY inicio DESC LIMIT 1", (a["id"],))
    if not rev:
        return None
    al = alcance(a) or {"n_min": max(1, a["dias"] - a["flex"] - FLEX_ALT),
                        "n_max": a["dias"] + a["flex"] + FLEX_ALT}
    tarifas = db.q("SELECT fuente, tramo, fecha, fecha_vuelta, precio, aerolinea, escalas, hora, "
                   "enlace, duracion_min, duracion_vuelta_min FROM tarifas WHERE revision_id=?", (rev["id"],))
    res = analizar(a, al, tarifas)
    horario = analisis_horario(a["id"])
    for c in res["combos"]:
        c["enlace_google_iv"] = fuentes.google_url_ida_vuelta(a["origen"], a["destino"], c["ida"], c["vuelta"],
                                                              personas_de(a))
    return {"revision": {k: rev[k] for k in ("id", "inicio", "fin", "motivo", "n_tarifas", "errores")},
            "mejor": res["mejor"], "combos": res["combos"], "n_min": al["n_min"], "n_max": al["n_max"],
            "alternativas": alternativas(a, res, horario), "horario": horario,
            "fuentes_con_datos": sorted({t["fuente"] for t in tarifas})}


# ---------------------------------------------------------------- cola de trabajo

def encolar(alerta_id: int | None, motivo: str) -> None:
    _cola.put((alerta_id, motivo))


def pendientes() -> int:
    return _cola.qsize()


def _trabajador() -> None:
    while True:
        alerta_id, motivo = _cola.get()
        try:
            if alerta_id is None:
                lista = db.q("SELECT * FROM alertas WHERE activa=1 ORDER BY id")
            else:
                lista = db.q("SELECT * FROM alertas WHERE id=?", (alerta_id,))
            for a in lista:
                try:
                    revisar_alerta(a, motivo)
                except Exception:
                    log.exception("Fallo revisando la alerta %s", a["id"])
        finally:
            ESTADO.update(ocupado=False, texto="", paso=0, total=0, alerta_id=None,
                          ultima_fin=ahora_iso())
            _cola.task_done()


def arrancar() -> None:
    threading.Thread(target=_trabajador, name="revisiones", daemon=True).start()
