"""Ficha de búsquedas en PDF para compartir con otras inmobiliarias.

Genera un documento elegante con la identidad NŌVA (dorado + terracota + crema),
con los requerimientos de los clientes ACTIVOS, el nombre anonimizado
("Alfonso R.") y CON las notas del requerimiento (limpiadas de teléfonos/correos),
pero sin datos de contacto ni seguimiento privado del CRM.
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

from fpdf import FPDF

from .matcher import formato_cop

# Paleta NŌVA.
DORADO = (176, 141, 87)        # #B08D57 — el dorado del logo
DORADO_SUAVE = (216, 196, 160)
TERRACOTA = (169, 113, 75)     # #A9714B
CAFE = (62, 47, 37)            # #3E2F25 — texto principal
CAFE_MEDIO = (107, 79, 58)     # #6B4F3A
CREMA = (250, 243, 234)        # #FAF3EA — fondo de tarjetas
GRIS_CALIDO = (140, 125, 110)

ETIQUETA_EXTRA = {"cuarto_servicio": "cuarto de servicio", "balcon": "balcón",
                  "deposito": "depósito", "duplex": "dúplex"}
ETIQUETA_OBLIG = {"barrio": "zona", "presupuesto": "presupuesto", "habitaciones":
                  "habitaciones", "banos": "baños", "metraje": "metraje", "extras": "extras"}


def _anon(nombre: str) -> str:
    """'Alfonso Rubiano' → 'Alfonso R.' (identificable solo para el broker)."""
    partes = [p for p in str(nombre or "").strip().split() if p]
    if not partes:
        return "Cliente"
    if len(partes) == 1:
        return partes[0].capitalize()
    return f"{partes[0].capitalize()} {partes[1][0].upper()}."


def _latin(t: str) -> str:
    """Blinda el texto al alfabeto del PDF (los emojis se omiten)."""
    return str(t).encode("latin-1", "ignore").decode("latin-1")


def _sanitizar(t: str) -> str:
    """Limpia datos privados de un texto antes de compartirlo (teléfonos, correos, links)."""
    t = re.sub(r"\S+@\S+", " ", str(t or ""))
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\+?\d[\d .\-]{6,}\d", " ", t)   # números tipo teléfono
    t = re.sub(r"\b(tel|cel|celular|whatsapp|wpp|contacto)\b[.:]?", " ", t, flags=re.I)
    return " ".join(t.split()).strip()


def _linea_specs(c: dict[str, Any]) -> list[str]:
    """Las líneas DETALLADAS de un cliente (con **negrilla** en las etiquetas)."""
    lineas: list[str] = []
    exc = c.get("exclusiones") or {}
    prefs = c.get("preferencias_evitar") or {}

    # Qué busca (tipo de inmueble, si se sabe)
    tipo = str(c.get("tipo") or exc.get("tipo") or "").strip()
    if tipo:
        lineas.append(f"**Busca:** {tipo.capitalize()}")

    # Dónde
    ubic = ", ".join(c.get("barrios") or [])
    zona = (c.get("zona") or "").strip()
    if ubic and zona:
        lineas.append(f"**Zona:** {ubic}  ({zona})")
    elif ubic or zona:
        lineas.append(f"**Zona:** {ubic or zona}")
    exc_barrios = exc.get("barrios") or []
    if exc_barrios:
        vista = ", ".join(exc_barrios[:5]) + (" y más" if len(exc_barrios) > 5 else "")
        lineas.append(f"**No busca en:** {vista}")

    # Presupuesto
    if c.get("presupuesto_max"):
        tope = formato_cop(c["presupuesto_max"])
        lineas.append(f"**Presupuesto:** hasta {tope}"
                      + (" mensuales (canon + admin)"
                         if (c.get("operacion") or "") == "arriendo" else ""))

    # Espacio: metraje + habitaciones + baños (incluye topes aprendidos)
    partes: list[str] = []
    amin = c.get("area_min")
    amax = c.get("area_max") or exc.get("area_max")
    if amin and amax:
        partes.append(f"{amin:g}-{amax:g} m2")
    elif amin:
        partes.append(f"desde {amin:g} m2")
    elif amax:
        partes.append(f"hasta {amax:g} m2")
    hmin = c.get("habitaciones_min")
    hmax = c.get("habitaciones_max") or exc.get("habitaciones_max")
    if hmin and hmax and hmin == hmax:
        partes.append(f"exactamente {hmin:g} hab")
    elif hmin and hmax:
        partes.append(f"{hmin:g}-{hmax:g} hab")
    elif hmin:
        partes.append(f"{hmin:g}+ hab")
    if c.get("banos_min"):
        partes.append(f"{c['banos_min']:g}+ baños")
    if partes:
        lineas.append("**Espacio:** " + "  ·  ".join(partes))

    # Antigüedad deseada (aprendida de sus afinaciones)
    ant = exc.get("antiguedad_max")
    if ant is not None:
        lineas.append("**Antigüedad:** " + ("para estrenar / obra nueva" if ant == 0
                      else f"máximo {ant:g} años de construido"))

    # Lo que idealmente debe tener (extras pedidos + los aprendidos de sus descartes)
    extras = list(dict.fromkeys((c.get("extras") or []) + (prefs.get("extras") or [])))
    extras_leg = [ETIQUETA_EXTRA.get(e, e).replace("_", " ") for e in extras]
    if extras_leg:
        lineas.append("**Ideal con:** " + ", ".join(extras_leg))

    # Lo que EVITA (aprendido de descartes y comentarios del broker)
    evita = list(dict.fromkeys((exc.get("palabras") or []) + (prefs.get("palabras") or [])))
    if evita:
        lineas.append("**Evita:** " + ", ".join(evita[:6]))

    # No negociable
    oblig = [ETIQUETA_OBLIG.get(o, o) for o in (c.get("obligatorios") or [])]
    if oblig:
        lineas.append("**Sí o sí:** " + ", ".join(oblig))

    # El detalle en palabras del broker (sin datos privados)
    notas = _sanitizar(c.get("notas") or "")
    if notas:
        lineas.append(f"**Notas:** {notas}")

    # Qué tan exigente es (le dice al aliado qué tan afinado debe ser lo que ofrezca)
    flex = str(c.get("flexibilidad") or "medio").lower()
    if flex == "estricto":
        lineas.append("**Perfil:** cliente exigente - solo opciones que cumplan TODO")
    elif flex == "flexible":
        lineas.append("**Perfil:** abierto a opciones parecidas")
    return lineas


def _logo_texto(pdf: FPDF, x: float, y: float) -> None:
    """Recrea el logo NŌVA en tipografía dorada (cuando no hay logo subido)."""
    pdf.set_text_color(*DORADO)
    pdf.set_font("helvetica", "", 24)
    letras = ["N", "O", "V", "A"]
    esp = 3.2                                     # aire entre letras, estilo del logo
    pos_x = x
    for i, letra in enumerate(letras):
        pdf.set_xy(pos_x, y)
        pdf.cell(pdf.get_string_width(letra) + 1, 10, letra)
        if i == 1:  # macrón (la línea sobre la O del logo)
            w_o = pdf.get_string_width("O")
            pdf.set_draw_color(*DORADO)
            pdf.set_line_width(0.7)
            pdf.line(pos_x + w_o * 0.18, y - 0.8, pos_x + w_o * 0.82, y - 0.8)
        pos_x += pdf.get_string_width(letra) + esp
    pdf.set_font("helvetica", "", 8.5)
    sub = "I N M O B I L I A R I A"
    pdf.set_xy(x + 0.5, y + 10.5)
    pdf.cell(0, 4, sub)


def generar_pdf(clientes: list[dict[str, Any]], quien: str = "Nova Inmobiliaria",
                logo_png: bytes | None = None) -> bytes:
    """Genera el PDF de búsquedas activas, listo para compartir."""
    activos = [c for c in clientes if (c.get("estado") or "activo") == "activo"]
    hoy = date.today().strftime("%d/%m/%Y")

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    def encabezado() -> None:
        # Membrete blanco con el logo (subido o recreado) y filo dorado.
        if logo_png:
            try:
                pdf.image(io.BytesIO(logo_png), x=12, y=5, h=17)
            except Exception:  # noqa: BLE001 - logo dañado → texto
                _logo_texto(pdf, 12, 6)
        else:
            _logo_texto(pdf, 12, 6)
        pdf.set_text_color(*GRIS_CALIDO)
        pdf.set_font("helvetica", "", 9)
        pdf.set_xy(-58, 8)
        pdf.cell(46, 5, _latin(f"Corte: {hoy}"), align="R")
        pdf.set_text_color(*CAFE_MEDIO)
        pdf.set_font("helvetica", "I", 10.5)
        pdf.set_xy(12, 24)
        pdf.cell(0, 5, _latin("Búsquedas activas de nuestros clientes - "
                              "¿tienes algo que encaje?"))
        pdf.set_draw_color(*DORADO)
        pdf.set_line_width(0.5)
        pdf.line(10, 32, 200, 32)
        pdf.set_y(38)

    encabezado()

    def _valor(c):
        return c.get("presupuesto_max") or 0

    # Ventas primero y, dentro de cada grupo, de MAYOR a menor presupuesto.
    venta = sorted([c for c in activos if (c.get("operacion") or "venta") != "arriendo"],
                   key=_valor, reverse=True)
    arriendo = sorted([c for c in activos if (c.get("operacion") or "venta") == "arriendo"],
                      key=_valor, reverse=True)

    for titulo_grupo, grupo in [("COMPRA", venta), ("ARRIENDO", arriendo)]:
        if not grupo:
            continue
        if pdf.get_y() > 255:
            pdf.add_page()
            pdf.set_y(14)
        pdf.set_text_color(*TERRACOTA)
        pdf.set_font("helvetica", "B", 13)
        titulo = _latin(f"Buscan en {titulo_grupo}  ({len(grupo)})")
        pdf.cell(pdf.get_string_width(titulo) + 2, 8, titulo)
        # detalle dorado: línea fina que completa el renglón
        y_lin = pdf.get_y() + 4.5
        pdf.set_draw_color(*DORADO_SUAVE)
        pdf.set_line_width(0.3)
        pdf.line(pdf.get_x() + 3, y_lin, 200, y_lin)
        pdf.ln(9)

        for c in grupo:
            lineas = [_latin(l) for l in _linea_specs(c)]
            pdf.set_font("helvetica", "", 10)
            n_render = sum(len(pdf.multi_cell(174, 5.2, l, dry_run=True, output="LINES",
                                              markdown=True))
                           for l in lineas) or 1
            alto = 9 + n_render * 5.2 + 5
            if pdf.get_y() + alto > 281:
                pdf.add_page()
                pdf.set_y(14)
            y0 = pdf.get_y()
            # Tarjeta crema con barra terracota y filo dorado inferior
            pdf.set_fill_color(*CREMA)
            pdf.rect(10, y0, 190, alto, "F")
            pdf.set_fill_color(*TERRACOTA)
            pdf.rect(10, y0, 2.2, alto, "F")
            pdf.set_draw_color(*DORADO_SUAVE)
            pdf.set_line_width(0.25)
            pdf.line(12.2, y0 + alto, 200, y0 + alto)
            # Nombre (anonimizado) en café + puntico dorado
            pdf.set_xy(16, y0 + 3)
            pdf.set_text_color(*CAFE_MEDIO)
            pdf.set_font("helvetica", "B", 11.5)
            nombre = _latin(_anon(c.get("nombre", "")))
            pdf.cell(pdf.get_string_width(nombre) + 1, 6, nombre)
            pdf.set_text_color(*DORADO)
            pdf.cell(4, 6, "·")
            # Cuerpo
            pdf.set_text_color(*CAFE)
            pdf.set_font("helvetica", "", 10)
            pdf.set_xy(16, y0 + 9.5)
            for l in lineas:
                pdf.set_x(16)
                pdf.multi_cell(176, 5.2, l, markdown=True)
            pdf.set_y(y0 + alto + 3.5)

    # Pie
    if pdf.get_y() > 265:
        pdf.add_page()
    pdf.ln(2)
    pdf.set_text_color(*GRIS_CALIDO)
    pdf.set_font("helvetica", "I", 9.5)
    pdf.multi_cell(0, 5, _latin("Si tienes un inmueble que encaje con alguna de estas "
                                "búsquedas, escríbenos y coordinamos en alianza. - "
                                f"{quien}"))
    return bytes(pdf.output())

def pdf_fuentes(cuentas: list[dict[str, Any]], portales: list[str],
                logo_png: bytes | None = None) -> bytes:
    """PDF interno con TODAS las fuentes y sus links, para revisión manual.

    `cuentas`: [{"usuario", "n_vigentes", "dias_ultima", "restringida"}]
    OJO: es de uso interno del broker (revela las fuentes) — no circular.
    """
    hoy = date.today().strftime("%d/%m/%Y")
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    if logo_png:
        try:
            pdf.image(io.BytesIO(logo_png), x=12, y=5, h=17)
        except Exception:  # noqa: BLE001
            _logo_texto(pdf, 12, 6)
    else:
        _logo_texto(pdf, 12, 6)
    pdf.set_text_color(*GRIS_CALIDO)
    pdf.set_font("helvetica", "", 9)
    pdf.set_xy(-58, 8)
    pdf.cell(46, 5, _latin(f"Corte: {hoy}"), align="R")
    pdf.set_text_color(*CAFE_MEDIO)
    pdf.set_font("helvetica", "I", 10.5)
    pdf.set_xy(12, 24)
    pdf.cell(0, 5, _latin("Fuentes que monitoreamos - documento INTERNO "
                          "(revela las fuentes: no circular)"))
    pdf.set_draw_color(*DORADO)
    pdf.set_line_width(0.5)
    pdf.line(10, 32, 200, 32)
    pdf.set_y(38)

    def titulo_seccion(texto: str) -> None:
        if pdf.get_y() > 255:
            pdf.add_page()
            pdf.set_y(14)
        pdf.set_text_color(*TERRACOTA)
        pdf.set_font("helvetica", "B", 13)
        t = _latin(texto)
        pdf.cell(pdf.get_string_width(t) + 2, 8, t)
        y_lin = pdf.get_y() + 4.5
        pdf.set_draw_color(*DORADO_SUAVE)
        pdf.set_line_width(0.3)
        pdf.line(pdf.get_x() + 3, y_lin, 200, y_lin)
        pdf.ln(10)

    titulo_seccion(f"Perfiles de Instagram  ({len(cuentas)})")
    for c in cuentas:
        if pdf.get_y() > 270:
            pdf.add_page()
            pdf.set_y(14)
        url = f"https://www.instagram.com/{c['usuario']}/"
        pdf.set_text_color(*CAFE)
        pdf.set_font("helvetica", "B", 10.5)
        etiqueta = "@" + c["usuario"] + ("   [!] restringida - revisar manual"
                                         if c.get("restringida") else "")
        pdf.cell(92, 6, _latin(etiqueta), link=url)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(*CAFE_MEDIO)
        detalle = f"{c.get('n_vigentes', 0)} publicacion(es) vigentes"
        if c.get("dias_ultima") is not None:
            detalle += f" - ultima hace {c['dias_ultima']} dia(s)"
        pdf.cell(60, 6, _latin(detalle))
        pdf.set_text_color(*DORADO)
        pdf.set_font("helvetica", "U", 8.5)
        pdf.cell(0, 6, "abrir perfil", link=url, align="R")
        pdf.ln(6.5)

    pdf.ln(4)
    titulo_seccion(f"Portales y sitios web  ({len(portales)})")
    for u in portales:
        if pdf.get_y() > 270:
            pdf.add_page()
            pdf.set_y(14)
        pdf.set_text_color(*CAFE)
        pdf.set_font("helvetica", "", 9.5)
        pdf.cell(4, 6, "-")
        pdf.set_text_color(*TERRACOTA)
        pdf.set_font("helvetica", "U", 9.5)
        pdf.cell(0, 6, _latin(u if len(u) <= 95 else u[:92] + "..."), link=u)
        pdf.ln(6.5)

    pdf.ln(6)
    pdf.set_text_color(*GRIS_CALIDO)
    pdf.set_font("helvetica", "I", 8.5)
    pdf.multi_cell(0, 4.5, _latin("Los links son clicables. Semaforo de la app: "
                                  "verde hasta 3 dias, amarillo 4-10, naranja 11-20, "
                                  "rojo 21-30. Generado por Nova Scraping."))
    return bytes(pdf.output())
