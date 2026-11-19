"""Modo nube visto desde tu PC.

Las revisiones las hace GitHub Actions aunque el PC esté apagado. Esta página:
- baja de GitHub la base de datos con los resultados (rama «datos»),
- guarda tus alertas en alertas.json (rama principal), lo que lanza su primera revisión,
- pide revisiones al momento («Revisar ahora»).
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

import db
import nube
from github_api import GitHub

log = logging.getLogger("buscador.remoto")
CARPETA = Path(__file__).parent
CONFIG = CARPETA / "nube.json"
RUTA_DB = CARPETA / "vuelos-nube.db"
FLUJO = "revisar.yml"
ARCHIVOS_CODIGO = ["app.py", "avisos.py", "db.py", "fuentes.py", "motor.py", "planificador.py",
                   "nube.py", "github_api.py", "remoto.py", "requirements.txt", "README.md",
                   "arrancar.bat", "static/index.html", "static/app.js", "static/estilo.css",
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


def subir_secretos(gh: GitHub) -> list[str]:
    subidos = []
    for nombre, clave in (("TELEGRAM_TOKEN", "telegram_token"), ("TELEGRAM_CHAT_ID", "telegram_chat_id"),
                          ("AVIASALES_TOKEN", "aviasales_token")):
        valor = db.ajuste(clave, "")
        if valor:
            gh.guardar_secreto(nombre, valor)
            subidos.append(nombre)
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


class RepoPublico(RuntimeError):
    """El repositorio es público y la persona aún no lo ha aceptado."""


def conectar(repo: str, token: str, aceptar_publico: bool = False) -> dict:
    """Comprueba el acceso, sube el código y el flujo, guarda los secretos y lanza la primera revisión.
    Si ya había otro repositorio conectado, el nuevo empieza de cero y el flujo del anterior se
    desactiva (para que no revisen los dos ni se gasten minutos)."""
    gh = GitHub(repo, token)
    info = gh.info()
    anterior = config()
    cambio = bool(anterior) and anterior["repo"].lower() != info.get("full_name", repo).lower()
    if not info.get("private") and not aceptar_publico:
        raise RepoPublico("El repositorio es público: cualquiera podrá ver tus alertas (origen, destino, "
                          "fechas y precio objetivo), el historial de precios y el registro de cada "
                          "revisión. Los tokens de Telegram y Aviasales siguen ocultos como secretos.")
    principal = info.get("default_branch") or "main"
    if gh.vacio():   # el primer archivo crea la rama principal
        gh.escribir("README.md", (CARPETA / "README.md").read_bytes(), "Primer commit [skip actions]")
        principal = gh.info().get("default_branch") or principal

    archivos = {ruta: (CARPETA / ruta).read_bytes() for ruta in ARCHIVOS_CODIGO}
    archivos[f".github/workflows/{FLUJO}"] = nube.flujo_yaml(principal).encode("utf-8")
    alertas_remotas, _ = gh.leer_json("alertas.json", principal)
    if alertas_remotas is None:
        archivos["alertas.json"] = json.dumps(_alertas_locales(), ensure_ascii=False, indent=2).encode("utf-8")
    gh.subir_commit(archivos, "Buscador de vuelos: código y flujo [skip actions]", principal)

    historial = False
    if not gh.sha_rama("datos"):
        base = _base_local_sin_secretos()
        if base:
            gh.rama_suelta("datos", {"vuelos.db": base}, "Datos iniciales (copiados del PC)")
            historial = True

    secretos = subir_secretos(gh)
    CONFIG.write_text(json.dumps({"repo": gh.repo, "token": token.strip(), "rama": principal},
                                 indent=2), encoding="utf-8")
    gh.lanzar(FLUJO, principal)
    anterior_parado = False
    if cambio:
        with _bloqueo:
            _cache.update(sha_db=None, alertas=None, secretos=None, ultima_ejecucion=None)
            _vaciar_copia_nube()
        try:   # el nuevo ya funciona: el anterior deja de revisar
            GitHub(anterior["repo"], anterior["token"]).desactivar_flujo(FLUJO)
            anterior_parado = True
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


def refrescar(forzar: bool = False) -> None:
    """Trae de GitHub los cambios (como mucho cada 30 s) y aplica alertas.json encima."""
    if not activo():
        return
    with _bloqueo:
        if not forzar and time.time() - _cache["comprobado"] < 30:
            return
        try:
            gh = cliente()
            datos, _ = gh.leer_json("alertas.json", rama())
            sha = gh.sha_rama("datos")
            if sha and sha != _cache["sha_db"]:
                contenido = gh.descargar("vuelos.db", "datos")
                if contenido:
                    _reemplazar_base(contenido)
                _cache["sha_db"] = sha
            usar_base_nube()
            db.iniciar(wal=False, ruta=RUTA_DB)
            _cache["alertas"] = datos or {"revisiones_dia": 5, "alertas": []}
            nube.sincronizar(_cache["alertas"])
            _cache["activas"] = gh.ejecuciones_activas()
            _cache["ultima_ejecucion"] = gh.ultima_ejecucion()
            if _cache["secretos"] is None:
                _cache["secretos"] = gh.secretos()
            _cache["error"] = None
        except Exception as e:
            log.warning("No se pudo sincronizar con GitHub: %s", e)
            _cache["error"] = str(e)
        _cache["comprobado"] = time.time()


def modificar_alertas(cambio, mensaje: str) -> dict:
    """Lee alertas.json de GitHub, aplica `cambio(datos)` y lo vuelve a guardar."""
    with _bloqueo:
        gh = cliente()
        datos, sha = gh.leer_json("alertas.json", rama())
        datos = datos or {"revisiones_dia": 5, "alertas": []}
        resultado = cambio(datos)
        gh.escribir("alertas.json", json.dumps(datos, ensure_ascii=False, indent=2).encode("utf-8"),
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
