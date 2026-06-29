"""Configuración central de Nova Scraping.

Carga las llaves desde el archivo .env y define rutas y constantes
que usa el resto de la aplicación.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Carpeta raíz del proyecto (un nivel arriba de /src).
BASE_DIR = Path(__file__).resolve().parent.parent

# Carga las variables del archivo .env (si existe).
load_dotenv(BASE_DIR / ".env")

# ── Llaves de APIs ────────────────────────────────────────────
def _leer_llave(nombre: str, defecto: str = "") -> str:
    """Lee una llave del .env (computador) o de Streamlit Secrets (internet)."""
    valor = os.getenv(nombre, "").strip()
    if valor:
        return valor
    try:  # En la versión de internet las llaves viven en Streamlit Secrets.
        import streamlit as st
        if nombre in st.secrets:
            return str(st.secrets[nombre]).strip()
    except Exception:
        pass
    return defecto


APIFY_TOKEN = _leer_llave("APIFY_TOKEN")
ANTHROPIC_API_KEY = _leer_llave("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _leer_llave("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# Base de datos en la nube (Postgres). Si está vacía, se usa SQLite local.
# Sirve para que los datos NO se pierdan cuando el servidor se reinicia.
DATABASE_URL = _leer_llave("DATABASE_URL")

# Contraseña para entrar a la app (cuando es pública). Si está vacía, no pide nada.
APP_PASSWORD = _leer_llave("APP_PASSWORD")

# ── Rutas de archivos ─────────────────────────────────────────
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
CUENTAS_FILE = CONFIG_DIR / "cuentas.txt"
DB_FILE = DATA_DIR / "nova.db"
CLIENTES_FILE = DATA_DIR / "clientes.xlsx"

# Asegura que la carpeta de datos exista.
DATA_DIR.mkdir(exist_ok=True)

# ── Parámetros del negocio ────────────────────────────────────
# Ventana de tiempo: solo nos interesan publicaciones recientes.
DIAS_RECIENTES = 30

# Al scrapear de forma incremental, repetimos unos días de solape para no
# perder publicaciones por diferencias de hora/zona.
DIAS_SOLAPE = 2

# Cuántos posts pedirle a Apify por cada cuenta (techo de seguridad).
MAX_POSTS_POR_CUENTA = 30


def guardar_llaves(apify_token: str | None = None,
                   anthropic_key: str | None = None) -> None:
    """Guarda las llaves en el archivo .env y las activa de inmediato.

    Permite que la usuaria pegue sus llaves desde la app, sin editar archivos.
    Solo actualiza las llaves que se pasen (deja las demás como estaban).
    """
    global APIFY_TOKEN, ANTHROPIC_API_KEY

    if apify_token is not None:
        APIFY_TOKEN = apify_token.strip()
    if anthropic_key is not None:
        ANTHROPIC_API_KEY = anthropic_key.strip()

    # Reescribe el archivo .env con los valores actuales.
    contenido = (
        f"APIFY_TOKEN={APIFY_TOKEN}\n"
        f"ANTHROPIC_API_KEY={ANTHROPIC_API_KEY}\n"
        f"ANTHROPIC_MODEL={ANTHROPIC_MODEL}\n"
    )
    (BASE_DIR / ".env").write_text(contenido, encoding="utf-8")

    # Las deja disponibles también para procesos hijos.
    os.environ["APIFY_TOKEN"] = APIFY_TOKEN
    os.environ["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY


def leer_cuentas() -> list[str]:
    """Devuelve la lista de usuarios de Instagram a monitorear.

    Ignora líneas vacías y comentarios. Quita la @ inicial si la tiene.
    """
    if not CUENTAS_FILE.exists():
        return []
    cuentas: list[str] = []
    for linea in CUENTAS_FILE.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        cuentas.append(_solo_usuario(linea))
    # Quita vacíos y duplicados conservando el orden.
    vistos: dict[str, None] = {}
    for c in cuentas:
        if c:
            vistos.setdefault(c, None)
    return list(vistos)


def _solo_usuario(texto: str) -> str:
    """Extrae el nombre de usuario aunque se pegue un enlace completo.

    'https://www.instagram.com/metricestate/' -> 'metricestate'
    '@metricestate' -> 'metricestate'
    """
    texto = texto.strip().rstrip("/")
    if "instagram.com" in texto:
        # Toma lo que va después de 'instagram.com/'
        texto = texto.split("instagram.com/", 1)[1]
        # Descarta posibles parámetros (?...) o subrutas.
        texto = texto.split("?")[0].split("/")[0]
    return texto.lstrip("@").strip()
