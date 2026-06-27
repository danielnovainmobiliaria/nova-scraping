"""Caché local en SQLite.

Guarda cada publicación de Instagram que ya descargamos y la información
estructurada que Claude extrajo de su caption. Así nunca pagamos dos veces
por leer el mismo post.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from . import config


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(config.DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Crea las tablas si no existen."""
    with _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id          TEXT PRIMARY KEY,   -- id único del post en Instagram
                cuenta      TEXT NOT NULL,      -- usuario del broker
                url         TEXT,               -- enlace al post
                caption     TEXT,               -- texto original del caption
                fecha       TEXT,               -- fecha de publicación (ISO)
                imagen      TEXT,               -- url de la imagen de portada
                datos_json  TEXT,               -- extracción de Claude (JSON) o NULL
                creado_en   TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Tabla de estado: recuerda, por ejemplo, la fecha del último scraping.
        con.execute(
            "CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)"
        )


def leer_meta(clave: str, defecto: Optional[str] = None) -> Optional[str]:
    with _conn() as con:
        fila = con.execute("SELECT valor FROM meta WHERE clave = ?", (clave,)).fetchone()
        return fila["valor"] if fila else defecto


def guardar_meta(clave: str, valor: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO meta (clave, valor) VALUES (?, ?)", (clave, str(valor))
        )


def guardar_post(post: dict[str, Any]) -> None:
    """Inserta un post nuevo. Si ya existe (mismo id), no lo toca."""
    with _conn() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO posts (id, cuenta, url, caption, fecha, imagen)
            VALUES (:id, :cuenta, :url, :caption, :fecha, :imagen)
            """,
            {
                "id": post["id"],
                "cuenta": post.get("cuenta", ""),
                "url": post.get("url", ""),
                "caption": post.get("caption", ""),
                "fecha": post.get("fecha", ""),
                "imagen": post.get("imagen", ""),
            },
        )


def guardar_extraccion(post_id: str, datos: dict[str, Any]) -> None:
    """Guarda el JSON estructurado que Claude extrajo de un caption."""
    with _conn() as con:
        con.execute(
            "UPDATE posts SET datos_json = ? WHERE id = ?",
            (json.dumps(datos, ensure_ascii=False), post_id),
        )


def posts_sin_extraer() -> list[sqlite3.Row]:
    """Posts que aún no han pasado por Claude."""
    with _conn() as con:
        cur = con.execute(
            "SELECT * FROM posts WHERE datos_json IS NULL AND caption != ''"
        )
        return cur.fetchall()


def posts_recientes(desde_iso: str) -> list[dict[str, Any]]:
    """Devuelve los posts ya extraídos publicados desde una fecha dada.

    Cada elemento combina los datos del post con la extracción de Claude.
    """
    with _conn() as con:
        cur = con.execute(
            """
            SELECT * FROM posts
            WHERE datos_json IS NOT NULL AND fecha >= ?
            ORDER BY fecha DESC
            """,
            (desde_iso,),
        )
        filas = cur.fetchall()

    resultado: list[dict[str, Any]] = []
    for fila in filas:
        datos = json.loads(fila["datos_json"])
        resultado.append(
            {
                "id": fila["id"],
                "cuenta": fila["cuenta"],
                "url": fila["url"],
                "caption": fila["caption"],
                "fecha": fila["fecha"],
                "imagen": fila["imagen"],
                **datos,  # operacion, barrio, area, precio, habitaciones, etc.
            }
        )
    return resultado


def contar_posts() -> int:
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
