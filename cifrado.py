"""Cifrado de lo que se guarda en el repositorio público de GitHub.

El repositorio es público (GitHub no cobra minutos), así que las alertas, el historial y
los datos de cada persona se guardan cifrados con AES-256-GCM. Sin la llave solo se ve
ruido. Las fechas de viaje de alguien dicen cuándo no estará en casa: no deben estar a la vista.

- La llave maestra está en nube.json (este PC) y en el secreto CLAVE_DATOS de GitHub.
- Cada persona tiene su propia llave, que sale de la maestra y de su chat de Telegram:
  no hace falta guardar ninguna lista. Va en su enlace personal, detrás de «#», que el
  navegador nunca envía a ningún servidor.

Formato: b"BV1" + nonce (12 bytes) + AES-GCM(gzip(datos)). La página pública lo abre con
WebCrypto y DecompressionStream (static/app.js, descifrarPaquete).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIA = b"BV1"


def nueva_clave() -> str:
    return _texto(os.urandom(32))


def _texto(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _bytes(clave: str) -> bytes:
    b = base64.urlsafe_b64decode(clave + "=" * (-len(clave) % 4))
    if len(b) != 32:
        raise ValueError("La llave de cifrado no es válida")
    return b


def es_cifrado(datos: bytes) -> bool:
    return datos[:3] == MAGIA


def cifrar(datos: bytes, clave: str) -> bytes:
    nonce = os.urandom(12)
    return MAGIA + nonce + AESGCM(_bytes(clave)).encrypt(nonce, gzip.compress(datos, mtime=0), None)


def descifrar(datos: bytes, clave: str) -> bytes:
    if not es_cifrado(datos):
        raise ValueError("El archivo no está cifrado con el formato del buscador")
    return gzip.decompress(AESGCM(_bytes(clave)).decrypt(datos[3:15], datos[15:], None))


def cifrar_json(obj, clave: str) -> bytes:
    return cifrar(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), clave)


def descifrar_json(datos: bytes, clave: str):
    return json.loads(descifrar(datos, clave).decode("utf-8"))


def persona(clave_maestra: str, chat: str) -> tuple[str, str]:
    """(nombre del archivo, llave) de una persona. Siempre los mismos para el mismo chat."""
    m = _bytes(clave_maestra)
    archivo = hmac.new(m, f"archivo|{chat}".encode(), hashlib.sha256).hexdigest()[:24]
    llave = _texto(hmac.new(m, f"llave|{chat}".encode(), hashlib.sha256).digest())
    return archivo, llave
