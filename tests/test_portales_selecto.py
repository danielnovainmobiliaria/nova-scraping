"""Lector estructurado de somosselecto.com (Webflow, campos fs-list-field)."""
from pathlib import Path

from src import portales_directo as pd

FIXTURE = Path(__file__).parent / "fixtures" / "selecto_dos_tarjetas.html"


def _items():
    html_txt = FIXTURE.read_text(encoding="utf-8")
    cortes = [m.start() for m in pd._RE_TARJETA_SELECTO.finditer(html_txt)] + [len(html_txt)]
    return [it for a, b in zip(cortes, cortes[1:]) if (it := pd._mapear_selecto(html_txt[a:b]))]


def test_lee_las_dos_tarjetas_con_sus_campos():
    a, b = _items()
    assert a["datos"]["operacion"] == "arriendo" and a["datos"]["precio"] == 25_000_000
    assert a["datos"]["barrio"] == "Chico Oriental" and a["datos"]["zona"] == "Chapinero"
    assert (a["datos"]["habitaciones"], a["datos"]["banos"], a["datos"]["parqueaderos"]) == (3, 4, 2)
    assert a["datos"]["area_m2"] == 235
    assert a["url"].startswith("https://www.somosselecto.com/properties/")
    assert a["imagen"].startswith("https://")
    assert b["datos"]["operacion"] == "venta" and b["datos"]["precio"] == 7_500_000_000
    assert b["datos"]["administracion"] == 3_941_100
    assert "Balcón" in b["datos"]["extras"]


def test_el_barrio_sale_del_titulo_y_sin_el_en():
    assert pd._barrio_del_titulo("Casa campestre en venta, en La Calera.") == "La Calera"
    assert pd._barrio_del_titulo("Apartamento a la venta con terraza en VIDERE, Chico Oriental.") == "Chico Oriental"


def test_soporta_selecto_por_lectura_directa():
    assert pd.soporta("https://www.somosselecto.com/propiedades")
