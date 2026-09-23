"""La ficha completa del candidato: completa sin pisar, y detecta lo que se fue."""
import json
from src import enriquecer as en

NEXT = '<script id="__NEXT_DATA__" type="application/json">%s</script>'


def _pagina(data):
    return NEXT % json.dumps({"props": {"pageProps": {"data": data}}})


def test_fincaraiz_trae_piso_antiguedad_y_texto():
    disp, datos, texto = en._ficha_fincaraiz(_pagina({
        "floor": 10, "antiquity": 5, "garage": 1, "stratum": 6, "sold": False,
        "title": "Apto en Chicó", "description": "Vista a los cerros, remodelado.",
        "facilities": [{"name": "Ascensor"}, {"name": "Terraza"}]}))
    assert disp is True
    assert datos == {"piso": 10, "antiguedad_anos": 5, "parqueaderos": 1, "estrato": 6}
    assert "remodelado" in texto and "Terraza" in texto


def test_fincaraiz_vendido_se_marca_como_ido():
    disp, _, _ = en._ficha_fincaraiz(_pagina({"sold": True, "floor": 2}))
    assert disp is False


def test_fusionar_no_pisa_lo_que_ya_tenia_y_suma_extras():
    post = {"id": "portal_1", "precio": 900000000, "piso": None, "extras": ["balcon"], "caption": "Apto"}
    out = en.fusionar(post, {"piso": 7}, {"precio": 1, "piso": 3, "extras": ["terraza"], "habitaciones": 2}, "Descripción larga")
    assert out["precio"] == 900000000          # lo de la lista manda
    assert out["piso"] == 7                    # lo estructurado del portal manda sobre la IA
    assert out["extras"] == ["balcon", "terraza"]
    assert out["habitaciones"] == 2            # hueco que llena la IA
    assert "Descripción larga" in out["caption"]


def test_solo_portales_y_no_metrocuadrado():
    assert en.se_puede_enriquecer({"id": "portal_x", "url": "https://coolhouse.com.co/propiedad/1/"})
    assert not en.se_puede_enriquecer({"id": "portal_x", "url": "https://www.metrocuadrado.com/inmueble/1"})
    assert not en.se_puede_enriquecer({"id": "3459812", "url": "https://www.instagram.com/p/x/"})


def test_penalizacion_aprendida_no_esconde():
    from src import matcher
    assert matcher.PEN_TOPE <= 15   # con umbral 80 y tarjetas de 82-100, más que esto las esconde
