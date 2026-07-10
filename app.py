"""Nova Scraping — App web.

Ejecuta:  streamlit run app.py

Flujo:
  1. Configuras las cuentas de Instagram a monitorear.
  2. Cargas tu Excel de clientes.
  3. Actualizas (trae posts de los últimos 30 días y los lee con IA).
  4. Ves las coincidencias por cliente y las compartes.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st

from src import clientes as mod_clientes
from src import config, db, matcher

st.set_page_config(page_title="Nova Scraping", page_icon="🏙️", layout="wide")

# ── Estilo visual NŌVA (terracota/café + detalles dorados, tarjetas suaves) ──
st.markdown("""
<style>
/* Pestañas más grandes y legibles */
.stTabs [data-baseweb="tab"] { font-size: 1.02rem; font-weight: 600; padding: 0.55rem 1.05rem; }
/* Tarjetas y contenedores con esquinas suaves */
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
details, div[data-testid="stExpander"] { border-radius: 12px; }
/* Botones y descargas redondeados */
.stButton > button, .stDownloadButton > button, .stLinkButton > a,
.stFormSubmitButton > button { border-radius: 10px; }
/* Números de las métricas en DORADO Nova */
div[data-testid="stMetricValue"] { color: #B08D57; font-weight: 700; }
/* Títulos con el café de la marca y detalle dorado */
h2, h3 { color: #6B4F3A; }
hr { border-color: #B08D57; }
/* Menos espacio muerto arriba */
.block-container { padding-top: 2.4rem; }

/* ══ CELULAR (pantallas angostas) ══════════════════════════ */
@media (max-width: 640px) {
  /* Aprovechar todo el ancho */
  .block-container { padding-left: 0.8rem; padding-right: 0.8rem; padding-top: 3.2rem; }
  /* Columnas: APILAR en vez de apretar (tarjetas legibles) */
  div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: 0.4rem; }
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
    flex: 1 1 100%; width: 100%; min-width: 100%;
  }
  /* …pero las métricas van de a DOS por fila (no gastan una fila entera) */
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:has(div[data-testid="stMetric"]) {
    flex: 1 1 47%; min-width: 47%;
  }
  /* Pestañas compactas y deslizables con el dedo */
  .stTabs [data-baseweb="tab-list"] { overflow-x: auto; flex-wrap: nowrap; }
  .stTabs [data-baseweb="tab"] { font-size: 0.88rem; padding: 0.45rem 0.6rem; white-space: nowrap; }
  /* Botones altos = fáciles de tocar con el dedo */
  .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
  .stLinkButton > a { min-height: 46px; }
  /* Fotos y tablas nunca se desbordan */
  img { max-width: 100% !important; height: auto; }
  div[data-testid="stMetricValue"] { font-size: 1.5rem; }
}
</style>
""", unsafe_allow_html=True)


# ── Caché de datos: evita repetir viajes a la base (Neon) en cada clic ──
@st.cache_resource
def _init_db_una_vez() -> bool:
    db.init_db()
    return True


_init_db_una_vez()


@st.cache_data(ttl=60, show_spinner=False)
def clientes_cacheados():
    """Clientes para LEER en la interfaz (se refresca solo al guardar o cada 60 s)."""
    return mod_clientes.cargar_guardados()


@st.cache_data(ttl=120, show_spinner=False)
def posts_cacheados():
    """Inmuebles para las coincidencias (se refresca tras scraping o cada 2 min)."""
    return db.posts_leidos()


@st.cache_data(ttl=60, show_spinner=False)
def contar_posts_cacheado() -> int:
    return db.contar_posts()


@st.cache_data(ttl=300, show_spinner=False)
def restringidas_cacheadas() -> list:
    try:
        return json.loads(db.leer_meta("cuentas_restringidas") or "[]")
    except json.JSONDecodeError:
        return []


# Cada vez que se GUARDAN clientes (desde cualquier botón), la caché se limpia
# sola para que la interfaz muestre siempre lo último.
if not getattr(mod_clientes.guardar_lista, "_con_cache", False):
    _guardar_lista_original = mod_clientes.guardar_lista

    def _guardar_lista_y_refrescar(lista):
        _guardar_lista_original(lista)
        st.cache_data.clear()

    _guardar_lista_y_refrescar._con_cache = True
    mod_clientes.guardar_lista = _guardar_lista_y_refrescar

EXTRAS_LEGIBLES = {
    "cuarto_servicio": "cuarto de servicio", "balcon": "balcón",
}


def bonito(extra: str) -> str:
    return EXTRAS_LEGIBLES.get(extra, extra)


def fecha_corte_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)).date().isoformat()


def dias_publicado(fecha_iso: str):
    """Días transcurridos desde la fecha de publicación (o None si no se sabe)."""
    if not fecha_iso:
        return None
    try:
        f = datetime.fromisoformat(str(fecha_iso)[:10]).date()
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - f).days


def color_publicacion(d) -> str:
    """Semáforo de publicaciones: 🟢 ≤3 días · 🟡 4-10 · 🟠 11-20 · 🔴 21-30."""
    if d is None:
        return ""
    if d <= 3:
        return "🟢"
    if d <= 10:
        return "🟡"
    if d <= 20:
        return "🟠"
    return "🔴"


def badge_frescura(fecha_iso: str) -> str:
    """Etiqueta con el semáforo de publicaciones (escala 3/10/20/30 días)."""
    d = dias_publicado(fecha_iso)
    if d is None:
        return ""
    emoji = color_publicacion(d)
    if d <= 0:
        return f"{emoji} Publicado hoy"
    return f"{emoji} Publicado hace {d} día{'s' if d != 1 else ''}"


def color_cliente(c) -> str:
    """Semáforo del cliente por fecha de inclusión: 🟢 ≤10 · 🟡 11-20 · 🟠 21-30 · 🔴 +30."""
    d = dias_publicado((c or {}).get("creado"))
    if d is None:
        return "🟢"
    if d <= 10:
        return "🟢"
    if d <= 20:
        return "🟡"
    if d <= 30:
        return "🟠"
    return "🔴"


# A partir de este puntaje consideramos que un inmueble es "afín" al cliente.
UMBRAL_AFIN = 70


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
    """Colapsa copias del mismo inmueble (misma huella) en una sola tarjeta.

    Se queda con la mejor copia (con fotos/videos > con más datos > más reciente) y
    recuerda los ids gemelos para que un descarte oculte TODAS las copias.
    """
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


def esc_md(t) -> str:
    """Evita que los $ del texto activen el modo matemático de Markdown."""
    return str(t).replace("$", "\\$")


def tabla_comparativa(cliente, post) -> str:
    """Tabla lado a lado: lo que ofrece el inmueble vs lo que pide el cliente."""
    filas = []
    op_post = matcher._inferir_operacion(post)
    op_cli = matcher._op_cliente((cliente or {}).get("operacion", ""))

    # Operación
    if op_cli:
        ok = "✅" if (not op_post or op_post == "ambos" or op_post == op_cli) else "❌"
        filas.append(("Operación", op_post or "sin dato", op_cli, ok))

    # Precio (en arriendo: canon + administración)
    pres = cliente.get("presupuesto_max")
    if pres:
        precio = post.get("precio")
        admin = post.get("administracion") or 0
        if not precio:
            filas.append(("Precio", "sin dato", f"hasta {matcher.formato_cop(pres)}", "⚠️ confirmar"))
        else:
            total = precio + admin if (admin and op_post == "arriendo") else precio
            mostrado = matcher.formato_cop(precio) + (
                f" + {matcher.formato_cop(admin)} admin" if admin and op_post == "arriendo" else "")
            rel = total / pres
            if rel <= 1.0:
                ver = "✅"
            elif rel <= 1.3:
                ver = f"⚠️ {round((rel - 1) * 100)}% encima"
            else:
                ver = "❌ muy caro"
            filas.append(("Precio", mostrado, f"hasta {matcher.formato_cop(pres)}", ver))

    # Habitaciones (exactas o rango)
    hmin = cliente.get("habitaciones_min")
    if hmin:
        hmax = (cliente.get("habitaciones_max")
                or (cliente.get("exclusiones") or {}).get("habitaciones_max") or hmin)
        pide = f"exactamente {hmin:g}" if hmax == hmin else f"{hmin:g} a {hmax:g}"
        habs = post.get("habitaciones")
        if habs is None:
            filas.append(("Habitaciones", "sin dato", pide, "⚠️ confirmar"))
        else:
            filas.append(("Habitaciones", f"{habs:g}", pide,
                          "✅" if hmin <= habs <= hmax else "❌"))

    # Metraje (rango efectivo, con techo implícito)
    if cliente.get("area_min") or cliente.get("area_max"):
        lo, hi = matcher._rango_area(cliente)
        pide = f"{lo:g}–{hi:g} m²" if hi < 1e9 else f"desde {lo:g} m²"
        area = post.get("area_m2")
        if not area:
            filas.append(("Metraje", "sin dato", pide, "⚠️ confirmar"))
        else:
            filas.append(("Metraje", f"{area:g} m²", pide,
                          "✅" if lo <= area <= hi else "❌ fuera de rango"))

    # Baños
    if cliente.get("banos_min"):
        banos = post.get("banos")
        pide = f"{cliente['banos_min']:g} o más"
        if banos is None:
            filas.append(("Baños", "sin dato", pide, "⚠️ confirmar"))
        else:
            filas.append(("Baños", f"{banos:g}", pide,
                          "✅" if banos >= cliente["banos_min"] else "❌"))

    # Ubicación
    if cliente.get("barrios") or cliente.get("zona"):
        p_ubi, _ = matcher._match_ubicacion(cliente, post)
        donde = post.get("barrio") or post.get("zona") or "sin dato"
        pide = ", ".join(cliente.get("barrios") or []) or (cliente.get("zona") or "")
        ver = "✅" if p_ubi >= 0.8 else ("⚠️ parecida" if p_ubi >= 0.5 else "❌ otra zona")
        filas.append(("Ubicación", donde, pide, ver))

    # Extras deseados
    if cliente.get("extras"):
        extras_post = set(post.get("extras") or [])
        pide = ", ".join(bonito(e) for e in cliente["extras"])
        presentes = [e for e in cliente["extras"] if e in extras_post]
        faltan = [e for e in cliente["extras"] if e not in extras_post]
        tiene = ", ".join(bonito(e) for e in presentes) or "no los menciona"
        ver = "✅" if not faltan else ("⚠️ falta: " + ", ".join(bonito(e) for e in faltan))
        filas.append(("Extras", tiene, pide, ver))

    # Antigüedad (si el cliente la pidió al afinar)
    ant_max = (cliente.get("exclusiones") or {}).get("antiguedad_max")
    if ant_max is not None:
        anos = post.get("antiguedad_anos")
        pide = "para estrenar" if ant_max == 0 else f"máx {ant_max:g} años"
        if anos is None:
            filas.append(("Antigüedad", "sin dato", pide, "⚠️ confirmar"))
        else:
            filas.append(("Antigüedad", f"{anos:g} años", pide,
                          "✅" if anos <= ant_max else "❌"))

    md = "| Criterio | 🏠 Este inmueble | 👤 Pide el cliente | ¿Cumple? |\n|---|---|---|---|\n"
    md += "\n".join(f"| **{c}** | {i} | {p} | {v} |" for c, i, p, v in filas)
    return esc_md(md)


def badge_afinidad(score: int) -> str:
    """Etiqueta de qué tan cerca está el inmueble del requerimiento del cliente."""
    if score >= 85:
        return "🟢 Muy afín"
    if score >= UMBRAL_AFIN:
        return "🟡 Afín"
    return "🟠 Menos afín"


def descargar_bytes(url: str):
    """Descarga un archivo (foto/video) y devuelve sus bytes, o None si falla."""
    try:
        r = requests.get(url, timeout=40)
        if r.ok and r.content:
            return r.content
    except Exception:  # noqa: BLE001
        pass
    return None


def slug_archivo(texto: str) -> str:
    base = "".join(c if c.isalnum() else "_" for c in (texto or "inmueble").lower())
    return base[:40].strip("_") or "inmueble"


def proceso_de(p: dict, estado: str, observaciones: str = "") -> dict:
    """Crea la ficha de seguimiento de un inmueble que se mueve para un cliente."""
    return {
        "post_id": p.get("id"),
        "ids_gemelos": p.get("ids_gemelos") or [p.get("id")],
        "huella": huella_inmueble(p),   # para ocultar también las copias en otras fuentes
        "resumen": p.get("resumen") or (p.get("caption", "")[:60]),
        "barrio": p.get("barrio", ""),
        "precio": p.get("precio"),
        "url": p.get("url", ""),
        "estado": estado,
        "observaciones": observaciones,
        "fecha": datetime.now(timezone.utc).date().isoformat(),
    }


def recalcular_preferencias(nombre: str) -> None:
    """Afina al cliente con la IA: aprende de sus descartes Y de los comentarios del broker."""
    if not config.ANTHROPIC_API_KEY:
        return
    cli = next((c for c in mod_clientes.cargar_guardados()
                if c["nombre"].lower() == nombre.lower()), None)
    if not cli:
        return
    try:
        from src import extractor
        señales = mod_clientes.aprendizajes_cliente(cli) + (cli.get("comentarios_ia") or [])
        prefs = extractor.aprender_preferencias(señales)
        mod_clientes.set_preferencias_evitar(nombre, prefs)
    except Exception:  # noqa: BLE001
        pass


def aplicar_exclusiones_de_texto(nombre: str, texto: str, cliente=None) -> dict:
    """Interpreta un texto (comentario o motivo de descarte) y aplica sus FILTROS DUROS
    (barrios, palabras, topes numéricos, antigüedad) al cliente. Devuelve lo que entendió.

    Así, lo que el broker escribe al descartar o afinar ANULA inmuebles similares —los
    actuales y los que lleguen— no solo les baja el puntaje.
    """
    vacio = {"excluir_barrios": [], "excluir_palabras": [], "limites": {},
             "tipo": None, "resumen": ""}
    if not config.ANTHROPIC_API_KEY or not (texto or "").strip():
        return vacio
    try:
        from src import extractor
        af = extractor.interpretar_afinacion(texto.strip(), cliente)
        if af["excluir_barrios"] or af["excluir_palabras"] or af["limites"] or af.get("tipo"):
            mod_clientes.agregar_exclusiones(
                nombre, af["excluir_barrios"], af["excluir_palabras"],
                af["limites"], af.get("tipo"))
        return af
    except Exception:  # noqa: BLE001
        return {**vacio, "error": True}


# Etiquetas visuales de los estados del embudo de seguimiento.
ESTADO_PROCESO_EMOJI = {
    "enviado": "📤 Enviado", "agendado": "📅 Agendado", "visitado": "👀 Visitado",
    "descartado": "❌ Descartado", "cerrado": "🟢 Cerrado",
}


def envios_cliente(c: dict) -> tuple[int, "int | None"]:
    """Cuántos inmuebles se le han ENVIADO a un cliente y hace cuántos días el último.

    Cuenta los procesos que NO son 'descartado' (esos no se enviaron, se rechazaron).
    """
    enviados = [pr for pr in (c.get("procesos") or []) if pr.get("estado") != "descartado"]
    dias = None
    fechas = [str(pr.get("fecha"))[:10] for pr in enviados if pr.get("fecha")]
    if fechas:
        try:
            ult = max(datetime.fromisoformat(f).date() for f in fechas)
            dias = (datetime.now(timezone.utc).date() - ult).days
        except ValueError:
            dias = None
    return len(enviados), dias


def cobertura_emoji(n: int) -> str:
    """🔴 sin cubrir · 🟡 poco cubierto · 🟢 bien cubierto."""
    return "🔴" if n == 0 else ("🟡" if n <= 2 else "🟢")


def comision_potencial(c: dict) -> float:
    """Comisión estimada de un cliente: la fijada, o estimada desde su presupuesto.

    Arriendo = 1 canon (≈ presupuesto). Venta = 3% del valor (≈ presupuesto).
    """
    com = float(c.get("comision") or 0)
    if com > 0:
        return com
    pres = float(c.get("presupuesto_max") or 0)
    if pres <= 0:
        return 0.0
    op = (c.get("operacion") or "venta").lower()
    return pres if op == "arriendo" else pres * COMISION_VENTA_PCT


def prob_cierre(c: dict) -> float:
    """Probabilidad estimada de cierre según qué tan avanzado va el negocio."""
    estados = {pr.get("estado") for pr in (c.get("procesos") or [])}
    if "visitado" in estados:
        return 0.50
    if "agendado" in estados:
        return 0.30
    if "enviado" in estados:
        return 0.15
    return 0.05  # aún no le hemos mandado nada


def render_procesos(c: dict) -> None:
    """Muestra y permite editar el embudo de inmuebles en seguimiento de un cliente."""
    procs = c.get("procesos") or []
    nombre = c["nombre"]
    # Los DESCARTADOS no estorban la vista, pero siguen guardados (así el inmueble
    # jamás vuelve a aparecer en Coincidencias para este cliente).
    activos_pr = [pr for pr in procs if pr.get("estado") != "descartado"]
    descartados_pr = [pr for pr in procs if pr.get("estado") == "descartado"]
    st.markdown(f"**📋 Inmuebles enviados y en proceso ({len(activos_pr)})**")
    if not activos_pr and not descartados_pr:
        st.caption("Marca inmuebles desde la pestaña Coincidencias para que entren aquí.")
        return
    opciones = mod_clientes.ESTADOS_PROCESO
    for pr in activos_pr:
        pid = pr.get("post_id", "")
        precio = matcher.formato_cop(pr.get("precio"))
        with st.container(border=True):
            st.caption(" · ".join(x for x in [pr.get("resumen", ""), pr.get("barrio", ""),
                                              precio] if x)
                       + (f"  ·  enviado el {pr.get('fecha')}" if pr.get("fecha") else ""))
            if pr.get("url"):
                st.markdown(f"🔗 [Ver el inmueble (solo tú — no compartas este link)]({pr['url']})")
            cc = st.columns([2, 4, 1, 1])
            estado = cc[0].selectbox(
                "Estado", opciones,
                index=opciones.index(pr["estado"]) if pr.get("estado") in opciones else 0,
                format_func=lambda e: ESTADO_PROCESO_EMOJI.get(e, e), key=f"pest_{nombre}_{pid}")
            obs = cc[1].text_input(
                "Observaciones (qué dijo el cliente)", value=pr.get("observaciones", ""),
                key=f"pobs_{nombre}_{pid}",
                help="Si descarta algo, anota por qué. La herramienta lo tendrá en cuenta.")
            if cc[2].button("💾", key=f"psave_{nombre}_{pid}", help="Guardar cambios"):
                mod_clientes.actualizar_proceso(nombre, pid, {"estado": estado, "observaciones": obs})
                recalcular_preferencias(nombre)
                st.toast("Guardado")
                st.rerun()
            if cc[3].button("↩️", key=f"prem_{nombre}_{pid}",
                            help="Sacar del proceso (vuelve a aparecer en coincidencias)"):
                mod_clientes.quitar_proceso(nombre, pid)
                st.rerun()
    if descartados_pr:
        with st.popover(f"🚫 {len(descartados_pr)} descartado(s) — ocultos, no se repetirán"):
            for pr in descartados_pr:
                pid = pr.get("post_id", "")
                d1, d2 = st.columns([5, 1])
                d1.caption(" · ".join(x for x in [pr.get("resumen", ""), pr.get("barrio", ""),
                                                  pr.get("observaciones", "")] if x))
                if d2.button("↩️", key=f"pundo_{nombre}_{pid}",
                             help="Deshacer el descarte (vuelve a Coincidencias)"):
                    mod_clientes.quitar_proceso(nombre, pid)
                    st.rerun()


def render_descargas(p: dict, prefijo: str) -> None:
    """Botones para descargar los archivos del inmueble (sin link, sin fuente).

    'prefijo' debe ser único por (cliente, inmueble) para no chocar claves cuando
    el mismo inmueble le sirve a varios clientes.
    """
    medios = p.get("media") or []
    st.caption("Descarga los archivos y compártelos directo a tu cliente — "
               "sin link y sin rastro de quién lo publicó.")
    base = slug_archivo(p.get("barrio") or p.get("resumen") or "inmueble")
    clave = f"prep_{p.get('id')}"  # los bytes se comparten por inmueble (da igual el cliente)
    if st.button("📥 Preparar archivos", key=f"btnprep_{prefijo}"):
        st.session_state[clave] = [
            {"tipo": med["tipo"], "data": descargar_bytes(med["url"])}
            for med in medios
        ]
    archivos = st.session_state.get(clave)
    if archivos:
        for i, a in enumerate(archivos, 1):
            if a["data"]:
                ext = "mp4" if a["tipo"] == "video" else "jpg"
                st.download_button(
                    f"⬇️ Descargar {a['tipo']} {i}", a["data"],
                    file_name=f"{base}_{i}.{ext}", key=f"dl_{prefijo}_{i}")
            else:
                st.caption(f"⚠️ {a['tipo']} {i}: el enlace expiró. Vuelve a "
                           "traer publicaciones para refrescarlo.")


def actualizar_publicaciones(log) -> None:
    """Trae TODO: Instagram + portales, y lee lo pendiente con IA.

    Cada fuente va por separado: si una falla, las demás siguen (igual que el robot).
    """
    from src import extractor, scraper, scraper_portales
    try:
        log("📷 Instagram…")
        scraper.scrapear_cuentas(config.leer_cuentas(), log=log)
    except Exception as e:  # noqa: BLE001
        log(f"⚠️ Instagram falló: {e} — sigo con los portales.")
    urls_p = config.leer_portales()
    if urls_p:
        try:
            log(f"🏠 Portales ({len(urls_p)})…")
            scraper_portales.scrapear_portales(urls_p, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ Portales fallaron: {e}")
    extractor.extraer_pendientes(log=log)


# ── Barra lateral ─────────────────────────────────────────────
st.sidebar.markdown("## 🏙️ Nova Scraping")
st.sidebar.caption("Tu radar de inmuebles — Nova Inmobiliaria")

st.sidebar.divider()
tiene_llaves = bool(config.APIFY_TOKEN and config.ANTHROPIC_API_KEY)
estado = "✅ Listas" if tiene_llaves else "❌ Faltan"
with st.sidebar.expander(f"🔑 Mis llaves — {estado}", expanded=not tiene_llaves):
    st.caption("Pega aquí tus llaves. Se guardan en tu computador y no hay que volver "
               "a hacerlo.")
    apify_in = st.text_input("Llave de Apify", value=config.APIFY_TOKEN,
                             type="password", placeholder="apify_api_...")
    claude_in = st.text_input("Llave de Claude (Anthropic)", value=config.ANTHROPIC_API_KEY,
                              type="password", placeholder="sk-ant-...")
    if st.button("💾 Guardar mis llaves"):
        config.guardar_llaves(apify_in, claude_in)
        st.success("¡Guardadas!")
        st.rerun()

st.sidebar.caption(
    "💾 Memoria: " + ("☁️ en la nube (permanente)" if config.DATABASE_URL
                      else "📍 local (se borra al reiniciar)"))

# ── Botón principal: correr el scraping ──
st.sidebar.divider()
st.sidebar.markdown("**▶️ Buscar inmuebles**")
correr = st.sidebar.button("🔄 Actualizar todo (IG + portales)",
                           type="primary", use_container_width=True)
st.sidebar.caption(f"📦 {contar_posts_cacheado()} inmuebles en memoria")

# ── Acción del botón: traer + leer publicaciones ──────────────
if correr:
    with st.status("Trayendo y leyendo Instagram y portales… "
                   "(puede tardar 5-10 min, no cierres la pestaña)", expanded=True) as estado_scrape:
        _lineas: list[str] = []

        def _log(m: str) -> None:
            _lineas.append(m)
            estado_scrape.write(m)

        try:
            actualizar_publicaciones(_log)
            st.cache_data.clear()   # que las pestañas vean lo nuevo de una
            estado_scrape.update(
                label=f"✅ ¡Listo! {db.contar_posts()} inmuebles en memoria. "
                      "Abre la pestaña 3️⃣ Coincidencias.", state="complete")
        except Exception as e:  # noqa: BLE001
            estado_scrape.update(label=f"⚠️ Ocurrió un problema: {e}", state="error")

# ===== INMUEBLE MANUAL → CLIENTES ============================
def cargar_inmuebles_manuales():
    try:
        return json.loads(db.leer_meta("inmuebles_manuales") or "[]")
    except json.JSONDecodeError:
        return []


def guardar_inmuebles_manuales(lista):
    db.guardar_meta("inmuebles_manuales", json.dumps(lista, ensure_ascii=False))


def _norm_link(u):
    """Normaliza un link para comparar (sin http, sin www, sin / final ni parámetros)."""
    u = (u or "").strip().lower()
    for p in ("https://", "http://"):
        if u.startswith(p):
            u = u[len(p):]
    if u.startswith("www."):
        u = u[4:]
    return u.split("?")[0].split("#")[0].rstrip("/")




# ── Pestañas ──────────────────────────────────────────────────
tab_fuentes, tab_clientes, tab_resultados, tab_crm = st.tabs(
    ["1️⃣ Fuentes", "2️⃣ Clientes", "3️⃣ Coincidencias", "4️⃣ CRM"]
)

# Etiquetas visuales de los estados del negocio.
ESTADOS_CRM = {"activo": "🟡 Activo", "ganado": "🟢 Ganado", "perdido": "🔴 Perdido"}

# Comisión de venta: 3% del valor total (ajustable a mano por negociación).
COMISION_VENTA_PCT = 0.03


def comision_sugerida(operacion: str, valor: float) -> int:
    """Comisión sugerida según el tipo de negocio.

    - Arriendo: el primer canon (sin administración) = el valor del canon.
    - Venta: 3% del valor total.
    """
    valor = float(valor or 0)
    if valor <= 0:
        return 0
    if (operacion or "").lower() == "arriendo":
        return round(valor)
    return round(valor * COMISION_VENTA_PCT)

# ===== 1. FUENTES ============================================
with tab_fuentes:
    st.subheader("📷 Perfiles que monitoreamos")

    with st.expander("➕ Agregar o quitar perfiles"):
        st.caption("Una cuenta por línea (con o sin @, o pegando el link del perfil). "
                   "Se guardan en la nube: NO se pierden al actualizar la app.")
        actuales = "\n".join(config.leer_cuentas())
        texto = st.text_area("Cuentas", value=actuales, height=200,
                             placeholder="arriendos_chapinero\ninmobiliaria_norte")
        if st.button("💾 Guardar cuentas"):
            config.guardar_cuentas([l for l in texto.splitlines() if l.strip()])
            st.success(f"Guardadas {len(config.leer_cuentas())} cuenta(s) en la nube. ✅")
            st.rerun()
    try:
        _restr_urls = restringidas_cacheadas()
    except json.JSONDecodeError:
        _restr_urls = []
    _restr_us = {config._solo_usuario(u) for u in _restr_urls if u}
    _cuentas = config.leer_cuentas()

    # Publicaciones vigentes (≤30 días) agrupadas por perfil, con semáforo.
    _posts_f = {}
    for p in posts_cacheados():
        d = dias_publicado(p.get("fecha"))
        if p.get("cuenta") in set(_cuentas) and d is not None and d <= 30:
            _posts_f.setdefault(p["cuenta"], []).append((d, p))

    st.caption("Semáforo: 🟢 hasta 3 días · 🟡 4-10 · 🟠 11-20 · 🔴 21-30 · "
               "✅ = ya lo tomaste para Coincidencias (no lo copies de nuevo).")
    _tomados = {_norm_link(x.get("link", "")) for x in cargar_inmuebles_manuales()
                if x.get("link")}
    _orden_ctas = sorted(_cuentas, key=lambda c: (-(len(_posts_f.get(c, []))),
                                                  c not in _restr_us))

    # 📄 PDF interno con TODAS las fuentes y sus links (para revisión manual).
    try:
        import base64 as _b64f
        from src import fichas as _fichas_f
        _logo_b64f = db.leer_meta("logo_png_b64")
        _logo_f = _b64f.b64decode(_logo_b64f) if _logo_b64f else None
        _stats_f = []
        for cta in _orden_ctas:
            _pubs_c = _posts_f.get(cta, [])
            _stats_f.append({"usuario": cta, "n_vigentes": len(_pubs_c),
                             "dias_ultima": min((d for d, _ in _pubs_c), default=None),
                             "restringida": cta in _restr_us})
        st.download_button(
            "📄 Descargar PDF con todas las fuentes y sus links (revisión manual)",
            _fichas_f.pdf_fuentes(_stats_f, config.leer_portales(), logo_png=_logo_f),
            f"fuentes_nova_{datetime.now(timezone.utc).date().isoformat()}.pdf",
            "application/pdf",
            help="Documento INTERNO: trae los links clicables de cada perfil y portal. "
                 "No lo compartas — revela tus fuentes.")
    except Exception as _e_pdf:  # noqa: BLE001 - el PDF nunca debe tumbar la pestaña
        st.caption(f"⚠️ No pude armar el PDF de fuentes: {_e_pdf}")

    for cta in _orden_ctas:
        pubs = sorted(_posts_f.get(cta, []), key=lambda t: t[0])
        if cta in _restr_us and not pubs:
            with st.expander(f"⚠️ @{cta} — Instagram no la deja leer (revísala manual)"):
                st.markdown(f"[Abrir el perfil @{cta} en Instagram]"
                            f"(https://www.instagram.com/{cta}/)")
            continue
        conteo = {"🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0}
        n_tomadas = 0
        for d, pp in pubs:
            conteo[color_publicacion(d)] += 1
            if pp.get("url") and _norm_link(pp["url"]) in _tomados:
                n_tomadas += 1
        resumen_c = "  ".join(f"{e}{n}" for e, n in conteo.items() if n)
        if n_tomadas:
            resumen_c += f"  ·  ✅{n_tomadas}"
        titulo = f"📷 @{cta} — {len(pubs)} publicación(es)" + (f"  ·  {resumen_c}" if resumen_c else "")
        if not pubs:
            titulo = f"📷 @{cta} — sin publicaciones vigentes"
        with st.expander(titulo):
            st.markdown(f"[Abrir el perfil @{cta} en Instagram](https://www.instagram.com/{cta}/)")
            for d, p in pubs:
                tomado = bool(p.get("url")) and _norm_link(p["url"]) in _tomados
                vendido = matcher.esta_vendido(p)
                linea = (("🚫 " if vendido else "") + ("✅ " if tomado else "")
                         + f"{color_publicacion(d)} **hace {d} día{'s' if d != 1 else ''}** — "
                         f"{esc_md((p.get('resumen') or p.get('caption', ''))[:75])}"
                         + ("  ·  **YA VENDIDO/ARRENDADO** (fuera del cruce)" if vendido else ""))
                if p.get("url"):
                    linea += f"  ·  [🔗 ver publicación]({p['url']})"
                if tomado:
                    linea += "  ·  **ya en Coincidencias**"
                st.markdown(linea)

    st.divider()
    with st.expander("🏠 Portales y sitios web (búsquedas que también leemos)"):
        st.caption("Pega la URL de tu **búsqueda** en el portal (una por línea). Ej: en Metrocuadrado "
                   "filtras *apartamentos en venta, zona norte* y copias el link de esa búsqueda. "
                   "La app abre la página con un navegador, baja los inmuebles y los lee con IA. "
                   "Funciona con Metrocuadrado, Fincaraíz y webs de agencias.")
        portales_actuales = "\n".join(config.leer_portales())
        texto_portales = st.text_area(
            "Portales (una URL por línea)", value=portales_actuales, height=130,
            placeholder="https://www.metrocuadrado.com/apartamentos/venta/bogota/chapinero/\n"
                        "https://www.fincaraiz.com.co/...\nhttps://www.myhome.com.co/...")
        if st.button("💾 Guardar portales"):
            config.guardar_portales([l.strip() for l in texto_portales.splitlines() if l.strip()])
            st.success(f"Guardados {len(config.leer_portales())} portal(es) en la nube. ✅")
            st.rerun()
        if config.leer_portales() and st.button("🏠 Leer inmuebles de portales", type="primary"):
            registro_p = st.empty()
            lineas_p: list[str] = []

            def log_p(msg: str) -> None:
                lineas_p.append(msg)
                registro_p.code("\n".join(lineas_p[-12:]))

            try:
                from src import scraper_portales
                n = scraper_portales.scrapear_portales(config.leer_portales(), log=log_p)
                st.cache_data.clear()   # que las pestañas vean lo nuevo de una
                st.success(f"¡Listo! Se agregaron {n} inmueble(s) de portales. "
                           "Míralos en 3️⃣ Coincidencias.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Problema leyendo portales: {e}")
        st.caption(f"⚙️ Tope de seguridad: {config.MAX_PAGINAS_PORTAL} páginas por corrida "
                   "(para que el gasto no se dispare). Cuesta unos centavos.")

        st.divider()
    st.subheader("🔄 Actualizar todo")
    st.caption(f"Trae los posts de los últimos {config.DIAS_RECIENTES} días "
               "y los lee con IA. Cada post se procesa una sola vez.")
    col1, col2 = st.columns(2)
    if col1.button("🔄 Actualizar todo (Instagram + portales)", type="primary"):
        registro = st.empty()
        lineas: list[str] = []

        def log(msg: str) -> None:
            lineas.append(msg)
            registro.code("\n".join(lineas[-12:]))

        try:
            actualizar_publicaciones(log)
            st.cache_data.clear()   # que las pestañas vean lo nuevo de una
            st.success("¡Actualización completa! (Instagram + portales)")
        except Exception as e:  # noqa: BLE001
            st.error(f"Ocurrió un problema: {e}")
    col2.metric("Posts en la caché", contar_posts_cacheado())

# ===== 2. CLIENTES ===========================================
EXTRAS_OPCIONES = [
    "estudio", "terraza", "balcon", "cuarto_servicio", "deposito",
    "parqueadero", "vista", "remodelado", "amoblado", "chimenea",
    "duplex", "penthouse",
]
ETIQUETA_EXTRA = {"cuarto_servicio": "cuarto de servicio", "balcon": "balcón"}

# Criterios que el cliente puede marcar como NO negociables (filtran duro).
OBLIGATORIOS_OPCIONES = matcher.OBLIGATORIOS_VALIDOS  # ["barrio","presupuesto",...]
ETIQUETA_OBLIGATORIO = {
    "barrio": "barrio/zona", "presupuesto": "presupuesto (no pasarse)",
    "habitaciones": "habitaciones mínimas", "banos": "baños mínimos",
    "metraje": "metraje (rango)", "extras": "extras deseados",
}

# Perfil de flexibilidad por cliente (qué tan acertado debe ser el inmueble).
FLEX_OPCIONES = matcher.FLEX_VALIDOS  # ["estricto","medio","flexible"]
ETIQUETA_FLEX = {
    "estricto": "🔒 Estricto — no cede, solo lo MUY acertado",
    "medio": "⚖️ Medio — equilibrado (recomendado)",
    "flexible": "🌊 Flexible — abierto a más opciones",
}
BADGE_FLEX = {"estricto": "🔒 Estricto", "medio": "⚖️ Medio", "flexible": "🌊 Flexible"}

# Prioridad del cliente: los 🔥 van SIEMPRE de primeros en toda la herramienta.
PRIORIDAD_OPCIONES = ["alta", "media", "baja"]
ETIQUETA_PRIORIDAD = {"alta": "🔥 Alta — con afán / responde rápido",
                      "media": "⭐ Media — ritmo normal",
                      "baja": "🌙 Baja — sin afán, explorando"}
BADGE_PRIORIDAD = {"alta": "🔥 Alta", "media": "⭐ Media", "baja": "🌙 Baja"}
ICONO_PRIORIDAD = {"alta": "🔥 ", "media": "", "baja": "🌙 "}
RANGO_PRIORIDAD = {"alta": 0, "media": 1, "baja": 2}


def prioridad_de(c) -> str:
    p = str((c or {}).get("prioridad") or "media").lower().strip()
    return p if p in PRIORIDAD_OPCIONES else "media"


def num_o_none(v):
    """Convierte 0/vacío en None (0 = 'no especificado')."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v and v > 0 else None


def parse_cop(texto):
    """Interpreta un presupuesto escrito como sea → número en pesos (o None).

    Entiende: 1'700.000.000, $1.700.000.000, 1700000000, 12M, 12MM, 800M-900M (toma el mayor).
    """
    import re
    s = str(texto or "").strip().lower().replace("$", "").replace(" ", "")
    if not s:
        return None
    if "-" in s:  # rango → tomar el mayor
        vals = [v for v in (parse_cop(p) for p in s.split("-")) if v]
        return max(vals) if vals else None
    m = re.match(r"^([\d.,']+)(mm|m)$", s)  # abreviatura de millones: 12m, 1.700m
    if m:
        num = m.group(1).replace("'", "")
        # Decimal de millón: "1,5M" o "2.5M" = 1.5 / 2.5 millones (1-2 dígitos tras el separador).
        md = re.match(r"^(\d+)[.,](\d{1,2})$", num)
        if md:
            return int(float(f"{md.group(1)}.{md.group(2)}") * 1_000_000)
        num = num.replace(".", "").replace(",", "")
        return int(num) * 1_000_000 if num.isdigit() else None
    digitos = "".join(ch for ch in s if ch.isdigit())
    return int(digitos) if digitos else None


def lista_a_texto(v) -> str:
    """['El Nogal','Rosales'] -> 'El Nogal, Rosales'."""
    if isinstance(v, list):
        return ", ".join(v)
    return str(v or "")


def texto_a_lista(v) -> list[str]:
    """'El Nogal, Rosales' -> ['El Nogal','Rosales']."""
    return [x.strip() for x in str(v or "").replace(";", ",").split(",") if x.strip()]


# Columnas de la "hoja de clientes" dentro de la app.
COLS_HOJA = ["⏳", "nombre", "telefono", "operacion", "flexibilidad", "prioridad", "barrios", "zona", "presupuesto",
             "area_min", "area_max", "habitaciones_min", "habitaciones_max", "banos_min",
             "extras", "obligatorios", "notas"]


def clientes_a_df(lista):
    """Convierte la lista de clientes (interna) en una tabla editable."""
    filas = []
    for c in lista:
        filas.append({
            "⏳": color_cliente(c),
            "nombre": c.get("nombre", ""),
            "telefono": c.get("telefono", ""),
            "operacion": c.get("operacion", "venta"),
            "flexibilidad": c.get("flexibilidad") or "medio",
            "prioridad": prioridad_de(c),
            "barrios": lista_a_texto(c.get("barrios")),
            "zona": c.get("zona", ""),
            "presupuesto": matcher.formato_cop(c.get("presupuesto_max")),
            "area_min": c.get("area_min"),
            "area_max": c.get("area_max"),
            "habitaciones_min": c.get("habitaciones_min"),
            "habitaciones_max": c.get("habitaciones_max"),
            "banos_min": c.get("banos_min"),
            "extras": lista_a_texto(c.get("extras")),
            "obligatorios": lista_a_texto(c.get("obligatorios")),
            "notas": c.get("notas", ""),
        })
    return pd.DataFrame(filas, columns=COLS_HOJA)


def refrescar_hoja_clientes():
    """Limpia la caché de datos para que la interfaz muestre lo último guardado."""
    st.cache_data.clear()


def coincide_busqueda(cliente, q):
    """True si el cliente coincide con el texto de búsqueda (ignora tildes/mayúsculas)."""
    q = mod_clientes._norm_nombre(q)  # minúsculas, sin tildes
    if not q:
        return True
    campos = [
        cliente.get("nombre", ""), cliente.get("telefono", ""), cliente.get("zona", ""),
        cliente.get("notas", ""), cliente.get("operacion", ""),
        " ".join(cliente.get("barrios") or []),
    ]
    return q in mod_clientes._norm_nombre(" ".join(str(c) for c in campos))


def excel_bytes(df) -> bytes:
    """Genera un archivo Excel (en memoria) a partir de la tabla."""
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    return buffer.getvalue()


def leer_tabla(archivo):
    """Lee un CSV o Excel subido y devuelve un DataFrame (probando codificaciones)."""
    nombre = archivo.name.lower()
    if nombre.endswith(".csv"):
        for enc in ("utf-8", "latin-1"):
            try:
                archivo.seek(0)
                return pd.read_csv(archivo, encoding=enc, sep=None, engine="python")
            except Exception:  # noqa: BLE001
                continue
        archivo.seek(0)
        return pd.read_csv(archivo)
    return pd.read_excel(archivo)


def _col_por_patron(df, patrones):
    """Encuentra la columna del DataFrame cuyo nombre contenga alguno de los patrones."""
    for col in df.columns:
        c = str(col).lower()
        if any(p in c for p in patrones):
            return col
    return None


def comparar_clientes_archivo(df, actuales):
    """Coteja el archivo contra los clientes de la herramienta (por nombre o teléfono).

    Devuelve (en_ambos, solo_archivo, solo_herramienta) con los nombres, o None si
    el archivo no tiene una columna de nombre reconocible.
    """
    col_nom = _col_por_patron(df, ["nombre", "name"])
    if col_nom is None:
        return None
    col_tel = _col_por_patron(df, ["tel", "cel", "phone", "movil", "móvil", "whats"])

    nombres_app = {mod_clientes._norm_nombre(c.get("nombre", "")): c.get("nombre", "")
                   for c in actuales}
    tels_app = {mod_clientes._norm_tel(c.get("telefono", "")): c.get("nombre", "")
                for c in actuales if mod_clientes._norm_tel(c.get("telefono", ""))}

    en_ambos: dict = {}
    solo_archivo: list = []
    for _, f in df.iterrows():
        nom = str(f.get(col_nom, "") or "").strip()
        if not nom or nom.lower() == "nan":
            continue
        nk = mod_clientes._norm_nombre(nom)
        tk = mod_clientes._norm_tel(str(f.get(col_tel, "") or "")) if col_tel is not None else ""
        match = nombres_app.get(nk) or (tels_app.get(tk) if tk else None)
        if match:
            en_ambos[mod_clientes._norm_nombre(match)] = match
        elif nom not in solo_archivo:
            solo_archivo.append(nom)
    solo_app = [c.get("nombre", "") for c in actuales
                if mod_clientes._norm_nombre(c.get("nombre", "")) not in en_ambos]
    return list(en_ambos.values()), solo_archivo, solo_app


def fila_a_texto(fila, columnas) -> str:
    """Convierte una fila ('columna: valor' por cada celda con dato) en un texto."""
    partes = []
    for col in columnas:
        valor = fila.get(col)
        if valor is None or (isinstance(valor, float) and pd.isna(valor)):
            continue
        texto = str(valor).strip()
        if texto and texto.lower() != "nan":
            partes.append(f"{col}: {texto}")
    return " | ".join(partes)


with tab_clientes:
    st.subheader("👥 Tus clientes y sus requerimientos")
    buscar_cli = st.text_input("🔍 Buscar cliente", key="buscar_cli",
                               placeholder="Escribe un nombre, barrio, teléfono o zona…")

    st.markdown("##### 🤖 Cuadro maestro — crea o edita clientes escribiendo")
    with st.expander("🤖 Escribe y la IA lo organiza (crear cliente nuevo o editar uno existente)",
                     expanded=True):
        _todos_m = clientes_cacheados()
        _lista_m = [c for c in _todos_m if coincide_busqueda(c, buscar_cli)] or _todos_m
        opciones_m = ["🆕 Cliente nuevo"] + [c["nombre"] for c in _lista_m]
        sel_m = st.selectbox(
            "¿Sobre quién trabajamos?", opciones_m, key="maestro_sel",
            help="Elige 🆕 para crear (puedes pegar VARIOS clientes a la vez) "
                 "o elige un cliente para editarlo con lo que escribas.")
        es_nuevo_m = sel_m == "🆕 Cliente nuevo"
        cliente_m = None if es_nuevo_m else next(
            (c for c in _lista_m if c["nombre"] == sel_m), None)
        if cliente_m:
            _r = []
            if cliente_m.get("operacion"): _r.append(cliente_m["operacion"])
            if cliente_m.get("presupuesto_max"):
                _r.append("hasta " + matcher.formato_cop(cliente_m["presupuesto_max"]))
            _hm = cliente_m.get("habitaciones_min"); _hx = cliente_m.get("habitaciones_max")
            if _hm: _r.append(f"{_hm:g} hab" if not _hx or _hx == _hm else f"{_hm:g}-{_hx:g} hab")
            if cliente_m.get("barrios"): _r.append(", ".join(cliente_m["barrios"][:4]))
            _dias_c = dias_publicado(cliente_m.get("creado"))
            st.caption("📌 Hoy: " + esc_md(" · ".join(_r) if _r else "sin datos aún")
                       + f"  ·  {ETIQUETA_PRIORIDAD.get(prioridad_de(cliente_m), '')}"
                       + f"  ·  {color_cliente(cliente_m)} cliente desde hace "
                       + (f"{_dias_c} día(s)" if _dias_c is not None else "hoy"))
        txt_m = st.text_area(
            "Escribe aquí (como te llegue: WhatsApp, correo, notas…)", height=140,
            key="maestro_txt",
            placeholder=("Ej: Andrés Gómez 300 123 4567, busca apto en las santas, "
                         "2 o 3 habitaciones, 900M, tiene afán.\n"
                         "(Puedes pegar varios clientes a la vez y los separo yo.)"
                         if es_nuevo_m else
                         f"Ej. de cambios para {sel_m}: súbele el presupuesto a 1.500 millones, "
                         "ahora quiere 3 habitaciones exactas, agrega el barrio Rosales y "
                         "tiene afán."))
        bm1, bm2 = st.columns(2)
        crear_m = bm1.button("➕ Crear cliente(s) nuevo(s)", use_container_width=True,
                             type="primary" if es_nuevo_m else "secondary")
        editar_m = bm2.button("✏️ Aplicar cambios al cliente elegido", use_container_width=True,
                              type="secondary" if es_nuevo_m else "primary",
                              disabled=es_nuevo_m,
                              help="Elige un cliente arriba para poder editarlo.")

        if crear_m:
            if not txt_m.strip():
                st.warning("Escribe primero los datos del cliente en el cuadro.")
            elif not config.ANTHROPIC_API_KEY:
                st.error("Falta la llave de Claude. Revisa «🔑 Mis llaves».")
            else:
                try:
                    with st.spinner("Interpretando con IA…"):
                        from src import extractor
                        nuevos = extractor.interpretar_texto_libre(txt_m, log=lambda m: None)
                    if not nuevos:
                        st.warning("No encontré datos de clientes en el texto. "
                                   "Incluye al menos un nombre.")
                    else:
                        existentes = mod_clientes.cargar_guardados()
                        antes = len(existentes)
                        nombres_previos = {mod_clientes._norm_nombre(c.get("nombre", ""))
                                           for c in existentes}
                        combinados = mod_clientes.fusionar_duplicados(existentes + nuevos)
                        mod_clientes.guardar_lista(combinados)
                        n_nuevos = len(combinados) - antes
                        nombres_nuevos = [c.get("nombre", "") for c in combinados
                                          if mod_clientes._norm_nombre(c.get("nombre", ""))
                                          not in nombres_previos]
                        st.success(f"Se interpretaron {len(nuevos)} cliente(s) → "
                                   f"🆕 {n_nuevos} nuevo(s)"
                                   + (": " + ", ".join(nombres_nuevos[:10]) if nombres_nuevos else "")
                                   + f" · 🔄 {max(0, len(nuevos) - n_nuevos)} actualizados. "
                                   f"👥 Total: {len(combinados)}. 🎉")
                        refrescar_hoja_clientes()
                        st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo interpretar: {e}")

        if editar_m and cliente_m:
            if not txt_m.strip():
                st.warning("Escribe primero los cambios en el cuadro.")
            elif not config.ANTHROPIC_API_KEY:
                st.error("Falta la llave de Claude. Revisa «🔑 Mis llaves».")
            else:
                try:
                    with st.spinner(f"Aplicando cambios a {sel_m} con IA…"):
                        from src import extractor
                        cambios = extractor.interpretar_edicion(txt_m, cliente_m)
                        af_m = aplicar_exclusiones_de_texto(sel_m, txt_m, cliente_m)
                    if not cambios and not (af_m.get("excluir_barrios") or
                                            af_m.get("excluir_palabras") or af_m.get("limites")):
                        st.warning("No entendí ningún cambio concreto. Sé más específico "
                                   "(ej: «presupuesto 1.500M», «2 o 3 habitaciones»).")
                    else:
                        colision = "nombre" in cambios and any(
                            c.get("nombre") != sel_m and
                            mod_clientes._norm_nombre(c.get("nombre", "")) ==
                            mod_clientes._norm_nombre(cambios["nombre"])
                            for c in mod_clientes.cargar_guardados())
                        if colision:
                            cambios.pop("nombre")
                            st.warning("No renombré: ya existe otro cliente con ese nombre.")
                        lista_g = mod_clientes.cargar_guardados()
                        aplicados = []
                        for c in lista_g:
                            if c.get("nombre") == sel_m:
                                for k, v in cambios.items():
                                    if v in (None, "", []):
                                        continue
                                    if k == "notas":
                                        c["notas"] = ((c.get("notas", "") + " | ")
                                                      if c.get("notas") else "") + str(v)
                                    else:
                                        c[k] = v
                                    aplicados.append(k)
                        mod_clientes.guardar_lista(lista_g)
                        detalle = ", ".join(dict.fromkeys(aplicados)) or "filtros"
                        st.success(f"✏️ Cambios aplicados a {cambios.get('nombre', sel_m)}: "
                                   f"{detalle}. ✅")
                        refrescar_hoja_clientes()
                        st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo editar: {e}")

        # Ajuste manual fino (campos exactos) y eliminar, sin salir del cuadro.
        with st.popover("⚙️ Ajuste manual (campos exactos) · 🗑️ eliminar", use_container_width=True):
            if not cliente_m:
                st.caption("Elige un cliente arriba (no 🆕) para ajustarlo a mano o eliminarlo.")
            else:
                with st.form("form_manual"):
                    g1, g2 = st.columns(2)
                    e_nombre = g1.text_input("Nombre", value=cliente_m.get("nombre", ""))
                    e_tel = g2.text_input("Teléfono", value=cliente_m.get("telefono", ""))
                    g1, g2, g3 = st.columns(3)
                    e_op = g1.selectbox("Compra / Arriendo", ["venta", "arriendo"],
                                        index=0 if (cliente_m.get("operacion") or "venta") == "venta" else 1)
                    _fx = cliente_m.get("flexibilidad") or "medio"
                    _fx = _fx if _fx in FLEX_OPCIONES else "medio"
                    e_flex = g2.selectbox("Flexibilidad", FLEX_OPCIONES,
                                          index=FLEX_OPCIONES.index(_fx),
                                          format_func=lambda f: ETIQUETA_FLEX.get(f, f))
                    e_prio = g3.selectbox("Prioridad", PRIORIDAD_OPCIONES,
                                          index=PRIORIDAD_OPCIONES.index(prioridad_de(cliente_m)),
                                          format_func=lambda x: BADGE_PRIORIDAD.get(x, x))
                    e_zona = st.text_input("Zona", value=cliente_m.get("zona", ""))
                    e_barrios = st.text_input("Barrios", value=lista_a_texto(cliente_m.get("barrios")))
                    e_pres = st.text_input("Presupuesto",
                                           value=matcher.formato_cop(cliente_m.get("presupuesto_max")))
                    g1, g2, g3, g4, g5 = st.columns(5)
                    e_amin = g1.number_input("Área mín", min_value=0, value=int(cliente_m.get("area_min") or 0))
                    e_amax = g2.number_input("Área máx", min_value=0, value=int(cliente_m.get("area_max") or 0))
                    e_hab = g3.number_input("Hab. mín", min_value=0, value=int(cliente_m.get("habitaciones_min") or 0))
                    e_hab_max = g4.number_input("Hab. máx", min_value=0,
                                                value=int(cliente_m.get("habitaciones_max") or 0),
                                                help="0 = exactamente las del mínimo.")
                    e_ban = g5.number_input("Baños", min_value=0, value=int(cliente_m.get("banos_min") or 0))
                    e_extras = st.multiselect(
                        "Extras", EXTRAS_OPCIONES, format_func=lambda x: ETIQUETA_EXTRA.get(x, x),
                        default=[x for x in (cliente_m.get("extras") or []) if x in EXTRAS_OPCIONES])
                    e_oblig = st.multiselect(
                        "🔒 No negociable", OBLIGATORIOS_OPCIONES,
                        format_func=lambda x: ETIQUETA_OBLIGATORIO.get(x, x),
                        default=[x for x in (cliente_m.get("obligatorios") or []) if x in OBLIGATORIOS_OPCIONES])
                    e_notas = st.text_input("Notas", value=cliente_m.get("notas", ""))
                    if st.form_submit_button("💾 Guardar cambios manuales", type="primary"):
                        clientes_g = mod_clientes.cargar_guardados()
                        colision = any(
                            c.get("nombre") != sel_m and
                            mod_clientes._norm_nombre(c.get("nombre", "")) ==
                            mod_clientes._norm_nombre(e_nombre)
                            for c in clientes_g)
                        if colision:
                            st.error(f"Ya existe otro cliente llamado «{e_nombre.strip()}». "
                                     "Usa un nombre distinto para no mezclarlos.")
                            st.stop()
                        for c in clientes_g:
                            if c.get("nombre") == sel_m:
                                c["nombre"] = e_nombre.strip()
                                c["telefono"] = "".join(ch for ch in e_tel if ch.isdigit())
                                c["operacion"] = e_op
                                c["flexibilidad"] = e_flex
                                c["prioridad"] = e_prio
                                c["barrios"] = texto_a_lista(e_barrios)
                                c["zona"] = e_zona.strip()
                                c["presupuesto_max"] = parse_cop(e_pres)
                                c["area_min"] = num_o_none(e_amin)
                                c["area_max"] = num_o_none(e_amax)
                                c["habitaciones_min"] = num_o_none(e_hab)
                                c["habitaciones_max"] = num_o_none(e_hab_max)
                                c["banos_min"] = num_o_none(e_ban)
                                c["extras"] = e_extras
                                c["obligatorios"] = e_oblig
                                c["notas"] = e_notas.strip()
                                break
                        mod_clientes.guardar_lista(clientes_g)
                        refrescar_hoja_clientes()
                        st.success(f"«{e_nombre.strip()}» actualizado. ✅")
                        st.rerun()
                if st.button("🗑️ Eliminar este cliente", key="man_del",
                             help="Quita este cliente de la lista (no se puede deshacer)."):
                    mod_clientes.eliminar(sel_m)
                    refrescar_hoja_clientes()
                    st.success(f"«{sel_m}» eliminado.")
                    st.rerun()

    # ── Importar con IA desde un archivo "como sea" ──────
    with st.expander("📁 SUBIR ARCHIVO MASIVO — CSV/Excel de Zoho (la IA coteja y une)"):
        st.caption("Sube tu lista tal como la tengas (CSV o Excel), aunque esté en "
                   "texto libre y con abreviaciones (ej. *“arriendo 12M, 2 alcobas, "
                   "Chapinero, mín 60 mts”*). La IA lee cada fila, la interpreta "
                   "(12M → $12.000.000) y la acomoda al formato. Revisa el resultado "
                   "en la tabla de abajo antes de guardar. Los que ya existan se ACTUALIZAN sin "
                   "duplicar (coteja por nombre o teléfono) y conservan su seguimiento CRM.")
        archivo_ia = st.file_uploader("Archivo de clientes (.csv o .xlsx)",
                                      type=["csv", "xlsx"], key="ia_uploader")
        comparar = importar = False
        if archivo_ia is not None:
            bcol1, bcol2 = st.columns(2)
            comparar = bcol1.button("🔍 Comparar sin guardar", use_container_width=True,
                                    help="Coteja el archivo contra tus clientes actuales y "
                                         "muestra quién falta en cada lado. No cambia NADA.")
            importar = bcol2.button("🤖 Interpretar y agregar con IA", type="primary",
                                    use_container_width=True)
        if comparar:
            try:
                dif = comparar_clientes_archivo(leer_tabla(archivo_ia), clientes_cacheados())
                if dif is None:
                    st.warning("No encontré una columna de nombre en el archivo. "
                               "Usa directo el botón de importar con IA.")
                else:
                    en_ambos, solo_csv, solo_app = dif
                    m1, m2, m3 = st.columns(3)
                    m1.metric("🟢 En ambos lados", len(en_ambos))
                    m2.metric("📄 Solo en el archivo", len(solo_csv))
                    m3.metric("🖥️ Solo en la herramienta", len(solo_app))
                    d1, d2 = st.columns(2)
                    with d1:
                        st.markdown("**📄 Vienen en el archivo y FALTAN aquí:**")
                        st.markdown("\n".join(f"- {n}" for n in solo_csv) or "*— ninguno —*")
                        if solo_csv:
                            st.caption("👉 Dale a «🤖 Interpretar y agregar» y entran "
                                       "sin duplicar los demás.")
                    with d2:
                        st.markdown("**🖥️ Están aquí y NO vienen en el archivo:**")
                        st.markdown("\n".join(f"- {n}" for n in solo_app) or "*— ninguno —*")
                        if solo_app:
                            st.caption("Tranquilo: importar NUNCA los borra. Si alguno sobra, "
                                       "elimínalo desde el editor ✏️.")
            except Exception as e:  # noqa: BLE001
                st.error(f"No pude comparar el archivo: {e}")
        if importar:
            if not config.ANTHROPIC_API_KEY:
                st.error("Falta la llave de Claude. Revisa «🔑 Mis llaves» en la barra lateral.")
            else:
                try:
                    df_in = leer_tabla(archivo_ia)
                    cols = list(df_in.columns)
                    textos = [fila_a_texto(fila, cols) for _, fila in df_in.iterrows()]
                    registro = st.empty()
                    lineas: list[str] = []

                    def log_ia(msg: str) -> None:
                        lineas.append(msg)
                        registro.code("\n".join(lineas[-10:]))

                    from src import extractor
                    nuevos = extractor.interpretar_clientes(textos, log=log_ia)
                    existentes = mod_clientes.cargar_guardados()
                    nombres_previos = {mod_clientes._norm_nombre(c.get("nombre", ""))
                                       for c in existentes}
                    antes = len(existentes)
                    # Une duplicados (mismo nombre o teléfono) tomando el más completo,
                    # y conserva el seguimiento CRM de los que ya existían.
                    combinados = mod_clientes.fusionar_duplicados(existentes + nuevos)
                    mod_clientes.guardar_lista(combinados)
                    n_nuevos = len(combinados) - antes
                    n_actualizados = max(0, len(nuevos) - n_nuevos)
                    nombres_nuevos = [c.get("nombre", "") for c in combinados
                                      if mod_clientes._norm_nombre(c.get("nombre", ""))
                                      not in nombres_previos]
                    detalle_nuevos = (": " + ", ".join(nombres_nuevos[:10])
                                      + (" …" if len(nombres_nuevos) > 10 else "")
                                      ) if nombres_nuevos else ""
                    st.success(f"¡Listo! Se leyeron {len(nuevos)} fila(s) del archivo → "
                               f"🆕 **{n_nuevos} cliente(s) NUEVO(s)**{detalle_nuevos} · "
                               f"🔄 {n_actualizados} ya existían (actualizados sin duplicar, "
                               f"CRM conservado) · 👥 Total: {len(combinados)} clientes.")
                    refrescar_hoja_clientes()
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo procesar el archivo: {e}")

    # Tabla con DOS columnas editables (prioridad y flexibilidad): clic en la celda,
    # eliges y se guarda al instante. Las demás columnas se cambian en el cuadro 🤖.
    st.markdown("##### 📋 Tu lista completa")
    st.caption("⏳ Antigüedad: 🟢 0-10 días · 🟡 11-20 · 🟠 21-30 · 🔴 +30.  ·  ✏️ Las columnas "
               "**prioridad** y **flexibilidad** se editan AQUÍ MISMO (clic en la celda); "
               "el resto, en el cuadro maestro 🤖.")
    todos = clientes_cacheados()
    lista_ver = [c for c in todos if coincide_busqueda(c, buscar_cli)]

    _PRIO_VISTA = {"alta": "🔥 alta", "media": "⭐ media", "baja": "🌙 baja"}
    _FLEX_VISTA = {"estricto": "🔒 estricto", "medio": "⚖️ medio", "flexible": "🌊 flexible"}
    df_tabla = clientes_a_df(lista_ver)
    if not df_tabla.empty:
        df_tabla["prioridad"] = df_tabla["prioridad"].map(lambda v: _PRIO_VISTA.get(v, "⭐ media"))
        df_tabla["flexibilidad"] = df_tabla["flexibilidad"].map(
            lambda v: _FLEX_VISTA.get(v, "⚖️ medio"))
    _ver_t = st.session_state.get("tabla_ver", 0)
    _key_t = f"tabla_clientes_{_ver_t}"
    st.data_editor(
        df_tabla, key=_key_t, hide_index=True, use_container_width=True,
        disabled=[c for c in df_tabla.columns if c not in ("prioridad", "flexibilidad")],
        column_config={
            "⏳": st.column_config.TextColumn("⏳", pinned=True, width="small"),
            "nombre": st.column_config.TextColumn(
                "nombre", pinned=True,
                help="Fijado a la izquierda: al deslizar la tabla (sobre todo en el "
                     "celular) siempre ves de quién es la fila."),
            "prioridad": st.column_config.SelectboxColumn(
                "prioridad", options=list(_PRIO_VISTA.values()), required=True,
                help="🔥 con afán · ⭐ normal · 🌙 sin afán — ordena toda la herramienta."),
            "flexibilidad": st.column_config.SelectboxColumn(
                "flexibilidad", options=list(_FLEX_VISTA.values()), required=True,
                help="🔒 solo lo muy acertado · ⚖️ equilibrado · 🌊 abierto a más opciones."),
        })
    _delta_t = (st.session_state.get(_key_t) or {}).get("edited_rows") or {}
    if _delta_t:
        _lista_g = mod_clientes.cargar_guardados()
        _cambiados = []
        for _idx, _cambios in _delta_t.items():
            try:
                _nom = df_tabla.iloc[int(_idx)]["nombre"]
            except Exception:  # noqa: BLE001
                continue
            for _c_g in _lista_g:
                if _c_g.get("nombre") == _nom:
                    if "prioridad" in _cambios:
                        _c_g["prioridad"] = str(_cambios["prioridad"]).split()[-1]
                    if "flexibilidad" in _cambios:
                        _c_g["flexibilidad"] = str(_cambios["flexibilidad"]).split()[-1]
                    _cambiados.append(_nom)
        if _cambiados:
            mod_clientes.guardar_lista(_lista_g)
            st.session_state["tabla_ver"] = _ver_t + 1
            st.toast("✅ Guardado: " + ", ".join(dict.fromkeys(_cambiados)))
            st.rerun()
    if buscar_cli:
        st.caption(f"Mostrando {len(lista_ver)} de {len(todos)} clientes que coinciden "
                   f"con «{buscar_cli}».")

    c1, c2, c3 = st.columns(3)
    # Ficha elegante para compartir con otras inmobiliarias: clientes ACTIVOS,
    # nombre anonimizado ("Alfonso R.") y sin teléfonos ni notas privadas.
    try:
        import base64 as _b64
        from src import fichas
        _logo_b64 = db.leer_meta("logo_png_b64")
        _logo = _b64.b64decode(_logo_b64) if _logo_b64 else None
        c1.download_button(
            "📄 Ficha para aliados (PDF)", fichas.generar_pdf(todos, logo_png=_logo),
            f"busquedas_nova_{datetime.now(timezone.utc).date().isoformat()}.pdf",
            "application/pdf", use_container_width=True,
            help="Diseño listo para enviar a otras inmobiliarias: búsquedas activas con "
                 "nombre anonimizado (Alfonso R.), sin teléfonos ni notas.")
    except Exception as e:  # noqa: BLE001
        c1.caption(f"PDF no disponible: {e}")
    # Mismo orden que la ficha: ventas primero, de mayor a menor presupuesto.
    _orden_aliados = sorted(
        todos, key=lambda c: ((c.get("operacion") or "venta") == "arriendo",
                              -(c.get("presupuesto_max") or 0)))
    c2.download_button(
        "⬇️ Copia de respaldo (Excel)", excel_bytes(clientes_a_df(_orden_aliados)),
        "clientes.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    with c3.popover("⬆️ Restaurar desde Excel", use_container_width=True):
        archivo = st.file_uploader("Sube tu copia (.xlsx)", type=["xlsx"])
        if archivo is not None and st.button("Restaurar ahora"):
            try:
                # Conserva el seguimiento CRM (procesos, exclusiones, comisiones) de los
                # clientes que ya existían: el Excel solo trae los requerimientos.
                mod_clientes.guardar_lista(
                    mod_clientes.fusionar_crm(mod_clientes.cargar_clientes(archivo)))
                st.success("¡Restaurado! (El seguimiento CRM de tus clientes se conservó.)")
                refrescar_hoja_clientes()
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo leer el Excel: {e}")

    # ── Logo para el membrete del PDF (se sube una vez y queda en la nube) ──
    with st.expander("🎨 Logo de Nova en el PDF (súbelo una sola vez)"):
        st.caption("Sube el logo (PNG) y aparecerá en el membrete de la ficha para aliados. "
                   "Queda guardado para siempre; mientras tanto, el PDF usa el logo en letras doradas.")
        logo_up = st.file_uploader("Logo (PNG)", type=["png"], key="logo_pdf")
        if logo_up is not None and st.button("💾 Guardar logo"):
            import base64 as _b64g
            db.guardar_meta("logo_png_b64", _b64g.b64encode(logo_up.read()).decode())
            st.cache_data.clear()
            st.success("¡Logo guardado! Descarga el PDF de nuevo y ya sale con tu logo.")
        if db.leer_meta("logo_png_b64"):
            st.caption("✅ Ya tienes un logo guardado (subir otro lo reemplaza).")

    st.session_state["clientes"] = clientes_cacheados()
    cols_pie = st.columns([2, 1])
    cols_pie[0].caption(f"👥 {len(st.session_state['clientes'])} cliente(s) guardado(s).")
    if cols_pie[1].button("🧹 Unir duplicados", use_container_width=True,
                          help="Junta clientes repetidos (mismo nombre o teléfono) en uno solo."):
        actuales = mod_clientes.cargar_guardados()
        unidos = mod_clientes.fusionar_duplicados(actuales)
        mod_clientes.guardar_lista(unidos)
        quitados = len(actuales) - len(unidos)
        st.success(f"Listo: {quitados} duplicado(s) unido(s)." if quitados
                   else "No había duplicados. ✅")
        refrescar_hoja_clientes()
        st.rerun()

# ===== 3. RESULTADOS =========================================
with tab_resultados:
    st.subheader("✨ Coincidencias por cliente")
    clientes = st.session_state.get("clientes", [])

    if st.session_state.get("flash_manual"):
        st.success(st.session_state.pop("flash_manual"))

    # ── Inmueble manual: lo que encuentres a mano entra al MISMO cruce ──
    with st.expander("➕ Meter un inmueble manual (link + descripción) — te digo al instante "
                     "a quién le puede servir"):
        st.caption("Para inmuebles que la máquina no ve (WhatsApp, aliados, cuentas ⚠️). "
                   "Pega la descripción y el link; al guardarlo te digo **de una** con qué "
                   "clientes hace match. Si el link ya está en el catálogo de Fuentes, basta "
                   "pegarlo (reutilizo datos y fotos).")
        with st.form("inmueble_manual", clear_on_submit=True):
            desc_man = st.text_area(
                "Descripción del inmueble", height=110,
                placeholder="Ej: Apartamento en venta en El Nogal, 120 m², 3 habitaciones, "
                            "2 baños, $1.800 millones, remodelado, con vista y parqueadero.")
            link_man = st.text_input("Link (evita repetidos)", placeholder="https://…")
            agregar_man = st.form_submit_button("➕ Agregar al cruce", type="primary")
        if agregar_man:
            dup_link = next(
                (x for x in cargar_inmuebles_manuales()
                 if link_man.strip() and _norm_link(x.get("link", "")) == _norm_link(link_man)),
                None)
            # Si el link viene del catálogo de Fuentes, ya tenemos TODO del scraping
            # (descripción, datos leídos por IA y hasta fotos): cero re-trabajo.
            scr = None
            if link_man.strip():
                _nl = _norm_link(link_man)
                scr = next((pp for pp in posts_cacheados()
                            if pp.get("url") and _norm_link(pp["url"]) == _nl), None)
            if not desc_man.strip() and not scr:
                st.warning("Pega la descripción, o un link que esté en el catálogo de Fuentes.")
            elif dup_link:
                st.warning(f"⚠️ Ese link **ya estaba incluido** (lo agregaste el "
                           f"{dup_link.get('fecha', '?')}). No lo dupliqué.")
            elif desc_man.strip() and not config.ANTHROPIC_API_KEY:
                st.error("Falta la llave de Claude. Revisa «🔑 Mis llaves».")
            else:
                try:
                    hoy_iso = datetime.now(timezone.utc).date().isoformat()
                    if desc_man.strip():
                        with st.spinner("Leyendo el inmueble…"):
                            from src import extractor
                            datos_man = extractor.interpretar_inmueble(desc_man.strip())
                        texto_man = desc_man.strip()
                        fecha_man = (scr or {}).get("fecha") or hoy_iso
                    else:
                        campos = ("es_inmueble", "operacion", "tipo", "barrio", "zona",
                                  "direccion", "area_m2", "precio", "administracion",
                                  "habitaciones", "banos", "parqueaderos", "estrato",
                                  "antiguedad_anos", "extras", "resumen")
                        datos_man = {k: scr.get(k) for k in campos}
                        datos_man["es_inmueble"] = True
                        texto_man = scr.get("caption", "")
                        fecha_man = scr.get("fecha") or hoy_iso
                    if datos_man.get("es_inmueble") is False:
                        st.warning("Eso no parece un inmueble concreto; revisa la descripción.")
                    else:
                        item_man = {
                            "id": "m_" + hashlib.md5((texto_man + link_man).encode("utf-8")).hexdigest()[:16],
                            "texto": texto_man, "link": link_man.strip(),
                            "fecha": fecha_man,          # fecha real de publicación si se conoce
                            "agregado": hoy_iso,          # cuándo lo tomaste tú (para el 🆕)
                            "cuenta": (scr or {}).get("cuenta") or "manual",
                            "imagen": (scr or {}).get("imagen", ""),
                            "media": (scr or {}).get("media") or [],
                            "datos": datos_man,
                        }
                        lista_man = [x for x in cargar_inmuebles_manuales()
                                     if x.get("id") != item_man["id"]]
                        lista_man.insert(0, item_man)
                        guardar_inmuebles_manuales(lista_man)
                        # 🎯 Respuesta inmediata: ¿con qué clientes hace match?
                        post_eval = {**datos_man, "id": item_man["id"],
                                     "caption": texto_man, "url": link_man.strip(),
                                     "fecha": fecha_man, "agregado": hoy_iso,
                                     "cuenta": item_man["cuenta"],
                                     "imagen": item_man["imagen"],
                                     "media": item_man["media"]}
                        ganadores = []
                        try:
                            res_ya = matcher.cruzar(clientes, [post_eval], score_minimo=70)
                            _h_eval = huella_inmueble(post_eval)
                            for _cli in clientes:
                                _ms = res_ya.get(_cli["nombre"]) or []
                                if not _ms:
                                    continue
                                _h_oc = {pr.get("huella")
                                         for pr in (_cli.get("procesos") or [])
                                         if pr.get("huella")}
                                if (post_eval["id"] in mod_clientes.ids_en_proceso(_cli)
                                        or (_h_eval and _h_eval in _h_oc)):
                                    continue    # ya se lo enviaste/descartaste antes
                                ganadores.append((_ms[0]["score"],
                                                  prioridad_de(_cli), _cli["nombre"]))
                        except Exception:  # noqa: BLE001 - el aviso nunca daña el guardado
                            pass
                        ganadores.sort(reverse=True)
                        base_g = ("✅ Inmueble agregado"
                                  + (" (tomado del catálogo, con sus fotos)"
                                     if scr and not desc_man.strip() else "")
                                  + ". En Fuentes quedó marcado con ✅.")
                        if ganadores:
                            lista_g = "  ·  ".join(
                                f"{ICONO_PRIORIDAD.get(pr_g, '')}**{n_g}** ({s_g}%)"
                                for s_g, pr_g, n_g in ganadores[:8])
                            extra_g = (f" y {len(ganadores) - 8} más"
                                       if len(ganadores) > 8 else "")
                            st.session_state["flash_manual"] = (
                                base_g + f"\n\n🎯 **Le puede servir a:** {lista_g}{extra_g}. "
                                "Ábrelos abajo para enviar o descartar.")
                        else:
                            st.session_state["flash_manual"] = (
                                base_g + "\n\n😕 Por ahora **no hace match** con ningún "
                                "cliente (revisa el ⚙️ umbral si te sorprende). Queda "
                                "guardado: si entra un cliente que encaje, aparecerá solo.")
                        st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo interpretar: {e}")
        _mans = cargar_inmuebles_manuales()
        if _mans:
            with st.popover(f"🗂️ Tus inmuebles manuales ({len(_mans)}) — administrar"):
                for item in _mans:
                    d_it = item.get("datos") or {}
                    mm1, mm2 = st.columns([5, 1])
                    mm1.markdown(f"**{esc_md(d_it.get('resumen') or item.get('texto', '')[:60])}**"
                                 f" · {item.get('fecha', '')}"
                                 + (f" · [link]({item['link']})" if item.get("link") else ""))
                    if mm2.button("🗑️", key=f"delm_{item.get('id')}"):
                        guardar_inmuebles_manuales(
                            [x for x in cargar_inmuebles_manuales()
                             if x.get("id") != item.get("id")])
                        st.rerun()

    # Cruce HÍBRIDO: el catálogo del scraping + lo que agregas a mano. La máquina
    # propone, tú decides (nada se envía sin tu clic). Los duplicados se unifican.
    posts = list(posts_cacheados())
    for item in cargar_inmuebles_manuales():
        d_it = item.get("datos") or {}
        if d_it.get("es_inmueble") is False:
            continue
        posts.append({**d_it, "id": item.get("id"), "caption": item.get("texto", ""),
                      "url": item.get("link", ""), "fecha": item.get("fecha", ""),
                      "agregado": item.get("agregado") or item.get("fecha", ""),
                      "cuenta": item.get("cuenta") or "manual",
                      "imagen": item.get("imagen", ""), "media": item.get("media") or []})

    if not clientes:
        st.warning("Primero carga tus clientes en la pestaña **2️⃣ Clientes**.")
    elif not posts:
        st.warning("Aún no hay inmuebles. Ve a **1️⃣ Fuentes** y dale a "
                   "**🔄 Traer y leer publicaciones**, o agrega uno manual arriba.")
    else:
        # Los controles finos viven plegados: los valores de fábrica funcionan bien
        # y así la pantalla queda limpia para lo importante (los inmuebles).
        with st.expander("⚙️ Ajustes de búsqueda (opcional — los valores de fábrica están bien)"):
            fcol1, fcol2 = st.columns(2)
            dias_venta = fcol1.slider("📅 Frescura VENTA (días)", 7, 180, 30, 1,
                                      help="Muestra inmuebles EN VENTA publicados en los últimos N días.")
            dias_arriendo = fcol2.slider("📅 Frescura ARRIENDO (días)", 3, 90, 20, 1,
                                         help="Los arriendos se toman más rápido: por defecto 20 días.")
            c1, c2, c3, c4 = st.columns(4)
            score_min = c1.slider("Coincidencia mínima (%)", 0, 100, 70, 5,
                                  help="Por defecto 70%: solo opciones afines. Bájalo si quieres "
                                       "ver más opciones (ej. 60 muestra las de datos incompletos).")
            flex_precio = c2.slider("Presupuesto: tope arriba (%)", 0, 40, 20, 5,
                                    help="Cuánto POR ENCIMA del presupuesto se permite. "
                                         "Por defecto 20% (margen acordado).")
            piso_precio = c3.slider("Presupuesto: precio mínimo (%)", 0, 100, 80, 5,
                                    help="Margen hacia ABAJO: 80% = muestra hasta 20% más "
                                         "barato. Menos que eso es otro segmento y se oculta.")
            flex_area = c4.slider("Flexibilidad en metraje (%)", 0, 40, 20, 5,
                                  help="Cuánto por fuera del rango de m² se permite. "
                                       "Por defecto 20% (margen acordado).")
            st.caption("Guía de lectura — Afinidad: 🟢 muy afín (≥85%) · 🟡 afín (≥70%) · "
                       "🟠 menos afín. Frescura: 🟢 ≤3 días · 🟡 4-10 · 🟠 11-20 · 🔴 21-30. "
                       "Cada cliente muestra primero lo MÁS afín; los enviados/descartados "
                       "desaparecen solos.")

        def _pasa_frescura(p):
            if str(p.get("id", "")).startswith(("m_", "asig_")):
                return True    # lo metiste tú a mano: no caduca por frescura
            d = dias_publicado(p.get("fecha"))
            if d is None:
                return True
            op = matcher._inferir_operacion(p)      # explícita o deducida por precio
            limite = dias_arriendo if op == "arriendo" else dias_venta
            return d <= limite

        posts = [p for p in posts if _pasa_frescura(p)]
        # El mismo inmueble visto en varias fuentes (IG + portal) se muestra UNA vez.
        n_antes = len(posts)
        posts = dedup_posts(posts)
        n_duplicados = n_antes - len(posts)

        # 🆕 Qué entró a la herramienta en las últimas 24 horas.
        _hoy_dt = datetime.now(timezone.utc).date()
        _fechas_nuevo = {_hoy_dt.isoformat(), (_hoy_dt - timedelta(days=1)).isoformat()}

        def es_nuevo(p) -> bool:
            return (p.get("agregado") or "") in _fechas_nuevo

        n_nuevos = sum(1 for p in posts if es_nuevo(p))
        if n_nuevos:
            st.success(f"🆕 **{n_nuevos} inmueble(s) entraron en las últimas 24 horas.** "
                       "En las tarjetas los reconoces por la insignia 🆕.")
        resultados = matcher.cruzar(
            clientes, posts, score_minimo=score_min,
            flex_precio=flex_precio / 100, flex_area=flex_area / 100,
            piso_precio=piso_precio / 100,
        )

        # Ocultar inmuebles que ya están en el embudo de seguimiento del cliente
        # (por id, por sus copias gemelas en otras fuentes, y por huella).
        ocultos = {c["nombre"]: mod_clientes.ids_en_proceso(c) for c in clientes}
        huellas_oc = {c["nombre"]: {pr.get("huella") for pr in (c.get("procesos") or [])
                                    if pr.get("huella")} for c in clientes}
        aprendizajes = {c["nombre"]: mod_clientes.aprendizajes_cliente(c) for c in clientes}
        oblig_map = {c["nombre"]: (c.get("obligatorios") or []) for c in clientes}
        flex_map = {c["nombre"]: (c.get("flexibilidad") or "medio") for c in clientes}
        prio_map = {c["nombre"]: prioridad_de(c) for c in clientes}
        com_map = {c["nombre"]: (c.get("comentarios_ia") or []) for c in clientes}
        cli_map = {c["nombre"]: c for c in clientes}
        en_embudo_n = {}
        for nombre in list(resultados):
            ids_oc = ocultos.get(nombre, set())
            h_oc = huellas_oc.get(nombre, set())
            antes_n = len(resultados[nombre])
            resultados[nombre] = [
                m for m in resultados[nombre]
                if not (set(m["post"].get("ids_gemelos") or [m["post"].get("id")]) & ids_oc)
                and (huella_inmueble(m["post"]) not in h_oc
                     if huella_inmueble(m["post"]) else True)
            ]
            # Cuántos pasaron el cruce pero están ocultos porque YA los trabajaste.
            en_embudo_n[nombre] = antes_n - len(resultados[nombre])

        # 📌 Inmuebles asignados a dedo por el broker (por link): entran directo a las
        # coincidencias del cliente, por encima del cruce, la frescura y el umbral.
        posts_por_link = {}
        for _p in posts:
            _nl = _norm_link(_p.get("url") or "")
            if _nl:
                posts_por_link.setdefault(_nl, _p)
        for _c in clientes:
            _nom = _c["nombre"]
            for _a in (_c.get("asignados") or []):
                _nl = _norm_link(_a.get("link") or "")
                if not _nl:
                    continue
                _pa = posts_por_link.get(_nl)
                if _pa is None:
                    _pa = {"id": "asig_" + hashlib.md5(_nl.encode()).hexdigest()[:14],
                           "url": (_a.get("link") or "").strip(),
                           "fecha": _a.get("fecha"),
                           "caption": _a.get("nota") or "",
                           "resumen": _a.get("nota")
                           or "Inmueble que asignaste por link (fuera del catálogo)",
                           "operacion": _c.get("operacion"), "es_inmueble": True}
                _idsp = set(_pa.get("ids_gemelos") or [_pa.get("id")])
                if _idsp & ocultos.get(_nom, set()):
                    continue          # ya lo envió o lo descartó: respeta el embudo
                _h = huella_inmueble(_pa)
                if _h and _h in huellas_oc.get(_nom, set()):
                    continue
                _ya = resultados.setdefault(_nom, [])
                _ids_ya = set()
                for _m in _ya:
                    _ids_ya |= set(_m["post"].get("ids_gemelos") or [_m["post"].get("id")])
                if _idsp & _ids_ya:
                    continue          # el cruce ya lo trae: no duplicar la tarjeta
                _ya.insert(0, {"score": 100, "post": _pa, "asignado": True,
                               "razones_ok": ["📌 Lo asignaste tú por link"],
                               "razones_no": []})

        total = sum(len(v) for v in resultados.values())
        st.caption(f"🔎 {len(posts)} publicaciones analizadas · **{total} coincidencias pendientes**"
                   + (f" · {n_duplicados} duplicado(s) unificados" if n_duplicados else "")
                   + f" · frescura: venta {dias_venta}d / arriendo {dias_arriendo}d")

        # ── Cuadro: inmuebles potenciales por cliente (cobertura) ──
        resumen = []
        for nombre, matches in resultados.items():
            n = len(matches)
            n_portal = sum(1 for m in matches if es_portal_post(m["post"]))
            n_man = sum(1 for m in matches if str(m["post"].get("id", "")).startswith("m_"))
            resumen.append({
                "Cliente": nombre,
                "Prioridad": BADGE_PRIORIDAD.get(prio_map.get(nombre, "media"), "⭐ Media"),
                "Perfil": BADGE_FLEX.get(flex_map.get(nombre, "medio"), "⚖️ Medio"),
                "📷 Instagram": n - n_portal - n_man,
                "🏠 Portales": n_portal,
                "🖊️ Tuyos": n_man,
                "Total": n,
                "Cobertura": "🔴 Buscar más" if n == 0 else ("🟡 Pocos" if n <= 2 else "🟢 Bien cubierto"),
            })
        # Primero los 🔥 y, dentro de cada prioridad, los más flojos de inventario.
        resumen.sort(key=lambda r: (RANGO_PRIORIDAD.get(prio_map.get(r["Cliente"], "media"), 1),
                                    r["Total"]))
        with st.expander("📋 Cobertura por cliente (cuántos inmuebles potenciales hay)", expanded=True):
            st.dataframe(pd.DataFrame(resumen), hide_index=True, use_container_width=True)
            st.caption("Ordenado de menos a más. Los 🔴/🟡 son a quienes conviene "
                       "buscarles más (incluso manual o ampliando criterios con los deslizadores).")

        # Orden de los clientes: 🔥 primero; a igual prioridad, más coincidencias arriba.
        def _clave_coin(kv):
            return (RANGO_PRIORIDAD.get(prio_map.get(kv[0], "media"), 1), -len(kv[1]))

        # Ventas y arriendos separados (ventas primero, igual que en el CRM).
        _orden_coin = []
        for _tit_g, _es_arr in [("🔑 Compra / Venta", False), ("🏠 Arriendo", True)]:
            _grupo = sorted(
                [kv for kv in resultados.items()
                 if ((cli_map.get(kv[0], {}).get("operacion") or "venta")
                     == "arriendo") == _es_arr],
                key=_clave_coin)
            if _grupo:
                _orden_coin.append(("header", f"{_tit_g} ({len(_grupo)} cliente(s))"))
                _orden_coin.extend(("cliente", kv) for kv in _grupo)
        for _tipo_it, _it in _orden_coin:
            if _tipo_it == "header":
                st.divider()
                st.markdown(f"##### {_it}")
                continue
            nombre, matches = _it
            icono_p = ICONO_PRIORIDAD.get(prio_map.get(nombre, "media"), "")
            _n_emb = en_embudo_n.get(nombre, 0)
            with st.expander(f"{icono_p}👤 {nombre} — {len(matches)} coincidencia(s)"
                             + (f"  ·  🗂️ {_n_emb} ya trabajada(s)" if _n_emb else ""),
                             expanded=(nombre == st.session_state.get("cliente_abierto"))):
                if _n_emb:
                    st.caption(f"🗂️ {_n_emb} candidato(s) que pasan el cruce están ocultos "
                               "porque ya los enviaste o descartaste — nada se pierde: "
                               "míralos (o recupéralos) en la pestaña CRM.")
                perfil = flex_map.get(nombre, "medio")
                st.caption(f"Perfil de búsqueda: **{ETIQUETA_FLEX.get(perfil, perfil)}**"
                           + ("  ·  con este perfil solo verás inmuebles muy acertados."
                              if perfil == "estricto" else ""))
                oblig = oblig_map.get(nombre, [])
                if oblig:
                    st.info("🔒 No negociable (filtra duro): "
                            + " · ".join(ETIQUETA_OBLIGATORIO.get(o, o) for o in oblig))
                aprend = aprendizajes.get(nombre, [])
                if aprend:
                    st.warning("🧠 Lo que NO le gustó a este cliente (tenlo en cuenta): "
                               + " · ".join(aprend))

                # ── Afinar con IA: comentarios libres del broker sobre este cliente ──
                exc_cli = (cli_map.get(nombre, {}).get("exclusiones") or {})
                exc_barrios = exc_cli.get("barrios") or []
                exc_palabras = exc_cli.get("palabras") or []
                caps_txt = []
                if exc_cli.get("area_max"):
                    caps_txt.append(f"máx {exc_cli['area_max']:g} m²")
                if exc_cli.get("area_min"):
                    caps_txt.append(f"mín {exc_cli['area_min']:g} m²")
                if exc_cli.get("precio_max"):
                    caps_txt.append(f"máx {matcher.formato_cop(exc_cli['precio_max'])}")
                if exc_cli.get("habitaciones_min"):
                    caps_txt.append(f"mín {exc_cli['habitaciones_min']:g} hab")
                if exc_cli.get("habitaciones_max"):
                    caps_txt.append(f"máx {exc_cli['habitaciones_max']:g} hab")
                if exc_cli.get("banos_min"):
                    caps_txt.append(f"mín {exc_cli['banos_min']:g} baños")
                if exc_cli.get("antiguedad_max"):
                    caps_txt.append(f"máx {exc_cli['antiguedad_max']:g} años de construido")
                if exc_cli.get("tipo"):
                    caps_txt.append(f"solo {exc_cli['tipo']}")
                hay_exc = bool(exc_barrios or exc_palabras or caps_txt)
                if hay_exc:
                    partes_x = []
                    if exc_barrios:
                        partes_x.append("barrios: " + ", ".join(exc_barrios))
                    if exc_palabras:
                        partes_x.append("palabras: " + ", ".join(exc_palabras))
                    if caps_txt:
                        partes_x.append("topes: " + ", ".join(caps_txt))
                    st.error("🚫 Anulando (filtro duro) — " + "  ·  ".join(partes_x))
                with st.popover("📌 Asignarle un inmueble por link", use_container_width=True):
                    st.caption("Pega el link de un inmueble que TÚ ves que le sirve — uno que "
                               "encontraste por fuera o uno que la herramienta no le asignó. "
                               "Queda fijado arriba de sus coincidencias. (Si es externo y "
                               "quieres ficha completa con datos, mejor usa «➕ Agregar un "
                               "inmueble manual» arriba.)")
                    _lk = st.text_input("Link del inmueble", key=f"asig_lk_{nombre}",
                                        placeholder="https://…")
                    _nota_a = st.text_input("Nota para ti (opcional)", key=f"asig_nota_{nombre}",
                                            placeholder="ej: me lo pasó un aliado · 3 hab · $1.400M")
                    if st.button("📌 Asignar", key=f"asig_btn_{nombre}"):
                        if not (_lk or "").strip():
                            st.warning("Pega primero el link.")
                        elif _norm_link(_lk) in {_norm_link(a.get("link") or "") for a in
                                                 (cli_map.get(nombre, {}).get("asignados") or [])}:
                            st.warning("Ese link ya está asignado a este cliente.")
                        else:
                            mod_clientes.asignar_inmueble(nombre, {
                                "link": _lk.strip(), "nota": _nota_a.strip(),
                                "fecha": datetime.now(timezone.utc).date().isoformat()})
                            st.toast(f"📌 Asignado a {nombre}")
                            st.session_state["cliente_abierto"] = nombre
                            st.rerun()
                with st.popover("🤖 Afinar con IA — ¿los resultados no son buenos?",
                                use_container_width=True):
                    st.caption("Escribe qué está mal o qué buscas. La IA **anula de una** lo que no "
                               "cumpla y agrega criterios nuevos. Ej: *«solo apartamentos, nada de "
                               "casas»*, *«exactamente 2 habitaciones»*, *«nada después de la calle "
                               "100»*, *«no más de 160 m²»*, *«máx 6 años de construido»*. "
                               "Aplica solo a **este** cliente.")
                    res_prev = st.session_state.get(f"afin_res_{nombre}")
                    if res_prev:
                        st.success("✨ " + res_prev)
                    coms_prev = com_map.get(nombre, [])
                    if coms_prev:
                        st.caption("📝 Ya le dijiste: " + "  ·  ".join(f"«{c}»" for c in coms_prev[-4:]))
                    txt = st.text_area("Tu comentario", key=f"afinar_txt_{nombre}", height=90,
                                       placeholder="ej: nada después de la 100; prioriza vista y que sea remodelado")
                    ccol1, ccol2 = st.columns(2)
                    if ccol1.button("✨ Afinar este cliente", key=f"afinar_btn_{nombre}",
                                    type="primary", use_container_width=True):
                        if not config.ANTHROPIC_API_KEY:
                            st.error("Falta la llave de Claude para afinar. Revisa «🔑 Mis llaves».")
                        elif not txt.strip():
                            st.warning("Escribe un comentario primero.")
                        else:
                            with st.spinner("Afinando con IA… (unos segundos)"):
                                mod_clientes.agregar_comentario_ia(nombre, txt.strip())
                                af = aplicar_exclusiones_de_texto(nombre, txt, cli_map.get(nombre))
                                recalcular_preferencias(nombre)   # ajuste suave
                            if af.get("error"):
                                st.error("⚠️ No pude aplicar el filtro (problema técnico con la "
                                         "IA). Tu comentario quedó guardado: vuelve a intentar "
                                         "con «✨ Afinar» en un momento.")
                            else:
                                st.session_state[f"afin_res_{nombre}"] = (
                                    af["resumen"] or "Lo tomé en cuenta para afinar la búsqueda.")
                                st.toast(f"✨ Afiné la búsqueda de {nombre}")
                                st.session_state["cliente_abierto"] = nombre
                                st.rerun()
                    if hay_exc and ccol2.button(
                            "♻️ Quitar exclusiones", key=f"afinar_clr_{nombre}",
                            use_container_width=True,
                            help="Vuelve a mostrar los inmuebles que habías anulado."):
                        mod_clientes.limpiar_exclusiones(nombre)
                        st.session_state.pop(f"afin_res_{nombre}", None)
                        st.toast(f"♻️ Quité las exclusiones de {nombre}")
                        st.session_state["cliente_abierto"] = nombre
                        st.rerun()

                if not matches:
                    st.write("Sin coincidencias por ahora.")
                    continue
                # Siempre de MAYOR a menor afinidad: lo más cercano al cliente, primero.
                matches = sorted(matches, key=lambda m: m["score"], reverse=True)
                sep_mostrado = False
                for m in matches:
                    if not sep_mostrado and m["score"] < UMBRAL_AFIN:
                        st.markdown("———  🔽 _Opciones menos afines (más lejos de lo que pidió)_  ———")
                        sep_mostrado = True
                    p = m["post"]
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"**{esc_md(p.get('resumen') or p.get('caption', '')[:80])}**")
                        info = []
                        if p.get("operacion"): info.append(p["operacion"].capitalize())
                        if p.get("barrio"): info.append(p["barrio"])
                        if p.get("precio"): info.append(matcher.formato_cop(p["precio"]))
                        if p.get("area_m2"): info.append(f"{p['area_m2']:g} m²")
                        if p.get("habitaciones") is not None: info.append(f"{p['habitaciones']:g} hab")
                        if p.get("banos") is not None: info.append(f"{p['banos']:g} baños")
                        st.caption(" · ".join(info))
                        _nuevo_txt = "🆕 **Entró hoy/ayer a la herramienta** · " if es_nuevo(p) else ""
                        if p.get("fecha_estimada"):
                            d_visto = dias_publicado(p.get("fecha"))
                            st.markdown(f"{_nuevo_txt}**👁️ En el portal, visto hace "
                                        f"{d_visto if d_visto is not None else '?'} día(s)** "
                                        "· el aviso no muestra su fecha real de publicación")
                        else:
                            frescura = badge_frescura(p.get("fecha"))
                            if frescura or _nuevo_txt:
                                st.markdown(f"{_nuevo_txt}**{frescura}**")
                        if p.get("otras_fuentes"):
                            st.caption("♻️ También publicado en: " + ", ".join(p["otras_fuentes"]))
                        if m["razones_ok"]:
                            st.markdown("✅ " + " · ".join(m["razones_ok"]))
                        if m["razones_no"]:
                            st.markdown("⚠️ " + " · ".join(m["razones_no"]))
                        st.caption(f"Fuente (solo tú): {fuente_post(p)} · "
                                   + ("visto el " if p.get("fecha_estimada") else "publicado ")
                                   + str(p.get("fecha", "")))
                        # Foto de portada (vista previa)
                        if p.get("imagen"):
                            st.image(p["imagen"], width=260)
                        # Texto LIMPIO para compartir: sin link y sin la fuente.
                        extras_txt = ", ".join(bonito(e) for e in p.get("extras", []))
                        mensaje = (
                            f"🏙️ {p.get('resumen') or 'Apartamento'}\n"
                            + (f"📍 {p.get('barrio','')}\n" if p.get('barrio') else "")
                            + (f"📐 {p.get('area_m2'):g} m²  " if p.get('area_m2') else "")
                            + (f"🛏️ {p.get('habitaciones'):g} hab  " if p.get('habitaciones') is not None else "")
                            + (f"🛁 {p.get('banos'):g} baños" if p.get('banos') is not None else "")
                            + "\n"
                            + (f"✨ {extras_txt}\n" if extras_txt else "")
                            + (f"💰 {matcher.formato_cop(p['precio'])}\n" if p.get('precio') else "")
                            + "\nEscríbeme para más información y agendar visita. — Nova Inmobiliaria"
                        )
                        a0, a1, a2 = st.columns(3)
                        with a0:
                            with st.popover("⚖️ Comparativo", use_container_width=True):
                                st.markdown(f"**⚖️ Este inmueble vs lo que pide {nombre}**")
                                st.markdown(tabla_comparativa(cli_map.get(nombre, {}), p))
                                st.caption("⚠️ confirmar = el aviso no trae ese dato: "
                                           "verifícalo antes de enviarlo.")
                        with a1:
                            with st.popover("📲 Texto para compartir", use_container_width=True):
                                st.caption("Listo para tu cliente: solo datos + tu marca, sin link ni fuente.")
                                st.code(mensaje, language=None)
                        with a2:
                            if p.get("media"):
                                with st.popover("📥 Descargar foto/video", use_container_width=True):
                                    render_descargas(p, f"{nombre}_{p.get('id', 'x')}")
                    with c2:
                        if m.get("asignado"):
                            st.metric("Coincidencia", "📌")
                            st.markdown("**Asignado por ti**")
                        else:
                            st.metric("Coincidencia", f"{m['score']}%")
                            st.markdown(f"**{badge_afinidad(m['score'])}**")
                        if p.get("url"):
                            st.link_button("🔗 Ver original (solo tú)", p["url"],
                                           use_container_width=True,
                                           help="Para que TÚ verifiques el inmueble. No lo compartas: revela la fuente.")
                        if st.button("📤 Marcar enviado", key=f"env_{nombre}_{p.get('id','x')}",
                                     help=f"Pasa al seguimiento de {nombre} (lo ves en el CRM)",
                                     use_container_width=True):
                            mod_clientes.agregar_proceso(nombre, proceso_de(p, "enviado"))
                            st.toast(f"📤 En seguimiento de {nombre}")
                            st.session_state["cliente_abierto"] = nombre
                            st.rerun()
                        with st.popover("🚫 Descartar", use_container_width=True):
                            st.caption("Escribe **por qué** no le sirvió. La IA lo convierte en filtro "
                                       "para este cliente y **anula inmuebles parecidos** (los de "
                                       "ahora y los que lleguen). Ej: *«primer piso»*, *«nada después "
                                       "de la 100»*, *«muy pequeño, menos de 100 m²»*, *«es casa, "
                                       "busca apto»*.")
                            obs = st.text_input(
                                "¿Por qué no le sirvió?",
                                key=f"obsdesc_{nombre}_{p.get('id','x')}",
                                placeholder="ej: primer piso · sin parqueadero · muy lejos · muy pequeño")
                            if st.button("Confirmar descarte",
                                         key=f"cdesc_{nombre}_{p.get('id','x')}"):
                                mod_clientes.agregar_proceso(
                                    nombre, proceso_de(p, "descartado", obs))
                                if obs.strip() and mod_clientes.es_motivo_administrativo(obs):
                                    # "repetido / ya enviado / ya vendido" habla del AVISO,
                                    # no del cliente: solo se oculta, sin crear filtros.
                                    st.toast("🚫 Oculto (repetido/no disponible). "
                                             "No creé filtros: el motivo no habla de los "
                                             "gustos del cliente.")
                                elif obs.strip():
                                    # Le damos a la IA los datos del inmueble descartado como
                                    # referencia (así "muy grande/pequeño/caro" se vuelve un tope).
                                    ctx = (f"El inmueble que descartó tenía: "
                                           f"{p.get('area_m2') or '?'} m², {p.get('habitaciones') or '?'} hab, "
                                           f"{matcher.formato_cop(p.get('precio')) or 'precio n/d'}, "
                                           f"barrio {p.get('barrio') or '?'}. "
                                           f"Motivo por el que NO le sirvió: {obs.strip()}")
                                    with st.spinner("Descartando y aprendiendo del motivo…"):
                                        af = aplicar_exclusiones_de_texto(nombre, ctx, cli_map.get(nombre))
                                        recalcular_preferencias(nombre)
                                    if af.get("error"):
                                        st.warning("Descartado ✅, pero NO pude convertir el motivo "
                                                   "en filtro (falla técnica de la IA). Vuelve a "
                                                   "escribirlo en «🤖 Afinar con IA» más tarde.")
                                    dur = []
                                    if af["excluir_barrios"]:
                                        dur.append("barrios: " + ", ".join(af["excluir_barrios"]))
                                    if af["excluir_palabras"]:
                                        dur.append("palabras: " + ", ".join(af["excluir_palabras"]))
                                    if af["limites"]:
                                        dur.append("topes")
                                    if af.get("tipo"):
                                        dur.append(f"solo {af['tipo']}")
                                    st.toast("🚫 Descartado. " + ("🔒 Filtro aplicado — "
                                             + " · ".join(dur) if dur else "Lo tendré en cuenta."))
                                else:
                                    st.toast(f"🚫 Descartado para {nombre}")
                                st.session_state["cliente_abierto"] = nombre
                                st.rerun()
                    st.divider()

        # Descarga de todos los matches en un CSV.
        filas = []
        for nombre, matches in resultados.items():
            for m in matches:
                p = m["post"]
                filas.append({
                    "cliente": nombre, "score": m["score"],
                    "operacion": p.get("operacion"), "barrio": p.get("barrio"),
                    "area_m2": p.get("area_m2"), "precio": p.get("precio"),
                    "habitaciones": p.get("habitaciones"), "banos": p.get("banos"),
                    "cuenta": p.get("cuenta"), "fecha": p.get("fecha"), "url": p.get("url"),
                })
        if filas:
            csv = pd.DataFrame(filas).to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Descargar coincidencias (CSV)", csv,
                               "coincidencias.csv", "text/csv")


# ===== 4. CRM (seguimiento) ==================================
with tab_crm:
    st.subheader("CRM — Seguimiento de clientes")
    crm_clientes = [
        {**c, "estado": c.get("estado", "activo"), "visitas": c.get("visitas", 0),
         "inmuebles_enviados": c.get("inmuebles_enviados", []),
         "notas_crm": c.get("notas_crm", "")}
        for c in st.session_state.get("clientes", [])
    ]

    if not crm_clientes:
        st.warning("Primero carga tus clientes en la pestaña **2️⃣ Clientes**.")
    else:
        # ── Resumen (tablero) ────────────────────────────────
        n_activos = sum(1 for c in crm_clientes if c["estado"] == "activo")
        n_ganados = sum(1 for c in crm_clientes if c["estado"] == "ganado")
        n_perdidos = sum(1 for c in crm_clientes if c["estado"] == "perdido")
        visitas_tot = sum(int(c.get("visitas") or 0) for c in crm_clientes)
        # Cobertura: cuántos envíos por cliente (procesos no descartados).
        cobertura = {c["nombre"]: envios_cliente(c) for c in crm_clientes}
        enviados_tot = sum(n for n, _ in cobertura.values())

        com_ganadas = sum(float(c.get("comision") or 0)
                          for c in crm_clientes if c["estado"] == "ganado")
        # En juego = lo pactado a mano si existe; si no, la comisión estimada por el
        # presupuesto (antes mostraba $0 junto a una proyección millonaria: confundía).
        com_en_juego = sum(float(c.get("comision") or 0) or comision_potencial(c)
                           for c in crm_clientes if c["estado"] == "activo")

        # Lo esencial a la vista; el detalle, en un cajón (menos ruido visual).
        m = st.columns(4)
        m[0].metric("👥 Clientes", len(crm_clientes))
        m[1].metric("🟡 Activos", n_activos)
        m[2].metric("💰 Comisiones ganadas", matcher.formato_cop(com_ganadas) or "$0")
        m[3].metric("⏳ En juego (activos)", matcher.formato_cop(com_en_juego) or "$0",
                    help="Suma de lo pactado (si lo fijaste) o la comisión estimada por el "
                         "presupuesto de cada cliente activo.")

        # ── Alerta de cobertura: a quién tenemos descuidado ──
        activos_list = [c for c in crm_clientes if c["estado"] == "activo"]
        sin_cubrir = [c["nombre"] for c in activos_list if cobertura[c["nombre"]][0] == 0]
        sin_movimiento = [c["nombre"] for c in activos_list
                          if cobertura[c["nombre"]][0] > 0 and (cobertura[c["nombre"]][1] or 0) >= 14]
        descuidados = sorted(set(sin_cubrir + sin_movimiento))
        if descuidados:
            st.warning("👀 **Ojo, tienes clientes descuidados:** " + " · ".join(descuidados)
                       + ". Mándales opciones para no perderlos.")

        with st.expander("📊 Más indicadores"):
            g = st.columns(6)
            g[0].metric("🟢 Ganados", n_ganados)
            g[1].metric("🔴 Perdidos", n_perdidos)
            g[2].metric("👣 Visitas", visitas_tot)
            g[3].metric("📤 Envíos", enviados_tot)
            g[4].metric("🔴 Sin cubrir", len(sin_cubrir),
                        help="Clientes activos con 0 envíos.")
            g[5].metric("🟢 Bien cubiertos",
                        sum(1 for c in activos_list if cobertura[c["nombre"]][0] >= 3),
                        help="Activos con 3+ inmuebles enviados.")

        # ── Proyección cerrando TODOS los activos: directo y compartido con aliado ──
        ganados_c = [c for c in crm_clientes if c["estado"] == "ganado"]

        def _proyeccion(op_filtro):
            """(ya ganado, potencial cerrando todos los activos) para un tipo de operación."""
            gan = sum(comision_potencial(c) for c in ganados_c
                      if (c.get("operacion") or "venta").lower() == op_filtro)
            pot = sum(comision_potencial(c) for c in activos_list
                      if (c.get("operacion") or "venta").lower() == op_filtro)
            return gan, pot

        with st.expander("📈 Proyección de ganancias (cerrando todos los activos)"):
            share = st.slider("Si el cliente es en alianza con otro broker, ¿qué % te queda?",
                              10, 100, 50, 5,
                              help="Muchos clientes llegan en alianza; ahí la comisión se reparte. "
                                   "Por defecto 50/50.")
            col_arr, col_ven = st.columns(2)
            for col, titulo, op_filtro in [(col_arr, "🏠 Arriendo", "arriendo"),
                                           (col_ven, "🔑 Venta", "venta")]:
                gan, pot = _proyeccion(op_filtro)
                with col:
                    st.markdown(f"**{titulo}**")
                    st.metric("🟢 Ya ganado", matcher.formato_cop(gan) or "$0")
                    st.metric("🙋 Cerrando todo — cliente directo", matcher.formato_cop(pot) or "$0",
                              help="Si cierras todos los activos y son clientes tuyos (100%).")
                    st.metric(f"🤝 Cerrando todo — compartido ({share}%)",
                              matcher.formato_cop(pot * share / 100) or "$0",
                              help="Si esos negocios fueran en alianza y te queda ese %.")
            st.caption("Arriendo = 1 canon · Venta = 3% del valor. Solo cuenta clientes ACTIVOS "
                       "(los perdidos no entran).")


        st.divider()
        fcrm1, fcrm2 = st.columns([2, 3])
        buscar_crm = fcrm1.text_input("🔍 Buscar cliente", key="buscar_crm",
                                      placeholder="Nombre, barrio, teléfono…")
        filtro = fcrm2.radio("Ver", ["Todos", "🟡 Activos", "📭 Sin envíos",
                                     "🟢 Ganados", "🔴 Perdidos"], horizontal=True)
        mapa_filtro = {"🟡 Activos": "activo", "🟢 Ganados": "ganado", "🔴 Perdidos": "perdido"}

        def _pasa_crm(c):
            if buscar_crm and not coincide_busqueda(c, buscar_crm):
                return False
            if filtro == "📭 Sin envíos":
                return cobertura[c["nombre"]][0] == 0 and c["estado"] == "activo"
            if filtro != "Todos" and c["estado"] != mapa_filtro.get(filtro):
                return False
            return True

        # Orden pedido por Daniel: 1) prioridad 🔥, 2) valor de mayor a menor.
        def _clave_crm(c):
            return (RANGO_PRIORIDAD.get(prioridad_de(c), 1),
                    -(c.get("presupuesto_max") or 0))

        # Ventas y arriendos separados (ventas primero, como en la ficha de aliados).
        _orden_crm = []
        for _tit_g, _es_arr in [("🔑 Compra / Venta", False), ("🏠 Arriendo", True)]:
            _vis = sorted(
                [c for c in crm_clientes
                 if ((c.get("operacion") or "venta") == "arriendo") == _es_arr
                 and _pasa_crm(c)],
                key=_clave_crm)
            if _vis:
                _orden_crm.append(("header", f"{_tit_g} ({len(_vis)})"))
                _orden_crm.extend(("cliente", c) for c in _vis)
        if not _orden_crm:
            st.caption("Nada que mostrar con esa búsqueda/filtro.")
        for _tipo_it, _it in _orden_crm:
            if _tipo_it == "header":
                st.divider()
                st.markdown(f"##### {_it}")
                continue
            c = _it
            nombre = c["nombre"]
            enviados = c.get("inmuebles_enviados") or []
            com_actual = float(c.get("comision") or 0)
            n_env, dias_ult = cobertura[nombre]
            cab = (f"{ICONO_PRIORIDAD.get(prioridad_de(c), '')}"
                   f"{ESTADOS_CRM.get(c['estado'], c['estado'])}  ·  **{nombre}**"
                   + (f"  ·  💵 {matcher.formato_cop(c.get('presupuesto_max'))}"
                      if c.get("presupuesto_max") else "")
                   + "  ·  " + ("📭 SIN ENVÍOS" if n_env == 0 else
                                f"{cobertura_emoji(n_env)} {n_env} envío(s)")
                   + f"  ·  👣 {c.get('visitas', 0)} visita(s)")
            if dias_ult is not None:
                cab += f"  ·  último hace {dias_ult}d"
            if com_actual > 0:
                cab += f"  ·  💰 {matcher.formato_cop(com_actual)}"
            with st.expander(cab, expanded=False):
                # Lo primero del cliente: SUS inmuebles (enviados/proceso).
                render_procesos(c)
                with st.popover("⚙️ Estado del negocio, visitas y comisión",
                                use_container_width=True):

                    op = (c.get("operacion") or "venta").lower()
                    es_arriendo = op == "arriendo"
                    with st.form(key=f"crm_form_{nombre}"):
                        col1, col2, col3 = st.columns([1, 1, 1])
                        estado = col1.selectbox(
                            "Estado del negocio", ["activo", "ganado", "perdido"],
                            index=["activo", "ganado", "perdido"].index(c["estado"]),
                            format_func=lambda e: ESTADOS_CRM[e], key=f"est_{nombre}")
                        prio_crm = col2.selectbox(
                            "Prioridad", PRIORIDAD_OPCIONES,
                            index=PRIORIDAD_OPCIONES.index(prioridad_de(c)),
                            format_func=lambda x: BADGE_PRIORIDAD.get(x, x), key=f"prio_{nombre}",
                            help="Los 🔥 salen de primeros en toda la herramienta.")
                        visitas = col3.number_input("Visitas realizadas", min_value=0,
                                                    value=int(c.get("visitas") or 0),
                                                    step=1, key=f"vis_{nombre}")

                        # ── Financiero (valor de cierre y comisión) ──
                        colv, colc = st.columns([1, 1])
                        valor_cierre = colv.number_input(
                            "Canon mensual acordado ($)" if es_arriendo
                            else "Precio de venta final ($)",
                            min_value=0, value=int(c.get("valor_cierre") or 0),
                            step=100_000 if es_arriendo else 10_000_000,
                            format="%d", key=f"val_{nombre}",
                            help="Ajústalo al valor final negociado.")
                        comision = colc.number_input(
                            "Comisión ($)", min_value=0, value=int(c.get("comision") or 0),
                            step=100_000, format="%d", key=f"com_{nombre}",
                            help=("Comisión = primer canon, sin administración."
                                  if es_arriendo else
                                  f"Comisión = {COMISION_VENTA_PCT * 100:.0f}% del valor. "
                                  "Todo es negociable: edítala si hace falta."))
                        sug = comision_sugerida(op, c.get("valor_cierre") or 0)
                        nota_calc = ("💡 Comisión = primer canon." if es_arriendo
                                     else f"💡 Comisión sugerida ({COMISION_VENTA_PCT * 100:.0f}%): {matcher.formato_cop(sug)}.")
                        st.caption(nota_calc + " Si dejas la comisión en 0, se calcula sola al guardar.")

                        # Los inmuebles enviados se ven en el embudo de abajo (render_procesos);
                        # aquí solo un resumen de lectura (antes había un cuadro que nunca se
                        # llenaba solo y confundía).
                        _envs = [pr for pr in (c.get("procesos") or [])
                                 if pr.get("estado") != "descartado"]
                        if _envs:
                            st.caption("📤 Enviados (del embudo de abajo): "
                                       + " · ".join((pr.get("resumen") or pr.get("post_id", ""))[:40]
                                                    for pr in _envs[:6])
                                       + (" …" if len(_envs) > 6 else ""))
                        notas_crm = st.text_area("Notas de seguimiento",
                                                 value=c.get("notas_crm", ""),
                                                 height=70, key=f"ncrm_{nombre}")
                        if st.form_submit_button("💾 Guardar seguimiento", type="primary"):
                            com_final = int(comision) if int(comision) > 0 \
                                else comision_sugerida(op, valor_cierre)
                            mod_clientes.actualizar_crm(nombre, {
                                "estado": estado,
                                "prioridad": prio_crm,
                                "visitas": int(visitas),
                                "valor_cierre": int(valor_cierre),
                                "comision": com_final,
                                "notas_crm": notas_crm.strip(),
                            })
                            st.success(f"Seguimiento de {nombre} guardado.")
                            st.rerun()

