"""Modo nube: lo que ejecuta GitHub Actions.

    python nube.py decidir   ->  imprime «revisar=si» o «revisar=no» (solo librería estándar)
    python nube.py revisar   ->  aplica alertas.json, revisa precios y deja vuelos.db lista para guardar

GitHub arranca el flujo en 8 huecos fijos al día (hora UTC). Cada día se eligen al azar
los que tocan (5 por defecto); GitHub suele arrancar con algunos minutos de retraso, lo
que añade todavía más azar a la hora real de la búsqueda.
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CARPETA = Path(__file__).parent
ARCHIVO_ALERTAS = CARPETA / "alertas.json"
HUECOS = ["17 0 * * *", "41 3 * * *", "9 6 * * *", "33 9 * * *",
          "52 12 * * *", "26 15 * * *", "4 18 * * *", "48 21 * * *"]
CAMPOS = ["origen", "destino", "fecha_desde", "fecha_hasta", "dias", "flex", "precio_objetivo",
          "solo_directos", "max_horas", "personas", "activa", "creada"]
CAMPOS_BUSQUEDA = {"origen", "destino", "fecha_desde", "fecha_hasta", "dias", "flex", "solo_directos",
                   "max_horas", "personas", "activa"}
MAX_PERSONAS = 9   # lo máximo que deja reservar de una vez Google Flights


# ---------------------------------------------------------------- planificación (sin dependencias)

def _hora_minuto(cron: str) -> tuple[int, int]:
    minuto, hora = cron.split()[:2]
    return int(hora), int(minuto)


def momento_programado(cron: str, ahora: datetime) -> datetime:
    """Última vez (UTC) que tocaba ese hueco; corrige los retrasos de GitHub."""
    h, m = _hora_minuto(cron)
    t = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t <= ahora else t - timedelta(days=1)


def elegidos(dia: date, n: int) -> list[int]:
    """Índices de los huecos que tocan ese día (al azar, pero igual en cada ejecución)."""
    n = max(1, min(int(n), len(HUECOS)))
    return sorted(random.Random(f"buscador-vuelos|{dia.isoformat()}").sample(range(len(HUECOS)), n))


def leer_alertas() -> dict:
    if ARCHIVO_ALERTAS.exists():
        return json.loads(ARCHIVO_ALERTAS.read_text(encoding="utf-8"))
    return {"revisiones_dia": 5, "alertas": []}


def decidir() -> None:
    evento, cron = os.environ.get("EVENTO", ""), os.environ.get("CRON", "")
    if evento != "schedule":
        print("revisar=si")
        return
    if cron not in HUECOS:
        print("revisar=no")
        return
    programado = momento_programado(cron, datetime.now(timezone.utc))
    toca = HUECOS.index(cron) in elegidos(programado.date(), leer_alertas().get("revisiones_dia", 5))
    print("revisar=si" if toca else "revisar=no")


# ---------------------------------------------------------------- alertas.json -> base de datos

def _normalizar(d: dict) -> list:
    return [
        str(d["origen"]).upper(), str(d["destino"]).upper(), d["fecha_desde"], d["fecha_hasta"],
        int(d["dias"]), int(d.get("flex") or 0), float(d["precio_objetivo"]),
        int(bool(d.get("solo_directos"))),
        float(d["max_horas"]) if d.get("max_horas") not in (None, "") else None,
        max(1, min(MAX_PERSONAS, int(d.get("personas") or 1))),
        int(bool(d.get("activa", True))), d.get("creada") or datetime.now().isoformat(timespec="seconds"),
    ]


def sincronizar(datos: dict) -> set[int]:
    """Aplica las definiciones de alertas.json a la base de datos.

    Devuelve los ids nuevos o cuya búsqueda ha cambiado (fechas, días, límite…).
    """
    import db
    definidas = {int(a["id"]): a for a in datos.get("alertas", [])}
    actuales = {a["id"]: a for a in db.q("SELECT * FROM alertas")}
    cambiadas: set[int] = set()
    for id_, d in definidas.items():
        valores = _normalizar(d)
        nuevo = dict(zip(CAMPOS, valores))
        viejo = actuales.get(id_)
        if viejo is None:
            db.ex(f"INSERT INTO alertas(id, {', '.join(CAMPOS)}) VALUES(?{', ?' * len(CAMPOS)})",
                  (id_, *valores))
            cambiadas.add(id_)
            continue
        distintos = {c for c in CAMPOS if c != "creada" and viejo[c] != nuevo[c]}
        if not distintos:
            continue
        reinicia_aviso = bool(distintos & {"precio_objetivo", "max_horas", "solo_directos", "fecha_desde",
                                           "fecha_hasta", "dias", "flex", "origen", "destino", "personas"})
        db.ex(f"UPDATE alertas SET {', '.join(f'{c}=?' for c in CAMPOS)}"
              f"{', ultimo_aviso_precio=NULL' if reinicia_aviso else ''} WHERE id=?", (*valores, id_))
        if distintos & CAMPOS_BUSQUEDA and nuevo["activa"]:
            cambiadas.add(id_)
    for id_ in set(actuales) - set(definidas):
        db.ex("DELETE FROM alertas WHERE id=?", (id_,))
    return cambiadas


def alerta_a_json(a: dict) -> dict:
    return {"id": a["id"], **{c: a[c] for c in CAMPOS}}


# ---------------------------------------------------------------- revisión en GitHub

def registrar_plan(n: int, cron: str | None) -> None:
    """Guarda las horas de hoy (hora de Madrid) para que la página las enseñe."""
    import db
    from zoneinfo import ZoneInfo
    madrid = ZoneInfo("Europe/Madrid")
    ahora_utc = datetime.now(timezone.utc)
    dia_utc = (momento_programado(cron, ahora_utc) if cron in HUECOS else ahora_utc).date()

    def local(i: int) -> str:
        h, m = _hora_minuto(HUECOS[i])
        return datetime(dia_utc.year, dia_utc.month, dia_utc.day, h, m, tzinfo=timezone.utc) \
            .astimezone(madrid).strftime("%Y-%m-%dT%H:%M")

    horas = [local(i) for i in elegidos(dia_utc, n)]
    dia = horas[0][:10]
    if not db.q("SELECT 1 FROM planificacion WHERE dia=?", (dia,)):
        db.exmany("INSERT INTO planificacion(dia, hora) VALUES(?, ?)", [(dia, h) for h in horas])
    if cron in HUECOS:
        db.ex("UPDATE planificacion SET estado='hecha' WHERE dia=? AND hora=?", (dia, local(HUECOS.index(cron))))
    # GitHub a veces no lanza un hueco programado: pasadas 3 h se da por perdido.
    limite = (datetime.now(madrid) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    db.ex("UPDATE planificacion SET estado='perdida' WHERE estado='pendiente' AND hora<?", (limite,))


def podar() -> None:
    """Mantiene pequeña la base de datos que se guarda en GitHub."""
    import db
    # Los enlaces solo hacen falta en la última revisión de cada alerta y son lo que más ocupa.
    db.ex("UPDATE tarifas SET enlace=NULL WHERE enlace IS NOT NULL AND revision_id NOT IN "
          "(SELECT MAX(id) FROM revisiones WHERE fin IS NOT NULL GROUP BY alerta_id)")
    limite = (date.today() - timedelta(days=180)).isoformat()
    db.ex("DELETE FROM revisiones WHERE inicio < ?", (limite,))
    db.ex("DELETE FROM planificacion WHERE dia < ?", (limite,))
    db.compactar()


def revisar() -> None:
    import logging
    import db
    import motor
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for ruidoso in ("primp", "fast_flights", "urllib3"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    db.iniciar(wal=False)
    datos = leer_alertas()
    cambiadas = sincronizar(datos)
    evento, cron = os.environ.get("EVENTO", ""), os.environ.get("CRON") or None
    registrar_plan(datos.get("revisiones_dia", 5), cron if evento == "schedule" else None)

    activas = db.q("SELECT * FROM alertas WHERE activa=1 ORDER BY id")
    pedida = (os.environ.get("ALERTA") or "").strip()
    if evento == "schedule":
        lista, motivo = activas, "programada"
    elif evento == "push":          # han cambiado las alertas: solo las nuevas o modificadas
        lista, motivo = [a for a in activas if a["id"] in cambiadas], "manual"
    elif pedida.isdigit():          # «Revisar ahora» de una alerta concreta
        lista, motivo = [a for a in activas if a["id"] == int(pedida)], "manual"
    else:
        lista, motivo = activas, "manual"
    print(f"Alertas a revisar: {[a['id'] for a in lista]} ({motivo})")
    for a in lista:
        try:
            motor.revisar_alerta(a, motivo)
        except Exception:
            logging.exception("Fallo revisando la alerta %s", a["id"])
    podar()


# ---------------------------------------------------------------- flujo de GitHub Actions

def flujo_yaml(rama: str) -> str:
    crons = "\n".join(f'    - cron: "{c}"' for c in HUECOS)
    return f"""name: Revisar vuelos
# Generado por el buscador de vuelos (nube.py). Si cambias los huecos, cámbialos también allí.
on:
  schedule:
{crons}
  push:
    branches: [{rama}]
    paths: ["alertas.json"]
  workflow_dispatch:
    inputs:
      alerta:
        description: "Id de la alerta (vacío = todas)"
        required: false
        default: ""

concurrency:
  group: buscador-vuelos
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  revisar:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    env:
      TZ: Europe/Madrid
      EVENTO: ${{{{ github.event_name }}}}
      CRON: ${{{{ github.event.schedule }}}}
      ALERTA: ${{{{ github.event.inputs.alerta }}}}
    steps:
      - uses: actions/checkout@v5
      - name: ¿Toca revisar ahora?
        id: decidir
        run: python3 nube.py decidir >> "$GITHUB_OUTPUT"
      - name: Preparar Python
        if: steps.decidir.outputs.revisar == 'si'
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
      - name: Instalar dependencias
        if: steps.decidir.outputs.revisar == 'si'
        run: pip install -r requirements.txt
      - name: Traer la base de datos
        if: steps.decidir.outputs.revisar == 'si'
        run: |
          if git fetch --depth=1 origin datos; then git show FETCH_HEAD:vuelos.db > vuelos.db; else echo "Primera revisión: base de datos nueva"; fi
      - name: Revisar precios
        if: steps.decidir.outputs.revisar == 'si'
        env:
          TELEGRAM_TOKEN: ${{{{ secrets.TELEGRAM_TOKEN }}}}
          TELEGRAM_CHAT_ID: ${{{{ secrets.TELEGRAM_CHAT_ID }}}}
          AVIASALES_TOKEN: ${{{{ secrets.AVIASALES_TOKEN }}}}
        run: python nube.py revisar
      - name: Guardar la base de datos
        if: always() && steps.decidir.outputs.revisar == 'si'
        env:
          GH_TOKEN: ${{{{ github.token }}}}
        run: |
          test -f vuelos.db || exit 0
          mkdir -p /tmp/datos && cp vuelos.db /tmp/datos/ && cd /tmp/datos
          git init -q -b datos
          git config user.name "buscador-vuelos"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add vuelos.db
          git commit -q -m "Datos $(date '+%Y-%m-%d %H:%M')"
          git push -q -f "https://x-access-token:${{GH_TOKEN}}@github.com/${{GITHUB_REPOSITORY}}.git" datos
"""


if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden == "decidir":
        decidir()
    elif orden == "revisar":
        revisar()
    else:
        sys.exit("Uso: python nube.py decidir | revisar")
