"""Lo que Daniel reportó el 2026-09-14 y no puede volver a pasar.

"las búsquedas no están siendo muy afinadas, por ejemplo en las zonas se
equivoca mucho, y también en los requerimientos, con rodolfo velazquez es claro
que necesita terraza o balcón y me está mostrando opciones que no tienen ni lo
uno ni lo otro".

Los casos de abajo salieron de sus datos reales, no de la imaginación.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matcher import (_alternativas, _extra_cumplido, _falla_obligatorio,
                         _match_ubicacion, _mismo_lugar, evaluar)

# Los barrios que pidió Rodolfo, tal cual están en su ficha.
RODOLFO_BARRIOS = ["Chapinero Alto", "Quinta Camacho", "Marly", "Rosales",
                   "El Nogal", "La Cabrera", "Chicó", "Santa Paula"]
CLI = {"barrios": RODOLFO_BARRIOS}


class TestUbicacion:
    def test_barrio_que_solo_comparte_una_palabra_no_coincide(self):
        # San Luis Chapinero NO es Chapinero Alto: comparten la palabra y ya.
        assert not _mismo_lugar("chapinero alto", "san luis chapinero")
        p, razon = _match_ubicacion(CLI, {"barrio": "San Luis Chapinero"})
        assert p == 0.0, razon

    def test_barrio_que_especifica_otro_si_coincide(self):
        # Chicó Norte SÍ es Chicó: empieza por él.
        assert _mismo_lugar("chico", "chico norte")
        p, razon = _match_ubicacion(CLI, {"barrio": "Chicó Norte"})
        assert p == 1.0
        assert "Chicó" in razon and "Chapinero" not in razon  # nombra el barrio CORRECTO

    def test_la_zona_deducida_no_cuenta_como_barrio(self):
        # Virrey está en la zona chapinero, pero no es "Chapinero Alto".
        p, razon = _match_ubicacion(CLI, {"barrio": "VIRREY"})
        assert p == 0.75
        assert "misma zona" in razon

    def test_zona_declarada_generica_no_cuenta_como_barrio(self):
        # El aviso dice zona="Chapinero" (los brokers se lo ponen a media ciudad).
        p, razon = _match_ubicacion(CLI, {"barrio": "Chicó Norte", "zona": "Chapinero"})
        assert "Chicó" in razon, razon

    def test_zona_declarada_que_si_es_barrio_sigue_contando(self):
        # Pero también usan el campo zona para un barrio de verdad.
        p, razon = _match_ubicacion(CLI, {"barrio": "", "zona": "Rosales"})
        assert p == 1.0 and "Rosales" in razon

    def test_fuera_de_bogota_no_coincide(self):
        assert _match_ubicacion(CLI, {"barrio": "Cota"})[0] == 0.0


class TestExtrasAlternativos:
    def test_entiende_la_o_como_alternativa(self):
        assert _alternativas("terraza o balcón") == {"terraza", "balcon"}
        assert _alternativas("terraza/balcón") == {"terraza", "balcon"}
        assert _alternativas("terraza|balcon") == {"terraza", "balcon"}

    def test_cualquiera_de_las_dos_cumple(self):
        assert _extra_cumplido("terraza o balcón", {"balcon"})
        assert _extra_cumplido("terraza o balcón", {"terraza"})

    def test_ninguna_no_cumple(self):
        assert not _extra_cumplido("terraza o balcón", {"parqueadero"})
        assert not _extra_cumplido("terraza o balcón", set())


class TestObligatorio:
    CLI = {"extras": ["terraza o balcón"], "obligatorios": ["extras"],
           "operacion": "venta", "barrios": RODOLFO_BARRIOS}

    def test_sin_lo_obligatorio_no_se_muestra(self):
        post = {"barrio": "Rosales", "extras": ["parqueadero"], "es_inmueble": True}
        assert evaluar(self.CLI, post) is None

    def test_un_aviso_mudo_tampoco_se_muestra(self):
        # El caso exacto que reportó: "ni se mencionan en la descripción".
        post = {"barrio": "Rosales", "extras": [], "es_inmueble": True}
        assert evaluar(self.CLI, post) is None

    def test_con_cualquiera_de_las_dos_si_se_muestra(self):
        for extra in (["terraza"], ["balcon"]):
            post = {"barrio": "Rosales", "extras": extra, "es_inmueble": True}
            assert evaluar(self.CLI, post) is not None, extra

    def test_sin_marcarlo_obligatorio_solo_resta(self):
        cli = dict(self.CLI, obligatorios=[])
        post = {"barrio": "Rosales", "extras": [], "es_inmueble": True}
        ev = evaluar(cli, post)
        assert ev is not None and any("no menciona" in r for r in ev["razones_no"])
