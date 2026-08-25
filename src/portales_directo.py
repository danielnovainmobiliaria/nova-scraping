"""Lectura DIRECTA de portales (sin Apify): gratis y, cuando el portal entrega
datos estructurados, también sin IA — más preciso que leer texto.

Estrategias por dominio:
- fincaraiz.com.co  → JSON __NEXT_DATA__ incrustado en el HTML (¡estructurado!,
  con fechas reales, administración y hasta el flag 'sold').
- Sitios "simples" que renderizan en servidor (WordPress y similares: aldana,
  debedout, myhome, topliving) → HTML a texto → la MISMA lectura con IA de siempre.
- Lo que no se pueda directo (ej. Metrocuadrado) lo sigue cubriendo Apify.
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Any

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

DOMINIOS_SIMPLES = ("inmobiliariaaldana.com", "debedout.co", "myhome.com.co",
                    "topliving.com.co")
PAGINAS_FINCARAIZ = 3      # páginas por búsqueda (≈21 avisos c/u); gratis


def soporta(url: str) -> bool:
    return "fincaraiz.com.co" in url or any(d in url for d in DOMINIOS_SIMPLES)


def _bajar(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text


# ── Fincaraíz: estructurado desde __NEXT_DATA__ ──────────────

def _listados_next_data(html_txt: str) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_txt, re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(1))
        return (d.get("props", {}).get("pageProps", {}).get("fetchResult", {})
                .get("searchFast", {}).get("data", []) or [])
    except (json.JSONDecodeError, AttributeError):
        return []


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _mapear_fincaraiz(e: dict) -> dict[str, Any] | None:
    """Convierte un listado del JSON de Fincaraíz al formato de la herramienta."""
    link = e.get("link") or ""
    if not link:
        return None
    precio = _num((e.get("price") or {}).get("amount"))
    admin = _num((e.get("commonExpenses") or {}).get("amount"))
    lm = (e.get("locations") or {}).get("location_main") or {}
    barrio = lm.get("name")
    if not barrio:
        # el título suele terminar "… en <barrio>, Bogotá"
        m = re.search(r" en ([^,]{3,40}), Bogot", e.get("title") or "")
        barrio = m.group(1).strip() if m else None
    anti = _num(e.get("antiquity"))
    if anti is None and _num(e.get("construction_year")):
        from datetime import date
        anti = max(0, date.today().year - int(e["construction_year"]))
    op = ((e.get("operation_type") or {}).get("name") or "").lower()
    datos = {
        "es_inmueble": True,
        "operacion": "arriendo" if "arriendo" in op or "rent" in op else "venta",
        "tipo": ((e.get("property_type") or {}).get("name") or "apartamento").lower(),
        "barrio": barrio, "zona": None, "direccion": e.get("address") or None,
        "area_m2": _num(e.get("m2")) or _num(e.get("m2Built")),
        "precio": precio, "administracion": admin,
        "habitaciones": _num(e.get("bedrooms")),
        "banos": _num(e.get("bathrooms")),
        "parqueaderos": _num(e.get("garage")),
        "estrato": _num(e.get("stratum")),
        "antiguedad_anos": anti,
        "extras": (["penthouse"] if e.get("penthouse") else []),
        "resumen": (e.get("title") or "")[:150],
        "no_disponible": bool(e.get("sold")),
        "publicado_hace_dias": None,
    }
    fecha = str(e.get("updated_at") or e.get("created_at") or "")[:10] or None
    return {
        "url": "https://www.fincaraiz.com.co" + link if link.startswith("/") else link,
        "caption": ((e.get("title") or "") + ". "
                    + (e.get("description") or ""))[:1200],
        "imagen": e.get("img") or "",
        "fecha": fecha,           # fecha REAL del portal (no estimada)
        "datos": datos,
    }


def _paginas_de(url: str, cuantas: int) -> list[str]:
    base = url.split("?")[0].rstrip("/")
    return [url] + [f"{base}/pagina{n}" for n in range(2, cuantas + 1)]


def leer_fincaraiz(url: str, log=print) -> list[dict]:
    items: list[dict] = []
    vistos: set[str] = set()
    for pagina in _paginas_de(url, PAGINAS_FINCARAIZ):
        try:
            crudos = _listados_next_data(_bajar(pagina))
        except requests.RequestException as e:
            log(f"   ⚠️ {pagina[:60]}: {e}")
            continue
        frescos = 0
        for e in crudos:
            it = _mapear_fincaraiz(e)
            if it and it["url"] not in vistos:
                vistos.add(it["url"])
                items.append(it)
                frescos += 1
        if not frescos:      # página sin nada nuevo → las siguientes menos
            break
    return items


# ── Sitios simples: HTML → texto para la lectura con IA de siempre ──

_RE_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S)


def leer_texto_simple(url: str, log=print) -> str:
    html_txt = _bajar(url)
    texto = _RE_TAGS.sub(" ", html_txt)
    texto = _html.unescape(texto)
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(l.strip() for l in texto.splitlines()))
    return texto.strip()
