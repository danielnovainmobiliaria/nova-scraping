"""Ficha de búsquedas en PDF para compartir con otras inmobiliarias.

Genera un documento elegante con los requerimientos de los clientes ACTIVOS,
con el nombre anonimizado ("Alfonso R.") y SIN datos privados (ni teléfono,
ni notas): solo lo que un aliado necesita para saber si tiene algo que encaje.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fpdf import FPDF

from .matcher import formato_cop

# Paleta Nova (la misma de la app).
VERDE = (15, 110, 93)
VERDE_SUAVE = (232, 244, 241)
GRIS_TEXTO = (55, 65, 81)
GRIS_CLARO = (120, 130, 140)

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


def _linea_specs(c: dict[str, Any]) -> list[str]:
    """Las líneas de especificaciones de un cliente (solo lo que tenga dato)."""
    lineas: list[str] = []

    ubic = ", ".join(c.get("barrios") or [])
    zona = (c.get("zona") or "").strip()
    if ubic and zona:
        lineas.append(f"Zona: {ubic}  ({zona})")
    elif ubic or zona:
        lineas.append(f"Zona: {ubic or zona}")

    partes: list[str] = []
    if c.get("presupuesto_max"):
        tope = formato_cop(c["presupuesto_max"])
        partes.append(f"Presupuesto: hasta {tope}"
                      + (" mensuales" if (c.get("operacion") or "") == "arriendo" else ""))
    amin, amax = c.get("area_min"), c.get("area_max")
    if amin and amax:
        partes.append(f"{amin:g}-{amax:g} m2")
    elif amin:
        partes.append(f"desde {amin:g} m2")
    elif amax:
        partes.append(f"hasta {amax:g} m2")
    if c.get("habitaciones_min"):
        partes.append(f"{c['habitaciones_min']:g}+ hab")
    if c.get("banos_min"):
        partes.append(f"{c['banos_min']:g}+ baños")
    if partes:
        lineas.append("  ·  ".join(partes))

    extras = [ETIQUETA_EXTRA.get(e, e).replace("_", " ") for e in (c.get("extras") or [])]
    if extras:
        lineas.append("Ideal con: " + ", ".join(extras))

    oblig = [ETIQUETA_OBLIG.get(o, o) for o in (c.get("obligatorios") or [])]
    if oblig:
        lineas.append("No negociable: " + ", ".join(oblig))
    return lineas


def generar_pdf(clientes: list[dict[str, Any]], quien: str = "Nova Inmobiliaria") -> bytes:
    """Genera el PDF de búsquedas activas, listo para compartir."""
    activos = [c for c in clientes if (c.get("estado") or "activo") == "activo"]
    hoy = date.today().strftime("%d/%m/%Y")

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    def encabezado() -> None:
        pdf.set_fill_color(*VERDE)
        pdf.rect(0, 0, 210, 26, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("helvetica", "B", 17)
        pdf.set_xy(12, 6)
        pdf.cell(0, 8, _latin(quien.upper()))
        pdf.set_font("helvetica", "", 10.5)
        pdf.set_xy(12, 15)
        pdf.cell(0, 5, _latin("Búsquedas activas de nuestros clientes - "
                              "¿tienes algo que encaje?"))
        pdf.set_font("helvetica", "", 9)
        pdf.set_xy(-52, 8)
        pdf.cell(40, 5, _latin(f"Corte: {hoy}"), align="R")
        pdf.set_y(32)

    encabezado()

    venta = [c for c in activos if (c.get("operacion") or "venta") != "arriendo"]
    arriendo = [c for c in activos if (c.get("operacion") or "venta") == "arriendo"]

    for titulo_grupo, grupo in [("COMPRA", venta), ("ARRIENDO", arriendo)]:
        if not grupo:
            continue
        # Título del grupo
        if pdf.get_y() > 255:
            pdf.add_page()
            pdf.set_y(14)
        pdf.set_text_color(*VERDE)
        pdf.set_font("helvetica", "B", 13)
        pdf.cell(0, 8, _latin(f"Buscan en {titulo_grupo}  ({len(grupo)})"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        for c in grupo:
            lineas = [_latin(l) for l in _linea_specs(c)]
            # Altura de la tarjeta: título + líneas (con envoltura de texto).
            pdf.set_font("helvetica", "", 10)
            n_render = sum(len(pdf.multi_cell(176, 5.2, l, dry_run=True, output="LINES"))
                           for l in lineas) or 1
            alto = 9 + n_render * 5.2 + 5
            if pdf.get_y() + alto > 281:
                pdf.add_page()
                pdf.set_y(14)
            y0 = pdf.get_y()
            # Tarjeta con barra de acento
            pdf.set_fill_color(*VERDE_SUAVE)
            pdf.rect(10, y0, 190, alto, "F")
            pdf.set_fill_color(*VERDE)
            pdf.rect(10, y0, 2.2, alto, "F")
            # Título
            pdf.set_xy(16, y0 + 3)
            pdf.set_text_color(*VERDE)
            pdf.set_font("helvetica", "B", 11.5)
            pdf.cell(0, 6, _latin(f"{_anon(c.get('nombre', ''))}"))
            # Cuerpo
            pdf.set_text_color(*GRIS_TEXTO)
            pdf.set_font("helvetica", "", 10)
            pdf.set_xy(16, y0 + 9.5)
            for l in lineas:
                pdf.set_x(16)
                pdf.multi_cell(176, 5.2, l)
            pdf.set_y(y0 + alto + 3)

    # Pie
    if pdf.get_y() > 265:
        pdf.add_page()
    pdf.ln(2)
    pdf.set_text_color(*GRIS_CLARO)
    pdf.set_font("helvetica", "I", 9.5)
    pdf.multi_cell(0, 5, _latin("Si tienes un inmueble que encaje con alguna de estas "
                                "búsquedas, escríbenos y coordinamos en alianza. - "
                                f"{quien}"))
    return bytes(pdf.output())
