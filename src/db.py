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

from . import config


def _build_engine():
    """Crea el motor de base de datos según haya o no DATABASE_URL en la nube."""
    raw = config.DATABASE_URL
    if not raw:
        # Sin nube: archivo local SQLite.
        return create_engine(f"sqlite:///{config.DB_FILE}")

    # Con nube: dejamos que psycopg (libpq) parsee la cadena tal cual. Así no se
    # daña la contraseña aunque tenga caracteres especiales (SQLAlchemy a veces la
    # reinterpreta mal; libpq es el parser de referencia que Neon espera).
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]

    def _crear_conexion():
        import psycopg
        return psycopg.connect(raw)

    return create_engine(
        "postgresql+psycopg://", creator=_crear_conexion, pool_pre_ping=True
    )


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
                datos_json  TEXT,
                media_json  TEXT
            )
            """
        ))
        con.execute(text(
            "CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)"
        ))
        con.execute(text(
            "CREATE TABLE IF NOT EXISTS clientes (nombre TEXT PRIMARY KEY, datos_json TEXT)"
        ))

    # Migración: agrega columnas nuevas a tablas que ya existían (cada una en su
    # propia transacción, porque un ALTER que falla aborta la transacción).
    for columna in ["media_json TEXT", "agregado TEXT"]:
        try:
            with _conn() as con:
                con.execute(text(f"ALTER TABLE posts ADD COLUMN {columna}"))
        except Exception:  # noqa: BLE001 - la columna ya existe
            pass


# ── Publicaciones (posts) ─────────────────────────────────────

def guardar_post(post: dict[str, Any]) -> bool:
    """Inserta un post nuevo. Devuelve True si se insertó, False si ya existía.

    (Antes había que contar toda la tabla antes y después para saberlo: dos
    viajes extra a la base POR CADA inmueble guardado.)
    """
    from datetime import date
    with _conn() as con:
        res = con.execute(text(
            """
            INSERT INTO posts (id, cuenta, url, caption, fecha, imagen, media_json, agregado)
            VALUES (:id, :cuenta, :url, :caption, :fecha, :imagen, :media_json, :agregado)
            ON CONFLICT (id) DO NOTHING
            """
        ), {
            "id": post["id"],
            "cuenta": post.get("cuenta", ""),
            "url": post.get("url", ""),
            "caption": post.get("caption", ""),
            "fecha": post.get("fecha", ""),
            "imagen": post.get("imagen", ""),
            "media_json": json.dumps(post.get("media") or [], ensure_ascii=False),
            "agregado": date.today().isoformat(),   # cuándo ENTRÓ a la herramienta
        })
        return bool(res.rowcount)


def actualizar_media(post_id: str, media: list) -> None:
    """Agrega/actualiza los archivos descargables de un post ya guardado."""
    with _conn() as con:
        con.execute(
            text("UPDATE posts SET media_json = :m WHERE id = :id"),
            {"m": json.dumps(media or [], ensure_ascii=False), "id": post_id},
        )


def actualizar_fecha(post_id: str, fecha: str) -> None:
    """Corrige la fecha de un post (ej. cuando el portal sí dice cuándo se publicó)."""
    with _conn() as con:
        con.execute(text("UPDATE posts SET fecha = :f WHERE id = :id"),
                    {"f": fecha, "id": post_id})


def eliminar_post(post_id: str) -> None:
    """Borra un inmueble de la base (p.ej. uno ingresado manualmente)."""
    with _conn() as con:
        con.execute(text("DELETE FROM posts WHERE id = :id"), {"id": post_id})


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
        media = json.loads(fila["media_json"]) if fila.get("media_json") else []
        resultado.append({
            "id": fila["id"], "cuenta": fila["cuenta"], "url": fila["url"],
            "caption": fila["caption"], "fecha": fila["fecha"], "imagen": fila["imagen"],
            "agregado": fila.get("agregado") or "",
            **datos,
            "media": media,
        })
    return resultado


def posts_leidos() -> list[dict[str, Any]]:
    """Todos los inmuebles ya leídos por la IA, SIN filtro de fecha.

    Así un inmueble no desaparece de las coincidencias solo por antigüedad: se
    mantiene hasta que el broker lo marque (o lo descarte).
    """
    return posts_recientes("0000-01-01")


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
    """Reemplaza toda la lista de clientes (de forma atómica).

    Si dos entradas comparten nombre, se FUSIONAN (antes se pisaban y una
    desaparecía en silencio con todo su seguimiento).
    """
    unicos: dict[str, dict[str, Any]] = {}
    for c in lista:
        k = c.get("nombre", "").strip().lower()
        if k in unicos:
            from . import clientes as _cl   # import perezoso (evita ciclo)
            base, otro = ((unicos[k], c) if _cl._completitud(unicos[k]) >= _cl._completitud(c)
                          else (c, unicos[k]))
            unicos[k] = _cl._fusionar_dos(dict(base), otro)
        else:
            unicos[k] = c
    filas = [{"n": c.get("nombre", ""), "d": json.dumps(c, ensure_ascii=False)}
             for c in unicos.values()]
    with _conn() as con:
        con.execute(text("DELETE FROM clientes"))
        if filas:   # inserción masiva: un solo viaje a la base, no uno por cliente
            con.execute(text("INSERT INTO clientes (nombre, datos_json) VALUES (:n, :d)"), filas)
