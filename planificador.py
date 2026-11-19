"""Planificador: cada día sortea N horas aleatorias (5 por defecto) y lanza una
revisión de todas las alertas activas en cada una."""
from __future__ import annotations

import logging
import random
import threading
import time as _time
from datetime import date, datetime, time, timedelta

import db
import motor

log = logging.getLogger("buscador.planificador")
MARGEN_RETRASO = timedelta(minutes=45)   # si el PC estaba apagado más que esto, se da por perdida


def _parametros() -> tuple[int, int, int, int]:
    n = max(1, min(24, int(db.ajuste("revisiones_dia"))))
    h0 = max(0, min(23, int(db.ajuste("hora_inicio"))))
    h1 = max(h0 + 1, min(24, int(db.ajuste("hora_fin"))))
    sep = max(0, int(db.ajuste("separacion_min")))
    return n, h0, h1, sep


def sortear(n: int, ini: int, fin: int, sep: int) -> list[int]:
    """n minutos del día al azar en [ini, fin), separados al menos `sep` minutos."""
    if n <= 0 or fin - ini < 1:
        return []
    sep = min(sep, int((fin - ini) / n * 0.9))
    for _ in range(5000):
        xs = sorted(random.randrange(ini, fin) for _ in range(n))
        if all(b - a >= sep for a, b in zip(xs, xs[1:])):
            return xs
    paso = (fin - ini) / n   # alternativa: un minuto al azar dentro de cada tramo
    return [int(ini + i * paso + random.uniform(0, paso * 0.9)) for i in range(n)]


def planificar(dia: date, forzar: bool = False) -> None:
    iso = dia.isoformat()
    existentes = db.q("SELECT estado FROM planificacion WHERE dia=?", (iso,))
    if existentes and not forzar:
        return
    n, h0, h1, sep = _parametros()
    ini, fin = h0 * 60, h1 * 60
    ahora = datetime.now()
    if dia == ahora.date():
        ini = max(ini, ahora.hour * 60 + ahora.minute + 2)
    if fin - ini < 5:
        return
    if forzar:
        db.ex("DELETE FROM planificacion WHERE dia=? AND estado='pendiente'", (iso,))
        ya = db.uno("SELECT COUNT(*) AS n FROM planificacion WHERE dia=?", (iso,))["n"]
        n_hoy = max(0, n - ya)
    else:
        # El primer día solo queda parte de la jornada: se mantiene la misma densidad.
        n_hoy = max(1, round(n * (fin - ini) / ((h1 - h0) * 60)))
    base = datetime.combine(dia, time())
    filas = [(iso, (base + timedelta(minutes=m)).isoformat(timespec="minutes"))
             for m in sortear(n_hoy, ini, fin, sep)]
    db.exmany("INSERT INTO planificacion(dia, hora) VALUES(?, ?)", filas)
    log.info("Revisiones planificadas para %s: %s", iso, [h for _, h in filas])


def plan_de_hoy() -> list[dict]:
    return db.q("SELECT hora, estado FROM planificacion WHERE dia=? ORDER BY hora",
                (date.today().isoformat(),))


def _bucle() -> None:
    import remoto
    while True:
        if remoto.activo():          # en modo GitHub las revisiones las lanza GitHub Actions
            _time.sleep(20)
            continue
        try:
            planificar(date.today())
            ahora = datetime.now()
            vencidas = db.q("SELECT id, hora FROM planificacion WHERE estado='pendiente' AND hora<=? "
                            "ORDER BY hora", (ahora.isoformat(timespec="minutes"),))
            lanzar = False
            for p in vencidas:
                if ahora - datetime.fromisoformat(p["hora"]) > MARGEN_RETRASO:
                    db.ex("UPDATE planificacion SET estado='perdida' WHERE id=?", (p["id"],))
                    log.info("Revisión de las %s perdida (equipo apagado o suspendido)", p["hora"])
                else:
                    db.ex("UPDATE planificacion SET estado='hecha' WHERE id=?", (p["id"],))
                    lanzar = True
            if lanzar:
                motor.encolar(None, "programada")
        except Exception:
            log.exception("Error en el planificador")
        _time.sleep(20)


def arrancar() -> None:
    threading.Thread(target=_bucle, name="planificador", daemon=True).start()
