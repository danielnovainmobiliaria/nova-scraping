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

# Sitios que renderizan en el servidor: se leen directo y GRATIS, sin Apify.
#
# Los tres últimos entraron el 2026-09-15 y comparten motor (el patrón
# /search?id_city=…&business_type[]=for_sale). Comprobado antes de agregarlos:
# space devuelve 17 precios en el HTML, hook 15, vpandco 14 — no hace falta
# JavaScript. `.inmo.co` cubre de una a hook y a vpandco, que ya eran fuentes
# pero estaban pasando por Apify sin necesidad.
DOMINIOS_SIMPLES = ("inmobiliariaaldana.com", "debedout.co", "myhome.com.co",
                    "topliving.com.co", "coolhouse.com.co", "muvstudio.com.co", "pads.com.co",
                    "spaceinmobiliaria.com", ".inmo.co", "belainmobiliaria.com")
PAGINAS_SIMPLES = 2        # páginas por búsqueda en los sitios de lectura directa
PAGINAS_FINCARAIZ = 3      # páginas por búsqueda (≈21 avisos c/u); gratis


def soporta(url: str) -> bool:
    return ("fincaraiz.com.co" in url or "somosselecto.com" in url
            or any(d in url for d in DOMINIOS_SIMPLES))


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


def es_busqueda_de_toda_bogota(url: str) -> bool:
    """La búsqueda general de la ciudad (…/bogota/bogota-dc), sin barrio."""
    return "fincaraiz.com.co" in url and url.rstrip("/").endswith("/bogota/bogota-dc")


def slug_barrio(nombre: str) -> str:
    """'Chicó Navarra' → 'chico-navarra' (así arma Fincaraíz sus rutas)."""
    t = str(nombre or "").lower().strip()
    for a, b in {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}.items():
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t


def busquedas_por_barrio(url_ciudad: str, barrios: list[str],
                         casas: list[str] = ()) -> list[str]:
    """La búsqueda de toda Bogotá, abierta barrio por barrio.

    Daniel (2026-09-23): "veo pocos hallazgos [...] siento que radar no está
    siendo aprovechado al máximo". Medido ese día: la búsqueda general de
    Fincaraíz trae los avisos más recientes de TODA la ciudad (770 en 30 días),
    y la mayoría caen en Cedritos, Colina, Salitre… donde ningún cliente busca.
    De Rosales, que piden 8 clientes, llegaban 37. Fincaraíz tiene una ruta por
    barrio (…/bogota/rosales) con el mismo JSON, gratis: se abre una por cada
    barrio que piden los clientes activos, y así el inventario que entra es el
    de los barrios que importan, no el de la ciudad entera.
    """
    base = url_ciudad.rstrip("/")
    if not base.endswith("/bogota/bogota-dc"):
        return []
    raiz = base[: -len("/bogota-dc")]
    # Un cliente que busca CASA no encuentra nada en una búsqueda de
    # apartamentos (Edwin Cabrera: casa en arriendo, 0 coincidencias porque
    # todas las rutas de portal eran de apartamentos). Fincaraíz tiene la misma
    # ruta con /casas/: se abre por cada barrio donde alguien pide casa.
    raiz_casas = raiz.replace("/apartamentos/", "/casas/")
    urls: list[str] = []
    for lista, r in ((barrios, raiz), (casas, raiz_casas)):
        for b in lista:
            sl = slug_barrio(b)
            if sl and sl != "bogota" and f"{r}/{sl}" not in urls:
                urls.append(f"{r}/{sl}")
    return urls


def _titulo(html_txt: str) -> str:
    m = re.search(r"<title>([^<]*)</title>", html_txt, re.S)
    return m.group(1).strip() if m else ""


def titulo_es_de_barrio(titulo: str) -> bool:
    """Fincaraíz no da 404 por un barrio que no conoce: devuelve la búsqueda de
    toda la ciudad con título "Apartamentos en Venta en Bogotá, d.c.". Un
    barrio de verdad se ve en el título: "… en Chico, Bogotá"."""
    # El ÚLTIMO " en " antes de la coma: "Apartamentos en Venta en Chico, Bogotá".
    m = re.search(r"(?:.* en )([^,]{2,60}), ", titulo)
    return bool(m) and slug_barrio(m.group(1)) not in ("bogota", "bogota-d-c")


def leer_fincaraiz(url: str, log=print, solo_si_es_barrio: bool = False) -> list[dict]:
    items: list[dict] = []
    vistos: set[str] = set()
    for pagina in _paginas_de(url, PAGINAS_FINCARAIZ):
        try:
            html_txt = _bajar(pagina)
        except requests.RequestException as e:
            log(f"   ⚠️ {pagina[:60]}: {e}")
            continue
        if solo_si_es_barrio and pagina == url and not titulo_es_de_barrio(_titulo(html_txt)):
            return []            # Fincaraíz no conoce ese barrio: devolvió la ciudad entera
        crudos = _listados_next_data(html_txt)
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


def leer_fincaraiz_por_barrios(url_ciudad: str, barrios: list[str], log=print,
                               casas: list[str] = ()) -> list[dict]:
    """Lee la búsqueda de toda Bogotá abierta por cada barrio pedido.

    Devuelve los avisos sin repetir (por link). Los barrios que Fincaraíz no
    reconoce se saltan en silencio (su ruta devuelve la ciudad entera, que ya
    se leyó). Van en paralelo de a 4: son ~50 rutas de 3 páginas, gratis."""
    from concurrent.futures import ThreadPoolExecutor
    urls = busquedas_por_barrio(url_ciudad, barrios, casas)
    if not urls:
        return []

    def _una(u: str) -> tuple[str, list[dict]]:
        try:
            return u, leer_fincaraiz(u, log=lambda *_: None, solo_si_es_barrio=True)
        except Exception as e:  # noqa: BLE001 - un barrio caído no tumba los demás
            log(f"   ⚠️ {u.rsplit('/', 1)[-1]}: {e}")
            return u, []

    items: list[dict] = []
    vistos: set[str] = set()
    con_avisos: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for u, lista in pool.map(_una, urls):
            if lista:
                con_avisos.append(("🏡 " if "/casas/" in u else "") + u.rsplit("/", 1)[-1])
            for it in lista:
                if it["url"] not in vistos:
                    vistos.add(it["url"])
                    items.append(it)
    log(f"   barrios con avisos ({len(con_avisos)}/{len(urls)}): " + ", ".join(con_avisos))
    return items


# ── Selecto (somosselecto.com): estructurado desde los atributos del CMS ──
#
# Daniel (2026-09-16) pidió agregar somosselecto.com/propiedades. Es un sitio
# Webflow y trae TODAS sus propiedades en una sola página (63 el día que se
# agregó, sin paginación), cada campo marcado con fs-list-field="…": precio,
# administración, alcobas, baños, parqueaderos, área, contrato, ciudad, zona,
# tipo, amoblamiento, antigüedad y los "ambientes". Se lee de ahí y no por IA:
# sale gratis, sin errores de lectura y con el LINK de cada inmueble, que el
# camino de texto plano pierde (a la IA le llega el texto sin los href).
#
# El barrio va en el TÍTULO ("Apartamento con balcón en venta, Chico oriental."),
# no en la zona (ahí ponen la localidad: "Chapinero"). Por eso el barrio se
# saca del título, y la zona queda como zona.

_RE_TARJETA_SELECTO = re.compile(r'class="item-project w-dyn-item"')
_RE_CAMPO_SELECTO = re.compile(r'fs-list-field="([^"]+)"[^>]*>([^<]*)<')
_RE_LINK_SELECTO = re.compile(r'href="(/properties/[^"]+)"')
_RE_FOTO_SELECTO = re.compile(r'background-image:url\(&quot;([^&]+)&quot;\)')


def _num_co(x: str | None) -> float | None:
    """'6.184.000.000' → 6184000000; '3,5' → 3.5; '' → None."""
    if not x:
        return None
    t = x.strip().replace("$", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    t = t.replace(",", ".")
    return _num(t)


def _barrio_del_titulo(titulo: str) -> str | None:
    """'Apartamento con balcón en venta, Chico oriental.' → 'Chico oriental'.
    'Apartamento a la venta con terraza en VIDERE, Chico Oriental.' → 'Chico Oriental'."""
    t = titulo.strip().rstrip(".")
    if "," in t:
        # "Casa campestre en venta, en La Calera." → "La Calera"
        cola = re.sub(r"^en\s+", "", t.rsplit(",", 1)[1].strip(), flags=re.I)
        if 3 <= len(cola) <= 40:
            return cola
    m = re.search(r"\ben\s+([A-ZÁÉÍÓÚÑ][\w\sáéíóúñÁÉÍÓÚÑ]{2,40})$", t)
    return m.group(1).strip() if m else None


def _mapear_selecto(tarjeta: str) -> dict[str, Any] | None:
    campos: dict[str, list[str]] = {}
    for k, v in _RE_CAMPO_SELECTO.findall(tarjeta):
        campos.setdefault(k.strip().lower(), []).append(_html.unescape(v).strip())

    def uno(k: str) -> str:
        return next((v for v in campos.get(k, []) if v), "")

    def ultimo(k: str) -> str:
        return next((v for v in reversed(campos.get(k, [])) if v and not v.lower().startswith("precio")), "")

    m = _RE_LINK_SELECTO.search(tarjeta)
    if not m:
        return None
    link = "https://www.somosselecto.com" + m.group(1)
    titulo = uno("nombre")
    venta = _num_co(uno("precio-venta"))
    arriendo = _num_co(uno("precio-arriendo"))
    contrato = uno("contrato").lower()
    es_arriendo = "arriendo" in contrato or (arriendo and not venta)
    precio = arriendo if es_arriendo else venta
    if not precio:
        return None
    anti = None
    tiempo = uno("tiempo").lower()
    if "estrenar" in tiempo:
        anti = 0
    elif (mm := re.search(r"(\d+)\s*a", tiempo)):
        anti = float(mm.group(1))
    extras = [e for e in campos.get("ambiente", []) if e]
    if "penthouse" in (uno("tipo") + " " + titulo).lower():
        extras.append("penthouse")
    if "amoblado" in uno("amoblamiento").lower() and "sin" not in uno("amoblamiento").lower():
        extras.append("amoblado")
    foto = _RE_FOTO_SELECTO.search(tarjeta)
    datos = {
        "es_inmueble": True,
        "operacion": "arriendo" if es_arriendo else "venta",
        "tipo": (uno("tipo") or "apartamento").lower(),
        # El sitio marca con fs-list-field="zona" TAMBIÉN el rótulo "Precio
        # venta" de la tarjeta; la zona de verdad es el último valor.
        "barrio": _barrio_del_titulo(titulo), "zona": ultimo("zona") or None,
        "direccion": None,
        "area_m2": _num_co(uno("area")),
        "precio": precio, "administracion": _num_co(uno("administracion")),
        "habitaciones": _num_co(uno("alcobas")),
        "banos": _num_co(uno("baños")) or _num_co(uno("banos")),
        "parqueaderos": _num_co(uno("parqueaderos")),
        "estrato": None,
        "antiguedad_anos": anti,
        "extras": extras,
        "resumen": titulo[:150],
        "no_disponible": False,
        "publicado_hace_dias": None,
    }
    return {
        "url": link,
        "caption": (titulo + ". " + ", ".join(extras))[:1200],
        "imagen": _html.unescape(foto.group(1)) if foto else "",
        "fecha": None,            # Selecto no publica fecha: queda "visto el"
        "datos": datos,
    }


def leer_selecto(url: str, log=print) -> list[dict]:
    html_txt = _bajar(url)
    cortes = [m.start() for m in _RE_TARJETA_SELECTO.finditer(html_txt)] + [len(html_txt)]
    items: list[dict] = []
    for a, b in zip(cortes, cortes[1:]):
        it = _mapear_selecto(html_txt[a:b])
        if it:
            items.append(it)
    log(f"   somosselecto.com: {len(items)} avisos en la página")
    return items


# ── Sitios simples: HTML → texto para la lectura con IA de siempre ──

_RE_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S)
_RE_ENLACE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
_RE_ENLACE_SIN_TEXTO = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>', re.I)


def _conservar_enlaces(html_txt: str, base: str) -> str:
    """Deja el link de cada <a> escrito en el texto, al lado de su contenido.

    Daniel (2026-09-23): "en myhome hace rato no me sale uno puntual sino que
    me muestra como un general de aptos". Pasaba porque el HTML se volvía
    texto plano SIN los href: la IA leía precio, barrio y metraje pero no
    tenía de dónde sacar el link, y la tarjeta terminaba apuntando a la
    página de búsqueda. Con el link junto al aviso, el prompt de portales
    (que ya pide "url: link directo al inmueble si aparece en el texto") lo
    devuelve, y la tarjeta abre la ficha del inmueble."""
    from urllib.parse import urljoin

    def _abs(h: str) -> str:
        h = _html.unescape(h.strip())
        if h.startswith(("#", "javascript:", "mailto:", "tel:", "callto:", "wa.me", "whatsapp:")):
            return ""
        return urljoin(base, h)

    def _con_texto(m: re.Match) -> str:
        h = _abs(m.group(1))
        return f" {m.group(2)} [{h}] " if h else m.group(2)

    texto = _RE_ENLACE.sub(_con_texto, html_txt)
    return texto


def paginas_simples(url: str, cuantas: int = PAGINAS_SIMPLES) -> list[str]:
    """Páginas 1..N de una búsqueda en los sitios de lectura directa.

    Patrones verificados el 2026-09-23: WordPress/Houzez (coolhouse, debedout,
    aldana, myhome) paginan con /page/N/; el motor de topliving y bela con
    /pagina/N/; los Wasi (space, hook, vpandco) con &page=N."""
    if cuantas <= 1:
        return [url]
    if "/pagina/" in url:
        return [re.sub(r"/pagina/\d+", f"/pagina/{n}", url) for n in range(1, cuantas + 1)]
    if "/search?" in url:
        sin = re.sub(r"[?&]page=\d+", "", url)
        return [url] + [f"{sin}&page={n}" for n in range(2, cuantas + 1)]
    if any(d in url for d in ("coolhouse.com.co", "debedout.co", "inmobiliariaaldana.com",
                              "myhome.com.co")) and "?" not in url:
        base = url.rstrip("/")
        return [url] + [f"{base}/page/{n}/" for n in range(2, cuantas + 1)]
    return [url]


def leer_texto_simple(url: str, log=print) -> str:
    html_txt = _conservar_enlaces(_bajar(url), url)
    texto = _RE_TAGS.sub(" ", html_txt)
    texto = _html.unescape(texto)
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(l.strip() for l in texto.splitlines()))
    return texto.strip()
