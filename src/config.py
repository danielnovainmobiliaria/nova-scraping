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
PORTALES_FILE = CONFIG_DIR / "portales.txt"
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

# Páginas máximas a leer por corrida de portales (techo de gasto/seguridad).
MAX_PAGINAS_PORTAL = 20


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


def _leer_cuentas_archivo() -> list[str]:
    """Lee config/cuentas.txt (solo como semilla inicial / respaldo local)."""
    if not CUENTAS_FILE.exists():
        return []
    cuentas: list[str] = []
    for linea in CUENTAS_FILE.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        cuentas.append(_solo_usuario(linea))
    vistos: dict[str, None] = {}
    for c in cuentas:
        if c:
            vistos.setdefault(c, None)
    return list(vistos)


def leer_cuentas() -> list[str]:
    """Lista de cuentas de Instagram a monitorear.

    IMPORTANTE: vive en la BASE DE DATOS (sobrevive a cada actualización de la app;
    el disco de Streamlit Cloud se borra en cada deploy). El archivo local solo
    sirve de semilla la primera vez.
    """
    import json as _json
    try:
        from . import db
        crudo = db.leer_meta("cuentas_ig")
        if crudo is not None:
            return [u for u in (_solo_usuario(x) for x in _json.loads(crudo)) if u]
    except Exception:  # noqa: BLE001 - sin BD (tests) → archivo
        return _leer_cuentas_archivo()
    cuentas = _leer_cuentas_archivo()
    if cuentas:
        try:
            from . import db
            db.guardar_meta("cuentas_ig", _json.dumps(cuentas, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
    return cuentas


def guardar_cuentas(cuentas: list[str]) -> None:
    """Guarda la lista de cuentas en la BD (permanente) y en el archivo (respaldo)."""
    import json as _json
    limpias: dict[str, None] = {}
    for c in cuentas:
        u = _solo_usuario(str(c))
        if u:
            limpias.setdefault(u, None)
    lista = list(limpias)
    from . import db
    db.guardar_meta("cuentas_ig", _json.dumps(lista, ensure_ascii=False))
    try:
        CUENTAS_FILE.write_text("# Cuentas de Instagram a monitorear (una por línea)\n"
                                + "\n".join(lista), encoding="utf-8")
    except Exception:  # noqa: BLE001 - disco de solo lectura en la nube
        pass


def _leer_portales_archivo() -> list[str]:
    if not PORTALES_FILE.exists():
        return []
    urls: list[str] = []
    for linea in PORTALES_FILE.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if not linea.startswith("http"):
            linea = "https://" + linea
        if linea not in urls:
            urls.append(linea)
    return urls


def leer_portales() -> list[str]:
    """URLs de portales a leer. Viven en la BD (igual que las cuentas)."""
    import json as _json
    try:
        from . import db
        crudo = db.leer_meta("portales_urls")
        if crudo is not None:
            return [u for u in _json.loads(crudo) if str(u).strip()]
    except Exception:  # noqa: BLE001
        return _leer_portales_archivo()
    urls = _leer_portales_archivo()
    if urls:
        try:
            from . import db
            db.guardar_meta("portales_urls", _json.dumps(urls, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
    return urls


def guardar_portales(urls: list[str]) -> None:
    """Guarda los portales en la BD (permanente) y en el archivo (respaldo)."""
    import json as _json
    limpias: list[str] = []
    for u in urls:
        u = str(u).strip()
        if not u:
            continue
        if not u.startswith("http"):
            u = "https://" + u
        if u not in limpias:
            limpias.append(u)
    from . import db
    db.guardar_meta("portales_urls", _json.dumps(limpias, ensure_ascii=False))
    try:
        PORTALES_FILE.write_text("# Portales / sitios web a leer (una URL por línea)\n"
                                 + "\n".join(limpias), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


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
