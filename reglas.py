"""Reglas de una alerta: las usan la página (app.py) y el bot de Telegram (bot.py)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

IATA = re.compile(r"^[A-Z]{3}$")
MAX_PERSONAS = 9          # lo máximo que deja reservar de una vez Google Flights
MAX_POR_PERSONA = 3       # alertas activas por persona creadas desde el bot (tú no tienes límite)
MAX_TOTAL = 15            # alertas activas en total: cada una alarga 2-10 min cada revisión


def leer_max_horas(valor) -> float | None:
    """Vacío = sin límite. Lanza ValueError con un texto para la persona."""
    if valor in (None, ""):
        return None
    try:
        horas = float(str(valor).replace(",", "."))
    except ValueError:
        raise ValueError("El límite de horas tiene que ser un número.")
    if not 1 <= horas <= 72:
        raise ValueError("El límite de horas por trayecto va de 1 a 72 (o vacío para no tener límite).")
    return horas


def leer_personas(valor) -> int:
    """Vacío = 1. Lanza ValueError con un texto para la persona."""
    if valor in (None, ""):
        return 1
    try:
        n = int(str(valor).strip())
    except ValueError:
        raise ValueError("El número de personas tiene que ser un número entero.")
    if not 1 <= n <= MAX_PERSONAS:
        raise ValueError(f"Pueden viajar de 1 a {MAX_PERSONAS} personas por alerta.")
    return n


def validar_alerta(d: dict) -> dict:
    """Comprueba el formulario. Lanza ValueError con el motivo."""
    origen = str(d.get("origen", "")).strip().upper()
    destino = str(d.get("destino", "")).strip().upper()
    if not IATA.match(origen) or not IATA.match(destino):
        raise ValueError("Origen y destino deben ser códigos de aeropuerto de 3 letras (p. ej. BCN, LIS).")
    if origen == destino:
        raise ValueError("El origen y el destino son el mismo aeropuerto.")
    try:
        desde = date.fromisoformat(d.get("fecha_desde", ""))
        hasta = date.fromisoformat(d.get("fecha_hasta", ""))
        dias = int(d.get("dias"))
        flex = int(d.get("flex") or 0)
        objetivo = float(str(d.get("precio_objetivo")).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Revisa las fechas, los días y el precio.")
    if desde < date.today() + timedelta(days=1):
        raise ValueError("La primera fecha tiene que ser a partir de mañana.")
    if hasta <= desde:
        raise ValueError("La fecha final tiene que ser posterior a la inicial.")
    if (hasta - desde).days > 120:
        raise ValueError("El rango máximo es de 120 días; divide la búsqueda en dos alertas.")
    if not 1 <= dias <= (hasta - desde).days:
        raise ValueError(f"Los días en destino tienen que estar entre 1 y {(hasta - desde).days} (lo que cabe en tu rango).")
    if not 0 <= flex <= 7:
        raise ValueError("La flexibilidad va de 0 a 7 días.")
    if objetivo <= 0:
        raise ValueError("El precio objetivo tiene que ser mayor que 0.")
    return {"origen": origen, "destino": destino, "fecha_desde": desde.isoformat(),
            "fecha_hasta": hasta.isoformat(), "dias": dias, "flex": flex, "precio_objetivo": objetivo,
            "solo_directos": bool(d.get("solo_directos")), "max_horas": leer_max_horas(d.get("max_horas")),
            "personas": leer_personas(d.get("personas")), "activa": True,
            "creada": datetime.now().isoformat(timespec="seconds")}


# ---------------------------------------------------------------- textos

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_corta(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def euros(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".") if x == round(x) else f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- enlace de la página pública → bot
# La página pública no puede escribir en GitHub: abre el bot con la alerta dentro del enlace
# (t.me/<bot>?start=<código>). Telegram solo admite hasta 64 caracteres A-Z a-z 0-9 _ -.
# BCN_LIS_270501_270531_7_2_2_150_48_0 = origen, destino, desde, hasta, días, flex, personas,
# objetivo (€ enteros), límite en medias horas (0 = sin límite), solo directos.

def codigo_alerta(d: dict) -> str:
    aammdd = lambda iso: iso[2:4] + iso[5:7] + iso[8:10]
    medias = int(round(float(d["max_horas"]) * 2)) if d.get("max_horas") else 0
    return "_".join(str(x) for x in (d["origen"], d["destino"], aammdd(d["fecha_desde"]), aammdd(d["fecha_hasta"]),
                                      int(d["dias"]), int(d.get("flex") or 0), int(d.get("personas") or 1),
                                      int(round(float(d["precio_objetivo"]))), medias, int(bool(d.get("solo_directos")))))


def leer_codigo(codigo: str) -> dict:
    """El código del enlace → formulario. Lanza ValueError si no se entiende."""
    partes = codigo.strip().split("_")
    if len(partes) != 10 or not all(re.fullmatch(r"[A-Za-z0-9]{1,6}", p) for p in partes):
        raise ValueError("El enlace no contiene una alerta válida.")
    o, d, f1, f2, dias, flex, personas, objetivo, medias, directos = partes
    iso = lambda s: f"20{s[0:2]}-{s[2:4]}-{s[4:6]}" if re.fullmatch(r"\d{6}", s) else ""
    return {"origen": o, "destino": d, "fecha_desde": iso(f1), "fecha_hasta": iso(f2), "dias": dias,
            "flex": flex, "personas": personas, "precio_objetivo": objetivo,
            "max_horas": (int(medias) / 2 if medias.isdigit() and int(medias) else None),
            "solo_directos": directos == "1"}
