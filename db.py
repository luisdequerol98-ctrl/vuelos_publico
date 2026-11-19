"""Base de datos SQLite del buscador (un único archivo: vuelos.db)."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

# RUTA: datos de alertas y precios. En modo nube, en tu PC apunta a la copia bajada de GitHub.
# RUTA_AJUSTES: tus ajustes locales (tokens…); nunca se suben a GitHub.
RUTA = Path(__file__).with_name("vuelos.db")
RUTA_AJUSTES = Path(__file__).with_name("vuelos.db")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS alertas(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  origen TEXT NOT NULL,
  destino TEXT NOT NULL,
  fecha_desde TEXT NOT NULL,          -- primer día en que puedes salir
  fecha_hasta TEXT NOT NULL,          -- último día en que puedes volver
  dias INTEGER NOT NULL,              -- días en destino (vuelta - ida)
  flex INTEGER NOT NULL DEFAULT 0,    -- ± días de duración que aceptas
  precio_objetivo REAL NOT NULL,      -- € por persona, ida y vuelta
  solo_directos INTEGER NOT NULL DEFAULT 0,
  max_horas REAL,                     -- máximo de horas por trayecto, con escalas (NULL = sin límite)
  personas INTEGER NOT NULL DEFAULT 1, -- adultos que viajan (los precios se guardan por persona)
  activa INTEGER NOT NULL DEFAULT 1,
  creada TEXT NOT NULL,
  ultimo_aviso_precio REAL            -- evita repetir el mismo aviso
);
CREATE TABLE IF NOT EXISTS revisiones(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alerta_id INTEGER NOT NULL REFERENCES alertas(id) ON DELETE CASCADE,
  inicio TEXT NOT NULL,
  fin TEXT,
  motivo TEXT NOT NULL,               -- programada | manual
  mejor_precio REAL,
  mejor_json TEXT,
  por_fuente_json TEXT,
  n_tarifas INTEGER DEFAULT 0,
  errores TEXT
);
CREATE TABLE IF NOT EXISTS tarifas(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id INTEGER NOT NULL REFERENCES revisiones(id) ON DELETE CASCADE,
  fuente TEXT NOT NULL,               -- google | ryanair | aviasales
  tramo TEXT NOT NULL,                -- ida | vuelta | iv (ida y vuelta juntos)
  fecha TEXT NOT NULL,
  fecha_vuelta TEXT,
  precio REAL NOT NULL,
  aerolinea TEXT,
  escalas INTEGER,
  hora TEXT,
  enlace TEXT,
  duracion_min INTEGER,               -- trayecto completo (de ida, si es iv), con escalas
  duracion_vuelta_min INTEGER         -- solo en iv
);
CREATE INDEX IF NOT EXISTS ix_tarifas_rev ON tarifas(revision_id);
CREATE INDEX IF NOT EXISTS ix_revisiones_alerta ON revisiones(alerta_id);
CREATE TABLE IF NOT EXISTS avisos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alerta_id INTEGER NOT NULL REFERENCES alertas(id) ON DELETE CASCADE,
  enviado TEXT NOT NULL,
  precio REAL,
  mensaje TEXT,
  ok INTEGER,
  detalle TEXT
);
CREATE TABLE IF NOT EXISTS planificacion(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dia TEXT NOT NULL,
  hora TEXT NOT NULL,                 -- fecha y hora ISO
  estado TEXT NOT NULL DEFAULT 'pendiente'   -- pendiente | hecha | perdida
);
CREATE TABLE IF NOT EXISTS ajustes(clave TEXT PRIMARY KEY, valor TEXT);
"""

AJUSTES_POR_DEFECTO = {
    "revisiones_dia": "5",
    "hora_inicio": "0",
    "hora_fin": "24",
    "separacion_min": "120",
}


def conectar(ruta: Path | None = None) -> sqlite3.Connection:
    c = sqlite3.connect(ruta or RUTA, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


# Columnas añadidas después de la primera versión: se crean si la base es antigua.
MIGRACIONES = {
    "alertas": [("max_horas", "REAL"), ("personas", "INTEGER NOT NULL DEFAULT 1")],
    "tarifas": [("duracion_min", "INTEGER"), ("duracion_vuelta_min", "INTEGER")],
}


def iniciar(wal: bool = True, ruta: Path | None = None) -> None:
    """Crea las tablas que falten. Sin WAL cuando la base viaja como un único archivo (GitHub)."""
    with closing(conectar(ruta)) as c:
        c.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
        c.executescript(ESQUEMA)
        for tabla, columnas in MIGRACIONES.items():
            existentes = {fila[1] for fila in c.execute(f"PRAGMA table_info({tabla})")}
            for nombre, tipo in columnas:
                if nombre not in existentes:
                    c.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
        for k, v in AJUSTES_POR_DEFECTO.items():
            c.execute("INSERT OR IGNORE INTO ajustes(clave, valor) VALUES(?, ?)", (k, v))
        c.commit()


def q(sql: str, params: tuple = ()) -> list[dict]:
    with closing(conectar()) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def uno(sql: str, params: tuple = ()) -> dict | None:
    filas = q(sql, params)
    return filas[0] if filas else None


def ex(sql: str, params: tuple = ()) -> int:
    with closing(conectar()) as c:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def exmany(sql: str, filas: list[tuple]) -> None:
    if not filas:
        return
    with closing(conectar()) as c:
        c.executemany(sql, filas)
        c.commit()


def compactar() -> None:
    """Vacía el diario y compacta el archivo antes de subirlo."""
    with closing(conectar()) as c:
        c.isolation_level = None
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute("VACUUM")


def ajuste(clave: str, defecto: str | None = None) -> str | None:
    """Valor de un ajuste. Una variable de entorno con el nombre en mayúsculas manda
    (así GitHub Actions pasa los secretos: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID…)."""
    entorno = os.environ.get(clave.upper())
    if entorno:
        return entorno
    with closing(conectar(RUTA_AJUSTES)) as c:
        fila = c.execute("SELECT valor FROM ajustes WHERE clave=?", (clave,)).fetchone()
    if fila is None or fila["valor"] in (None, ""):
        return defecto if defecto is not None else AJUSTES_POR_DEFECTO.get(clave)
    return fila["valor"]


def guardar_ajuste(clave: str, valor: str | None) -> None:
    with closing(conectar(RUTA_AJUSTES)) as c:
        c.execute("INSERT INTO ajustes(clave, valor) VALUES(?, ?) "
                  "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, valor))
        c.commit()
