"""Lectura de portales y sitios web inmobiliarios.

Usa el actor "apify/website-content-crawler" para abrir cada URL con un navegador
real (necesario porque los portales arman la página con JavaScript), baja el texto
y luego lo interpreta con Claude para sacar los inmuebles. Así funciona para
cualquier sitio (Metrocuadrado, Fincaraíz, webs de agencias, etc.).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
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
    """ID estable del inmueble.

    - Con link propio: el link normalizado (sin parámetros ni / final).
    - Sin link: huella por dominio + barrio + área + habitaciones + baños + dirección.
      OJO: sin precio ni página, para que una rebaja o el cambio de página NO lo
      dupliquen (el registro existente se actualiza en vez de crear otro).
    """
    link = (d.get("url") or "").strip()
    if link:
        base = link.split("?")[0].split("#")[0].rstrip("/").lower()
    else:
        barrio = str(d.get("barrio") or "").strip().lower()
        base = (f"{_dominio(pagina_url)}|{barrio}|{d.get('area_m2')}|"
                f"{d.get('habitaciones')}|{d.get('banos')}|{d.get('direccion')}")
    return "portal_" + hashlib.md5(base.encode("utf-8")).hexdigest()[:18]


def _urls_paginadas(urls: list[str], max_paginas: int) -> list[str]:
    """Expande cada búsqueda a varias páginas de resultados (verificado por portal).

    Antes solo se leía la página 1 de cada búsqueda (~20 avisos de miles). Ahora el
    tope de páginas se reparte entre las búsquedas con patrón de paginación conocido:
    Metrocuadrado usa ?page=N y Fincaraíz /paginaN. Sitios sin patrón quedan con su URL.
    """
    conocidos = [u for u in urls if "metrocuadrado.com" in u or "fincaraiz.com" in u]
    extra = max(0, max_paginas - len(urls))
    por_busqueda = (extra // len(conocidos)) if conocidos else 0
    out: list[str] = []
    for u in urls:
        out.append(u)
        base = u.split("?")[0].rstrip("/")
        if "metrocuadrado.com" in u:
            out.extend(f"{base}/?page={n}" for n in range(2, 2 + por_busqueda))
        elif "fincaraiz.com" in u:
            out.extend(f"{base}/pagina{n}" for n in range(2, 2 + por_busqueda))
    return out[:max_paginas]


def _fecha_publicacion(d: dict, hoy) -> tuple[str, bool]:
    """(fecha, es_estimada): la fecha real si el portal dice 'publicado hace X días';
    si no la dice, el día en que lo vimos por primera vez (estimada)."""
    dias = d.get("publicado_hace_dias")
    try:
        dias = int(dias) if dias is not None else None
    except (TypeError, ValueError):
        dias = None
    if dias is not None and 0 <= dias <= 3650:
        return (hoy - timedelta(days=dias)).isoformat(), False
    return hoy.isoformat(), True


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
    paginas = _urls_paginadas(urls, max_paginas)
    cliente = ApifyClient(config.APIFY_TOKEN)
    run_input = {
        "startUrls": [{"url": u} for u in paginas],
        "maxCrawlPages": max_paginas,
        "maxCrawlDepth": 0,                    # solo las páginas indicadas (predecible y barato)
        "crawlerType": "playwright:firefox",   # navegador real (portales con JavaScript)
        "saveMarkdown": True,
        "dynamicContentWaitSecs": 20,          # espera a que carguen los avisos (JS)
        "proxyConfiguration": {"useApifyProxy": True},
    }
    log(f"Abriendo {len(urls)} búsqueda(s) → {len(paginas)} página(s) de resultados…")
    run = cliente.actor(ACTOR_CRAWLER).call(run_input=run_input)
    if run is None or not run.default_dataset_id:
        raise RuntimeError("El lector de portales no devolvió resultados.")

    hoy_dt = datetime.now(timezone.utc).date()
    hoy = hoy_dt.isoformat()
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
            fecha, estimada = _fecha_publicacion(d, hoy_dt)
            d["fecha_estimada"] = estimada   # la UI lo muestra como "visto el", no "publicado"
            antes = db.contar_posts()
            db.guardar_post({
                "id": pid, "cuenta": fuente, "url": link,
                "caption": (d.get("resumen") or "")[:500],
                "fecha": fecha, "imagen": "", "media": [],
            })
            db.guardar_extraccion(pid, d)   # actualiza datos (ej. precio rebajado) sin duplicar
            if not estimada:
                db.actualizar_fecha(pid, fecha)  # corrige la fecha si ahora sí la conocemos
            if db.contar_posts() > antes:
                nuevos += 1

    db.guardar_meta("ultimo_scrape_portales", hoy)
    log(f"Listo. Se guardaron {nuevos} inmueble(s) nuevos de portales.")
    return nuevos
