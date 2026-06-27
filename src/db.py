"""Almacenamiento persistente: SQLite (local) o Postgres (nube).

Si hay una variable DATABASE_URL configurada (en .env local o en los Secrets de
Streamlit), se usa una base de datos Postgres en la nube → los datos NO se pierden
aunque el servidor se reinicie. Si no, se usa un archivo SQLite local (útil para
pruebas en el computador).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from . import config


def _build_engine():
    """Crea el motor de base de datos según haya o no DATABASE_URL en la nube."""
    raw = config.DATABASE_URL
    if not raw:
        # Sin nube: archivo local SQLite.
        return create_engine(f"sqlite:///{config.DB_FILE}")

    # Con nube: Postgres con el driver psycopg (maneja SSL y SNI de Neon nativamente).
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    url = make_url(raw).set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True)


_engine = _build_engine()


@contextmanager
def _conn() -> Iterator[Any]:
    with _engine.begin() as con:
        yield con


def init_db() -> None:
    """Crea las tablas si no existen (funciona igual en SQLite y Postgres)."""
    with _conn() as con:
        con.execute(text(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id          TEXT PRIMARY KEY,
                cuenta      TEXT,
                url         TEXT,
                caption     TEXT,
                fecha       TEXT,
                imagen      TEXT,
                datos_json  TEXT
            )
            """
        ))
        con.execute(text(
            "CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)"
        ))
        con.execute(text(
            "CREATE TABLE IF NOT EXISTS clientes (nombre TEXT PRIMARY KEY, datos_json TEXT)"
        ))


# ── Publicaciones (posts) ─────────────────────────────────────

def guardar_post(post: dict[str, Any]) -> None:
    """Inserta un post nuevo. Si ya existe (mismo id), no lo toca."""
    with _conn() as con:
        con.execute(text(
            """
            INSERT INTO posts (id, cuenta, url, caption, fecha, imagen)
            VALUES (:id, :cuenta, :url, :caption, :fecha, :imagen)
            ON CONFLICT (id) DO NOTHING
            """
        ), {
            "id": post["id"],
            "cuenta": post.get("cuenta", ""),
            "url": post.get("url", ""),
            "caption": post.get("caption", ""),
            "fecha": post.get("fecha", ""),
            "imagen": post.get("imagen", ""),
        })


def guardar_extraccion(post_id: str, datos: dict[str, Any]) -> None:
    with _conn() as con:
        con.execute(
            text("UPDATE posts SET datos_json = :d WHERE id = :id"),
            {"d": json.dumps(datos, ensure_ascii=False), "id": post_id},
        )


def posts_sin_extraer() -> list[dict[str, Any]]:
    with _conn() as con:
        res = con.execute(text(
            "SELECT * FROM posts WHERE datos_json IS NULL AND caption <> ''"
        ))
        return [dict(r._mapping) for r in res]


def posts_recientes(desde_iso: str) -> list[dict[str, Any]]:
    with _conn() as con:
        res = con.execute(text(
            """
            SELECT * FROM posts
            WHERE datos_json IS NOT NULL AND fecha >= :d
            ORDER BY fecha DESC
            """
        ), {"d": desde_iso})
        filas = [dict(r._mapping) for r in res]

    resultado: list[dict[str, Any]] = []
    for fila in filas:
        datos = json.loads(fila["datos_json"])
        resultado.append({
            "id": fila["id"], "cuenta": fila["cuenta"], "url": fila["url"],
            "caption": fila["caption"], "fecha": fila["fecha"], "imagen": fila["imagen"],
            **datos,
        })
    return resultado


def contar_posts() -> int:
    with _conn() as con:
        return con.execute(text("SELECT COUNT(*) FROM posts")).scalar() or 0


# ── Estado (meta) ─────────────────────────────────────────────

def leer_meta(clave: str, defecto: Optional[str] = None) -> Optional[str]:
    with _conn() as con:
        valor = con.execute(
            text("SELECT valor FROM meta WHERE clave = :c"), {"c": clave}
        ).scalar()
        return valor if valor is not None else defecto


def guardar_meta(clave: str, valor: str) -> None:
    with _conn() as con:
        con.execute(text(
            """
            INSERT INTO meta (clave, valor) VALUES (:c, :v)
            ON CONFLICT (clave) DO UPDATE SET valor = :v
            """
        ), {"c": clave, "v": str(valor)})


# ── Clientes ──────────────────────────────────────────────────

def leer_clientes() -> list[dict[str, Any]]:
    with _conn() as con:
        res = con.execute(text("SELECT datos_json FROM clientes"))
        return [json.loads(r[0]) for r in res]


def guardar_clientes(lista: list[dict[str, Any]]) -> None:
    """Reemplaza toda la lista de clientes (de forma atómica)."""
    # Quita duplicados por nombre conservando el último.
    unicos: dict[str, dict[str, Any]] = {}
    for c in lista:
        unicos[c.get("nombre", "").strip().lower()] = c
    with _conn() as con:
        con.execute(text("DELETE FROM clientes"))
        for c in unicos.values():
            con.execute(
                text("INSERT INTO clientes (nombre, datos_json) VALUES (:n, :d)"),
                {"n": c.get("nombre", ""), "d": json.dumps(c, ensure_ascii=False)},
            )
