"""Avisos por Telegram."""
from __future__ import annotations

from html import escape

import requests

import db
from fuentes import DURACION_ESTIMADA, NOMBRES

API = "https://api.telegram.org/bot{token}/{metodo}"


def configurado() -> bool:
    return bool(db.ajuste("telegram_token", "") and db.ajuste("telegram_chat_id", ""))


def enviar(texto: str) -> tuple[bool, str]:
    token, chat = db.ajuste("telegram_token", ""), db.ajuste("telegram_chat_id", "")
    if not token or not chat:
        return False, "Telegram sin configurar: el aviso solo se ve en la página."
    try:
        r = requests.post(API.format(token=token, metodo="sendMessage"), timeout=20, json={
            "chat_id": chat, "text": texto, "parse_mode": "HTML",
            "disable_web_page_preview": True})
        datos = r.json()
        if datos.get("ok"):
            return True, "Enviado por Telegram"
        return False, f"Telegram respondió: {datos.get('description', r.status_code)}"
    except Exception as e:  # red caída, token mal copiado…
        return False, f"No se pudo contactar con Telegram: {e}"


def detectar_chat(token: str) -> str | None:
    """Devuelve el chat del último mensaje que alguien ha escrito al bot."""
    r = requests.get(API.format(token=token, metodo="getUpdates"), timeout=20)
    datos = r.json()
    if not datos.get("ok"):
        raise RuntimeError(datos.get("description") or "token no válido")
    for upd in reversed(datos.get("result") or []):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            return str(chat["id"])
    return None


DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_corta(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def euros(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".") if x == round(x) else f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def duracion_txt(minutos: int | None, estimada: bool = False) -> str:
    if not minutos:
        return ""
    h, m = divmod(int(minutos), 60)
    return ("≈" if estimada else "") + (f"{h} h {m:02d} min" if h else f"{m} min")


def _linea_tramo(icono: str, nombre: str, fecha: str, t: dict) -> str:
    partes = [fecha_corta(fecha)]
    if t.get("hora"):
        partes.append(t["hora"])
    if t.get("aerolinea"):
        partes.append(escape(t["aerolinea"]))
    if t.get("escalas"):
        partes.append(f"{t['escalas']} escala{'s' if t['escalas'] > 1 else ''}")
    if t.get("duracion_min"):
        partes.append(duracion_txt(t["duracion_min"], t["fuente"] in DURACION_ESTIMADA))
    partes.append(f"{euros(t['precio'])} en {NOMBRES.get(t['fuente'], t['fuente'])}")
    return f"{icono} {nombre}: " + " · ".join(partes)


def mensaje(alerta: dict, combo: dict, alternativas: list[dict]) -> str:
    personas = max(1, int(alerta.get("personas") or 1))
    if personas == 1:
        precio = f"<b>{euros(combo['precio'])}</b> ida y vuelta (objetivo {euros(alerta['precio_objetivo'])})"
    else:
        precio = (f"<b>{euros(combo['precio'])} por persona</b> ida y vuelta "
                  f"(objetivo {euros(alerta['precio_objetivo'])}) · "
                  f"<b>{euros(round(combo['precio'] * personas, 2))} en total</b> para {personas}")
    lineas = [
        "✈️ <b>Vuelo por debajo de tu objetivo</b>",
        f"{alerta['origen']} → {alerta['destino']} · {precio}",
        "",
    ]
    if combo["tipo"] == "separados":
        lineas.append(_linea_tramo("🛫", "Ida", combo["ida"], combo["tramo_ida"]))
        lineas.append(_linea_tramo("🛬", "Vuelta", combo["vuelta"], combo["tramo_vuelta"]))
        lineas.append("Son dos billetes de solo ida: se compran por separado.")
        if personas > 1 and "ryanair" in (combo["tramo_ida"]["fuente"], combo["tramo_vuelta"]["fuente"]):
            lineas.append(f"Ryanair da el precio de la plaza más barata: para {personas} puede salir algo más caro.")
        lineas.append("")
        lineas.append(f'<a href="{escape(combo["tramo_ida"]["enlace"] or "")}">Ver ida</a> · '
                      f'<a href="{escape(combo["tramo_vuelta"]["enlace"] or "")}">Ver vuelta</a>')
    else:
        t = combo["tramo_ida"]
        lineas.append(f"🛫 {fecha_corta(combo['ida'])} → 🛬 {fecha_corta(combo['vuelta'])} "
                      f"({combo['dias']} días)")
        tiempos = " / ".join(x for x in (duracion_txt(t.get("duracion_min")),
                                         duracion_txt(t.get("duracion_vuelta_min"))) if x)
        detalle = [x for x in (escape(t.get("aerolinea") or ""),
                               f"{t['escalas']} escalas" if t.get("escalas") else "",
                               f"{tiempos} de trayecto" if tiempos else "") if x]
        lineas.append(" · ".join(detalle + [f"billete de ida y vuelta en {NOMBRES.get(t['fuente'], t['fuente'])}"]))
        lineas.append("")
        lineas.append(f'<a href="{escape(t.get("enlace") or "")}">Ver el vuelo</a>')
    mejores = [a for a in alternativas if a.get("ahorro")]
    if mejores:
        a = mejores[0]
        lineas.append("")
        lineas.append(f"💡 Aún más barato: {escape(a['titulo'])} → {euros(a['precio'])} "
                      f"(−{euros(a['ahorro'])})")
    return "\n".join(lineas)
