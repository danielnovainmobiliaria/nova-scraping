"""Nova Scraping — App web.

Ejecuta:  streamlit run app.py

Flujo:
  1. Configuras las cuentas de Instagram a monitorear.
  2. Cargas tu Excel de clientes (o usas el modo demo).
  3. Actualizas (trae posts de los últimos 30 días y los lee con IA).
  4. Ves las coincidencias por cliente y las compartes.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from src import clientes as mod_clientes
from src import config, db, matcher
from src.sample_data import CLIENTES_DEMO, POSTS_DEMO

st.set_page_config(page_title="Nova Scraping", page_icon="🏙️", layout="wide")
db.init_db()

EXTRAS_LEGIBLES = {
    "cuarto_servicio": "cuarto de servicio", "balcon": "balcón",
}


def bonito(extra: str) -> str:
    return EXTRAS_LEGIBLES.get(extra, extra)


def fecha_corte_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)).date().isoformat()


# ── Barra lateral ─────────────────────────────────────────────
st.sidebar.title("🏙️ Nova Scraping")
modo = st.sidebar.radio(
    "Modo de trabajo",
    ["Demo (sin costo)", "Real (Instagram + IA)"],
    help="El modo Demo usa datos de ejemplo para que veas cómo funciona el cruce "
         "sin gastar nada. El modo Real trae publicaciones de Instagram con Apify "
         "y las lee con Claude.",
)
es_demo = modo.startswith("Demo")

st.sidebar.divider()
tiene_llaves = bool(config.APIFY_TOKEN and config.ANTHROPIC_API_KEY)
estado = "✅ Listas" if tiene_llaves else "❌ Faltan"
with st.sidebar.expander(f"🔑 Mis llaves — {estado}", expanded=not tiene_llaves and not es_demo):
    st.caption("Pega aquí tus llaves. Se guardan en tu computador y no hay que volver "
               "a hacerlo. (El modo Demo no las necesita.)")
    apify_in = st.text_input("Llave de Apify", value=config.APIFY_TOKEN,
                             type="password", placeholder="apify_api_...")
    claude_in = st.text_input("Llave de Claude (Anthropic)", value=config.ANTHROPIC_API_KEY,
                              type="password", placeholder="sk-ant-...")
    if st.button("💾 Guardar mis llaves"):
        config.guardar_llaves(apify_in, claude_in)
        st.success("¡Guardadas! Ya puedes usar el modo Real.")
        st.rerun()

# ── Pestañas ──────────────────────────────────────────────────
tab_fuentes, tab_clientes, tab_resultados = st.tabs(
    ["1️⃣ Fuentes (Instagram)", "2️⃣ Clientes", "3️⃣ Coincidencias"]
)

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

    st.divider()
    st.subheader("Actualizar publicaciones")
    if es_demo:
        st.info("Estás en **modo Demo**: se usan 5 publicaciones de ejemplo. "
                "Cambia a **modo Real** en la barra lateral para traer posts reales.")
    else:
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

# ===== 2. CLIENTES ===========================================
EXTRAS_OPCIONES = [
    "estudio", "terraza", "balcon", "cuarto_servicio", "deposito",
    "parqueadero", "vista", "remodelado", "amoblado", "chimenea",
    "duplex", "penthouse",
]
ETIQUETA_EXTRA = {"cuarto_servicio": "cuarto de servicio", "balcon": "balcón"}


def num_o_none(v):
    """Convierte 0/vacío en None (0 = 'no especificado')."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v and v > 0 else None


def lista_a_texto(v) -> str:
    """['El Nogal','Rosales'] -> 'El Nogal, Rosales'."""
    if isinstance(v, list):
        return ", ".join(v)
    return str(v or "")


def texto_a_lista(v) -> list[str]:
    """'El Nogal, Rosales' -> ['El Nogal','Rosales']."""
    return [x.strip() for x in str(v or "").replace(";", ",").split(",") if x.strip()]


# Columnas de la "hoja de clientes" dentro de la app.
COLS_HOJA = ["nombre", "operacion", "barrios", "zona", "presupuesto_max",
             "area_min", "area_max", "habitaciones_min", "banos_min", "extras", "notas"]


def clientes_a_df(lista):
    """Convierte la lista de clientes (interna) en una tabla editable."""
    filas = []
    for c in lista:
        filas.append({
            "nombre": c.get("nombre", ""),
            "operacion": c.get("operacion", "venta"),
            "barrios": lista_a_texto(c.get("barrios")),
            "zona": c.get("zona", ""),
            "presupuesto_max": c.get("presupuesto_max"),
            "area_min": c.get("area_min"),
            "area_max": c.get("area_max"),
            "habitaciones_min": c.get("habitaciones_min"),
            "banos_min": c.get("banos_min"),
            "extras": lista_a_texto(c.get("extras")),
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
            "operacion": str(fila.get("operacion", "") or "venta").strip().lower(),
            "barrios": texto_a_lista(fila.get("barrios")),
            "zona": str(fila.get("zona", "") or "").strip(),
            "presupuesto_max": num_o_none(fila.get("presupuesto_max")),
            "area_min": num_o_none(fila.get("area_min")),
            "area_max": num_o_none(fila.get("area_max")),
            "habitaciones_min": num_o_none(fila.get("habitaciones_min")),
            "banos_min": num_o_none(fila.get("banos_min")),
            "extras": [e.lower() for e in texto_a_lista(fila.get("extras"))],
            "perimetro": "",
            "notas": str(fila.get("notas", "") or "").strip(),
        })
    return out


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

    if es_demo:
        st.session_state["clientes"] = CLIENTES_DEMO
        st.info("Modo Demo: se usan 3 clientes de ejemplo. Cambia a modo Real "
                "para administrar tus clientes de verdad.")
        st.dataframe(clientes_a_df(CLIENTES_DEMO), use_container_width=True, hide_index=True)
    else:
        st.caption("Esta es tu **hoja de clientes**. Funciona como un Excel: escribe en las "
                   "celdas, agrega filas con el **+** de abajo, o borra una fila seleccionándola. "
                   "Al terminar, dale **Guardar**. Descarga tu copia maestra cuando quieras.")
        with st.expander("ℹ️ Cómo llenar cada columna"):
            st.markdown(
                "- **operacion**: escribe `venta` o `arriendo`.\n"
                "- **barrios**: varios separados por coma → `El Nogal, Rosales`.\n"
                "- **presupuesto_max**: número en pesos sin puntos → `1900000000`.\n"
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
                        for c in nuevos:
                            mod_clientes.agregar_o_actualizar(c)
                        st.success(f"¡Se agregaron {len(nuevos)} cliente(s)! "
                                   "Revísalos en la tabla y dale Guardar si todo está bien.")
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"No se pudo procesar el archivo: {e}")

        df_actual = clientes_a_df(mod_clientes.cargar_guardados())
        editado = st.data_editor(
            df_actual, num_rows="dynamic", use_container_width=True, hide_index=True,
            key="editor_clientes",
            column_config={
                "nombre": st.column_config.TextColumn("nombre", required=True),
                "operacion": st.column_config.SelectboxColumn(
                    "operacion", options=["venta", "arriendo"]),
                "presupuesto_max": st.column_config.NumberColumn(
                    "presupuesto_max", format="%d", help="En pesos, sin puntos"),
                "area_min": st.column_config.NumberColumn("area_min", format="%d"),
                "area_max": st.column_config.NumberColumn("area_max", format="%d"),
                "habitaciones_min": st.column_config.NumberColumn("habitaciones_min", format="%d"),
                "banos_min": st.column_config.NumberColumn("banos_min", format="%d"),
            },
        )

        c1, c2, c3 = st.columns(3)
        if c1.button("💾 Guardar cambios", type="primary", use_container_width=True):
            mod_clientes.guardar_lista(df_a_clientes(editado))
            st.success("¡Clientes guardados!")
            st.rerun()
        c2.download_button(
            "⬇️ Descargar copia (Excel)", excel_bytes(editado),
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
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo leer el Excel: {e}")

        st.session_state["clientes"] = mod_clientes.cargar_guardados()
        st.caption(f"👥 {len(st.session_state['clientes'])} cliente(s) guardado(s).")

# ===== 3. RESULTADOS =========================================
with tab_resultados:
    st.subheader("Coincidencias por cliente")
    clientes = st.session_state.get("clientes", [])

    if es_demo:
        posts = POSTS_DEMO
    else:
        posts = db.posts_recientes(fecha_corte_iso())

    if not clientes:
        st.warning("Primero carga tus clientes en la pestaña **2️⃣ Clientes**.")
    elif not posts:
        st.warning("Aún no hay publicaciones. Ve a la pestaña **1️⃣ Fuentes** y "
                   "actualiza (o usa el modo Demo).")
    else:
        st.caption("Ajusta qué tan flexible quieres ser. La búsqueda muestra "
                   "opciones **similares**, no solo idénticas.")
        c1, c2, c3 = st.columns(3)
        score_min = c1.slider("Coincidencia mínima (%)", 0, 100, 50, 5,
                              help="Sube el valor para ver solo los matches más fuertes.")
        flex_precio = c2.slider("Flexibilidad en presupuesto (%)", 0, 40, 15, 5,
                                help="Cuánto por encima del presupuesto se permite. "
                                     "Ej: 15% deja ver opciones hasta 15% más caras.")
        flex_area = c3.slider("Flexibilidad en metraje (%)", 0, 40, 15, 5,
                              help="Cuánto por fuera del rango de m² se permite.")
        resultados = matcher.cruzar(
            clientes, posts, score_minimo=score_min,
            flex_precio=flex_precio / 100, flex_area=flex_area / 100,
        )

        total = sum(len(v) for v in resultados.values())
        st.caption(f"{len(posts)} publicaciones analizadas · {total} coincidencias encontradas")

        for nombre, matches in resultados.items():
            with st.expander(f"👤 {nombre} — {len(matches)} coincidencia(s)",
                             expanded=bool(matches)):
                if not matches:
                    st.write("Sin coincidencias por ahora.")
                    continue
                for m in matches:
                    p = m["post"]
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"**{p.get('resumen') or p.get('caption','')[:80]}**")
                        info = []
                        if p.get("operacion"): info.append(p["operacion"].capitalize())
                        if p.get("barrio"): info.append(p["barrio"])
                        if p.get("precio"): info.append(f"${p['precio']:,.0f}")
                        if p.get("area_m2"): info.append(f"{p['area_m2']:g} m²")
                        if p.get("habitaciones") is not None: info.append(f"{p['habitaciones']:g} hab")
                        if p.get("banos") is not None: info.append(f"{p['banos']:g} baños")
                        st.caption(" · ".join(info))
                        if m["razones_ok"]:
                            st.markdown("✅ " + " · ".join(m["razones_ok"]))
                        if m["razones_no"]:
                            st.markdown("⚠️ " + " · ".join(m["razones_no"]))
                        if p.get("url"):
                            st.markdown(f"[Ver publicación]({p['url']}) · @{p.get('cuenta','')} · {p.get('fecha','')}")
                        # Mensaje listo para compartir por WhatsApp.
                        extras_txt = ", ".join(bonito(e) for e in p.get("extras", []))
                        mensaje = (
                            f"Hola {nombre.split()[0]}, encontré esta opción que puede interesarte:\n"
                            f"{p.get('resumen','')}\n"
                            f"{p.get('barrio','')} · {p.get('area_m2','?')} m² · "
                            f"{p.get('habitaciones','?')} hab · {p.get('banos','?')} baños\n"
                            + (f"Extras: {extras_txt}\n" if extras_txt else "")
                            + (f"Valor: ${p['precio']:,.0f}\n" if p.get('precio') else "")
                            + (f"{p.get('url','')}" if p.get('url') else "")
                        )
                        with st.popover("📲 Texto para compartir"):
                            st.code(mensaje, language=None)
                    with c2:
                        st.metric("Coincidencia", f"{m['score']}%")
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
