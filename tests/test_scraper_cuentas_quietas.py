"""Una cuenta pública sin nada nuevo NO es una cuenta bloqueada.

El actor de Apify responde "no_items" igual para una cuenta privada que para
una pública que no publicó después del corte. Verificado el 2026-09-23 con
@mauricioduranrealtor (pública): pedirle solo lo de hoy → "no_items".
"""
from src.scraper import _clasificar_error

NO_ITEMS = {"error": "no_items", "errorDescription": "Empty or private data for provided input"}


def test_cuenta_ya_leida_sin_novedad_no_es_falla():
    assert _clasificar_error(NO_ITEMS, primera_lectura=False) == "sin_novedad"


def test_cuenta_nunca_leida_sin_posts_si_es_falla():
    # Se le pidió el mes entero y no salió nada: privada o restringida.
    assert _clasificar_error(NO_ITEMS, primera_lectura=True) == "falla"


def test_otros_errores_siguen_siendo_falla():
    assert _clasificar_error({"error": "not_found"}, primera_lectura=False) == "falla"
    assert _clasificar_error({"error": "blocked"}, primera_lectura=False) == "falla"
