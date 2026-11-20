"""Bot de Telegram del buscador de vuelos. Lo ejecuta GitHub Actions cada 10 minutos (bot.yml).

Cualquiera con el enlace de la página pública puede crear alertas: la página abre el bot con
la alerta dentro del enlace (/start <código>, ver reglas.codigo_alerta). El bot la da de alta
con el chat de esa persona, así que los avisos le llegan a ella, y le contesta con su enlace
personal a la página pública (gráficos e historial de sus alertas).

Órdenes: /alertas (con /pausar_N, /reanudar_N y /borrar_N) y /ayuda.
El registro de GitHub es público: aquí no se imprime nada de lo que escribe la gente.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import requests

import nube
import reglas
from github_api import ErrorGitHub, GitHub

CARPETA = Path(__file__).parent
TELEGRAM = "https://api.telegram.org/bot{token}/{metodo}"


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def llamar(self, metodo: str, **datos):
        r = requests.post(TELEGRAM.format(token=self.token, metodo=metodo), json=datos, timeout=30)
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(f"Telegram ({metodo}): {d.get('description', r.status_code)}")
        return d["result"]

    def enviar(self, chat: str, texto: str) -> None:
        self.llamar("sendMessage", chat_id=chat, text=texto, parse_mode="HTML", disable_web_page_preview=True)


def _ciudades() -> dict[str, str]:
    try:
        filas = json.loads((CARPETA / "static" / "aeropuertos.json").read_text(encoding="utf-8"))
        return {f[0]: f[1] for f in filas}
    except Exception:
        return {}


@dataclass
class Contexto:
    repo: str
    clave: str
    dueno: str                       # tu chat: tus alertas no llevan chat_id y no tienen límite
    ciudades: dict = field(default_factory=_ciudades)

    def pagina(self) -> str:
        return nube.pagina_publica(self.repo)

    def enlace(self, chat: str) -> str:
        return nube.enlace_personal(self.repo, self.clave, chat)

    def ruta(self, a: dict) -> str:
        c = lambda x: escape(self.ciudades.get(x, x))
        return f"{c(a['origen'])} → {c(a['destino'])}"

    def dueno_de(self, a: dict) -> str:
        return str(a.get("chat_id") or self.dueno)


def describir(a: dict, ctx: Contexto) -> str:
    personas = int(a.get("personas") or 1)
    flex = f" (±{a['flex']})" if a.get("flex") else ""
    lineas = [f"<b>{ctx.ruta(a)}</b>",
              f"📅 Ida desde el {reglas.fecha_corta(a['fecha_desde'])} · vuelta hasta el "
              f"{reglas.fecha_corta(a['fecha_hasta'])} · {a['dias']} días{flex}"]
    objetivo = f"🎯 {reglas.euros(float(a['precio_objetivo']))} por persona"
    if personas > 1:
        objetivo = f"👥 {personas} personas · " + objetivo + f" ({reglas.euros(float(a['precio_objetivo']) * personas)} en total)"
    lineas.append(objetivo)
    extras = []
    if a.get("max_horas"):
        horas = f"{float(a['max_horas']):g}".replace(".", ",")
        extras.append(f"máx. {horas} h por trayecto")
    if a.get("solo_directos"):
        extras.append("solo directos")
    if extras:
        lineas.append("⏱ " + " · ".join(extras))
    return "\n".join(lineas)


def _enlace_txt(ctx: Contexto, chat: str) -> str:
    return (f'📊 <a href="{escape(ctx.enlace(chat))}">Tus alertas, gráficos e historial</a> '
            "(enlace personal: no lo compartas)")


def procesar(updates: list[dict], datos: dict, ctx: Contexto) -> tuple[list[tuple[str, str]], bool, list[int]]:
    """Aplica los mensajes sobre alertas.json (ya descifrado). Devuelve (respuestas, hay_cambios, ids_nuevos)."""
    respuestas: list[tuple[str, str]] = []
    cambios, nuevas = False, []
    alertas = datos.setdefault("alertas", [])
    for u in updates:
        m = u.get("message") or {}
        chat = m.get("chat") or {}
        if chat.get("type") != "private" or not m.get("text"):
            continue
        cid = str(chat["id"])
        nombre = ((m.get("from") or {}).get("first_name") or "").strip()[:60]
        orden, _, resto = m["text"].strip().partition(" ")
        orden = orden.split("@")[0].lower()
        mias = [a for a in alertas if ctx.dueno_de(a) == cid]

        if orden == "/start" and resto.strip():
            previa = next((a for a in alertas if a.get("update_id") == u["update_id"]), None)
            if previa:   # ya se creó en una ejecución anterior que no llegó a confirmar el mensaje
                respuestas.append((cid, f"✅ <b>Alerta creada</b>\n{describir(previa, ctx)}\n\n{_enlace_txt(ctx, cid)}"))
                continue
            try:
                nueva = reglas.validar_alerta(reglas.leer_codigo(resto.strip()))
            except ValueError as e:
                respuestas.append((cid, f"⚠️ No he podido crear la alerta: {escape(str(e))}\n\n"
                                        f"Vuelve a la página y revísala: {ctx.pagina()}"))
                continue
            igual = next((a for a in mias if all(a.get(k) == nueva[k] for k in
                                                ("origen", "destino", "fecha_desde", "fecha_hasta", "dias"))), None)
            if igual:
                respuestas.append((cid, f"Ya tienes esta alerta (n.º {igual['id']}).\n\n{_enlace_txt(ctx, cid)}"))
                continue
            if cid != ctx.dueno:
                if sum(1 for a in mias if a.get("activa", True)) >= reglas.MAX_POR_PERSONA:
                    respuestas.append((cid, f"Ya tienes {reglas.MAX_POR_PERSONA} alertas activas, el máximo. "
                                            "Pausa o borra alguna con /alertas y vuelve a crearla."))
                    continue
                if sum(1 for a in alertas if a.get("activa", True)) >= reglas.MAX_TOTAL:
                    respuestas.append((cid, "Ahora mismo el buscador está al completo. Prueba dentro de unos días."))
                    continue
            ultimo = max([int(a["id"]) for a in alertas] + [int(datos.get("ultimo_id") or 0)], default=0)
            nueva.update(id=ultimo + 1, update_id=u["update_id"],
                         chat_id=None if cid == ctx.dueno else cid, quien=None if cid == ctx.dueno else nombre or None)
            alertas.append(nueva)
            datos["ultimo_id"] = nueva["id"]
            cambios = True
            nuevas.append(nueva["id"])
            respuestas.append((cid, f"✅ <b>Alerta creada</b>\n{describir(nueva, ctx)}\n\n"
                                    "La primera búsqueda empieza en unos minutos y tarda entre 5 y 15. Después "
                                    "reviso los precios varias veces al día y te escribo cuando encuentre un vuelo "
                                    "por debajo de tu objetivo.\n\n"
                                    f"{_enlace_txt(ctx, cid)}\nPara ver, pausar o borrar tus alertas: /alertas"))

        elif orden in ("/alertas", "/mis_alertas"):
            if not mias:
                respuestas.append((cid, f"No tienes alertas. Créalas en {ctx.pagina()}"))
                continue
            bloques = []
            for a in sorted(mias, key=lambda x: int(x["id"])):
                estado = "" if a.get("activa", True) else " · ⏸ en pausa"
                acciones = (f"/reanudar_{a['id']}" if not a.get("activa", True) else f"/pausar_{a['id']}") \
                    + f" · /borrar_{a['id']}"
                bloques.append(f"<b>n.º {a['id']}</b>{estado}\n{describir(a, ctx)}\n{acciones}")
            respuestas.append((cid, "Tus alertas:\n\n" + "\n\n".join(bloques) + f"\n\n{_enlace_txt(ctx, cid)}"))

        elif (accion := re.fullmatch(r"/(pausar|reanudar|borrar)_(\d+)", orden)):
            que, num = accion.group(1), int(accion.group(2))
            a = next((x for x in mias if int(x["id"]) == num), None)
            if not a:
                respuestas.append((cid, "No encuentro esa alerta entre las tuyas. Mira /alertas"))
                continue
            if que == "borrar":
                alertas.remove(a)
                texto = f"🗑 Borrada: {ctx.ruta(a)}."
            elif que == "pausar":
                a["activa"] = False
                texto = f"⏸ En pausa: {ctx.ruta(a)}. Para reanudarla: /reanudar_{num}"
            else:
                if cid != ctx.dueno and sum(1 for x in mias if x.get("activa", True)) >= reglas.MAX_POR_PERSONA:
                    respuestas.append((cid, f"Ya tienes {reglas.MAX_POR_PERSONA} alertas activas: pausa otra antes."))
                    continue
                a["activa"] = True
                texto = f"▶️ Reanudada: {ctx.ruta(a)}. La revisaré en la próxima ronda."
            cambios = True
            respuestas.append((cid, texto + " La página de tus alertas se actualiza en unos minutos."))

        else:   # /start sin alerta, /ayuda o cualquier otra cosa
            hola = f"👋 Hola{', ' + escape(nombre) if nombre else ''}. " if orden == "/start" else ""
            texto = (f"{hola}Soy el buscador de vuelos baratos.\n\n"
                     f"1. Crea una alerta en {ctx.pagina()}: destino, fechas y el precio que quieres pagar.\n"
                     "2. Al pulsar «Crear alerta en Telegram» vuelves aquí: pulsa Iniciar.\n"
                     "3. Reviso los precios varias veces al día y te escribo cuando encuentre tu precio.\n\n"
                     "Contesto cada 10 minutos más o menos, no al momento.\nTus alertas: /alertas")
            if mias:
                texto += f"\n\n{_enlace_txt(ctx, cid)}"
            respuestas.append((cid, texto))
    return respuestas, cambios, nuevas


def main() -> int:
    token, clave = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("CLAVE_DATOS", "")
    repo, gh_token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
    dueno, rama = os.environ.get("TELEGRAM_CHAT_ID", ""), os.environ.get("RAMA", "main")
    if not (token and clave and repo and gh_token and dueno):
        print("Faltan secretos (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID o CLAVE_DATOS): sube el código otra vez desde la app.")
        return 1
    tg = Telegram(token)
    updates = tg.llamar("getUpdates", timeout=0, allowed_updates=["message"])
    if not updates:
        print("Sin mensajes")
        return 0
    ctx = Contexto(repo=repo, clave=clave, dueno=dueno)
    gh = GitHub(repo, gh_token)
    for intento in range(4):
        crudo, sha = gh.leer_json("alertas.json", rama)
        datos = nube.abrir_alertas(crudo, clave)
        respuestas, cambios, nuevas = procesar(updates, datos, ctx)
        if not cambios:
            break
        contenido = json.dumps(nube.cerrar_alertas(datos, clave), ensure_ascii=False, indent=2).encode("utf-8")
        try:
            gh.escribir("alertas.json", contenido, "Alertas desde el bot de Telegram [skip actions]", sha, rama)
            break
        except ErrorGitHub as e:
            if "GitHub 409" not in str(e) or intento == 3:   # 409: alguien la cambió a la vez → se repite
                raise
    tg.llamar("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0)   # mensajes leídos
    if cambios:
        # Lo que guarda el bot no lanza la revisión por sí solo (GitHub no encadena flujos así).
        try:
            gh.lanzar(nube.FLUJO_REVISAR, rama, {"alerta": str(nuevas[0]) if nuevas else "exportar"}, intentos=2)
        except ErrorGitHub as e:
            print(f"No se pudo lanzar la revisión: {str(e)[:80]}")
    fallos = 0
    for chat, texto in respuestas:
        try:
            tg.enviar(chat, texto)
        except Exception:
            fallos += 1
    print(f"Mensajes: {len(updates)} · respuestas: {len(respuestas) - fallos} (fallos: {fallos}) · "
          f"alertas nuevas: {len(nuevas)} · cambios: {'sí' if cambios else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
