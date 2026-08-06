"""Limpieza de inventario: fuera los avisos que ya no son comercializables.

Regla de Daniel (2026-08): un arriendo con más de 20 días publicado ya casi
seguro se tomó; una venta con más de un mes y 10 días (40) ya se enfrió.
Se BORRAN de la base para que el catálogo solo tenga oportunidades reales
(y las consultas sean más livianas).
"""
from __future__ import annotations

from datetime import date

from . import db, matcher

DIAS_VENTA_MAX = 40      # un mes y 10 días
DIAS_ARRIENDO_MAX = 20


def _link_norm(u: str | None) -> str:
    u = (u or "").strip().lower()
    for pre in ("https://", "http://"):
        u = u.removeprefix(pre)
    u = u.removeprefix("www.")
    return u.split("?")[0].rstrip("/")


def purgar_no_comercializables(log=print) -> int:
    """Borra avisos vencidos. Protegidos: los 📌 asignados por el broker
    (los eligió a dedo) y los inmuebles manuales/asignados (no viven aquí)."""
    posts = db.posts_leidos()
    hoy = date.today()
    protegidos: set[str] = set()
    for c in db.leer_clientes():
        for a in (c.get("asignados") or []):
            n = _link_norm(a.get("link"))
            if n:
                protegidos.add(n)

    vencidos: list[str] = []
    for p in posts:
        pid = str(p.get("id") or "")
        if not pid or pid.startswith(("m_", "asig_")):
            continue
        try:
            d = (hoy - date.fromisoformat(str(p.get("fecha") or "")[:10])).days
        except (TypeError, ValueError):
            continue                    # sin fecha legible: no se toca
        op = matcher._inferir_operacion(p) or ""
        tope = DIAS_ARRIENDO_MAX if op == "arriendo" else DIAS_VENTA_MAX
        if d <= tope:
            continue
        if _link_norm(p.get("url")) in protegidos:
            continue                    # 📌 del broker: se conserva
        vencidos.append(pid)
    db.eliminar_posts(vencidos)
    borrados = len(vencidos)
    if borrados:
        log(f"🧹 Limpieza: {borrados} aviso(s) vencidos borrados "
            f"(arriendo >{DIAS_ARRIENDO_MAX}d, venta >{DIAS_VENTA_MAX}d).")
    else:
        log("🧹 Limpieza: nada vencido que borrar.")
    return borrados
