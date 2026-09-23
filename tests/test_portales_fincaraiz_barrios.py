"""Fincaraíz por barrio: la búsqueda de toda Bogotá se abre por cada barrio
que piden los clientes, y los barrios que el portal no conoce se saltan."""
from src import portales_directo as pd

CIUDAD = "https://www.fincaraiz.com.co/venta/apartamentos/bogota/bogota-dc"


def test_slug_como_lo_arma_fincaraiz():
    assert pd.slug_barrio("Chicó Navarra") == "chico-navarra"
    assert pd.slug_barrio(" La Cabrera ") == "la-cabrera"
    assert pd.slug_barrio("Santa Bárbara Oriental") == "santa-barbara-oriental"


def test_busquedas_por_barrio_sin_repetir():
    urls = pd.busquedas_por_barrio(CIUDAD, ["Chicó", "chico", "Rosales", "", "Bogotá"])
    assert urls == ["https://www.fincaraiz.com.co/venta/apartamentos/bogota/chico",
                    "https://www.fincaraiz.com.co/venta/apartamentos/bogota/rosales"]


def test_solo_se_abre_la_busqueda_general():
    assert pd.busquedas_por_barrio("https://www.fincaraiz.com.co/venta/apartamentos/bogota/chico",
                                   ["Rosales"]) == []
    assert pd.es_busqueda_de_toda_bogota(CIUDAD)
    assert not pd.es_busqueda_de_toda_bogota(CIUDAD.replace("bogota-dc", "chico"))


def test_titulo_distingue_barrio_de_ciudad_entera():
    assert pd.titulo_es_de_barrio("Apartamentos en Venta en Chico, Bogotá")
    assert pd.titulo_es_de_barrio("Apartamentos en Arriendo en Santa ana oriental, Bogotá")
    assert not pd.titulo_es_de_barrio("Apartamentos en Venta en Bogotá, d.c.")
    assert not pd.titulo_es_de_barrio("Apartamentos en Venta en Bogotá")


def test_barrio_desconocido_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(pd, "_bajar", lambda url: "<title>Apartamentos en Venta en Bogotá, d.c.</title>")
    assert pd.leer_fincaraiz(CIUDAD.replace("bogota-dc", "parque-93"), solo_si_es_barrio=True) == []


def test_casas_abren_la_ruta_de_casas():
    urls = pd.busquedas_por_barrio(CIUDAD, ["Rosales"], casas=["Santa Bárbara", "Rosales"])
    assert urls == ["https://www.fincaraiz.com.co/venta/apartamentos/bogota/rosales",
                    "https://www.fincaraiz.com.co/venta/casas/bogota/santa-barbara",
                    "https://www.fincaraiz.com.co/venta/casas/bogota/rosales"]
