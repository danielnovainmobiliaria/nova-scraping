"""Radar para Brokerap: pre-calcula las coincidencias y las publica en la base.

Brokerap (Next.js) NO corre Python: este módulo deja la tabla `radar` servida
(una fila por tarjeta, con todo lo que la página necesita) para que la web solo
haga un SELECT. Se publica al final de cada actualización (botón 🔄 o robot).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from . import clientes as mod_clientes
from . import db, matcher
from .nucleo import dedup_posts, dias_publicado, fuente_post, huella_inmueble, norm_link

# Valores de fábrica (los mismos de los deslizadores de la app clásica).
# Se pueden cambiar desde Brokerap (⚙️ Ajustes → meta config_radar).
CONFIG_DEFECTO = {
    "umbral": 70, "flex_precio": 0.20, "flex_area": 0.20, "piso_precio": 0.80,
    "dias_venta": 40, "dias_arriendo": 20,
}


def _config() -> dict:
    try:
        crudo = json.loads(db.leer_meta("config_radar") or "{}")
    except json.JSONDecodeError:
        crudo = {}
    cfg = dict(CONFIG_DEFECTO)
    for k in cfg:
        try:
            if crudo.get(k) is not None:
                cfg[k] = type(CONFIG_DEFECTO[k])(crudo[k])
        except (TypeError, ValueError):
            pass
    return cfg


def _manuales_como_posts() -> list[dict]:
    """Los inmuebles manuales entran al cruce igual que en la app clásica."""
    try:
        manuales = json.loads(db.leer_meta("inmuebles_manuales") or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for item in manuales:
        d = item.get("datos") or {}
        if d.get("es_inmueble") is False:
            continue
        out.append({**d, "id": item.get("id"), "caption": item.get("texto", ""),
                    "url": item.get("link", ""), "fecha": item.get("fecha", ""),
                    "agregado": item.get("agregado") or item.get("fecha", ""),
                    "cuenta": item.get("cuenta") or "manual",
                    "imagen": item.get("imagen", ""),
                    "media": item.get("media") or []})
    return out


def _crear_tabla() -> None:
    with db._conn() as con:
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS radar (
                cliente TEXT NOT NULL,
                post_id TEXT NOT NULL,
                score INTEGER,
                operacion_cliente TEXT,
                prioridad TEXT,
                resumen TEXT, barrio TEXT,
                precio DOUBLE PRECISION, administracion DOUBLE PRECISION,
                area_m2 DOUBLE PRECISION, habitaciones DOUBLE PRECISION,
                banos DOUBLE PRECISION,
                fecha TEXT, fecha_estimada BOOLEAN, dias INTEGER,
                url TEXT, fuente TEXT, imagen TEXT,
                asignado BOOLEAN, nuevo_24h BOOLEAN,
                razones_ok TEXT, razones_no TEXT,
                huella TEXT, ids_gemelos TEXT,
                extras TEXT, media TEXT,
                PRIMARY KEY (cliente, post_id)
            )"""))
    # Columnas nuevas sobre tablas viejas: cada ALTER en SU PROPIA transacción
    # (en Postgres un ALTER fallido aborta la transacción y se lleva los demás).
    for col in ("huella TEXT", "ids_gemelos TEXT", "extras TEXT", "media TEXT"):
        try:
            with db._conn() as con:
                con.execute(text(f"ALTER TABLE radar ADD COLUMN {col}"))
        except Exception:  # noqa: BLE001 - ya existe
            pass


def publicar_radar(log=print) -> int:
    """Calcula las coincidencias visibles (misma lógica de la pestaña 3) y las
    publica en la tabla `radar`. Devuelve cuántas tarjetas quedaron."""
    _crear_tabla()
    cfg = _config()
    hoy = datetime.now(timezone.utc).date()
    clientes = [c for c in mod_clientes.cargar_guardados()
                if (c.get("estado") or "activo") == "activo" and not c.get("en_pausa")]
    posts = db.posts_leidos() + _manuales_como_posts()

    def fresco(p):
        d = dias_publicado(p.get("fecha"))
        if str(p.get("id", "")).startswith(("m_", "asig_")):
            return True   # lo metió el broker a mano: no caduca por frescura
        if d is None:
            return True
        op = matcher._inferir_operacion(p)
        return d <= (cfg["dias_arriendo"] if op == "arriendo" else cfg["dias_venta"])

    pool = dedup_posts([p for p in posts
                        if p.get("es_inmueble", True) and not matcher.esta_vendido(p)
                        and fresco(p)])
    resultados = matcher.cruzar(clientes, pool, score_minimo=cfg["umbral"],
                                flex_precio=cfg["flex_precio"],
                                flex_area=cfg["flex_area"],
                                piso_precio=cfg["piso_precio"])

    por_link = {}
    for p in posts:
        nl = norm_link(p.get("url") or "")
        if nl:
            por_link.setdefault(nl, p)

    filas = []
    ayer = {hoy.isoformat()}
    from datetime import timedelta
    ayer.add((hoy - timedelta(days=1)).isoformat())
    for c in clientes:
        nombre = c["nombre"]
        ids_oc = mod_clientes.ids_en_proceso(c)
        h_oc = {pr.get("huella") for pr in (c.get("procesos") or []) if pr.get("huella")}
        vistos_id = set()
        matches = []
        for m in (resultados.get(nombre) or []):
            p = m["post"]
            if set(p.get("ids_gemelos") or [p.get("id")]) & ids_oc:
                continue
            h = huella_inmueble(p)
            if h and h in h_oc:
                continue
            matches.append((m, False))
            vistos_id |= set(p.get("ids_gemelos") or [p.get("id")])
        # 📌 asignados a dedo (por link): fijados arriba, sin importar frescura
        for a in (c.get("asignados") or []):
            nl = norm_link(a.get("link") or "")
            p = por_link.get(nl)
            if p is None:
                p = {"id": "asig_" + nl[:24], "url": a.get("link"),
                     "fecha": a.get("fecha"), "resumen": a.get("nota")
                     or "Inmueble asignado por ti", "operacion": c.get("operacion")}
            if set([p.get("id")]) & (ids_oc | vistos_id):
                continue
            h = huella_inmueble(p)
            if h and h in h_oc:
                continue
            matches.append(({"score": 100, "post": p,
                             "razones_ok": ["📌 Lo asignaste tú"], "razones_no": []}, True))
        for m, asignado in matches:
            p = m["post"]
            d = dias_publicado(p.get("fecha"))
            filas.append({
                "cliente": nombre, "post_id": str(p.get("id")),
                "score": int(m["score"]),
                "operacion_cliente": ("arriendo" if (c.get("operacion") or "venta") == "arriendo"
                                      else "venta"),
                "prioridad": (c.get("prioridad") or "media"),
                "resumen": (p.get("resumen") or (p.get("caption") or "")[:90]) or "",
                "barrio": p.get("barrio"), "precio": p.get("precio"),
                "administracion": p.get("administracion"),
                "area_m2": p.get("area_m2"), "habitaciones": p.get("habitaciones"),
                "banos": p.get("banos"),
                "fecha": str(p.get("fecha") or "")[:10] or None,
                "fecha_estimada": bool(p.get("fecha_estimada")),
                "dias": d, "url": p.get("url"), "fuente": fuente_post(p),
                # Los archivos del CDN de Instagram caducan en ~2 días: imagen y
                # media solo se publican mientras sus URLs sigan vivas.
                "imagen": (p.get("imagen") or "") if (p.get("fecha_estimada")
                          or str(p.get("id", "")).startswith("portal_")
                          or (d is not None and d <= 2)) else "",
                "extras": json.dumps(p.get("extras") or [], ensure_ascii=False),
                "media": json.dumps(p.get("media") or [], ensure_ascii=False)
                         if (str(p.get("id", "")).startswith("portal_")
                             or (d is not None and d <= 2)) else "[]",
                "asignado": asignado,
                "nuevo_24h": (p.get("agregado") or "") in ayer,
                "razones_ok": json.dumps(m.get("razones_ok") or [], ensure_ascii=False),
                "razones_no": json.dumps(m.get("razones_no") or [], ensure_ascii=False),
                "huella": huella_inmueble(p),
                "ids_gemelos": json.dumps(p.get("ids_gemelos") or [p.get("id")],
                                          ensure_ascii=False),
            })

    with db._conn() as con:
        con.execute(text("DELETE FROM radar"))
        if filas:
            cols = list(filas[0].keys())
            marcadores = ", ".join(f":{c}" for c in cols)
            con.execute(text(f"INSERT INTO radar ({', '.join(cols)}) "
                             f"VALUES ({marcadores})"), filas)
    db.guardar_meta("radar_publicado", datetime.now(timezone.utc).isoformat())
    log(f"📡 Radar publicado: {len(filas)} tarjetas de {len(clientes)} cliente(s).")
    return len(filas)
