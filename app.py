"""Buscador de vuelos baratos: servidor local.

Arranque:  .venv\\Scripts\\python.exe app.py --abrir
Página:    http://127.0.0.1:5077

Dos modos:
- local: este PC revisa los precios (solo mientras está encendido);
- GitHub: las revisiones las hace GitHub Actions aunque el PC esté apagado, y esta
  página enseña los resultados y guarda allí las alertas (ver remoto.py y nube.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import webbrowser
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

# En redes de empresa con inspección HTTPS (Netskope, Zscaler…) el proxy firma las webs
# con su propio certificado, que está en el almacén de Windows pero no en el de Python.
# truststore hace que Python use el almacén de Windows, como el navegador.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from flask import Flask, jsonify, request, send_from_directory

import avisos
import db
import motor
import nube
import planificador
import remoto

PUERTO = 5077
CARPETA = Path(__file__).parent
app = Flask(__name__, static_folder=str(CARPETA / "static"), static_url_path="/static")
IATA = re.compile(r"^[A-Z]{3}$")


def error(texto: str, codigo: int = 400):
    return jsonify({"error": texto}), codigo


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
            "activa": True, "creada": motor.ahora_iso()}


# ---------------------------------------------------------------- página

@app.get("/")
def portada():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------- alertas

def _resumen(a: dict) -> dict:
    ultima = db.uno("SELECT inicio, mejor_precio, errores FROM revisiones WHERE alerta_id=? "
                    "AND fin IS NOT NULL ORDER BY inicio DESC LIMIT 1", (a["id"],))
    n = db.uno("SELECT COUNT(*) AS n FROM revisiones WHERE alerta_id=? AND fin IS NOT NULL",
               (a["id"],))["n"]
    # El mejor precio hasta ahora y la primera vez que se vio.
    mejor = db.uno("SELECT inicio, mejor_precio FROM revisiones WHERE alerta_id=? AND fin IS NOT NULL "
                   "AND mejor_precio IS NOT NULL ORDER BY mejor_precio, inicio LIMIT 1", (a["id"],))
    return {**a, "ultima": ultima, "revisiones": n,
            "minimo": mejor["mejor_precio"] if mejor else None,
            "minimo_fecha": mejor["inicio"] if mejor else None}


def _vuelo(m: dict | None) -> dict | None:
    """Lo justo para describir en el historial el vuelo más barato de una revisión."""
    if not m:
        return None
    tramo = lambda t: {k: t.get(k) for k in ("fuente", "aerolinea", "escalas", "precio")} if t else None
    return {"ida": m["ida"], "vuelta": m["vuelta"], "dias": m["dias"], "tipo": m["tipo"],
            "tramo_ida": tramo(m.get("tramo_ida")), "tramo_vuelta": tramo(m.get("tramo_vuelta"))}


@app.get("/api/alertas")
def listar():
    remoto.refrescar()
    return jsonify([_resumen(a) for a in db.q("SELECT * FROM alertas ORDER BY activa DESC, id DESC")])


@app.post("/api/alertas")
def crear():
    try:
        nueva = validar_alerta(request.get_json(force=True) or {})
    except ValueError as e:
        return error(str(e))
    if remoto.activo():
        def anadir(datos):
            ids = [a["id"] for a in datos["alertas"]] + [a["id"] for a in db.q("SELECT id FROM alertas")]
            nueva["id"] = max(ids, default=0) + 1
            datos["alertas"].append(nueva)
            return nueva["id"]
        try:
            nuevo = remoto.modificar_alertas(anadir, f"Nueva alerta {nueva['origen']}-{nueva['destino']}")
        except Exception as e:
            return error(f"No se pudo guardar en GitHub: {e}")
        return jsonify({"id": nuevo})
    campos = [c for c in nube.CAMPOS]
    nuevo = db.ex(f"INSERT INTO alertas({', '.join(campos)}) VALUES({', '.join('?' * len(campos))})",
                  tuple(int(nueva[c]) if isinstance(nueva[c], bool) else nueva[c] for c in campos))
    motor.encolar(nuevo, "manual")
    return jsonify({"id": nuevo})


@app.patch("/api/alertas/<int:alerta_id>")
def modificar(alerta_id: int):
    d = request.get_json(force=True) or {}
    cambios = {}
    if "activa" in d:
        cambios["activa"] = bool(d["activa"])
    if "precio_objetivo" in d:
        try:
            cambios["precio_objetivo"] = float(str(d["precio_objetivo"]).replace(",", "."))
        except ValueError:
            return error("Precio no válido.")
    if "max_horas" in d:
        try:
            cambios["max_horas"] = leer_max_horas(d["max_horas"])
        except ValueError as e:
            return error(str(e))
    if not cambios:
        return jsonify({"ok": True})

    if remoto.activo():
        def aplicar(datos):
            for a in datos["alertas"]:
                if int(a["id"]) == alerta_id:
                    a.update(cambios)
        # Solo hace falta revisar al momento si cambia lo que se busca; si no, se aplica en la
        # próxima revisión programada y no se gastan minutos de GitHub.
        revisar_ya = "max_horas" in cambios or cambios.get("activa") is True
        try:
            remoto.modificar_alertas(aplicar, f"Cambio en la alerta {alerta_id}"
                                              f"{'' if revisar_ya else ' [skip actions]'}")
        except Exception as e:
            return error(f"No se pudo guardar en GitHub: {e}")
        return jsonify({"ok": True})

    for campo, valor in cambios.items():
        reinicia = ", ultimo_aviso_precio=NULL" if campo in ("precio_objetivo", "max_horas") else ""
        db.ex(f"UPDATE alertas SET {campo}=?{reinicia} WHERE id=?",
              (int(valor) if isinstance(valor, bool) else valor, alerta_id))
    if "max_horas" in cambios:
        motor.encolar(alerta_id, "manual")   # el límite cambia qué vuelos se piden a Google
    return jsonify({"ok": True})


@app.delete("/api/alertas/<int:alerta_id>")
def borrar(alerta_id: int):
    if remoto.activo():
        def quitar(datos):
            datos["alertas"] = [a for a in datos["alertas"] if int(a["id"]) != alerta_id]
        try:
            remoto.modificar_alertas(quitar, f"Alerta {alerta_id} borrada [skip actions]")
        except Exception as e:
            return error(f"No se pudo guardar en GitHub: {e}")
    db.ex("DELETE FROM alertas WHERE id=?", (alerta_id,))
    return jsonify({"ok": True})


@app.post("/api/alertas/<int:alerta_id>/revisar")
def revisar(alerta_id: int):
    if remoto.activo():
        try:
            remoto.revisar_ahora(alerta_id)
        except Exception as e:
            return error(f"GitHub no aceptó la petición: {e}")
        return jsonify({"ok": True, "nube": True})
    motor.encolar(alerta_id, "manual")
    return jsonify({"ok": True})


@app.get("/api/alertas/<int:alerta_id>")
def detalle(alerta_id: int):
    remoto.refrescar()
    a = db.uno("SELECT * FROM alertas WHERE id=?", (alerta_id,))
    if not a:
        return error("No existe esa alerta.", 404)
    historial = db.q("SELECT id, inicio, motivo, mejor_precio, mejor_json, por_fuente_json, errores, "
                     "n_tarifas FROM revisiones WHERE alerta_id=? AND fin IS NOT NULL ORDER BY inicio",
                     (alerta_id,))
    record = None   # el vuelo más barato visto hasta ahora (la primera vez que salió a ese precio)
    for h in historial:
        h["por_fuente"] = json.loads(h.pop("por_fuente_json") or "{}")
        m = json.loads(h.pop("mejor_json") or "null")
        h["vuelo"] = _vuelo(m)
        if m and h["mejor_precio"] is not None and (record is None or h["mejor_precio"] < record["precio"]):
            record = {**m, "precio": h["mejor_precio"], "revision_id": h["id"], "inicio": h["inicio"]}
    avs = db.q("SELECT enviado, precio, ok, detalle FROM avisos WHERE alerta_id=? ORDER BY enviado DESC",
               (alerta_id,))
    return jsonify({"alerta": _resumen(a), "historial": historial, "record": record,
                    "ultima": motor.detalle_ultima(a), "avisos": avs})


# ---------------------------------------------------------------- estado y ajustes

@app.get("/api/estado")
def estado():
    if remoto.activo():
        info = remoto.estado()
        ultima = db.uno("SELECT MAX(fin) AS fin FROM revisiones")
        return jsonify({**info, "ultima_fin": ultima["fin"] if ultima else None,
                        "plan_hoy": planificador.plan_de_hoy()})
    return jsonify({**motor.ESTADO, "modo": "local", "pendientes": motor.pendientes(),
                    "plan_hoy": planificador.plan_de_hoy(),
                    "telegram": avisos.configurado(),
                    "aviasales": bool(db.ajuste("aviasales_token", ""))})


def _mascara(valor: str) -> str:
    return (valor[:4] + "…" + valor[-4:]) if len(valor) > 10 else ("•" * len(valor))


@app.get("/api/ajustes")
def ver_ajustes():
    tg, av = db.ajuste("telegram_token", ""), db.ajuste("aviasales_token", "")
    conf = remoto.config()
    datos = {"telegram_token": _mascara(tg) if tg else "", "telegram_chat_id": db.ajuste("telegram_chat_id", ""),
             "aviasales_token": _mascara(av) if av else "",
             **{k: db.ajuste(k) for k in db.AJUSTES_POR_DEFECTO},
             "github": {"conectado": bool(conf), "repo": conf["repo"] if conf else ""}}
    if conf:
        datos["revisiones_dia"] = remoto.estado()["revisiones_dia"]
    return jsonify(datos)


@app.post("/api/ajustes")
def guardar_ajustes():
    d = request.get_json(force=True) or {}
    secretos_cambian = False
    for clave in ("telegram_token", "aviasales_token"):
        valor = str(d.get(clave, "")).strip()
        if valor and "…" not in valor and "•" not in valor and valor != db.ajuste(clave, ""):
            db.guardar_ajuste(clave, valor)
            secretos_cambian = True
    if "telegram_chat_id" in d and str(d["telegram_chat_id"]).strip() != db.ajuste("telegram_chat_id", ""):
        db.guardar_ajuste("telegram_chat_id", str(d["telegram_chat_id"]).strip())
        secretos_cambian = True
    try:
        n = int(d.get("revisiones_dia", db.ajuste("revisiones_dia")))
        h0 = int(d.get("hora_inicio", db.ajuste("hora_inicio")))
        h1 = int(d.get("hora_fin", db.ajuste("hora_fin")))
        sep = int(d.get("separacion_min", db.ajuste("separacion_min")))
    except ValueError:
        return error("Los valores de la planificación tienen que ser números enteros.")

    if remoto.activo():
        if not 1 <= n <= len(nube.HUECOS):
            return error(f"En GitHub se pueden hacer de 1 a {len(nube.HUECOS)} revisiones al día.")
        try:
            if secretos_cambian:
                remoto.subir_secretos(remoto.cliente())
            if n != remoto.estado()["revisiones_dia"]:
                remoto.modificar_alertas(lambda datos: datos.update(revisiones_dia=n),
                                         f"{n} revisiones al día [skip actions]")
        except Exception as e:
            return error(f"Guardado en este PC, pero no en GitHub: {e}")
        return jsonify({"ok": True})

    if not (1 <= n <= 24 and 0 <= h0 < h1 <= 24 and 0 <= sep <= 600):
        return error("Planificación no válida: 1–24 revisiones, horas entre 0 y 24 y separación de 0 a 600 min.")
    cambia_plan = (n, h0, h1, sep) != tuple(int(db.ajuste(k)) for k in
                                            ("revisiones_dia", "hora_inicio", "hora_fin", "separacion_min"))
    for k, v in (("revisiones_dia", n), ("hora_inicio", h0), ("hora_fin", h1), ("separacion_min", sep)):
        db.guardar_ajuste(k, str(v))
    if cambia_plan:
        planificador.planificar(date.today(), forzar=True)
    return jsonify({"ok": True})


@app.post("/api/ajustes/detectar-chat")
def detectar_chat():
    token = db.ajuste("telegram_token", "")
    if not token:
        return error("Primero guarda el token del bot.")
    try:
        chat = avisos.detectar_chat(token)
    except Exception as e:
        return error(f"Telegram no acepta el token: {e}")
    if not chat:
        return error("No hay mensajes: abre tu bot en Telegram, pulsa Iniciar, escríbele «hola» y vuelve a probar.")
    db.guardar_ajuste("telegram_chat_id", chat)
    if remoto.activo():
        try:
            remoto.subir_secretos(remoto.cliente())
        except Exception as e:
            return error(f"Chat detectado, pero no se pudo guardar en GitHub: {e}")
    return jsonify({"chat_id": chat})


@app.post("/api/ajustes/probar-aviso")
def probar_aviso():
    ok, detalle_envio = avisos.enviar("✅ <b>Buscador de vuelos</b>\nLos avisos llegan bien. "
                                      "Te escribiré cuando encuentre un vuelo a tu precio.")
    return (jsonify({"ok": True}) if ok else error(detalle_envio))


@app.post("/api/plan/resortear")
def resortear():
    if remoto.activo():
        return error("En modo GitHub las horas se sortean solas cada día.")
    planificador.planificar(date.today(), forzar=True)
    return jsonify({"plan_hoy": planificador.plan_de_hoy()})


@app.post("/api/github/conectar")
def github_conectar():
    d = request.get_json(force=True) or {}
    repo, token = str(d.get("repo", "")).strip(), str(d.get("token", "")).strip()
    # también vale la dirección copiada del navegador: https://github.com/usuario/nombre(.git)
    repo = re.sub(r"^(https?://)?(www\.)?github\.com/", "", repo, flags=re.I).strip("/")
    repo = re.sub(r"\.git$", "", repo, flags=re.I)
    if "/" not in repo:
        conf = remoto.config()
        repo = repo or (conf or {}).get("repo", "")
    if not token and remoto.config():
        token = remoto.config()["token"]   # actualizar el código con el token ya guardado
    if "/" not in repo or not token:
        return error("Indica el repositorio como usuario/nombre y el token.")
    if motor.ESTADO["ocupado"] or motor.pendientes():
        return error("Hay una revisión en curso en este PC: espera a que termine.")
    try:
        resultado = remoto.conectar(repo, token, aceptar_publico=bool(d.get("aceptar_publico")))
    except remoto.RepoPublico as e:
        return jsonify({"error": str(e), "codigo": "publico"}), 409
    except Exception as e:
        return error(str(e))
    return jsonify(resultado)


@app.post("/api/github/desconectar")
def github_desconectar():
    remoto.desconectar()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- arranque

def configurar_log() -> None:
    manejador = RotatingFileHandler(CARPETA / "buscador.log", maxBytes=2_000_000, backupCount=3,
                                    encoding="utf-8")
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(manejador)
    for ruidoso in ("werkzeug", "primp", "fast_flights", "urllib3"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def main() -> None:
    p = argparse.ArgumentParser(description="Buscador de vuelos baratos")
    p.add_argument("--abrir", action="store_true", help="abre la página en el navegador")
    args = p.parse_args()
    configurar_log()
    db.iniciar()                      # base local: ajustes y, en modo local, también los datos
    if remoto.activo():
        remoto.usar_base_nube()
        threading.Thread(target=remoto.refrescar, kwargs={"forzar": True}, daemon=True).start()
    motor.arrancar()
    planificador.arrancar()           # no hace nada mientras el modo GitHub esté activo
    if args.abrir:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PUERTO}")).start()
    app.run(host="127.0.0.1", port=PUERTO, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
