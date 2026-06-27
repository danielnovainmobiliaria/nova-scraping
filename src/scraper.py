"""Descarga de publicaciones de Instagram usando Apify.

Usa el actor oficial "apify/instagram-scraper", que recibe una lista de
usuarios y devuelve sus publicaciones recientes con el caption, la fecha,
la URL y la imagen de portada.

Documentación del actor: https://apify.com/apify/instagram-scraper
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from apify_client import ApifyClient

from . import config, db

ACTOR_ID = "apify/instagram-scraper"


def _fecha_corte_iso() -> str:
    """Fecha (solo día) desde la cual nos interesan los posts."""
    corte = datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)
    return corte.date().isoformat()


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
    run_input: dict[str, Any] = {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in cuentas],
        "resultsType": "posts",
        "resultsLimit": config.MAX_POSTS_POR_CUENTA,
        "onlyPostsNewerThan": _fecha_corte_iso(),  # ventana de 30 días
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }

    log(f"Pidiendo a Apify los posts de {len(cuentas)} cuenta(s)…")
    run = cliente.actor(ACTOR_ID).call(run_input=run_input)
    if run is None or not run.default_dataset_id:
        raise RuntimeError("Apify no devolvió resultados. Revisa tu plan o las cuentas.")

    nuevos = 0
    restringidas = 0
    dataset = cliente.dataset(run.default_dataset_id).iterate_items()
    for item in dataset:
        if item.get("error"):  # perfil restringido/privado: Instagram lo bloquea
            restringidas += 1
            continue
        post = _normalizar(item)
        if post is None:
            continue
        antes = db.contar_posts()
        db.guardar_post(post)
        if db.contar_posts() > antes:
            nuevos += 1

    if restringidas:
        log(f"⚠️ {restringidas} cuenta(s) no se pudieron leer (perfil restringido o privado).")
    log(f"Listo. Se guardaron {nuevos} publicaciones nuevas.")
    return nuevos


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
        "fecha": (item.get("timestamp") or "")[:10],  # YYYY-MM-DD
        "imagen": item.get("displayUrl", ""),
    }
