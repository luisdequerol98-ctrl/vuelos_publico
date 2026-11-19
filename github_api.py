"""Cliente mínimo de la API de GitHub. Sirve sin tener git instalado.

El token es un «fine-grained personal access token» limitado a un repositorio, con
permisos de lectura y escritura en Contents, Actions, Secrets y Workflows.
"""
from __future__ import annotations

import base64
import time

import requests

API = "https://api.github.com"


class ErrorGitHub(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo = repo.strip().strip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "buscador-vuelos",
        })

    # ------------------------------------------------------------ básico
    def _pedir(self, metodo: str, ruta: str, *, aceptar=(200, 201, 204), **kw) -> requests.Response:
        url = ruta if ruta.startswith("http") else f"{API}/repos/{self.repo}{ruta}"
        r = self.s.request(metodo, url, timeout=60, **kw)
        if r.status_code not in aceptar:
            try:
                mensaje = r.json().get("message", "")
            except ValueError:
                mensaje = r.text[:200]
            pistas = {
                401: "el token no es válido o ha caducado",
                403: "al token le falta algún permiso (Contents, Actions, Secrets o Workflows)",
                404: "no encuentro el repositorio o el token no tiene acceso a él",
            }
            raise ErrorGitHub(f"GitHub {r.status_code}: {pistas.get(r.status_code, mensaje)} ({mensaje})")
        return r

    def info(self) -> dict:
        return self._pedir("GET", "").json()

    def vacio(self) -> bool:
        r = self._pedir("GET", "/commits", params={"per_page": 1}, aceptar=(200, 409))
        return r.status_code == 409

    def sha_rama(self, rama: str) -> str | None:
        r = self._pedir("GET", f"/branches/{rama}", aceptar=(200, 404))
        return r.json()["commit"]["sha"] if r.status_code == 200 else None

    # ------------------------------------------------------------ archivos
    def leer_json(self, ruta: str, rama: str | None = None) -> tuple[dict | None, str | None]:
        """(contenido, sha) de un JSON pequeño; (None, None) si no existe."""
        import json
        r = self._pedir("GET", f"/contents/{ruta}", params={"ref": rama} if rama else None, aceptar=(200, 404))
        if r.status_code == 404:
            return None, None
        datos = r.json()
        return json.loads(base64.b64decode(datos["content"]).decode("utf-8")), datos["sha"]

    def descargar(self, ruta: str, rama: str) -> bytes | None:
        """Contenido binario de un archivo (hasta 100 MB)."""
        r = self._pedir("GET", f"/contents/{ruta}", params={"ref": rama}, aceptar=(200, 404),
                        headers={"Accept": "application/vnd.github.raw"})
        return r.content if r.status_code == 200 else None

    def firma(self) -> dict:
        """Autor de los commits con el correo anónimo de GitHub (usuario@users.noreply.github.com).
        Sin esto GitHub pone el correo principal de la cuenta, y en un repositorio público
        cualquiera lo vería."""
        if not getattr(self, "_firma", None):
            try:
                u = self._pedir("GET", f"{API}/user").json()
                self._firma = {"name": u["login"], "email": f"{u['id']}+{u['login']}@users.noreply.github.com"}
            except Exception:
                self._firma = {"name": "buscador-vuelos",
                               "email": "41898282+github-actions[bot]@users.noreply.github.com"}
        return self._firma

    def escribir(self, ruta: str, contenido: bytes, mensaje: str, sha: str | None = None,
                 rama: str | None = None) -> None:
        cuerpo = {"message": mensaje, "content": base64.b64encode(contenido).decode("ascii"),
                  "author": self.firma(), "committer": self.firma()}
        if sha:
            cuerpo["sha"] = sha
        if rama:
            cuerpo["branch"] = rama
        self._pedir("PUT", f"/contents/{ruta}", json=cuerpo)

    def _arbol(self, archivos: dict[str, bytes], base: str | None = None) -> str:
        entradas = []
        for ruta, contenido in archivos.items():
            blob = self._pedir("POST", "/git/blobs", json={
                "content": base64.b64encode(contenido).decode("ascii"), "encoding": "base64"}).json()
            entradas.append({"path": ruta, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        cuerpo = {"tree": entradas}
        if base:
            cuerpo["base_tree"] = base
        return self._pedir("POST", "/git/trees", json=cuerpo).json()["sha"]

    def subir_commit(self, archivos: dict[str, bytes], mensaje: str, rama: str) -> None:
        """Sube varios archivos en un único commit encima de la rama."""
        padre = self.sha_rama(rama)
        if not padre:
            raise ErrorGitHub(f"La rama {rama} no existe")
        base = self._pedir("GET", f"/git/commits/{padre}").json()["tree"]["sha"]
        arbol = self._arbol(archivos, base)
        commit = self._pedir("POST", "/git/commits", json={
            "message": mensaje, "tree": arbol, "parents": [padre],
            "author": self.firma(), "committer": self.firma()}).json()["sha"]
        self._pedir("PATCH", f"/git/refs/heads/{rama}", json={"sha": commit, "force": False})

    def rama_suelta(self, rama: str, archivos: dict[str, bytes], mensaje: str) -> None:
        """Crea (o reemplaza) una rama con un único commit sin historial."""
        arbol = self._arbol(archivos)
        commit = self._pedir("POST", "/git/commits", json={
            "message": mensaje, "tree": arbol, "parents": [],
            "author": self.firma(), "committer": self.firma()}).json()["sha"]
        if self.sha_rama(rama):
            self._pedir("PATCH", f"/git/refs/heads/{rama}", json={"sha": commit, "force": True})
        else:
            self._pedir("POST", "/git/refs", json={"ref": f"refs/heads/{rama}", "sha": commit})

    # ------------------------------------------------------------ Actions
    def desactivar_flujo(self, flujo: str) -> None:
        """Deja de ejecutarse (se reactiva en la pestaña Actions del repositorio)."""
        self._pedir("PUT", f"/actions/workflows/{flujo}/disable")

    def lanzar(self, flujo: str, rama: str, entradas: dict | None = None, intentos: int = 6) -> None:
        for i in range(intentos):
            try:
                self._pedir("POST", f"/actions/workflows/{flujo}/dispatches",
                            json={"ref": rama, "inputs": entradas or {}})
                return
            except ErrorGitHub:
                if i == intentos - 1:
                    raise
                time.sleep(4)   # un flujo recién subido tarda unos segundos en estar disponible

    def ejecuciones_activas(self) -> int:
        total = 0
        for estado in ("in_progress", "queued"):
            r = self._pedir("GET", "/actions/runs", params={"status": estado, "per_page": 1})
            total += r.json().get("total_count", 0)
        return total

    def ultima_ejecucion(self) -> dict | None:
        r = self._pedir("GET", "/actions/runs", params={"per_page": 1})
        runs = r.json().get("workflow_runs") or []
        return runs[0] if runs else None

    # ------------------------------------------------------------ secretos
    def secretos(self) -> set[str]:
        r = self._pedir("GET", "/actions/secrets", params={"per_page": 100})
        return {s["name"] for s in r.json().get("secrets", [])}

    def guardar_secreto(self, nombre: str, valor: str) -> None:
        from nacl import encoding, public
        clave = self._pedir("GET", "/actions/secrets/public-key").json()
        caja = public.SealedBox(public.PublicKey(clave["key"].encode(), encoding.Base64Encoder()))
        cifrado = base64.b64encode(caja.encrypt(valor.encode("utf-8"))).decode("ascii")
        self._pedir("PUT", f"/actions/secrets/{nombre}", json={"encrypted_value": cifrado, "key_id": clave["key_id"]})
