"""Lo que enseña la página de cada alerta. Lo usan la página de tu PC (app.py) y la
página pública (nube.py lo exporta cifrado para cada persona)."""
from __future__ import annotations

import json

import db
import motor


def resumen(a: dict) -> dict:
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


def vuelo(m: dict | None) -> dict | None:
    """Lo justo para describir en el historial el vuelo más barato de una revisión."""
    if not m:
        return None
    tramo = lambda t: {k: t.get(k) for k in ("fuente", "aerolinea", "escalas", "precio")} if t else None
    return {"ida": m["ida"], "vuelta": m["vuelta"], "dias": m["dias"], "tipo": m["tipo"],
            "tramo_ida": tramo(m.get("tramo_ida")), "tramo_vuelta": tramo(m.get("tramo_vuelta"))}


def detalle(a: dict) -> dict:
    historial = db.q("SELECT id, inicio, motivo, mejor_precio, mejor_json, por_fuente_json, errores, "
                     "n_tarifas FROM revisiones WHERE alerta_id=? AND fin IS NOT NULL ORDER BY inicio",
                     (a["id"],))
    record = None   # el vuelo más barato visto hasta ahora (la primera vez que salió a ese precio)
    for h in historial:
        h["por_fuente"] = json.loads(h.pop("por_fuente_json") or "{}")
        m = json.loads(h.pop("mejor_json") or "null")
        h["vuelo"] = vuelo(m)
        if m and h["mejor_precio"] is not None and (record is None or h["mejor_precio"] < record["precio"]):
            record = {**m, "precio": h["mejor_precio"], "revision_id": h["id"], "inicio": h["inicio"]}
    avs = db.q("SELECT enviado, precio, ok, detalle FROM avisos WHERE alerta_id=? ORDER BY enviado DESC",
               (a["id"],))
    return {"alerta": resumen(a), "historial": historial, "record": record,
            "ultima": motor.detalle_ultima(a), "avisos": avs}
