"""Modo nube: lo que ejecuta GitHub Actions.

    python nube.py decidir   ->  imprime «revisar=si» o «revisar=no» (solo librería estándar)
    python nube.py revisar   ->  aplica alertas.json, revisa precios, prepara la página pública
                                 de cada persona y deja la base cifrada lista para guardar

GitHub arranca el flujo en 8 huecos fijos al día (hora UTC). Cada día se eligen al azar
los que tocan (5 por defecto); GitHub suele arrancar con algunos minutos de retraso, lo
que añade todavía más azar a la hora real de la búsqueda.

El repositorio es público: alertas.json, la base de datos y los datos de la página pública
van cifrados (cifrado.py) y el registro de cada ejecución no enseña rutas ni precios.
"""
from __future__ import annotations

import base64
import json
import os
import random
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from reglas import MAX_PERSONAS

CARPETA = Path(__file__).parent
ARCHIVO_ALERTAS = CARPETA / "alertas.json"
BASE_CIFRADA = CARPETA / "vuelos.db.cifrado"
CARPETA_WEB = CARPETA / "web"      # un archivo cifrado por persona para la página pública
FLUJO_REVISAR = "revisar.yml"
HUECOS = ["17 0 * * *", "41 3 * * *", "9 6 * * *", "33 9 * * *",
          "52 12 * * *", "26 15 * * *", "4 18 * * *", "48 21 * * *"]
CAMPOS = ["origen", "destino", "fecha_desde", "fecha_hasta", "dias", "flex", "precio_objetivo",
          "solo_directos", "max_horas", "personas", "chat_id", "quien", "activa", "creada"]
CAMPOS_BUSQUEDA = {"origen", "destino", "fecha_desde", "fecha_hasta", "dias", "flex", "solo_directos",
                   "max_horas", "personas", "activa"}


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
    """alertas.json tal cual. Si está cifrado solo se ve revisiones_dia (lo que necesita decidir)."""
    if ARCHIVO_ALERTAS.exists():
        return json.loads(ARCHIVO_ALERTAS.read_text(encoding="utf-8"))
    return {"revisiones_dia": 5, "alertas": []}


# ---------------------------------------------------------------- cifrado de alertas.json

def abrir_alertas(datos: dict | None, clave: str | None) -> dict:
    """Contenido de alertas.json → {"revisiones_dia", "alertas"}, descifrado si hace falta."""
    if not datos:
        return {"revisiones_dia": 5, "alertas": []}
    if "cifrado" not in datos:
        return {"revisiones_dia": datos.get("revisiones_dia", 5), "alertas": datos.get("alertas", [])}
    if not clave:
        raise RuntimeError("alertas.json está cifrado y falta la llave (CLAVE_DATOS)")
    import cifrado
    contenido = cifrado.descifrar_json(base64.b64decode(datos["cifrado"]), clave)
    contenido["revisiones_dia"] = datos.get("revisiones_dia", contenido.get("revisiones_dia", 5))
    contenido.setdefault("alertas", [])
    return contenido


def cerrar_alertas(datos: dict, clave: str) -> dict:
    """{"revisiones_dia", "alertas"} → lo que se guarda en GitHub (revisiones_dia queda a la vista:
    lo lee «decidir» antes de instalar nada y no dice nada de nadie)."""
    import cifrado
    return {"nota": "Alertas cifradas por el buscador de vuelos (cifrado.py).",
            "revisiones_dia": int(datos.get("revisiones_dia", 5)),
            "cifrado": base64.b64encode(cifrado.cifrar_json(datos, clave)).decode("ascii")}


# ---------------------------------------------------------------- página pública

def pagina_publica(repo: str) -> str:
    """Dirección de GitHub Pages del repositorio (carpeta docs de la rama principal)."""
    dueno, nombre = repo.split("/", 1)
    return f"https://{dueno.lower()}.github.io/{nombre}/"


def enlace_personal(repo: str, clave: str, chat: str) -> str:
    """Enlace a la página pública con las alertas de esa persona. La llave va detrás de «#»:
    el navegador no la envía a ningún servidor."""
    import cifrado
    archivo, llave = cifrado.persona(clave, str(chat))
    return f"{pagina_publica(repo)}#u={archivo}&k={llave}"


def _publica(a: dict) -> dict:
    return {k: v for k, v in a.items() if k not in ("chat_id", "ultimo_aviso_precio")}


def exportar_web(clave: str, chat_dueno: str | None, carpeta: Path = CARPETA_WEB) -> int:
    """Un archivo cifrado por persona con sus alertas, gráficos e historial. Devuelve cuántos."""
    import cifrado
    import db
    import informe
    shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True)
    grupos: dict[str, list[dict]] = defaultdict(list)
    for a in db.q("SELECT * FROM alertas ORDER BY activa DESC, id DESC"):
        dueno = a["chat_id"] or chat_dueno
        if dueno:
            grupos[str(dueno)].append(a)
    generado = datetime.now().isoformat(timespec="seconds")
    for chat, lista in grupos.items():
        archivo, llave = cifrado.persona(clave, chat)
        detalles = {}
        for a in lista:
            d = informe.detalle(a)
            d["alerta"] = _publica(d["alerta"])
            detalles[str(a["id"])] = d
        paquete = {"generado": generado, "alertas": [_publica(informe.resumen(a)) for a in lista],
                   "detalles": detalles}
        (carpeta / f"{archivo}.bin").write_bytes(cifrado.cifrar_json(paquete, llave))
    return len(grupos)


def preparar_base(clave: str | None) -> None:
    """vuelos.db.cifrado (bajada de la rama datos) → vuelos.db para trabajar."""
    import db
    if BASE_CIFRADA.exists():
        if not clave:
            raise RuntimeError("La base de datos está cifrada y falta la llave (CLAVE_DATOS)")
        import cifrado
        db.RUTA.write_bytes(cifrado.descifrar(BASE_CIFRADA.read_bytes(), clave))
    # si no, vale la vuelos.db sin cifrar de antes (se cifra al guardar) o una nueva


def guardar_base(clave: str) -> None:
    import cifrado
    import db
    BASE_CIFRADA.write_bytes(cifrado.cifrar(db.RUTA.read_bytes(), clave))


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
        str(d["chat_id"]) if d.get("chat_id") not in (None, "") else None,
        str(d["quien"])[:60] if d.get("quien") else None,
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
    # El registro de las ejecuciones de un repositorio público lo puede leer cualquiera:
    # solo avisos y errores, sin rutas, fechas ni precios.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    clave = os.environ.get("CLAVE_DATOS") or None
    preparar_base(clave)
    db.iniciar(wal=False)
    datos = abrir_alertas(leer_alertas(), clave)
    cambiadas = sincronizar(datos)
    evento, cron = os.environ.get("EVENTO", ""), os.environ.get("CRON") or None
    registrar_plan(datos.get("revisiones_dia", 5), cron if evento == "schedule" else None)

    activas = db.q("SELECT * FROM alertas WHERE activa=1 ORDER BY id")
    pedida = (os.environ.get("ALERTA") or "").strip()
    if evento == "schedule":
        lista, motivo = activas, "programada"
    elif pedida == "exportar":      # el bot ha pausado o borrado algo: solo se rehace la página pública
        lista, motivo = [], "manual"
    elif evento == "push":          # han cambiado las alertas: solo las nuevas o modificadas
        lista, motivo = [a for a in activas if a["id"] in cambiadas], "manual"
    elif pedida.isdigit():          # «Revisar ahora» de una alerta concreta
        lista, motivo = [a for a in activas if a["id"] == int(pedida)], "manual"
    else:
        lista, motivo = activas, "manual"
    if evento != "schedule":
        # Si GitHub descarta una ejecución pedida (solo guarda una en espera), las alertas
        # nuevas se revisan en la siguiente, sea cual sea.
        hechas = {r["alerta_id"] for r in db.q("SELECT DISTINCT alerta_id FROM revisiones WHERE fin IS NOT NULL")}
        lista += [a for a in activas if a["id"] not in hechas and a not in lista]
    print(f"Alertas a revisar: {len(lista)} ({motivo})")
    for a in lista:
        try:
            motor.revisar_alerta(a, motivo)
        except Exception as e:
            print(f"Fallo revisando una alerta: {type(e).__name__}")
    podar()
    if clave:
        n = exportar_web(clave, os.environ.get("TELEGRAM_CHAT_ID"))
        guardar_base(clave)
        print(f"Página pública: datos de {n} persona(s), cifrados")


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
        description: "Id de la alerta (vacío = todas; exportar = solo rehacer la página pública)"
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
    timeout-minutes: 240
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
          if git fetch --depth=1 origin datos; then
            git show FETCH_HEAD:vuelos.db.cifrado > vuelos.db.cifrado 2>/dev/null || rm -f vuelos.db.cifrado
            if [ ! -s vuelos.db.cifrado ]; then rm -f vuelos.db.cifrado; git show FETCH_HEAD:vuelos.db > vuelos.db 2>/dev/null || rm -f vuelos.db; fi
          else
            echo "Primera revisión: base de datos nueva"
          fi
      - name: Revisar precios
        if: steps.decidir.outputs.revisar == 'si'
        env:
          TELEGRAM_TOKEN: ${{{{ secrets.TELEGRAM_TOKEN }}}}
          TELEGRAM_CHAT_ID: ${{{{ secrets.TELEGRAM_CHAT_ID }}}}
          AVIASALES_TOKEN: ${{{{ secrets.AVIASALES_TOKEN }}}}
          CLAVE_DATOS: ${{{{ secrets.CLAVE_DATOS }}}}
        run: python nube.py revisar
      - name: Guardar la base de datos
        if: always() && steps.decidir.outputs.revisar == 'si'
        env:
          GH_TOKEN: ${{{{ github.token }}}}
        run: |
          mkdir -p /tmp/datos
          # Con la llave solo se guarda la base cifrada; la vuelos.db sin cifrar no sale de aquí.
          if [ -f vuelos.db.cifrado ]; then cp vuelos.db.cifrado /tmp/datos/
          elif [ -f vuelos.db ]; then cp vuelos.db /tmp/datos/
          else exit 0; fi
          if [ -d web ]; then cp -r web /tmp/datos/; fi
          cd /tmp/datos
          git init -q -b datos
          git config user.name "buscador-vuelos"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A
          git commit -q -m "Datos $(date '+%Y-%m-%d %H:%M')"
          git push -q -f "https://x-access-token:${{GH_TOKEN}}@github.com/${{GITHUB_REPOSITORY}}.git" datos
"""


FLUJO_BOT = "bot.yml"


def bot_yaml(rama: str) -> str:
    """Bot de Telegram: cada 10 min mira si alguien le ha escrito (sin mensajes, acaba en segundos)."""
    return f"""name: Bot de Telegram
# Generado por el buscador de vuelos (nube.py). Lee los mensajes del bot y contesta (bot.py).
on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:

concurrency:
  group: bot-telegram
  cancel-in-progress: false

permissions:
  contents: write
  actions: write

jobs:
  bot:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      TELEGRAM_TOKEN: ${{{{ secrets.TELEGRAM_TOKEN }}}}
    steps:
      - name: ¿Hay mensajes?
        id: mensajes
        run: |
          python3 - >> "$GITHUB_OUTPUT" <<'FIN'
          import json, os, urllib.request
          token = os.environ.get("TELEGRAM_TOKEN", "")
          hay = False
          if token:
              url = f"https://api.telegram.org/bot{{token}}/getUpdates?limit=1&timeout=0"
              with urllib.request.urlopen(url, timeout=20) as r:
                  hay = bool(json.load(r).get("result"))
          print("hay=" + ("si" if hay else "no"))
          FIN
      - uses: actions/checkout@v5
        if: steps.mensajes.outputs.hay == 'si'
        with:
          ref: {rama}
      - name: Preparar Python
        if: steps.mensajes.outputs.hay == 'si'
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Instalar dependencias
        if: steps.mensajes.outputs.hay == 'si'
        run: pip install requests cryptography
      - name: Contestar
        if: steps.mensajes.outputs.hay == 'si'
        env:
          TELEGRAM_CHAT_ID: ${{{{ secrets.TELEGRAM_CHAT_ID }}}}
          CLAVE_DATOS: ${{{{ secrets.CLAVE_DATOS }}}}
          GH_TOKEN: ${{{{ github.token }}}}
          RAMA: {rama}
        run: python bot.py
"""


if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden == "decidir":
        decidir()
    elif orden == "revisar":
        revisar()
    else:
        sys.exit("Uso: python nube.py decidir | revisar")
