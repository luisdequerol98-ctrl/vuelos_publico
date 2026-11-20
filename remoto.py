"""Modo nube visto desde tu PC.

Las revisiones las hace GitHub Actions aunque el PC esté apagado. Esta página:
- baja de GitHub la base de datos con los resultados (rama «datos»),
- guarda tus alertas en alertas.json (rama principal), lo que lanza su primera revisión,
- pide revisiones al momento («Revisar ahora»),
- sube el código, el bot de Telegram (bot.py) y la página pública (carpeta docs).

El repositorio es público: alertas.json y la base de datos van cifrados con la llave de
nube.json, que también se guarda en GitHub como secreto CLAVE_DATOS (ver cifrado.py).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

import requests

import cifrado
import db
import nube
from github_api import GitHub

log = logging.getLogger("buscador.remoto")
CARPETA = Path(__file__).parent
CONFIG = CARPETA / "nube.json"
RUTA_DB = CARPETA / "vuelos-nube.db"
FLUJO = nube.FLUJO_REVISAR
ARCHIVOS_CODIGO = ["app.py", "avisos.py", "db.py", "fuentes.py", "motor.py", "planificador.py",
                   "nube.py", "github_api.py", "remoto.py", "cifrado.py", "reglas.py", "informe.py",
                   "bot.py", "requirements.txt", "README.md", "arrancar.bat",
                   "static/index.html", "static/app.js", "static/estilo.css",
                   "static/aeropuertos.json", "herramientas/generar_aeropuertos.py"]

_bloqueo = threading.RLock()
_cache = {"sha_db": None, "alertas": None, "comprobado": 0.0, "activas": 0, "secretos": None,
          "ultima_ejecucion": None, "error": None}


# ---------------------------------------------------------------- configuración

def config() -> dict | None:
    try:
        datos = json.loads(CONFIG.read_text(encoding="utf-8"))
        return datos if datos.get("repo") and datos.get("token") else None
    except (FileNotFoundError, ValueError):
        return None


def activo() -> bool:
    return config() is not None


def cliente() -> GitHub:
    c = config()
    if not c:
        raise RuntimeError("El modo GitHub no está configurado.")
    return GitHub(c["repo"], c["token"])


def rama() -> str:
    return (config() or {}).get("rama", "main")


def clave() -> str | None:
    """Llave maestra de los datos cifrados (None si aún no se ha subido el código con cifrado)."""
    return (config() or {}).get("clave_datos")


def usar_base_nube() -> None:
    """A partir de aquí la página lee la copia bajada de GitHub."""
    db.RUTA = RUTA_DB
    if not RUTA_DB.exists():
        db.iniciar(wal=False, ruta=RUTA_DB)


# ---------------------------------------------------------------- conectar y subir

def _alertas_locales() -> dict:
    with closing(db.conectar(db.RUTA_AJUSTES)) as c:
        filas = [dict(r) for r in c.execute("SELECT * FROM alertas ORDER BY id")]
    return {"revisiones_dia": int(db.ajuste("revisiones_dia")),
            "alertas": [nube.alerta_a_json(a) for a in filas]}


def _base_local_sin_secretos() -> bytes | None:
    """Copia de tu base local para conservar el historial, sin tokens ni ajustes."""
    origen = db.RUTA_AJUSTES
    if not origen.exists():
        return None
    with closing(sqlite3.connect(origen)) as c:
        if not c.execute("SELECT COUNT(*) FROM revisiones").fetchone()[0]:
            return None
    carpeta = Path(tempfile.mkdtemp())
    copia = carpeta / "vuelos.db"
    with closing(sqlite3.connect(origen)) as fuente, closing(sqlite3.connect(copia)) as destino:
        fuente.backup(destino)
    with closing(sqlite3.connect(copia)) as c:
        c.isolation_level = None
        c.execute("DELETE FROM ajustes")
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute("VACUUM")
    contenido = copia.read_bytes()
    shutil.rmtree(carpeta, ignore_errors=True)
    return contenido


def subir_secretos(gh: GitHub, llave: str | None = None) -> list[str]:
    subidos = []
    for nombre, clave_ajuste in (("TELEGRAM_TOKEN", "telegram_token"), ("TELEGRAM_CHAT_ID", "telegram_chat_id"),
                                 ("AVIASALES_TOKEN", "aviasales_token")):
        valor = db.ajuste(clave_ajuste, "")
        if valor:
            gh.guardar_secreto(nombre, valor)
            subidos.append(nombre)
    llave = llave or clave()
    if llave:
        gh.guardar_secreto("CLAVE_DATOS", llave)
        subidos.append("CLAVE_DATOS")
    _cache["secretos"] = None
    return subidos


def _vaciar_copia_nube() -> None:
    """Al cambiar de repositorio, la copia de este PC empieza de cero (sin alertas ni historial
    del anterior, que si no se mezclarían con las alertas nuevas que reciban el mismo número)."""
    carpeta = Path(tempfile.mkdtemp())
    vacia = carpeta / "vuelos.db"
    db.iniciar(wal=False, ruta=vacia)
    _reemplazar_base(vacia.read_bytes())
    shutil.rmtree(carpeta, ignore_errors=True)


def nombre_bot() -> str:
    """@usuario del bot de Telegram (lo necesita la página pública para abrirlo)."""
    token = db.ajuste("telegram_token", "")
    if not token:
        return ""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20).json()
        return (r.get("result") or {}).get("username", "")
    except Exception as e:
        log.warning("No se pudo leer el nombre del bot: %s", type(e).__name__)
        return ""


def archivos_web(repo: str) -> dict[str, bytes]:
    """La página pública (GitHub Pages, carpeta docs): la misma página que en tu PC, en «modo
    público». Lee los datos cifrados de cada persona de la rama datos."""
    est = CARPETA / "static"
    indice = (est / "index.html").read_text(encoding="utf-8")
    indice = indice.replace('href="/static/estilo.css"', 'href="estilo.css"').replace(
        '<script src="/static/app.js"></script>', '<script src="config.js"></script>\n  <script src="app.js"></script>')
    ajustes = {"bot": nombre_bot(), "datos": f"https://raw.githubusercontent.com/{repo}/datos/web/"}
    return {
        "docs/index.html": indice.encode("utf-8"),
        "docs/config.js": f"window.BUSCADOR_PUBLICO = {json.dumps(ajustes)};\n".encode("utf-8"),
        "docs/app.js": (est / "app.js").read_bytes(),
        "docs/estilo.css": (est / "estilo.css").read_bytes(),
        "docs/aeropuertos.json": (est / "aeropuertos.json").read_bytes(),
        "docs/.nojekyll": b"",
    }


class RepoPublico(RuntimeError):
    """El repositorio es público y la persona aún no lo ha aceptado."""


def conectar(repo: str, token: str, aceptar_publico: bool = False) -> dict:
    """Comprueba el acceso, sube el código, los flujos y la página pública, guarda los secretos y
    lanza la primera revisión. Si ya había otro repositorio conectado, el nuevo empieza de cero y
    el flujo del anterior se desactiva (para que no revisen los dos ni se gasten minutos)."""
    gh = GitHub(repo, token)
    info = gh.info()
    anterior = config()
    cambio = bool(anterior) and anterior["repo"].lower() != info.get("full_name", repo).lower()
    if not info.get("private") and not aceptar_publico:
        raise RepoPublico("El repositorio es público: cualquiera podrá ver el código y que existe un buscador, "
                          "pero las alertas, el historial y los datos de Telegram se guardan cifrados.")
    principal = info.get("default_branch") or "main"
    if gh.vacio():   # el primer archivo crea la rama principal
        gh.escribir("README.md", (CARPETA / "README.md").read_bytes(), "Primer commit [skip actions]")
        principal = gh.info().get("default_branch") or principal

    # La llave se conserva mientras sea el mismo repositorio (si no, no se podrían leer sus datos).
    llave = (None if cambio else (anterior or {}).get("clave_datos")) or cifrado.nueva_clave()
    secretos = subir_secretos(gh, llave)   # antes del código: la próxima revisión ya necesita la llave

    archivos = {ruta: (CARPETA / ruta).read_bytes() for ruta in ARCHIVOS_CODIGO}
    archivos[f".github/workflows/{FLUJO}"] = nube.flujo_yaml(principal).encode("utf-8")
    archivos[f".github/workflows/{nube.FLUJO_BOT}"] = nube.bot_yaml(principal).encode("utf-8")
    archivos.update(archivos_web(gh.repo))
    alertas_remotas, _ = gh.leer_json("alertas.json", principal)
    if alertas_remotas is None:
        datos = _alertas_locales()
    else:   # las que ya había (sin cifrar si eran de antes del cifrado)
        datos = nube.abrir_alertas(alertas_remotas, llave)
    archivos["alertas.json"] = json.dumps(nube.cerrar_alertas(datos, llave), ensure_ascii=False,
                                          indent=2).encode("utf-8")
    gh.subir_commit(archivos, "Buscador de vuelos: código, flujos y página pública [skip actions]", principal)

    historial = False
    if not gh.sha_rama("datos"):
        base = _base_local_sin_secretos()
        if base:
            gh.rama_suelta("datos", {"vuelos.db.cifrado": cifrado.cifrar(base, llave)}, "Datos iniciales (copiados del PC)")
            historial = True

    CONFIG.write_text(json.dumps({"repo": gh.repo, "token": token.strip(), "rama": principal,
                                  "clave_datos": llave}, indent=2), encoding="utf-8")
    gh.lanzar(FLUJO, principal)
    anterior_parado = False
    if cambio:
        with _bloqueo:
            _cache.update(sha_db=None, alertas=None, secretos=None, ultima_ejecucion=None)
            _vaciar_copia_nube()
        try:   # el nuevo ya funciona: el anterior deja de revisar
            viejo = GitHub(anterior["repo"], anterior["token"])
            viejo.desactivar_flujo(FLUJO)
            anterior_parado = True
            viejo.desactivar_flujo(nube.FLUJO_BOT)
        except Exception as e:
            log.warning("No se pudo desactivar el flujo de %s: %s", anterior["repo"], e)
    usar_base_nube()
    refrescar(forzar=True)
    return {"repo": gh.repo, "historial_subido": historial, "secretos": secretos,
            "anterior": anterior["repo"] if cambio else None, "anterior_parado": anterior_parado}


def desconectar() -> None:
    """Vuelve al modo local. Lo que hay en GitHub se queda allí (desactiva el flujo en GitHub si no lo quieres)."""
    if CONFIG.exists():
        CONFIG.rename(CONFIG.with_suffix(".json.desactivado"))
    db.RUTA = db.RUTA_AJUSTES


def compartir() -> dict | None:
    """Página pública y tu enlace personal (para Ajustes)."""
    c = config()
    if not c or not c.get("clave_datos"):
        return None
    chat = db.ajuste("telegram_chat_id", "")
    return {"pagina": nube.pagina_publica(c["repo"]),
            "enlace": nube.enlace_personal(c["repo"], c["clave_datos"], chat) if chat else None,
            "ajustes_pages": f"https://github.com/{c['repo']}/settings/pages"}


# ---------------------------------------------------------------- sincronizar

def _reemplazar_base(contenido: bytes) -> None:
    temporal = RUTA_DB.with_suffix(".descarga")
    temporal.write_bytes(contenido)
    for sufijo in ("-wal", "-shm"):
        Path(str(RUTA_DB) + sufijo).unlink(missing_ok=True)
    for intento in range(10):   # en Windows falla si justo hay una consulta abierta
        try:
            os.replace(temporal, RUTA_DB)
            return
        except PermissionError:
            time.sleep(0.3)
    raise RuntimeError("No se pudo actualizar la copia local de la base de datos")


def _bajar_base(gh: GitHub) -> bytes | None:
    cifrada = gh.descargar("vuelos.db.cifrado", "datos")
    if cifrada:
        if not clave():
            raise RuntimeError("La base de GitHub está cifrada y este PC no tiene la llave (nube.json)")
        return cifrado.descifrar(cifrada, clave())
    return gh.descargar("vuelos.db", "datos")   # de antes del cifrado


def refrescar(forzar: bool = False) -> None:
    """Trae de GitHub los cambios (como mucho cada 30 s) y aplica alertas.json encima."""
    if not activo():
        return
    with _bloqueo:
        if not forzar and time.time() - _cache["comprobado"] < 30:
            return
        try:
            gh = cliente()
            crudo, _ = gh.leer_json("alertas.json", rama())
            datos = nube.abrir_alertas(crudo, clave())
            sha = gh.sha_rama("datos")
            if sha and sha != _cache["sha_db"]:
                contenido = _bajar_base(gh)
                if contenido:
                    _reemplazar_base(contenido)
                _cache["sha_db"] = sha
            usar_base_nube()
            db.iniciar(wal=False, ruta=RUTA_DB)
            _cache["alertas"] = datos
            nube.sincronizar(_cache["alertas"])
            _cache["activas"] = gh.ejecuciones_activas(FLUJO)          # sin contar las del bot
            _cache["ultima_ejecucion"] = gh.ultima_ejecucion(FLUJO)
            if _cache["secretos"] is None:
                _cache["secretos"] = gh.secretos()
            _cache["error"] = None
        except Exception as e:
            log.warning("No se pudo sincronizar con GitHub: %s", e)
            _cache["error"] = str(e)
        _cache["comprobado"] = time.time()


def modificar_alertas(cambio, mensaje: str) -> dict:
    """Lee alertas.json de GitHub, aplica `cambio(datos)` y lo vuelve a guardar (cifrado)."""
    with _bloqueo:
        gh = cliente()
        crudo, sha = gh.leer_json("alertas.json", rama())
        datos = nube.abrir_alertas(crudo, clave())
        resultado = cambio(datos)
        guardar = nube.cerrar_alertas(datos, clave()) if clave() else datos
        gh.escribir("alertas.json", json.dumps(guardar, ensure_ascii=False, indent=2).encode("utf-8"),
                    mensaje, sha, rama())
        _cache["alertas"] = datos
        nube.sincronizar(datos)
        _cache["comprobado"] = 0.0
        return resultado


def revisar_ahora(alerta_id: int | None = None) -> None:
    cliente().lanzar(FLUJO, rama(), {"alerta": str(alerta_id or "")})
    _cache["comprobado"] = 0.0


def estado() -> dict:
    refrescar()
    secretos = _cache["secretos"] or set()
    ultima = _cache["ultima_ejecucion"] or {}
    return {
        "modo": "nube", "repo": (config() or {}).get("repo"),
        "ocupado": _cache["activas"] > 0, "texto": "Revisión en curso en GitHub" if _cache["activas"] else "",
        "paso": 0, "total": 0, "pendientes": 0, "alerta_id": None,
        "telegram": {"TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"} <= secretos,
        "aviasales": "AVIASALES_TOKEN" in secretos,
        "error": _cache["error"],
        "ultima_ejecucion": {"estado": ultima.get("status"), "resultado": ultima.get("conclusion"),
                             "inicio": ultima.get("run_started_at"), "url": ultima.get("html_url")} if ultima else None,
        "revisiones_dia": (_cache["alertas"] or {}).get("revisiones_dia", 5),
    }
