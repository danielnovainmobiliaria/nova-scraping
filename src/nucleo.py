"""Helpers compartidos del catálogo (los usa la app de Streamlit Y el Radar).

Extraídos de app.py para que src/radar.py no dependa del script de la interfaz.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import clientes as mod_clientes


def dias_publicado(fecha_iso):
    """Días transcurridos desde la fecha de publicación (o None si no se sabe)."""
    if not fecha_iso:
        return None
    try:
        f = datetime.fromisoformat(str(fecha_iso)[:10]).date()
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - f).days


def es_portal_post(p) -> bool:
    """True si el inmueble vino de un portal/sitio web (no de Instagram)."""
    return str(p.get("id", "")).startswith("portal_")


def huella_inmueble(p) -> str | None:
    """'Huella' del inmueble (barrio+área+hab+precio) para reconocer el MISMO
    apartamento visto en varias fuentes (Instagram y un portal, por ejemplo)."""
    barrio = mod_clientes._norm_nombre(str(p.get("barrio") or ""))
    area, precio = p.get("area_m2"), p.get("precio")
    if not barrio or not area or not precio:
        return None            # sin datos suficientes no se puede agrupar con confianza
    habs = p.get("habitaciones")
    return f"{barrio}|{round(float(area))}|{habs if habs is not None else '?'}|{int(precio)}"


def dedup_posts(posts):
    """Colapsa copias del mismo inmueble (misma huella) en una sola tarjeta."""
    grupos: dict = {}
    orden: list = []
    for i, p in enumerate(posts):
        h = huella_inmueble(p) or f"__unico_{i}"
        if h not in grupos:
            grupos[h] = []
            orden.append(h)
        grupos[h].append(p)
    out = []
    for h in orden:
        g = grupos[h]
        if len(g) == 1:
            p = g[0]
        else:
            p = dict(max(g, key=lambda x: (bool(x.get("media")),
                                           sum(1 for v in x.values() if v not in (None, "", [])),
                                           str(x.get("fecha") or ""))))
            otras = sorted({x.get("cuenta", "") for x in g if x.get("cuenta")} - {p.get("cuenta", "")})
            if otras:
                p["otras_fuentes"] = otras
        p["ids_gemelos"] = [x.get("id") for x in g]
        out.append(p)
    return out


def fuente_post(p) -> str:
    """Etiqueta de la fuente del inmueble (red, portal o ingresado a mano)."""
    if str(p.get("id", "")).startswith("asig_"):
        return "📌 lo asignaste tú (link externo)"
    if str(p.get("id", "")).startswith("m_"):
        return "🖊️ ingresado por ti"
    if es_portal_post(p):
        return f"🏠 {p.get('cuenta', 'portal')}"
    return f"📷 @{p.get('cuenta', '')}"


def norm_link(u) -> str:
    """Normaliza un link para comparar (sin http, sin www, sin / final ni parámetros)."""
    u = (u or "").strip().lower()
    for p in ("https://", "http://"):
        if u.startswith(p):
            u = u[len(p):]
    if u.startswith("www."):
        u = u[4:]
    return u.split("?")[0].split("#")[0].rstrip("/")
