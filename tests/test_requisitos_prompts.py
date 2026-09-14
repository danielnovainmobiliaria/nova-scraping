"""Que un requisito escrito no se quede invisible.

El 2026-09-14 Rodolfo Velázquez pidió "terraza o balcón OBLIGATORIO". El
requisito quedó en las notas, que para el motor son texto muerto, y encima el
camino de la IA lo habría borrado igual: `interpretar_clientes` filtraba los
extras con `e in EXTRAS_VALIDOS`, y "terraza o balcón" no es ninguna de esas
claves sueltas. Aunque el prompt le pidiera escribir la alternativa, la línea
siguiente se la comía sin decir nada.

Los casos de abajo cierran ese camino: lo que la IA escriba en la forma que el
motor entiende tiene que llegar hasta el cruce, y lo que NO se pueda verificar
de verdad no puede convertirse en un filtro que esconde inmuebles.
"""
import sys, os, types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# src.extractor importa anthropic/dotenv/sqlalchemy, que viven en el .venv del
# proyecto; pytest corre con el Python del sistema, que no los tiene. Como aquí
# solo se prueban funciones puras (ni una llamada a la IA ni a la base), se
# ponen módulos de mentira para que el import no se caiga.
for _n in ("anthropic", "dotenv", "sqlalchemy"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["sqlalchemy"].create_engine = lambda *a, **k: None
sys.modules["sqlalchemy"].text = lambda *a, **k: None

from src.extractor import (EXTRAS_VALIDOS, OBLIGATORIOS_VALIDOS, SYSTEM_AFINACION,
                           SYSTEM_CLIENTES, SYSTEM_EDICION, SYSTEM_TEXTO_LIBRE,
                           limpiar_extras)
from src.matcher import _alternativas, _extra_cumplido, _falla_obligatorio


# ── Lo que pidió Rodolfo tiene que sobrevivir el viaje ──────────────

def test_la_alternativa_ya_no_se_borra():
    """Antes esto devolvía [] y el requisito desaparecía sin dejar rastro."""
    assert limpiar_extras(["terraza o balcón"]) == ["terraza o balcon"]


def test_se_acepta_como_lo_escribe_un_humano():
    for crudo in ("terraza o balcón", "Terraza O Balcón", "terraza/balcón",
                  "terraza|balcon", "terraza / balcón"):
        assert limpiar_extras([crudo]) == ["terraza o balcon"], crudo


def test_un_extra_normal_sigue_intacto():
    assert limpiar_extras(["terraza", "parqueadero"]) == ["terraza", "parqueadero"]


def test_tolera_como_lo_escribe_la_ia():
    # tildes, mayúsculas y el "de" que el motor no lleva
    assert limpiar_extras(["Balcón"]) == ["balcon"]
    assert limpiar_extras(["cuarto de servicio"]) == ["cuarto_servicio"]


def test_lo_que_el_motor_no_conoce_no_entra():
    assert limpiar_extras(["sauna", "cancha de tenis"]) == []


def test_media_alternativa_no_vale():
    """"sauna o terraza" NO puede quedar en "terraza".

    Quedarse con la mitad conocida vuelve MÁS estricto lo que el cliente pidió
    suelto: marcado como obligatorio escondería los avisos con sauna y sin
    terraza. Regla de Daniel: descartar solo por lo que el aviso dice de verdad.
    """
    assert limpiar_extras(["sauna o terraza"]) == []


def test_sin_repetidos():
    assert limpiar_extras(["terraza", "Terraza"]) == ["terraza"]
    assert limpiar_extras(["terraza o terraza"]) == ["terraza"]


# ── Y el motor tiene que entender lo que sale de aquí ───────────────

def test_el_formato_canonico_es_el_que_el_motor_parte():
    (valor,) = limpiar_extras(["terraza o balcón"])
    assert _alternativas(valor) == {"terraza", "balcon"}


def test_al_aviso_le_basta_con_el_balcon():
    """Los 308 avisos (12% de 2.565) que tienen balcón pero no terraza."""
    (valor,) = limpiar_extras(["terraza o balcón"])
    aviso = {"extras": ["balcon"], "caption": "Lindo apto con balcón amplio"}
    assert _extra_cumplido(valor, aviso) is True
    # Y el que no tiene ninguna de las dos sigue descartándose.
    assert _falla_obligatorio(
        {"extras": [valor], "obligatorios": ["extras"]},
        {"extras": ["deposito"], "caption": "Apto interior, sin balcón"}) is not None


# ── Los prompts: que sepan escribirlo y que no se coman la exigencia ─

def test_los_prompts_de_cliente_ensenan_la_alternativa():
    """Ningún prompt mencionaba qué hacer con un "A o B": por eso la IA elegía
    uno y tiraba el otro."""
    for prompt in (SYSTEM_CLIENTES, SYSTEM_TEXTO_LIBRE, SYSTEM_EDICION):
        assert "terraza o balcon" in prompt


def test_los_prompts_de_cliente_mapean_la_exigencia_a_extras():
    """SYSTEM_CLIENTES pedía detectar lo obligatorio, pero ninguno de sus
    ejemplos mapeaba a "extras", que era justo el que hacía falta."""
    for prompt in (SYSTEM_CLIENTES, SYSTEM_TEXTO_LIBRE, SYSTEM_EDICION):
        assert 'obligatorios ["extras"]' in prompt


def test_la_afinacion_ya_sabe_agregar_y_no_solo_excluir():
    """Antes solo tenía claves para EXCLUIR: "súbele que necesita terraza sí o
    sí" no tenía a dónde ir."""
    assert '"agregar_extras"' in SYSTEM_AFINACION
    assert '"obligatorios"' in SYSTEM_AFINACION
    # Y la lista de claves válidas se interpola de verdad (no quedó literal).
    assert str(EXTRAS_VALIDOS) in SYSTEM_AFINACION
    assert str(OBLIGATORIOS_VALIDOS) in SYSTEM_AFINACION


# ── Prender el candado no puede exigir lo que nadie exigió ──────────
#
# matcher._falla_obligatorio no exige el extra que el broker acaba de nombrar:
# recorre TODOS los extras duros de la ficha. Y esa lista casi siempre son
# DESEOS, porque la IA mete ahí todo lo que el cliente mencionó de pasada.
#
# Medido sobre los 2.551 avisos vivos, con la ficha real de Juan Camilo Mora
# (terraza + parqueadero + remodelado + family_room): exigirlos todos a la vez
# deja 8 avisos, el 0,3%. Un "súbele que necesita terraza sí o sí" no puede
# costar eso.
sys.modules.setdefault("pandas", types.ModuleType("pandas"))

from src.clientes import fusionar_requisitos  # noqa: E402


def test_no_prende_el_candado_sobre_deseos_que_nadie_exigio():
    c = {"nombre": "Juan Camilo Mora",
         "extras": ["terraza", "parqueadero", "remodelado", "family_room"],
         "obligatorios": []}
    sin_exigir = fusionar_requisitos(c, ["terraza"], ["extras"])
    assert c["obligatorios"] == []          # el candado NO se prendió solo
    assert "terraza" in sin_exigir          # y se dice cuál quedó sin exigir


def test_si_no_arrastra_nada_si_prende_el_candado():
    """Cuando lo exigido es lo ÚNICO duro de la ficha, no hay sorpresa que dar."""
    c = {"nombre": "Laura Mojica", "extras": ["terraza o balcon"], "obligatorios": []}
    assert fusionar_requisitos(c, ["terraza o balcon"], ["extras"]) == []
    assert c["obligatorios"] == ["extras"]


def test_un_deseo_nuevo_no_se_cuela_en_una_ficha_que_ya_exige():
    """Rodolfo ya exige terraza o balcón (1.073 avisos). Un "ojalá jardín" de
    regalo lo bajaría a los 102 que mencionan jardín: 4,0%."""
    c = {"nombre": "Rodolfo Velazquez", "extras": ["terraza o balcon"],
         "obligatorios": ["extras"]}
    sin_exigir = fusionar_requisitos(c, ["jardin"], [])
    assert "jardin" not in c["extras"]
    assert sin_exigir == ["jardin"]


def test_los_descriptivos_no_cuentan_como_arrastre():
    """"exterior" e "iluminado" nunca descartan, así que su presencia en la
    ficha no puede bloquear una exigencia de verdad."""
    c = {"nombre": "Jorge Herrera", "extras": ["exterior"], "obligatorios": []}
    assert fusionar_requisitos(c, ["terraza"], ["extras"]) == []
    assert c["obligatorios"] == ["extras"]


def test_sigue_sumando_y_nunca_quita():
    c = {"nombre": "X", "extras": ["terraza"], "obligatorios": ["barrio"]}
    fusionar_requisitos(c, ["parqueadero"], ["presupuesto"])
    assert c["extras"] == ["terraza", "parqueadero"]
    assert c["obligatorios"] == ["barrio", "presupuesto"]
