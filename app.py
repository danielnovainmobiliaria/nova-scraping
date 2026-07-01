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
db.init_db()

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


def badge_frescura(fecha_iso: str) -> str:
    """Etiqueta con semáforo: 🟢 reciente, 🟡 unas semanas, 🔴 más viejo."""
    d = dias_publicado(fecha_iso)
    if d is None:
        return ""
    emoji = "🟢" if d <= 7 else ("🟡" if d <= 21 else "🔴")
    if d <= 0:
        return f"{emoji} Publicado hoy"
    return f"{emoji} Publicado hace {d} día{'s' if d != 1 else ''}"


# A partir de este puntaje consideramos que un inmueble es "afín" al cliente.
UMBRAL_AFIN = 70


def es_portal_post(p) -> bool:
    """True si el inmueble vino de un portal/sitio web (no de Instagram)."""
    return str(p.get("id", "")).startswith("portal_")


def fuente_post(p) -> str:
    """Etiqueta de la fuente del inmueble (con ícono según sea red o portal)."""
    if es_portal_post(p):
        return f"🏠 {p.get('cuenta', 'portal')}"
    return f"📷 @{p.get('cuenta', '')}"


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
        return vacio


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
    st.markdown(f"**📋 Inmuebles en proceso ({len(procs)})**")
    if not procs:
        st.caption("Marca inmuebles desde la pestaña Coincidencias para que entren aquí.")
        return
    opciones = mod_clientes.ESTADOS_PROCESO
    for pr in procs:
        pid = pr.get("post_id", "")
        precio = matcher.formato_cop(pr.get("precio"))
        with st.container(border=True):
            st.caption(" · ".join(x for x in [pr.get("resumen", ""), pr.get("barrio", ""), precio] if x))
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
    """Trae los posts de Instagram y los lee con IA (usado por los botones)."""
    from src import extractor, scraper
    scraper.scrapear_cuentas(config.leer_cuentas(), log=log)
    extractor.extraer_pendientes(log=log)


# ── Barra lateral ─────────────────────────────────────────────
st.sidebar.title("🏙️ Nova Scraping")

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
correr = st.sidebar.button("🔄 Traer y leer publicaciones",
                           type="primary", use_container_width=True)
st.sidebar.caption(f"📦 {db.contar_posts()} inmuebles en memoria")

# ── Acción del botón: traer + leer publicaciones ──────────────
if correr:
    with st.status("Trayendo y leyendo publicaciones de Instagram… "
                   "(puede tardar 3-5 min, no cierres la pestaña)", expanded=True) as estado_scrape:
        _lineas: list[str] = []

        def _log(m: str) -> None:
            _lineas.append(m)
            estado_scrape.write(m)

        try:
            actualizar_publicaciones(_log)
            estado_scrape.update(
                label=f"✅ ¡Listo! {db.contar_posts()} inmuebles en memoria. "
                      "Abre la pestaña 3️⃣ Coincidencias.", state="complete")
        except Exception as e:  # noqa: BLE001
            estado_scrape.update(label=f"⚠️ Ocurrió un problema: {e}", state="error")

# ── Pestañas ──────────────────────────────────────────────────
tab_fuentes, tab_clientes, tab_resultados, tab_manual, tab_crm = st.tabs(
    ["1️⃣ Fuentes (Instagram)", "2️⃣ Clientes", "3️⃣ Coincidencias",
     "🔎 Inmueble → Clientes", "4️⃣ CRM"]
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
    st.subheader("Cuentas de Instagram a monitorear")
    st.caption("Una cuenta por línea. Se guardan en config/cuentas.txt")
    actuales = "\n".join(config.leer_cuentas())
    texto = st.text_area("Cuentas", value=actuales, height=200,
                         placeholder="arriendos_chapinero\ninmobiliaria_norte")
    if st.button("💾 Guardar cuentas"):
        cuentas = [l.strip().lstrip("@") for l in texto.splitlines() if l.strip()]
        cabecera = "# Cuentas de Instagram a monitorear (una por línea)\n"
        config.CUENTAS_FILE.write_text(cabecera + "\n".join(cuentas), encoding="utf-8")
        st.success(f"Guardadas {len(cuentas)} cuenta(s).")

    cuentas_guardadas = config.leer_cuentas()
    try:
        _restr_urls = json.loads(db.leer_meta("cuentas_restringidas") or "[]")
    except json.JSONDecodeError:
        _restr_urls = []
    restringidas_us = {config._solo_usuario(u) for u in _restr_urls if u}
    # Incluye los perfiles restringidos aunque ya no estén en la lista configurada.
    perfiles_abrir = list(dict.fromkeys(
        list(cuentas_guardadas) + [u for u in restringidas_us if u and u not in cuentas_guardadas]))
    if perfiles_abrir:
        n_restr = sum(1 for c in perfiles_abrir if c in restringidas_us)
        with st.expander(f"🔗 Abrir perfiles ({len(perfiles_abrir)}) para revisar a mano",
                         expanded=False):
            st.caption("Clic en un perfil para abrirlo en Instagram y buscar manualmente."
                       + (f"  ·  ⚠️ = Instagram no lo dejó leer ({n_restr}); estos son los que más "
                          "conviene revisar aquí." if n_restr else ""))
            st.markdown(" · ".join(
                f"[@{c}{' ⚠️' if c in restringidas_us else ''}](https://www.instagram.com/{c}/)"
                for c in perfiles_abrir))

    st.divider()
    st.subheader("🏠 Portales y sitios web")
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
        ulist = [l.strip() for l in texto_portales.splitlines() if l.strip()]
        cab = "# Portales / sitios web a leer (una URL por línea)\n"
        config.PORTALES_FILE.write_text(cab + "\n".join(ulist), encoding="utf-8")
        st.success(f"Guardados {len(ulist)} portal(es).")
    if config.leer_portales() and st.button("🏠 Leer inmuebles de portales", type="primary"):
        registro_p = st.empty()
        lineas_p: list[str] = []

        def log_p(msg: str) -> None:
            lineas_p.append(msg)
            registro_p.code("\n".join(lineas_p[-12:]))

        try:
            from src import scraper_portales
            n = scraper_portales.scrapear_portales(config.leer_portales(), log=log_p)
            st.success(f"¡Listo! Se agregaron {n} inmueble(s) de portales. "
                       "Míralos en 3️⃣ Coincidencias.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Problema leyendo portales: {e}")
    st.caption(f"⚙️ Tope de seguridad: {config.MAX_PAGINAS_PORTAL} páginas por corrida "
               "(para que el gasto no se dispare). Cuesta unos centavos.")

    st.divider()
    st.subheader("Actualizar publicaciones")
    st.caption(f"Trae los posts de los últimos {config.DIAS_RECIENTES} días "
               "y los lee con IA. Cada post se procesa una sola vez.")
    col1, col2 = st.columns(2)
    if col1.button("🔄 Traer y leer publicaciones", type="primary"):
        registro = st.empty()
        lineas: list[str] = []

        def log(msg: str) -> None:
            lineas.append(msg)
            registro.code("\n".join(lineas[-12:]))

        try:
            from src import extractor, scraper
            scraper.scrapear_cuentas(config.leer_cuentas(), log=log)
            extractor.extraer_pendientes(log=log)
            st.success("¡Actualización completa!")
        except Exception as e:  # noqa: BLE001
            st.error(f"Ocurrió un problema: {e}")
    col2.metric("Posts en la caché", db.contar_posts())

    # ── Cuentas que Instagram no dejó leer (revisar a mano) ──
    try:
        restr = json.loads(db.leer_meta("cuentas_restringidas") or "[]")
    except json.JSONDecodeError:
        restr = []
    if restr:
        st.divider()
        st.markdown(f"**⚠️ {len(restr)} perfil(es) que Instagram NO dejó leer** "
                    "(ábrelos a mano para revisar su inventario):")
        for u in restr:
            st.markdown(f"- [{u}]({u})")
        st.caption("Son perfiles restringidos por Instagram. No es falla de la app — "
                   "revísalos manualmente cuando quieras.")

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
        num = m.group(1).replace("'", "").replace(".", "").replace(",", "")
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
COLS_HOJA = ["nombre", "telefono", "operacion", "flexibilidad", "barrios", "zona", "presupuesto",
             "area_min", "area_max", "habitaciones_min", "banos_min", "extras",
             "obligatorios", "notas"]


def clientes_a_df(lista):
    """Convierte la lista de clientes (interna) en una tabla editable."""
    filas = []
    for c in lista:
        filas.append({
            "nombre": c.get("nombre", ""),
            "telefono": c.get("telefono", ""),
            "operacion": c.get("operacion", "venta"),
            "flexibilidad": c.get("flexibilidad") or "medio",
            "barrios": lista_a_texto(c.get("barrios")),
            "zona": c.get("zona", ""),
            "presupuesto": matcher.formato_cop(c.get("presupuesto_max")),
            "area_min": c.get("area_min"),
            "area_max": c.get("area_max"),
            "habitaciones_min": c.get("habitaciones_min"),
            "banos_min": c.get("banos_min"),
            "extras": lista_a_texto(c.get("extras")),
            "obligatorios": lista_a_texto(c.get("obligatorios")),
            "notas": c.get("notas", ""),
        })
    return pd.DataFrame(filas, columns=COLS_HOJA)


def df_a_clientes(df):
    """Convierte la tabla editada de vuelta al formato interno."""
    out = []
    for _, fila in df.iterrows():
        nombre = str(fila.get("nombre", "") or "").strip()
        if not nombre or nombre.lower() == "nan":
            continue
        out.append({
            "nombre": nombre,
            "telefono": "".join(ch for ch in str(fila.get("telefono", "") or "") if ch.isdigit()),
            "operacion": str(fila.get("operacion", "") or "venta").strip().lower(),
            "barrios": texto_a_lista(fila.get("barrios")),
            "zona": str(fila.get("zona", "") or "").strip(),
            "presupuesto_max": parse_cop(fila.get("presupuesto")),
            "area_min": num_o_none(fila.get("area_min")),
            "area_max": num_o_none(fila.get("area_max")),
            "habitaciones_min": num_o_none(fila.get("habitaciones_min")),
            "banos_min": num_o_none(fila.get("banos_min")),
            "extras": [e.lower() for e in texto_a_lista(fila.get("extras"))],
            "obligatorios": [o for o in texto_a_lista(fila.get("obligatorios"))
                             if o in OBLIGATORIOS_OPCIONES],
            "perimetro": "",
            "notas": str(fila.get("notas", "") or "").strip(),
        })
    return out


def refrescar_hoja_clientes():
    """Reconstruye la hoja editable desde la base y reinicia el editor.

    Necesario para que la tabla no pierda los cambios entre recargas: usamos un
    DataFrame estable en sesión y cambiamos la 'versión' (key) solo cuando hace falta.
    """
    st.session_state["df_clientes"] = clientes_a_df(mod_clientes.cargar_guardados())
    st.session_state["hoja_ver"] = st.session_state.get("hoja_ver", 0) + 1


def _aplicar_columna(cliente, col, val):
    """Aplica el valor editado de una columna de la hoja al cliente."""
    val = "" if val is None else val
    if col == "nombre":
        cliente["nombre"] = str(val).strip()
    elif col == "telefono":
        cliente["telefono"] = "".join(ch for ch in str(val) if ch.isdigit())
    elif col == "operacion":
        cliente["operacion"] = str(val or "venta").strip().lower()
    elif col == "flexibilidad":
        cliente["flexibilidad"] = (str(val or "medio").strip().lower()
                                   if str(val or "").strip().lower() in FLEX_OPCIONES else "medio")
    elif col == "barrios":
        cliente["barrios"] = texto_a_lista(val)
    elif col == "zona":
        cliente["zona"] = str(val).strip()
    elif col == "presupuesto":
        cliente["presupuesto_max"] = parse_cop(val)
    elif col in ("area_min", "area_max", "habitaciones_min", "banos_min"):
        cliente[col] = num_o_none(val)
    elif col == "extras":
        cliente["extras"] = [e.lower() for e in texto_a_lista(val)]
    elif col == "obligatorios":
        cliente["obligatorios"] = [o for o in texto_a_lista(val) if o in OBLIGATORIOS_OPCIONES]
    elif col == "notas":
        cliente["notas"] = str(val).strip()


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


def _cliente_nuevo_vacio():
    return {"nombre": "", "telefono": "", "operacion": "venta", "flexibilidad": "medio",
            "barrios": [], "zona": "", "presupuesto_max": None, "area_min": None,
            "area_max": None, "habitaciones_min": None, "banos_min": None, "extras": [],
            "obligatorios": [], "perimetro": "", "notas": ""}


def guardar_edicion_hoja(editor_key):
    """Lee los cambios del editor (delta) y los aplica sobre los clientes guardados.

    Es el método confiable: toma exactamente lo que el usuario editó/agregó/borró,
    sin depender de cómo Streamlit devuelva el DataFrame.
    """
    delta = st.session_state.get(editor_key) or {}
    clientes = mod_clientes.cargar_guardados()
    for idx_str, cambios in (delta.get("edited_rows") or {}).items():
        i = int(idx_str)
        if 0 <= i < len(clientes):
            for col, val in cambios.items():
                _aplicar_columna(clientes[i], col, val)
    for i in sorted(delta.get("deleted_rows") or [], reverse=True):
        if 0 <= i < len(clientes):
            del clientes[i]
    for fila in (delta.get("added_rows") or []):
        nuevo = _cliente_nuevo_vacio()
        for col, val in fila.items():
            _aplicar_columna(nuevo, col, val)
        if nuevo["nombre"].strip():
            clientes.append(nuevo)
    mod_clientes.guardar_lista(clientes)


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
    st.subheader("Tus clientes y sus requerimientos")

    st.caption("Agrega clientes con cualquiera de los métodos de abajo. Para **editar o "
               "borrar**, usa el editor **«✏️»**. La tabla del final es solo para ver.")
    buscar_cli = st.text_input("🔍 Buscar cliente", key="buscar_cli",
                               placeholder="Escribe un nombre, barrio, teléfono o zona…")

    # ── Agregar un cliente nuevo (MISMO formato que Zoho, con IA) ──
    with st.expander("➕ Agregar un cliente nuevo", expanded=False):
        st.caption("Llénalo igual que tu formulario de Zoho. La **misma IA** lo interpreta "
                   "(corrige abreviaturas como 12M) y, si el cliente ya existe (mismo nombre "
                   "o teléfono), lo **une automáticamente** sin duplicar.")
        with st.form("nuevo_cliente", clear_on_submit=True):
            c1, c2 = st.columns(2)
            z_nombre = c1.text_input("Nombre *", placeholder="Alfonso Rubiano")
            z_tel = c2.text_input("Teléfono", placeholder="300 123 4567")
            c1, c2 = st.columns(2)
            z_op = c1.selectbox("Compra / Arriendo", ["Compra", "Arriendo"])
            z_zona = c2.text_input("Zona", placeholder="Chicó, Rosales, Cabrera")
            z_pres = st.text_input("Presupuesto",
                                   placeholder="ej: 12M   ·   800M-900M   ·   1.900.000.000")
            z_req = st.text_area("Requerimiento", height=110,
                                 placeholder="2 habitaciones\n90 m2 o más\ncon estudio y parqueadero")
            z_oblig = st.multiselect(
                "🔒 No negociable (sí o sí) — filtra duro",
                OBLIGATORIOS_OPCIONES, format_func=lambda o: ETIQUETA_OBLIGATORIO.get(o, o),
                help="Lo que marques aquí se exige obligatoriamente; el resto se busca flexible.")
            z_flex = st.selectbox(
                "¿Qué tan flexible es este cliente?", FLEX_OPCIONES, index=1,
                format_func=lambda f: ETIQUETA_FLEX.get(f, f),
                help="Estricto = solo inmuebles muy acertados (clientes que no ceden, como Eleonora). "
                     "Flexible = abierto a más opciones. Medio = equilibrado.")
            if st.form_submit_button("➕ Agregar cliente", type="primary"):
                if not z_nombre.strip():
                    st.error("Ponle un nombre al cliente.")
                elif not config.ANTHROPIC_API_KEY:
                    st.error("Falta la llave de Claude para interpretar. Revisa «🔑 Mis llaves».")
                else:
                    from src import extractor
                    blob = (f"Nombre: {z_nombre} | Teléfono: {z_tel} | "
                            f"Compra / Arriendo: {z_op} | Zona: {z_zona} | "
                            f"Presupuesto: {z_pres} | Requerimiento: {z_req}")
                    nuevos = extractor.interpretar_clientes([blob], log=lambda m: None)
                    if not nuevos:
                        st.error("No se pudo interpretar. Revisa los datos.")
                    else:
                        # Une lo que la IA detectó como obligatorio con lo que marcaste.
                        nuevos[0]["obligatorios"] = sorted(
                            set(nuevos[0].get("obligatorios") or []) | set(z_oblig))
                        nuevos[0]["flexibilidad"] = z_flex  # lo que elegiste manda
                        existentes = mod_clientes.cargar_guardados()
                        antes = len(existentes)
                        combinados = mod_clientes.fusionar_duplicados(existentes + nuevos)
                        mod_clientes.guardar_lista(combinados)
                        if len(combinados) == antes:
                            st.success(f"«{z_nombre.strip()}» ya existía → se unió/actualizó "
                                       "sin duplicar. ✅")
                        else:
                            st.success(f"Cliente «{z_nombre.strip()}» agregado e interpretado. 🎉")
                        refrescar_hoja_clientes()
                        st.rerun()

    # ── Pegar TODO en un cuadro y que la IA lo organice ──
    with st.expander("📋 Pegar todo en un cuadro (la IA lo organiza)", expanded=False):
        st.caption("Pega aquí lo que sea (un mensaje de WhatsApp, un correo, una lista de varios "
                   "clientes…). La IA detecta uno o varios clientes, los interpreta y los une si "
                   "ya existen.")
        texto_libre = st.text_area("Pega aquí la info", height=160, key="texto_libre",
                                   placeholder="Ej:\nJuan, 300 555 1212, compra Chicó 2 hab hasta 800M\n"
                                               "Marcela quiere arriendo en Rosales, 3 alcobas, 12M, con estudio")
        if st.button("🤖 Interpretar y agregar", key="btn_texto_libre"):
            if not config.ANTHROPIC_API_KEY:
                st.error("Falta la llave de Claude. Revisa «🔑 Mis llaves».")
            elif not texto_libre.strip():
                st.error("Pega algún texto primero.")
            else:
                try:
                    from src import extractor
                    with st.spinner("Interpretando…"):
                        nuevos = extractor.interpretar_texto_libre(texto_libre, log=lambda m: None)
                    if not nuevos:
                        st.error("No reconocí clientes en el texto. Revisa que tenga datos.")
                    else:
                        existentes = mod_clientes.cargar_guardados()
                        antes = len(existentes)
                        combinados = mod_clientes.fusionar_duplicados(existentes + nuevos)
                        mod_clientes.guardar_lista(combinados)
                        nombres = ", ".join(c.get("nombre", "") for c in nuevos)
                        st.success(f"Se interpretaron {len(nuevos)} cliente(s): {nombres}. "
                                   f"(Quedan {len(combinados)} en total, duplicados unidos.) 🎉")
                        refrescar_hoja_clientes()
                        st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo interpretar: {e}")

    # ── Editar / renombrar un cliente (vía confiable, sin la tabla) ──
    with st.expander("✏️ Editar o renombrar un cliente", expanded=True):
        _lista = mod_clientes.cargar_guardados()
        if not _lista:
            st.caption("Aún no hay clientes.")
        else:
            opciones = ([c["nombre"] for c in _lista if coincide_busqueda(c, buscar_cli)]
                        or [c["nombre"] for c in _lista])
            sel = st.selectbox("¿Cuál cliente quieres editar?", opciones, key="edit_sel")
            cliente_e = next((c for c in _lista if c["nombre"] == sel), _lista[0])
            with st.form("editar_cliente"):
                g1, g2 = st.columns(2)
                e_nombre = g1.text_input("Nombre", value=cliente_e.get("nombre", ""))
                e_tel = g2.text_input("Teléfono", value=cliente_e.get("telefono", ""))
                g1, g2 = st.columns(2)
                e_op = g1.selectbox("Compra / Arriendo", ["venta", "arriendo"],
                                    index=0 if (cliente_e.get("operacion") or "venta") == "venta" else 1)
                _fx = (cliente_e.get("flexibilidad") or "medio")
                _fx = _fx if _fx in FLEX_OPCIONES else "medio"
                e_flex = g2.selectbox("Flexibilidad", FLEX_OPCIONES,
                                      index=FLEX_OPCIONES.index(_fx),
                                      format_func=lambda f: ETIQUETA_FLEX.get(f, f),
                                      help="Estricto = no cede (solo lo muy acertado). "
                                           "Flexible = abierto a más opciones.")
                e_zona = st.text_input("Zona", value=cliente_e.get("zona", ""))
                e_barrios = st.text_input("Barrios", value=lista_a_texto(cliente_e.get("barrios")))
                e_pres = st.text_input("Presupuesto",
                                       value=matcher.formato_cop(cliente_e.get("presupuesto_max")))
                g1, g2, g3, g4 = st.columns(4)
                e_amin = g1.number_input("Área mín", min_value=0, value=int(cliente_e.get("area_min") or 0))
                e_amax = g2.number_input("Área máx", min_value=0, value=int(cliente_e.get("area_max") or 0))
                e_hab = g3.number_input("Habitac.", min_value=0, value=int(cliente_e.get("habitaciones_min") or 0))
                e_ban = g4.number_input("Baños", min_value=0, value=int(cliente_e.get("banos_min") or 0))
                e_extras = st.multiselect(
                    "Extras", EXTRAS_OPCIONES, format_func=lambda x: ETIQUETA_EXTRA.get(x, x),
                    default=[x for x in (cliente_e.get("extras") or []) if x in EXTRAS_OPCIONES])
                e_oblig = st.multiselect(
                    "🔒 No negociable", OBLIGATORIOS_OPCIONES,
                    format_func=lambda x: ETIQUETA_OBLIGATORIO.get(x, x),
                    default=[x for x in (cliente_e.get("obligatorios") or []) if x in OBLIGATORIOS_OPCIONES])
                e_notas = st.text_input("Notas", value=cliente_e.get("notas", ""))
                if st.form_submit_button("💾 Guardar cambios", type="primary"):
                    clientes = mod_clientes.cargar_guardados()
                    for c in clientes:
                        if c.get("nombre") == sel:
                            c["nombre"] = e_nombre.strip()
                            c["telefono"] = "".join(ch for ch in e_tel if ch.isdigit())
                            c["operacion"] = e_op
                            c["flexibilidad"] = e_flex
                            c["barrios"] = texto_a_lista(e_barrios)
                            c["zona"] = e_zona.strip()
                            c["presupuesto_max"] = parse_cop(e_pres)
                            c["area_min"] = num_o_none(e_amin)
                            c["area_max"] = num_o_none(e_amax)
                            c["habitaciones_min"] = num_o_none(e_hab)
                            c["banos_min"] = num_o_none(e_ban)
                            c["extras"] = e_extras
                            c["obligatorios"] = e_oblig
                            c["notas"] = e_notas.strip()
                            break
                    mod_clientes.guardar_lista(clientes)
                    refrescar_hoja_clientes()
                    st.success(f"«{e_nombre.strip()}» actualizado. ✅")
                    st.rerun()

            if st.button("🗑️ Eliminar este cliente", key="edit_del",
                         help="Quita este cliente de la lista (no se puede deshacer)."):
                mod_clientes.eliminar(sel)
                refrescar_hoja_clientes()
                st.success(f"«{sel}» eliminado.")
                st.rerun()

    with st.expander("ℹ️ Cómo llenar cada columna"):
        st.markdown(
            "- **operacion**: escribe `venta` o `arriendo`.\n"
            "- **barrios**: varios separados por coma → `El Nogal, Rosales`.\n"
            "- **presupuesto**: como quieras → `1'700.000.000`, `12M` o `1900000000`.\n"
            "- **area_min / area_max**: metros cuadrados. Deja vacío si no importa.\n"
            "- **habitaciones_min / banos_min**: mínimo deseado. Vacío = no filtra.\n"
            f"- **extras**: separados por coma. Válidos: {', '.join(EXTRAS_OPCIONES)}."
        )

    # ── Importar con IA desde un archivo "como sea" ──────
    with st.expander("🤖 Importar clientes desde un archivo (con IA)"):
        st.caption("Sube tu lista tal como la tengas (CSV o Excel), aunque esté en "
                   "texto libre y con abreviaciones (ej. *“arriendo 12M, 2 alcobas, "
                   "Chapinero, mín 60 mts”*). La IA lee cada fila, la interpreta "
                   "(12M → $12.000.000) y la acomoda al formato. Revisa el resultado "
                   "en la tabla de abajo antes de guardar.")
        archivo_ia = st.file_uploader("Archivo de clientes (.csv o .xlsx)",
                                      type=["csv", "xlsx"], key="ia_uploader")
        if archivo_ia is not None and st.button("🤖 Interpretar y agregar con IA"):
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
                    # Une duplicados (mismo nombre) tomando el más completo, y
                    # conserva el seguimiento CRM de los que ya existían.
                    combinados = mod_clientes.fusionar_duplicados(existentes + nuevos)
                    mod_clientes.guardar_lista(combinados)
                    st.success(f"¡Listo! Se procesaron {len(nuevos)} fila(s) → "
                               f"{len(combinados)} cliente(s) en total (duplicados unidos). "
                               "Revísalos en la tabla y dale Guardar si todo está bien.")
                    refrescar_hoja_clientes()
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo procesar el archivo: {e}")

    # La tabla es SOLO para ver. (La data_editor de Streamlit no guardaba bien los
    # cambios, así que toda edición se hace con el editor "✏️" de arriba.)
    st.info("👁️ Esta tabla es **solo para ver**. Para **editar, renombrar o borrar** un "
            "cliente, usa **«✏️ Editar o renombrar un cliente»** (arriba). Eso sí guarda bien.")
    todos = mod_clientes.cargar_guardados()
    lista_ver = [c for c in todos if coincide_busqueda(c, buscar_cli)]
    st.dataframe(clientes_a_df(lista_ver), use_container_width=True, hide_index=True)
    if buscar_cli:
        st.caption(f"Mostrando {len(lista_ver)} de {len(todos)} clientes que coinciden "
                   f"con «{buscar_cli}».")

    c2, c3 = st.columns(2)
    c2.download_button(
        "⬇️ Descargar copia (Excel)", excel_bytes(clientes_a_df(todos)),
        "clientes.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    with c3.popover("⬆️ Restaurar desde Excel", use_container_width=True):
        archivo = st.file_uploader("Sube tu copia (.xlsx)", type=["xlsx"])
        if archivo is not None and st.button("Restaurar ahora"):
            try:
                mod_clientes.guardar_lista(mod_clientes.cargar_clientes(archivo))
                st.success("¡Restaurado!")
                refrescar_hoja_clientes()
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo leer el Excel: {e}")

    st.session_state["clientes"] = mod_clientes.cargar_guardados()
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
    st.subheader("Coincidencias por cliente")
    clientes = st.session_state.get("clientes", [])

    # Todos los inmuebles leídos (no se ocultan por antigüedad).
    posts = db.posts_leidos()

    if not clientes:
        st.warning("Primero carga tus clientes en la pestaña **2️⃣ Clientes**.")
    elif not posts:
        st.warning("Aún no hay publicaciones. Ve a la pestaña **1️⃣ Fuentes** y "
                   "actualiza (o usa el modo Demo).")
    else:
        st.caption("Ajusta qué tan flexible quieres ser. La búsqueda muestra "
                   "opciones **similares**, no solo idénticas.")
        dias_max = st.slider("📅 Frescura máxima (días desde publicado)", 7, 180, 30, 1,
                             help="Solo muestra inmuebles publicados en los últimos N días. "
                                  "Por defecto 30 (lo acordado). Súbelo solo si quieres ver más antiguos.")
        posts = [p for p in posts
                 if (dias_publicado(p.get("fecha")) is None or dias_publicado(p.get("fecha")) <= dias_max)]
        c1, c2, c3, c4 = st.columns(4)
        score_min = c1.slider("Coincidencia mínima (%)", 0, 100, 50, 5,
                              help="Sube el valor para ver solo los matches más fuertes.")
        flex_precio = c2.slider("Presupuesto: tope arriba (%)", 0, 40, 15, 5,
                                help="Cuánto POR ENCIMA del presupuesto se permite. "
                                     "Ej: 15% deja ver opciones hasta 15% más caras.")
        piso_precio = c3.slider("Presupuesto: precio mínimo (%)", 0, 100, 70, 5,
                                help="Oculta inmuebles DEMASIADO BARATOS (otro segmento). "
                                     "Ej: 70% oculta lo que cueste menos del 70% del presupuesto "
                                     "del cliente. Bájalo si quieres ver opciones más económicas.")
        flex_area = c4.slider("Flexibilidad en metraje (%)", 0, 40, 15, 5,
                              help="Cuánto por fuera del rango de m² se permite.")
        resultados = matcher.cruzar(
            clientes, posts, score_minimo=score_min,
            flex_precio=flex_precio / 100, flex_area=flex_area / 100,
            piso_precio=piso_precio / 100,
        )

        # Ocultar inmuebles que ya están en el embudo de seguimiento del cliente.
        ocultos = {c["nombre"]: mod_clientes.ids_en_proceso(c) for c in clientes}
        aprendizajes = {c["nombre"]: mod_clientes.aprendizajes_cliente(c) for c in clientes}
        oblig_map = {c["nombre"]: (c.get("obligatorios") or []) for c in clientes}
        flex_map = {c["nombre"]: (c.get("flexibilidad") or "medio") for c in clientes}
        com_map = {c["nombre"]: (c.get("comentarios_ia") or []) for c in clientes}
        cli_map = {c["nombre"]: c for c in clientes}
        for nombre in list(resultados):
            resultados[nombre] = [
                m for m in resultados[nombre]
                if m["post"].get("id") not in ocultos.get(nombre, set())
            ]

        total = sum(len(v) for v in resultados.values())
        st.caption(f"{len(posts)} publicaciones analizadas · {total} coincidencias pendientes "
                   "(los que marcas como enviados o descartados desaparecen).")
        st.caption("Frescura del aviso: 🟢 reciente (≤7 días, más fácil de conseguir) · "
                   "🟡 unas semanas · 🔴 más viejo (puede estar ya tomado).")
        st.caption("Cada cliente muestra primero lo MÁS afín a su requerimiento. Afinidad: "
                   "🟢 muy afín (≥85%) · 🟡 afín (≥70%) · 🟠 menos afín (más lejos de lo pedido).")

        # ── Cuadro: inmuebles potenciales por cliente (cobertura) ──
        resumen = []
        for nombre, matches in resultados.items():
            n = len(matches)
            n_portal = sum(1 for m in matches if es_portal_post(m["post"]))
            n_ig = n - n_portal
            resumen.append({
                "Cliente": nombre,
                "Perfil": BADGE_FLEX.get(flex_map.get(nombre, "medio"), "⚖️ Medio"),
                "📷 Instagram": n_ig,
                "🏠 Portales": n_portal,
                "Total": n,
                "Cobertura": "🔴 Buscar más" if n == 0 else ("🟡 Pocos" if n <= 2 else "🟢 Bien cubierto"),
            })
        resumen.sort(key=lambda r: r["Total"])  # los más flojos primero
        with st.expander("📋 Cobertura por cliente (cuántos inmuebles potenciales hay)", expanded=True):
            st.dataframe(pd.DataFrame(resumen), hide_index=True, use_container_width=True)
            st.caption("Ordenado de menos a más. Los 🔴/🟡 son a quienes conviene "
                       "buscarles más (incluso manual o ampliando criterios con los deslizadores).")

        for nombre, matches in resultados.items():
            with st.expander(f"👤 {nombre} — {len(matches)} coincidencia(s)",
                             expanded=(nombre == st.session_state.get("cliente_abierto"))):
                perfil = flex_map.get(nombre, "medio")
                st.caption(f"Perfil de búsqueda: **{ETIQUETA_FLEX.get(perfil, perfil)}**"
                           + ("  ·  con este perfil solo verás inmuebles muy acertados."
                              if perfil == "estricto" else ""))
                _n_portal = sum(1 for m in matches if es_portal_post(m["post"]))
                _n_ig = len(matches) - _n_portal
                st.markdown(f"**Scraping para este cliente:** 📷 Instagram: **{_n_ig}**  ·  "
                            f"🏠 Portales: **{_n_portal}**")
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
                            mod_clientes.agregar_comentario_ia(nombre, txt.strip())
                            af = aplicar_exclusiones_de_texto(nombre, txt, cli_map.get(nombre))
                            recalcular_preferencias(nombre)   # ajuste suave (priorizar/penalizar)
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
                        st.markdown(f"**{p.get('resumen') or p.get('caption','')[:80]}**")
                        info = []
                        if p.get("operacion"): info.append(p["operacion"].capitalize())
                        if p.get("barrio"): info.append(p["barrio"])
                        if p.get("precio"): info.append(matcher.formato_cop(p["precio"]))
                        if p.get("area_m2"): info.append(f"{p['area_m2']:g} m²")
                        if p.get("habitaciones") is not None: info.append(f"{p['habitaciones']:g} hab")
                        if p.get("banos") is not None: info.append(f"{p['banos']:g} baños")
                        st.caption(" · ".join(info))
                        frescura = badge_frescura(p.get("fecha"))
                        if frescura:
                            st.markdown(f"**{frescura}**")
                        if m["razones_ok"]:
                            st.markdown("✅ " + " · ".join(m["razones_ok"]))
                        if m["razones_no"]:
                            st.markdown("⚠️ " + " · ".join(m["razones_no"]))
                        st.caption(f"Fuente (solo tú): {fuente_post(p)} · publicado {p.get('fecha', '')}")
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
                        a1, a2 = st.columns(2)
                        with a1:
                            with st.popover("📲 Texto para compartir", use_container_width=True):
                                st.caption("Listo para tu cliente: solo datos + tu marca, sin link ni fuente.")
                                st.code(mensaje, language=None)
                        with a2:
                            if p.get("media"):
                                with st.popover("📥 Descargar foto/video", use_container_width=True):
                                    render_descargas(p, f"{nombre}_{p.get('id', 'x')}")
                    with c2:
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
                                if obs.strip():
                                    af = aplicar_exclusiones_de_texto(nombre, obs, cli_map.get(nombre))
                                    recalcular_preferencias(nombre)
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


with tab_manual:
    st.subheader("🔎 Pega un inmueble y mira a qué clientes les sirve")
    st.caption("Ingresa la descripción de un inmueble que viste (y su link de referencia). "
               "La IA lo interpreta y te dice **a qué clientes encaja**, con el porqué. "
               "Es más manual, pero te da control total sobre lo que entra.")

    clientes_m = mod_clientes.cargar_guardados()
    if not clientes_m:
        st.warning("Primero carga tus clientes en la pestaña **2️⃣ Clientes**.")
    else:
        with st.form("inmueble_manual", clear_on_submit=True):
            desc = st.text_area(
                "Descripción del inmueble", height=130,
                placeholder="Ej: Apartamento en venta en El Nogal, 120 m², 3 habitaciones, "
                            "2 baños, $1.800 millones, remodelado, con vista y parqueadero.")
            link = st.text_input("Link (para tu referencia)", placeholder="https://…")
            if st.form_submit_button("🔎 ¿A qué clientes les sirve?", type="primary"):
                dup_link = next(
                    (x for x in cargar_inmuebles_manuales()
                     if link.strip() and _norm_link(x.get("link", "")) == _norm_link(link)),
                    None)
                if not desc.strip():
                    st.warning("Escribe la descripción del inmueble.")
                elif not config.ANTHROPIC_API_KEY:
                    st.error("Falta la llave de Claude. Revisa «🔑 Mis llaves».")
                elif dup_link:
                    st.warning(f"⚠️ Esa publicación **ya estaba incluida** (la agregaste el "
                               f"{dup_link.get('fecha', '?')}). No la dupliqué; la ves abajo en la lista.")
                else:
                    try:
                        from src import extractor
                        datos = extractor.interpretar_inmueble(desc.strip())
                        item = {
                            "id": "m_" + hashlib.md5((desc + link).encode("utf-8")).hexdigest()[:16],
                            "texto": desc.strip(), "link": link.strip(),
                            "fecha": datetime.now(timezone.utc).date().isoformat(), "datos": datos,
                        }
                        lista = [x for x in cargar_inmuebles_manuales() if x.get("id") != item["id"]]
                        lista.insert(0, item)
                        guardar_inmuebles_manuales(lista)
                        st.success("¡Listo! Abajo te muestro a qué clientes les sirve.")
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"No se pudo interpretar: {e}")

        manuales = cargar_inmuebles_manuales()
        if not manuales:
            st.caption("Aún no has ingresado inmuebles. Agrega el primero arriba. 👆")
        else:
            st.caption("ℹ️ Esto se recalcula solo: si agregas o editas un cliente, los inmuebles "
                       "de abajo se reasignan automáticamente a quien les sirva.")
        filtro_cli = st.selectbox(
            "Ver solo los inmuebles que le sirven a:",
            ["(todos los clientes)"] + [c["nombre"] for c in clientes_m],
            key="manual_filtro_cli") if manuales else "(todos los clientes)"

        mostrados = 0
        for item in manuales:
            datos = item.get("datos", {}) or {}
            post = {**datos, "caption": item.get("texto", ""),
                    "url": item.get("link", ""), "id": item.get("id")}
            ms = []
            for c in clientes_m:
                ev = matcher.evaluar(c, post)
                if ev:
                    ms.append((c["nombre"], ev))
            ms.sort(key=lambda x: x[1]["score"], reverse=True)
            buenos = [m for m in ms if m[1]["score"] >= 50]
            if filtro_cli != "(todos los clientes)" and filtro_cli not in [n for n, _ in buenos]:
                continue
            mostrados += 1
            with st.container(border=True):
                resumen = datos.get("resumen") or (item.get("texto", "")[:80])
                st.markdown(f"**{resumen}**")
                info = []
                if datos.get("operacion"): info.append(datos["operacion"].capitalize())
                if datos.get("barrio"): info.append(datos["barrio"])
                if datos.get("precio"): info.append(matcher.formato_cop(datos["precio"]))
                if datos.get("area_m2"): info.append(f"{datos['area_m2']:g} m²")
                if datos.get("habitaciones") is not None: info.append(f"{datos['habitaciones']:g} hab")
                if datos.get("banos") is not None: info.append(f"{datos['banos']:g} baños")
                st.caption(("🤖 La IA entendió: " + " · ".join(info)) if info
                           else "🤖 No logré sacar datos claros; revisa la descripción.")
                if item.get("link"):
                    st.markdown(f"🔗 [Abrir inmueble (tu referencia)]({item['link']})")

                if buenos:
                    st.markdown(f"**✅ Le sirve a {len(buenos)} cliente(s):**")
                    for nombre, ev in buenos:
                        st.markdown(f"- {badge_afinidad(ev['score'])} **{ev['score']}%** — {nombre}")
                        if ev["razones_ok"]:
                            st.caption("　✅ " + " · ".join(ev["razones_ok"]))
                        if ev["razones_no"]:
                            st.caption("　⚠️ " + " · ".join(ev["razones_no"]))
                else:
                    st.info("Ningún cliente encaja claramente (≥50%) con este inmueble.")
                    if ms:
                        st.caption("Los más cercanos: "
                                   + " · ".join(f"{n} ({e['score']}%)" for n, e in ms[:3]))
                if st.button("🗑️ Quitar este inmueble", key=f"delm_{item.get('id')}"):
                    guardar_inmuebles_manuales(
                        [x for x in cargar_inmuebles_manuales() if x.get("id") != item.get("id")])
                    st.rerun()

        if manuales and filtro_cli != "(todos los clientes)" and mostrados == 0:
            st.info(f"Ningún inmueble de tu lista le sirve a **{filtro_cli}** todavía.")


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
        com_en_juego = sum(float(c.get("comision") or 0)
                           for c in crm_clientes if c["estado"] == "activo")

        m = st.columns(5)
        m[0].metric("👥 Clientes", len(crm_clientes))
        m[1].metric("🟡 Activos", n_activos)
        m[2].metric("🟢 Ganados", n_ganados)
        m[3].metric("🔴 Perdidos", n_perdidos)
        m[4].metric("👣 Visitas", visitas_tot)

        f1, f2, f3 = st.columns(3)
        f1.metric("💰 Comisiones ganadas", matcher.formato_cop(com_ganadas) or "$0")
        f2.metric("⏳ Comisiones en juego (activos)", matcher.formato_cop(com_en_juego) or "$0")
        f3.metric("📤 Envíos totales", enviados_tot)

        # ── Alerta de cobertura: a quién tenemos descuidado ──
        activos_list = [c for c in crm_clientes if c["estado"] == "activo"]
        sin_cubrir = [c["nombre"] for c in activos_list if cobertura[c["nombre"]][0] == 0]
        sin_movimiento = [c["nombre"] for c in activos_list
                          if cobertura[c["nombre"]][0] > 0 and (cobertura[c["nombre"]][1] or 0) >= 14]
        g1, g2, g3 = st.columns(3)
        g1.metric("🔴 Activos sin cubrir", len(sin_cubrir), help="Clientes activos con 0 envíos.")
        g2.metric("⏰ Sin enviar +14 días", len(sin_movimiento),
                  help="Activos a los que no les mandas algo hace más de 2 semanas.")
        g3.metric("🟢 Bien cubiertos (3+)",
                  sum(1 for c in activos_list if cobertura[c["nombre"]][0] >= 3))
        descuidados = sorted(set(sin_cubrir + sin_movimiento))
        if descuidados:
            st.warning("👀 **Ojo, tienes clientes descuidados:** " + " · ".join(descuidados)
                       + ". Mándales opciones para no perderlos.")

        # ── Proyección cerrando TODOS los activos: directo y compartido con aliado ──
        ganados_c = [c for c in crm_clientes if c["estado"] == "ganado"]

        def _proyeccion(op_filtro):
            """(ya ganado, potencial cerrando todos los activos) para un tipo de operación."""
            gan = sum(comision_potencial(c) for c in ganados_c
                      if (c.get("operacion") or "venta").lower() == op_filtro)
            pot = sum(comision_potencial(c) for c in activos_list
                      if (c.get("operacion") or "venta").lower() == op_filtro)
            return gan, pot

        with st.expander("📈 Proyección de ganancias (cerrando todos los activos)", expanded=True):
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
        filtro = st.radio("Ver", ["Todos", "🟡 Activos", "🟢 Ganados", "🔴 Perdidos"],
                          horizontal=True)
        mapa_filtro = {"🟡 Activos": "activo", "🟢 Ganados": "ganado", "🔴 Perdidos": "perdido"}

        for c in crm_clientes:
            if filtro != "Todos" and c["estado"] != mapa_filtro.get(filtro):
                continue
            nombre = c["nombre"]
            enviados = c.get("inmuebles_enviados") or []
            com_actual = float(c.get("comision") or 0)
            n_env, dias_ult = cobertura[nombre]
            cab = (f"{ESTADOS_CRM.get(c['estado'], c['estado'])}  ·  **{nombre}**  ·  "
                   f"{cobertura_emoji(n_env)} {n_env} envío(s)  ·  👣 {c.get('visitas', 0)} visita(s)")
            if dias_ult is not None:
                cab += f"  ·  último hace {dias_ult}d"
            if com_actual > 0:
                cab += f"  ·  💰 {matcher.formato_cop(com_actual)}"
            with st.container(border=True):
                st.markdown(cab)

                op = (c.get("operacion") or "venta").lower()
                es_arriendo = op == "arriendo"
                with st.form(key=f"crm_form_{nombre}"):
                    col1, col2 = st.columns([1, 1])
                    estado = col1.selectbox(
                        "Estado del negocio", ["activo", "ganado", "perdido"],
                        index=["activo", "ganado", "perdido"].index(c["estado"]),
                        format_func=lambda e: ESTADOS_CRM[e], key=f"est_{nombre}")
                    visitas = col2.number_input("Visitas realizadas", min_value=0,
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

                    enviados_txt = st.text_area(
                        "Inmuebles enviados (uno por línea)",
                        value="\n".join(enviados), height=100, key=f"env_txt_{nombre}",
                        help="Se llena solo cuando marcas inmuebles en Coincidencias, "
                             "pero también puedes editarlo a mano.")
                    notas_crm = st.text_area("Notas de seguimiento",
                                             value=c.get("notas_crm", ""),
                                             height=70, key=f"ncrm_{nombre}")
                    if st.form_submit_button("💾 Guardar seguimiento", type="primary"):
                        com_final = int(comision) if int(comision) > 0 \
                            else comision_sugerida(op, valor_cierre)
                        mod_clientes.actualizar_crm(nombre, {
                            "estado": estado,
                            "visitas": int(visitas),
                            "valor_cierre": int(valor_cierre),
                            "comision": com_final,
                            "inmuebles_enviados": [l.strip() for l in enviados_txt.splitlines() if l.strip()],
                            "notas_crm": notas_crm.strip(),
                        })
                        st.success(f"Seguimiento de {nombre} guardado.")
                        st.rerun()

                # Embudo de inmuebles en proceso (fuera del formulario: tiene botones).
                st.divider()
                render_procesos(c)
