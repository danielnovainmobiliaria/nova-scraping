"""Lectura de portales y sitios web inmobiliarios.

Usa el actor "apify/website-content-crawler" para abrir cada URL con un navegador
real (necesario porque los portales arman la página con JavaScript), baja el texto
y luego lo interpreta con Claude para sacar los inmuebles. Así funciona para
cualquier sitio (Metrocuadrado, Fincaraíz, webs de agencias, etc.).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

from apify_client import ApifyClient

from . import config, db, extractor

ACTOR_CRAWLER = "apify/website-content-crawler"


def _dominio(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "") or "portal"
    except Exception:  # noqa: BLE001
        return "portal"


def _id_inmueble(d: dict, pagina_url: str) -> str:
    """ID estable: por el link del inmueble, o por su 'huella' (barrio+precio+área)."""
    link = (d.get("url") or "").strip()
    if link:
        base = link
    else:
        base = f"{pagina_url}|{d.get('barrio')}|{d.get('precio')}|{d.get('area_m2')}|{d.get('habitaciones')}"
    return "portal_" + hashlib.md5(base.encode("utf-8")).hexdigest()[:18]


def scrapear_portales(urls: list[str], log=print, max_paginas: int | None = None) -> int:
    """Lee los portales/sitios indicados y guarda los inmuebles encontrados.

    Devuelve cuántos inmuebles NUEVOS se agregaron.
    """
    if not config.APIFY_TOKEN:
        raise RuntimeError("Falta APIFY_TOKEN. Pégalo en «🔑 Mis llaves» de la barra lateral.")
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY para leer los portales con IA.")
    urls = [u.strip() for u in (urls or []) if u and u.strip()]
    if not urls:
        log("No hay portales configurados (pégalos en la pestaña Fuentes).")
        return 0

    max_paginas = max_paginas or config.MAX_PAGINAS_PORTAL
    cliente = ApifyClient(config.APIFY_TOKEN)
    run_input = {
        "startUrls": [{"url": u} for u in urls],
        "maxCrawlPages": max_paginas,
        "maxCrawlDepth": 0,                    # solo las páginas pegadas (predecible y barato)
        "crawlerType": "playwright:firefox",   # navegador real (portales con JavaScript)
        "saveMarkdown": True,
        "proxyConfiguration": {"useApifyProxy": True},
    }
    log(f"Abriendo {len(urls)} portal(es) con navegador… (tope {max_paginas} páginas)")
    run = cliente.actor(ACTOR_CRAWLER).call(run_input=run_input)
    if run is None or not run.default_dataset_id:
        raise RuntimeError("El lector de portales no devolvió resultados.")

    hoy = datetime.now(timezone.utc).date().isoformat()
    nuevos = 0
    for item in cliente.dataset(run.default_dataset_id).iterate_items():
        pagina_url = item.get("url", "")
        texto = item.get("markdown") or item.get("text") or ""
        if not texto.strip():
            continue
        fuente = _dominio(pagina_url)
        inmuebles = extractor.extraer_inmuebles_pagina(texto, fuente=fuente, log=log)
        log(f"@{fuente}: {len(inmuebles)} inmueble(s) leído(s).")
        for d in inmuebles:
            link = (d.get("url") or "").strip() or pagina_url
            pid = _id_inmueble(d, pagina_url)
            antes = db.contar_posts()
            db.guardar_post({
                "id": pid, "cuenta": fuente, "url": link,
                "caption": (d.get("resumen") or "")[:500],
                "fecha": hoy, "imagen": "", "media": [],
            })
            db.guardar_extraccion(pid, d)
            if db.contar_posts() > antes:
                nuevos += 1

    db.guardar_meta("ultimo_scrape_portales", hoy)
    log(f"Listo. Se guardaron {nuevos} inmueble(s) nuevos de portales.")
    return nuevos
