"""Descarga de publicaciones de Instagram usando Apify.

Usa el actor oficial "apify/instagram-scraper", que recibe una lista de
usuarios y devuelve sus publicaciones recientes con el caption, la fecha,
la URL y la imagen de portada.

Documentación del actor: https://apify.com/apify/instagram-scraper
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from apify_client import ApifyClient

from . import config, db

ACTOR_ID = "apify/instagram-scraper"


def _fecha_corte_iso() -> str:
    """Piso de 30 días: la ventana de inmuebles 'activos' que nos interesa."""
    corte = datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)
    return corte.date().isoformat()


def _lecturas_por_cuenta() -> dict[str, str]:
    """Última fecha de lectura EXITOSA de cada cuenta (para no perder posts)."""
    try:
        return json.loads(db.leer_meta("ultimas_lecturas") or "{}")
    except json.JSONDecodeError:
        return {}


def _cutoff_incremental(cuentas: list[str]) -> str:
    """Desde qué fecha pedir publicaciones para NO repetir lo ya descargado.

    Usa la última lectura EXITOSA más ANTIGUA entre las cuentas: si una cuenta
    estuvo restringida unos días, al recuperarse la ventana retrocede lo necesario
    para no perder sus posts (antes el corte era global y esos posts se perdían).
    Nunca pasa del piso de 30 días.
    """
    piso = (datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)).date()
    lecturas = _lecturas_por_cuenta()
    fechas = []
    for c in cuentas:
        f = lecturas.get(c)
        if not f:
            return piso.isoformat()   # alguna cuenta nunca leída → ventana completa
        try:
            fechas.append(date.fromisoformat(f))
        except ValueError:
            return piso.isoformat()
    if not fechas:
        return piso.isoformat()
    desde = min(fechas) - timedelta(days=config.DIAS_SOLAPE)
    return max(piso, desde).isoformat()


def scrapear_cuentas(cuentas: list[str], log=print) -> int:
    """Trae los posts recientes de las cuentas y los guarda en la caché.

    Devuelve cuántos posts nuevos se guardaron.
    Requiere APIFY_TOKEN configurado en el .env.
    """
    if not config.APIFY_TOKEN:
        raise RuntimeError(
            "Falta APIFY_TOKEN en el archivo .env. "
            "Consíguelo en https://console.apify.com/account/integrations"
        )
    if not cuentas:
        log("No hay cuentas configuradas en config/cuentas.txt")
        return 0

    cliente = ApifyClient(config.APIFY_TOKEN)

    # Instagram exige proxies residenciales y enlaces de perfil (directUrls),
    # de lo contrario bloquea la lectura ("Empty or private data").
    corte = _cutoff_incremental(cuentas)
    es_incremental = bool(_lecturas_por_cuenta()) or db.leer_meta("ultimo_scrape") is not None
    run_input: dict[str, Any] = {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in cuentas],
        "resultsType": "posts",
        # Primera corrida (ventana de 30 días): límite amplio para cuentas activas;
        # incremental (pocos días): el límite normal alcanza de sobra.
        "resultsLimit": config.MAX_POSTS_POR_CUENTA if es_incremental else 100,
        "onlyPostsNewerThan": corte,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }

    if es_incremental:
        log(f"Modo ahorro: trayendo solo lo NUEVO desde {corte} "
            "(no se repiten inmuebles ya descargados).")
    else:
        log(f"Primera búsqueda: trayendo los últimos {config.DIAS_RECIENTES} días.")
    log(f"Pidiendo a Apify los posts de {len(cuentas)} cuenta(s)…")
    run = cliente.actor(ACTOR_ID).call(run_input=run_input)
    if run is None or not run.default_dataset_id:
        raise RuntimeError("Apify no devolvió resultados. Revisa tu plan o las cuentas.")

    nuevos = 0
    restringidas: list[str] = []
    dataset = cliente.dataset(run.default_dataset_id).iterate_items()
    for item in dataset:
        if item.get("error"):  # perfil restringido/privado: Instagram lo bloquea
            url = item.get("inputUrl") or item.get("url") or ""
            if url and url not in restringidas:
                restringidas.append(url)
            continue
        post = _normalizar(item)
        if post is None:
            continue
        if db.guardar_post(post):
            nuevos += 1

    # Recuerda cuándo scrapeamos y qué cuentas no se dejaron leer.
    hoy = datetime.now(timezone.utc).date().isoformat()
    db.guardar_meta("ultimo_scrape", hoy)
    db.guardar_meta("cuentas_restringidas", json.dumps(restringidas, ensure_ascii=False))
    # Última lectura exitosa POR CUENTA: las restringidas conservan su fecha vieja,
    # así la próxima corrida retrocede y recupera lo que se les perdió.
    lecturas = _lecturas_por_cuenta()
    restr_usuarios = {config._solo_usuario(u) for u in restringidas}
    lecturas.update({c: hoy for c in cuentas if c not in restr_usuarios})
    db.guardar_meta("ultimas_lecturas", json.dumps(lecturas, ensure_ascii=False))

    if restringidas:
        log(f"⚠️ {len(restringidas)} cuenta(s) no se pudieron leer (perfil restringido o privado).")
    log(f"Listo. Se guardaron {nuevos} publicaciones nuevas.")
    return nuevos


def _media(item: dict[str, Any]) -> list[dict[str, str]]:
    """Lista de archivos descargables del post (videos y fotos), sin duplicar.

    Maneja posts de video, fotos sueltas y carruseles (Sidecar).
    """
    media: list[dict[str, str]] = []
    vistos: set[str] = set()

    def agregar(tipo: str, url: str | None) -> None:
        if url and url not in vistos:
            vistos.add(url)
            media.append({"tipo": tipo, "url": url})

    # Video principal
    if item.get("videoUrl"):
        agregar("video", item["videoUrl"])
    # Carrusel: cada elemento puede ser foto o video
    hijos = item.get("childPosts") or []
    if hijos:
        for hijo in hijos:
            if hijo.get("videoUrl"):
                agregar("video", hijo["videoUrl"])
            else:
                agregar("foto", hijo.get("displayUrl"))
    else:
        for img in (item.get("images") or []):
            agregar("foto", img)
    # Respaldo: la imagen de portada
    if not media:
        agregar("foto", item.get("displayUrl"))
    return media


def _normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convierte un item crudo de Apify al formato que usa nuestra caché."""
    post_id = item.get("id") or item.get("shortCode")
    caption = item.get("caption") or ""
    if not post_id or not caption.strip():
        return None

    return {
        "id": str(post_id),
        "cuenta": item.get("ownerUsername", ""),
        "url": item.get("url", ""),
        "caption": caption,
        # YYYY-MM-DD; si Apify no trae la fecha, usa hoy (antes quedaba '' e invisible).
        "fecha": (item.get("timestamp") or "")[:10]
                 or datetime.now(timezone.utc).date().isoformat(),
        "imagen": item.get("displayUrl", ""),
        "media": _media(item),
    }
