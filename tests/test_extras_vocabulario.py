"""El vocabulario de características: lo que Daniel no podía ni pedir.

Antes del 2026-09-14 había requerimientos que no se podían ESCRIBIR, porque la
palabra no existía en EXTRAS_VALIDOS. La ficha de Luisa Galindo lo confesaba:
"SÍ O SÍ piscina / club house. (El motor no tiene esa característica en su
lista, así que hay que verificarla a mano en cada coincidencia.)"

Los casos de abajo salieron de los 2.795 avisos reales y de las 23 fichas, no
de la imaginación. Los porcentajes que se citan están medidos sobre los 2.551
avisos que son inmueble y siguen disponibles.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matcher import (EXTRAS_SUAVES, SINONIMOS_EXTRA, _extra_cumplido,
                         _falla_obligatorio, es_extra_suave, evaluar)

# Un aviso de verdad no trae la lista `extras` con el vocabulario nuevo: los
# 2.551 se leyeron ANTES de que existiera. Todo sale de releer el caption.
BASE = {"es_inmueble": True, "operacion": "venta", "barrio": "Rosales",
        "precio": 1_000_000_000, "area_m2": 120, "habitaciones": 2, "banos": 2,
        "extras": []}


def aviso(caption: str, **extra):
    return {**BASE, "caption": caption, **extra}


class TestVocabularioNuevo:
    """Las palabras que faltaban, con los sinónimos que de verdad se escriben."""

    def test_nadie_escribe_club_house_pero_si_salon_comunal(self):
        # 19,4% de los avisos hablan de esto, casi siempre con otras palabras.
        p = aviso("El edificio cuenta con salón comunal, vigilancia 24/7 y zona bbq.")
        assert _extra_cumplido("club_house", p)

    def test_gym_cuenta_como_gimnasio(self):
        # "gym" tiene 3 letras: el filtro viejo de len < 4 se lo comía entero.
        assert _extra_cumplido("gimnasio", aviso("Zonas comunes: gym, sauna y turco."))

    def test_estar_de_tv_es_family_room(self):
        assert _extra_cumplido("family_room", aviso("3 habitaciones, estar de TV, estudio."))

    def test_zona_verde_privada_es_jardin(self):
        assert _extra_cumplido("jardin", aviso("Hermosa casa con zona verde privada en Cota."))

    def test_luz_natural_es_iluminado(self):
        assert _extra_cumplido("iluminado", aviso("Exterior, con excelente iluminación natural."))

    def test_luisa_ya_puede_pedir_piscina_o_club_house(self):
        # Lo que su ficha decía que el motor no sabía hacer.
        cli = {"extras": ["piscina o club_house"], "obligatorios": ["extras"]}
        con = aviso("El conjunto cuenta con club house, piscina y canchas de tenis.")
        solo_piscina = aviso("Edificio con piscina cubierta y jacuzzi.")
        sin_nada = aviso("Apartamento remodelado de 120 m2 con balcón.")
        assert _falla_obligatorio(cli, con) is None
        assert _falla_obligatorio(cli, solo_piscina) is None   # basta cualquiera
        assert _falla_obligatorio(cli, sin_nada) is not None


class TestTrampasDelLenguaje:
    """Tres confusiones que salieron de LEER los avisos, no de suponer."""

    def test_zona_social_no_es_club_house(self):
        # 277 avisos dicen "zona social" y casi siempre es la sala-comedor del
        # propio apartamento. Aceptarla habría inflado club_house de 494 a 673
        # avisos con puro falso positivo.
        p = aviso("Gran zona social a doble altura con chimenea y salida a la terraza.")
        assert not _extra_cumplido("club_house", p)

    def test_patio_de_ropas_no_es_jardin(self):
        # De los 52 avisos con "patio", 29 son "patio de ropas"/"patio interior".
        assert not _extra_cumplido("jardin", aviso("Cocina integral con patio de ropas amplio."))

    def test_sala_de_estar_no_es_family_room(self):
        # De los 127 avisos con la palabra "estar", la mayoría son la sala.
        assert not _extra_cumplido("family_room", aviso("Sala de estar, comedor y cocina abierta."))

    def test_sin_piscina_no_cuenta_como_piscina(self):
        assert not _extra_cumplido("piscina", aviso("Conjunto sin piscina, pero con gimnasio."))

    def test_el_club_house_dicho_en_plural_tambien_cuenta(self):
        # En español el plural se le pega a TODAS las palabras: el motor probaba
        # "salon socials" y nunca "salones sociales". Medido: 14 avisos vivos lo
        # anuncian así y se perdían enteros (club_house pasó de 494 a 507).
        # Con Luisa, que lo tiene OBLIGATORIO, un aviso perdido no baja de
        # puntaje: desaparece.
        assert _extra_cumplido("club_house", aviso(
            "El conjunto cuenta con gym, salones sociales y zona bbq."))
        assert _extra_cumplido("club_house", aviso(
            "Conjunto de 22 años con 2 salones comunales y parqueadero de visitantes."))

    def test_el_plural_en_ingles_no_se_perdio_por_el_camino(self):
        # "family rooms" se pluraliza solo al final, contra la regla española.
        # Son 3 avisos: la regla nueva tenía que sumar, no reemplazar.
        assert _extra_cumplido("family_room", aviso("Dos family rooms independientes."))


class TestExtrasSuaves:
    """La regla de oro de Daniel, aplicada al vocabulario.

    "hay datos que son difíciles de ver en descripciones, como los 10 años
    máximo, eso es algo que yo puedo revisar y ver si cumple, pero hay cosas
    como la terraza, pues que si no tiene terraza y el cliente sí o sí necesita
    terraza, no debería salir".

    Medido sobre los 1.475 avisos con descripción de verdad (400+ caracteres),
    contando con los sinónimos que usa el motor: "exterior" lo dicen 422 (28,6%)
    e "interior" 135 (9,2%) — el 63,9% no dice ni lo uno ni lo otro.
    "iluminado" lo dicen 448 (30,4%) y "oscuro"/"poca luz" lo dice CERO. Callar
    no es un dato.
    """

    def test_exterior_e_iluminado_son_suaves_y_el_resto_no(self):
        assert es_extra_suave("exterior") and es_extra_suave("iluminado")
        for duro in ("terraza", "piscina", "club_house", "jardin", "family_room"):
            assert not es_extra_suave(duro), duro

    def test_una_alternativa_con_parte_dura_no_es_suave(self):
        # "terraza o exterior" tiene una mitad que sí se anuncia: manda la dura.
        assert not es_extra_suave("terraza o exterior")

    def test_un_extra_suave_nunca_descarta_aunque_sea_obligatorio(self):
        # Ana Alba: "Debe ser iluminado". Si esto descartara, se quedaría sin
        # nada por una palabra que el 72% de los avisos simplemente no escribe.
        cli = {"extras": ["iluminado"], "obligatorios": ["extras"]}
        assert _falla_obligatorio(cli, aviso("Apartamento de 120 m2 en Rosales.")) is None

    def test_el_aviso_mudo_sale_avisado_en_vez_de_desaparecer(self):
        cli = {"nombre": "Ana", "extras": ["iluminado"], "barrios": ["Rosales"]}
        ev = evaluar(cli, aviso("Apartamento de 120 m2 en Rosales."))
        assert ev is not None
        assert any("no dice si es iluminado" in r for r in ev["razones_no"])

    def test_pedir_un_extra_suave_no_puede_bajar_el_puntaje(self):
        # Lo que pasaba antes, medido: cargarle "iluminado" a Claudia García la
        # dejaba en 0 coincidencias cuando tenía 24, y a Ana Alba en 0 de 9.
        p = aviso("Apartamento de 120 m2 en Rosales.")
        sin = evaluar({"nombre": "x", "barrios": ["Rosales"]}, p)
        con = evaluar({"nombre": "x", "barrios": ["Rosales"], "extras": ["iluminado"]}, p)
        assert con["score"] >= sin["score"]

    def test_decirlo_si_lo_sube(self):
        # Y cuando el aviso SÍ lo dice, el inmueble rankea por encima. Se pide
        # más metraje del que tiene para que el puntaje no toque el techo de
        # 100 y el empujón se pueda ver.
        cli = {"nombre": "x", "barrios": ["Rosales"], "area_min": 130,
               "extras": ["iluminado"]}
        mudo = evaluar(cli, aviso("Apartamento de 120 m2 en Rosales."))
        dicho = evaluar(cli, aviso("Apartamento de 120 m2 en Rosales, muy iluminado."))
        assert mudo["score"] < 100
        assert dicho["score"] > mudo["score"]

    def test_un_extra_duro_que_falta_si_sigue_castigando(self):
        # La otra mitad de la regla: la terraza sí se anuncia, así que su
        # ausencia sigue costando. Esto no se tocó.
        p = aviso("Apartamento de 120 m2 en Rosales.")
        sin = evaluar({"nombre": "x", "barrios": ["Rosales"]}, p)
        con = evaluar({"nombre": "x", "barrios": ["Rosales"], "extras": ["terraza"]}, p)
        assert con["score"] < sin["score"]


class TestLasDosTablasNoSeSeparan:
    """matcher y extractor llevan la MISMA tabla, duplicada a propósito.

    El motor de cruce no puede importar el extractor (arrastra la librería
    `anthropic`, que no está en el Python con el que corren estas pruebas), así
    que la tabla vive en los dos lados — igual que OBLIGATORIOS_VALIDOS. Para
    que no se separen en silencio, el extractor se lee como TEXTO y se comparan.
    """

    @staticmethod
    def _literal(nombre: str):
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "extractor.py")
        with open(ruta, encoding="utf-8") as fh:
            arbol = ast.parse(fh.read())
        for nodo in arbol.body:
            if isinstance(nodo, ast.Assign) and any(
                    getattr(d, "id", "") == nombre for d in nodo.targets):
                return ast.literal_eval(nodo.value)
        raise AssertionError(f"{nombre} no está en extractor.py")

    @staticmethod
    def _plano(texto: str) -> str:
        t = texto.lower().strip()
        for a, b in {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}.items():
            t = t.replace(a, b)
        return t

    def test_los_dos_diccionarios_dicen_lo_mismo(self):
        del_extractor = self._literal("SINONIMOS_EXTRA")
        assert set(del_extractor) == set(SINONIMOS_EXTRA)
        for clave, sinos in del_extractor.items():
            # El extractor los escribe con tilde (van en el prompt); el matcher
            # sin tilde (compara contra texto ya normalizado). Fuera de eso,
            # tienen que ser exactamente los mismos.
            assert [self._plano(s) for s in sinos] == \
                   [self._plano(s) for s in SINONIMOS_EXTRA[clave]], clave

    def test_todo_sinonimo_apunta_a_un_extra_que_existe(self):
        validos = set(self._literal("EXTRAS_VALIDOS"))
        assert set(SINONIMOS_EXTRA) <= validos
        assert {"exterior", "iluminado"} <= validos
        assert EXTRAS_SUAVES <= validos

    def test_las_8_palabras_que_faltaban_ya_existen(self):
        validos = set(self._literal("EXTRAS_VALIDOS"))
        # Una por cliente que no podía expresar lo que pedía.
        for palabra in ("piscina", "club_house", "jardin", "exterior",
                        "iluminado", "family_room", "gimnasio", "ascensor"):
            assert palabra in validos, palabra


class TestZonaInfantil:
    """Amenities para niños (Carolina Wil, 2026-09-22). Los avisos dicen
    "parque infantil" (128), "zona infantil" (55), "salón de niños" (33),
    "juegos infantiles" (19)... nunca "zona_infantil"."""

    def test_parque_infantil(self):
        assert _extra_cumplido("zona_infantil", aviso("Conjunto con piscina, gimnasio y parque infantil."))

    def test_parque_para_ninos(self):
        assert _extra_cumplido("zona_infantil", aviso("El conjunto cuenta con: seguridad 24 horas, parque para niños, teatrino."))

    def test_juego_de_ninos(self):
        assert _extra_cumplido("zona_infantil", aviso("Áreas de uso social: juego de niños, salón social, piscina."))

    def test_un_apartamento_sin_nada_de_eso_no_cumple(self):
        assert not _extra_cumplido("zona_infantil", aviso("Apartamento con chimenea, estudio y dos parqueaderos."))
