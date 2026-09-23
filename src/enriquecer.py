"""Ficha completa de cada candidato: lo que la lista no dice, la página sí.

Daniel (2026-09-23): *"hace rato no encuentro una opción real y buena para
enviarle a un cliente [...] veo pocas opciones y no tan adecuadas"*. Medido
ese día: sus fichas piden piso alto (5 clientes), vista, remodelado, terraza,
CBS; los avisos de LISTA dicen el piso en el 3 % de los casos. Y 65 de 208
tarjetas tenían más de 20 días sin que nadie supiera si el inmueble seguía
disponible.

Aquí se abre la página del inmueble —solo de los que YA pasaron el cruce, unos
200, no los 5.000 de la base— y se saca lo que la lista calla: piso,
antigüedad, parqueaderos, estrato, la descripción larga (de donde salen
terraza, remodelado, vista, CBS) y si sigue publicado. Fincaraíz lo trae
estructurado en su __NEXT_DATA__ (floor, antiquity, facilities, sold) sin IA;
las agencias van por texto + la misma IA de siempre. Metrocuadrado no: su
ficha es JavaScript puro y por Apify costaría; se deja como está.

Cada ficha se guarda en la tabla `detalles` y se vuelve a mirar a los
DIAS_RECHEQUEO días (para saber si se fue). Instagram no se toca: un post no
tiene "ficha" y pedir el post por Apify cuesta.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

import requests
from sqlalchemy import text

from . import db, extractor, portales_directo

DIAS_RECHEQUEO = 5
MAX_POR_CORRIDA = 250
HILOS = 4

_RE_SE_FUE = re.compile(
    r"\b(vendido|vendida|arrendado|arrendada|ya no (est[aá]|se encuentra) disponible|"
    r"no disponible|inmueble no encontrado|propiedad no encontrada|p[aá]gina no encontrada)\b", re.I)


def _crear_tabla() -> None:
    with db._conn() as con:
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS detalles (
                post_id    TEXT PRIMARY KEY,
                visto      TEXT,
                disponible BOOLEAN,
                datos_json TEXT
            )"""))


def _vistos(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    with db._conn() as con:
        res = con.execute(text("SELECT post_id, visto, disponible FROM detalles "
                               "WHERE post_id IN :ids").bindparams(
                                   __import__("sqlalchemy").bindparam("ids", expanding=True)),
                          {"ids": ids})
        return {r[0]: {"visto": r[1], "disponible": r[2]} for r in res}


def _guardar(post_id: str, disponible: bool | None, datos: dict) -> None:
    with db._conn() as con:
        con.execute(text("DELETE FROM detalles WHERE post_id = :id"), {"id": post_id})
        con.execute(text("INSERT INTO detalles (post_id, visto, disponible, datos_json) "
                         "VALUES (:id, :v, :d, :j)"),
                    {"id": post_id, "v": date.today().isoformat(), "d": disponible,
                     "j": json.dumps(datos, ensure_ascii=False)})


def se_puede_enriquecer(post: dict) -> bool:
    pid = str(post.get("id") or "")
    url = str(post.get("url") or "")
    return (pid.startswith("portal_") and url.startswith("http")
            and "metrocuadrado.com" not in url)


# ── Fincaraíz: estructurado ─────────────────────────────────

def _ficha_fincaraiz(html_txt: str) -> tuple[bool | None, dict, str]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_txt, re.S)
    if not m:
        return None, {}, ""
    try:
        d = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("data") or {}
    except (json.JSONDecodeError, AttributeError):
        return None, {}, ""
    if not d:
        return None, {}, ""
    num = portales_directo._num
    datos = {
        "piso": num(d.get("floor")),
        "antiguedad_anos": (num(d.get("antiquity")) if num(d.get("antiquity")) is not None
                            else (0 if d.get("antiquity") in (0, "0") else None)),
        "parqueaderos": num(d.get("garage")),
        "estrato": num(d.get("stratum")),
        "direccion": d.get("address") or None,
    }
    facilidades = ", ".join(str(f.get("name") or "") for f in (d.get("facilities") or []))
    texto = f"{d.get('title') or ''}. {d.get('description') or ''}\nCaracterísticas: {facilidades}"
    return (not bool(d.get("sold"))), {k: v for k, v in datos.items() if v is not None}, texto


# ── Lectura de UNA ficha ────────────────────────────────────

def leer_ficha(url: str) -> tuple[bool | None, dict, str]:
    """(disponible, datos estructurados, texto largo). disponible None = no se supo."""
    try:
        r = requests.get(url, headers={"User-Agent": portales_directo.UA}, timeout=30)
    except requests.RequestException:
        return None, {}, ""
    if r.status_code in (404, 410):
        return False, {}, ""
    if r.status_code != 200:
        return None, {}, ""
    if "fincaraiz.com.co" in url:
        return _ficha_fincaraiz(r.text)
    texto = portales_directo._RE_TAGS.sub(" ", r.text)
    texto = re.sub(r"\s+", " ", __import__("html").unescape(texto)).strip()
    # Solo el arranque de la página: un "vendido" en el pie ("otros inmuebles
    # vendidos") no habla de ESTE aviso.
    cabeza = texto[:1500]
    if _RE_SE_FUE.search(cabeza):
        return False, {}, texto
    return True, {}, texto


def fusionar(post: dict, datos: dict, leido: dict | None, texto: str) -> dict:
    """Completa el aviso con la ficha SIN pisar lo que ya tenía.

    Lo estructurado del portal (piso, antigüedad…) sí manda sobre lo que la IA
    dedujo de la lista; la IA de la ficha solo llena huecos y suma extras."""
    out = dict(post)
    for k, v in datos.items():
        if v is not None:
            out[k] = v
    for k, v in (leido or {}).items():
        if k in ("es_inmueble", "resumen", "url", "operacion", "precio", "administracion"):
            continue
        if k == "extras":
            out["extras"] = sorted(set(out.get("extras") or []) | set(v or []))
        elif out.get(k) in (None, "", []) and v not in (None, "", []):
            out[k] = v
    if texto:
        base = str(out.get("caption") or "")
        if texto[:200] not in base:
            out["caption"] = (base + "\n" + texto)[:6000]
    return out


def enriquecer(posts: list[dict], log=print, max_n: int = MAX_POR_CORRIDA) -> int:
    """Abre la ficha de los candidatos que aún no se han mirado (o que toca
    volver a mirar) y los completa EN SITIO. Devuelve cuántos se abrieron."""
    from concurrent.futures import ThreadPoolExecutor
    _crear_tabla()
    candidatos = {str(p["id"]): p for p in posts if se_puede_enriquecer(p)}
    if not candidatos:
        return 0
    corte = (date.today() - timedelta(days=DIAS_RECHEQUEO)).isoformat()
    vistos = _vistos(list(candidatos))
    pendientes = [p for pid, p in candidatos.items()
                  if pid not in vistos or str(vistos[pid].get("visto") or "") < corte][:max_n]
    if not pendientes:
        return 0
    log(f"🔎 Abriendo la ficha de {len(pendientes)} candidato(s) "
        f"({len(candidatos) - len(pendientes)} ya vistas)…")

    def _una(p: dict) -> tuple[dict, bool | None, dict, str]:
        disponible, datos, texto = leer_ficha(p["url"])
        return p, disponible, datos, texto

    se_fueron = 0
    completados = 0
    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        fichas = list(pool.map(_una, pendientes))
    for p, disponible, datos, texto in fichas:
        pid = str(p["id"])
        if disponible is False:
            p["no_disponible"] = True
            se_fueron += 1
            _guardar(pid, False, {})
            _persistir(p)
            continue
        if disponible is None and not datos and not texto:
            _guardar(pid, None, {})       # no se pudo abrir: se reintenta en 5 días
            continue
        leido: dict | None = None
        if texto.strip():
            try:
                leido = extractor.interpretar_inmueble(texto[:4000])
            except Exception as e:  # noqa: BLE001 - sin IA igual sirve lo estructurado
                log(f"   ⚠️ IA no leyó la ficha de {pid}: {e}")
        nuevo = fusionar(p, datos, leido, texto)
        p.clear()
        p.update(nuevo)
        _guardar(pid, True, {**datos, "extras": (leido or {}).get("extras") or []})
        _persistir(p)
        completados += 1
    log(f"🔎 Fichas: {completados} completadas, {se_fueron} ya no están publicadas.")
    return len(fichas)


_CAMPOS_TABLA = {"id", "cuenta", "url", "caption", "fecha", "imagen", "media", "agregado",
                 "ids_gemelos", "otras_fuentes"}


def _persistir(p: dict) -> None:
    """Deja lo aprendido en `posts` para que sirva en todas las corridas."""
    datos = {k: v for k, v in p.items() if k not in _CAMPOS_TABLA}
    db.guardar_extraccion(str(p["id"]), datos)
    db.actualizar_caption(str(p["id"]), str(p.get("caption") or ""))
