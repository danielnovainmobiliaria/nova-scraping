"""El perímetro de calles: lo que Daniel escribió en las notas y el motor no leía.

5 de sus 23 clientes tienen el área de búsqueda escrita como una cuadrícula
—"calle 60 a la 106", "entre carrera 8 y 10"— y hasta el 2026-09-14 eso era
texto muerto: `perimetro` no aparecía ni una vez en matcher.py.

TODAS las direcciones de abajo salieron de /tmp/posts.json, tal cual las
escribieron los brokers. Ninguna es inventada: la mitad de los casos raros
(el "Cr18 Cll85" sin espacios, el "# 00-20" imposible, la "Cra 1 Este") son
justamente los que rompían las primeras versiones del lector.

La regla de oro manda aquí igual que en el piso: solo se descarta la dirección
que SE PUDO LEER y quedó fuera. Medido, únicamente el 33% de los 2.795 avisos
deja leer una calle o una carrera; exigirle dirección a los otros dos tercios
escondería casi todo el inventario.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matcher import (_fuera_del_perimetro, _perimetro, direccion_del_post,
                         evaluar)


def _dir(texto):
    return direccion_del_post({"direccion": texto})


class TestLectorDeDirecciones:
    """Las formas reales en que un broker bogotano escribe una dirección."""

    def test_la_forma_de_siempre(self):
        assert _dir("Calle 94 # 11-30")[:2] == (94, 11)
        assert _dir("Carrera 8 #170-52, Bogotá, Colombia")[:2] == (170, 8)

    def test_calle_y_carrera_nombradas_las_dos(self):
        assert _dir("Calle 114 con Carrera 19A")[:2] == (114, 19)
        assert _dir("Cra 5 Calle 81")[:2] == (81, 5)

    def test_el_numero_que_sigue_es_la_via_cruzada(self):
        # "Calle 134 con 10": el 10 es la carrera, aunque no lo diga.
        assert _dir("Calle 134 con 10")[:2] == (134, 10)
        assert _dir("cra 18 con 91")[:2] == (91, 18)
        assert _dir("CALLE 92   60- 83")[:2] == (92, 60)      # sin '#' ni 'con'
        assert _dir("Carrera 15 No 100-43")[:2] == (100, 15)  # con 'No'

    def test_abreviaturas_y_sin_espacios(self):
        assert _dir("Cll 85")[:2] == (85, None)
        assert _dir("Cra 18")[:2] == (None, 18)
        assert _dir("Cr18 Cll85")[:2] == (85, 18)          # pegado, como lo escriben
        assert _dir("KR 16 # 96 - 30")[:2] == (96, 16)
        assert _dir("CL 86 # 11 - 50 AP 503 - BRR CHICO VIRREY")[:2] == (86, 11)

    def test_numeros_con_letra_y_bis(self):
        assert _dir("Calle 103A con Cra 17")[:2] == (103, 17)
        assert _dir("CL 127C BIS # 7B - 60")[:2] == (127, 7)
        assert _dir("Cl 127 A #5C - 41, Bogotá, Colombia")[:2] == (127, 5)

    def test_avenida_calle_y_avenida_carrera_si_traen_el_numero(self):
        assert _dir("AC 26 # 85K - 93")[:2] == (26, 85)
        assert _dir("Avenida Carrera 30 # 14 - 43")[:2] == (14, 30)

    def test_lo_que_no_es_una_direccion_no_se_inventa(self):
        # Sin estos, "Santa Paula 109" se leería como la calle 109.
        for texto in ("Edificio Prato", "Frente al Gun Club", "CEDRITOS",
                      "Santa Paula 109", "Attico 123, Usaquén", "", None):
            assert _dir(texto)[:2] == (None, None), texto

    def test_el_nombre_del_edificio_no_estorba(self):
        assert _dir("Conjunto Residencial Bosque Verde, Calle 131, Bogotá")[:2] == (131, None)
        assert _dir("Calle 145 #45-10, Prado Pinzon, Bogotá, Colombia")[:2] == (145, 45)

    def test_el_apartamento_no_se_confunde_con_la_carrera(self):
        assert _dir("Calle 127 Apto 502")[:2] == (127, None)
        assert _dir("Carrera 7 # 89-10 Apto 1002")[:2] == (89, 7)


class TestLoQueElLectorNoCubre:
    """A propósito no se leen: se prefiere "no sé" antes que descartar mal."""

    def test_diagonales_y_transversales_no_caen_en_la_cuadricula(self):
        # Van en diagonal: no tienen un número de calle ni de carrera.
        assert _dir("Transversal 4 # 51A-34, Chapinero, Bogotá")[:2] == (None, None)
        assert _dir("Diagonal 45a Sur # 26A-60, Rafael Uribe Uribe")[:2] == (None, None)

    def test_las_avenidas_con_nombre_no_se_traducen_a_numero(self):
        # De "AVDA BOYACÁ" solo se aprovecha la calle que la acompaña.
        assert _dir("AVDA BOYACÁ CON CALLE 169D")[:2] == (169, None)
        assert _dir("AUTOPISTA NORTE CON CALLE 142")[:2] == (142, None)

    def test_la_carrera_este_esta_en_otra_numeracion(self):
        # "Cra 1 Este" queda al ORIENTE de la carrera 1: no se puede comparar
        # con un rango normal. La calle de esa misma dirección sí sirve.
        assert _dir("Cra 1 Este #70-57, Bogotá, Colombia")[:2] == (70, None)
        assert _dir("Cra 4 Este # 110B -21")[:2] == (110, None)

    def test_un_numero_imposible_anula_la_lectura_entera(self):
        # "Cra 159" no existe; si se aceptara, el 7 de al lado entraría como
        # calle 7 y un perímetro del norte descartaría el aviso por error.
        assert _dir("Cra 159 con 7")[:2] == (None, None)
        assert _dir("CALLE 72 A # 00-20 ESTE")[:2] == (72, None)   # la carrera 0 no existe


class TestPerimetro:
    ROD = {"exclusiones": {"perimetro": {"calle_min": 60, "calle_max": 106}}}

    def test_descarta_lo_que_el_aviso_dice_que_esta_fuera(self):
        per = self.ROD["exclusiones"]["perimetro"]
        assert _fuera_del_perimetro(per, {"direccion": "Calle 134 entre 9 y 15"})
        assert _fuera_del_perimetro(per, {"direccion": "Calle 28 #13A-24, Bogotá"})

    def test_deja_pasar_lo_que_esta_dentro(self):
        per = self.ROD["exclusiones"]["perimetro"]
        assert _fuera_del_perimetro(per, {"direccion": "Calle 85 # 11 - 50"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Cll 106 Cr 9"}) is None  # el borde entra

    def test_si_no_se_pudo_leer_la_direccion_NO_descarta(self):
        # La regla de oro: dos de cada tres avisos no dejan leer dirección.
        per = self.ROD["exclusiones"]["perimetro"]
        for texto in ("Edificio Prato", "CEDRITOS", "", None):
            assert _fuera_del_perimetro(per, {"direccion": texto}) is None, texto

    def test_el_aviso_sin_direccion_sale_pero_avisado(self):
        cli = dict(self.ROD, barrios=["Chicó"], operacion="venta")
        ev = evaluar(cli, {"barrio": "Chicó", "es_inmueble": True,
                           "direccion": "Edificio Prato"})
        assert ev is not None
        assert any("no se pudo verificar tu perímetro" in r for r in ev["razones_no"])

    def test_el_aviso_dentro_del_perimetro_lo_dice_a_favor(self):
        cli = dict(self.ROD, barrios=["Chicó"], operacion="venta")
        ev = evaluar(cli, {"barrio": "Chicó", "es_inmueble": True,
                           "direccion": "Calle 94 # 11-30"})
        assert any("dentro de tu perímetro" in r for r in ev["razones_ok"])

    def test_solo_se_responde_por_el_eje_que_el_cliente_limito(self):
        """Decir "dentro" por un eje que él no limitó es prometer de más.

        Caso real: María solo tiene tope de CALLE (hasta la 95). El aviso
        "Carrera 106 con Cra 23" no deja leer ninguna calle, y aun así le salía
        como "dentro de tu perímetro (carrera 106)" — un apartamento de
        Fontibón, a media ciudad de Rosales, marcado como verificado.
        """
        cli = {"exclusiones": {"perimetro": {"calle_max": 95}},
               "barrios": ["Chicó"], "operacion": "venta"}
        ev = evaluar(cli, {"barrio": "Chicó", "es_inmueble": True,
                           "direccion": "Carrera 106 con Cra 23"})
        assert not any("dentro de tu perímetro" in r for r in ev["razones_ok"])
        assert any("no dice la calle" in r for r in ev["razones_no"])

    def test_el_aviso_avisa_exactamente_lo_que_falta(self):
        # Álvaro limita las dos vías: si el aviso solo trae la calle, la
        # carrera queda pendiente de que él la confirme.
        cli = {"exclusiones": {"perimetro": {"calle_min": 88, "calle_max": 90,
                                             "carrera_min": 8, "carrera_max": 10}},
               "barrios": ["Chicó"], "operacion": "venta"}
        ev = evaluar(cli, {"barrio": "Chicó", "es_inmueble": True,
                           "direccion": "Calle 89"})
        assert any("dentro de tu perímetro (calle 89)" in r for r in ev["razones_ok"])
        assert any("no dice la carrera" in r for r in ev["razones_no"])

    def test_el_aviso_fuera_del_perimetro_no_se_muestra(self):
        cli = dict(self.ROD, barrios=["Chicó"], operacion="venta")
        assert evaluar(cli, {"barrio": "Chicó", "es_inmueble": True,
                             "direccion": "Calle 134 con 10"}) is None

    def test_cada_llave_es_opcional(self):
        # María Ordónez solo escribió un techo: "hasta la calle 95".
        per = {"calle_max": 95}
        assert _fuera_del_perimetro(per, {"direccion": "Calle 96 con Carrera 8"})
        assert _fuera_del_perimetro(per, {"direccion": "Calle 30 #13-20"}) is None

    def test_el_sur_no_esta_en_un_perimetro_del_norte(self):
        # "Calle 84 Sur" está a media ciudad de la calle 84 del norte.
        per = {"calle_min": 60, "calle_max": 106}
        assert _fuera_del_perimetro(per, {"direccion": "Calle 84 Sur #14c 51, Bogotá"})
        # Pero si el cliente no puso rango de calles, no hay con qué compararlo.
        assert _fuera_del_perimetro({"carrera_max": 90},
                                    {"direccion": "Calle 84 Sur #14c 51"}) is None

    def test_el_sur_del_nombre_de_un_edificio_no_descarta(self):
        """El "sur" es lo único que descarta sin leer la cuadrícula: va pegado al número.

        Las 29 direcciones con "sur" de los 2.795 avisos la traen así ("calle 84
        sur", "# 39I - 81 Sur", "diagonal 78 bis sur") y las 29 se siguen
        detectando. Un edificio del norte que se llame "Mirador del Sur" no
        puede hacer desaparecer el aviso: ahí la palabra no dice dónde queda.
        """
        per = {"calle_min": 60, "calle_max": 106}
        assert _fuera_del_perimetro(per, {"direccion": "Mirador del Sur, Bogotá"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Conjunto Parque Sur, Calle 100 #15-20"}) is None
        # Las formas reales, todas de /tmp/posts.json, siguen descartando.
        for texto in ("Calle 84 Sur #14c 51, Bogotá, Colombia",
                      "AK 68 # 39I - 81 Sur",
                      "Calle 66 A Sur #66-81, Bogotá, Colombia",
                      "Milan Apartamentos Usme, Diagonal 78 Bis Sur, Bogotá"):
            assert _fuera_del_perimetro(per, {"direccion": texto}), texto

    def test_tambien_se_lee_anidado_bajo_limites(self):
        # La IA de Brokerap guarda los topes bajo exclusiones["limites"].
        cli = {"exclusiones": {"limites": {"perimetro": {"calle_max": 95}}}}
        assert _perimetro(cli) == {"calle_max": 95}
        assert evaluar(dict(cli, barrios=["Chicó"], operacion="venta"),
                       {"barrio": "Chicó", "es_inmueble": True,
                        "direccion": "Calle 102 # 19-55 Apto 102"}) is None


class TestLosCincoClientesReales:
    """Sus perímetros, escritos como los dictó Daniel, contra avisos reales."""

    def test_rodolfo_de_chapinero_a_santa_paula(self):
        # "Perímetro: de Chapinero hasta Santa Paula, calle 60 a la 106."
        per = {"calle_min": 60, "calle_max": 106}
        assert _fuera_del_perimetro(per, {"direccion": "CL 70 A # 1 - 86 AP 301"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Calle 127"})          # Santa Bárbara
        assert _fuera_del_perimetro(per, {"direccion": "Calle 49 #6-60"})     # abajo de la 60

    def test_alvaro_calle_90_a_88_entre_carrera_8_y_10(self):
        per = {"calle_min": 88, "calle_max": 90, "carrera_min": 8, "carrera_max": 10}
        assert _fuera_del_perimetro(per, {"direccion": "Calle 90 con 10"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Cr19A Cll106"})       # calle y carrera fuera
        assert _fuera_del_perimetro(per, {"direccion": "Cra 20 #88-10, Bogotá"})  # la calle sirve, la carrera no

    def test_march_calle_100_a_116(self):
        per = {"calle_min": 100, "calle_max": 116}
        assert _fuera_del_perimetro(per, {"direccion": "Calle 113 con 10"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Cra 14 con 119"})     # Santa Bárbara arriba
        assert _fuera_del_perimetro(per, {"direccion": "Cl 95 #8-16"})

    def test_juan_camilo_no_lleva_tope_por_el_oriente(self):
        """"entre la séptima y la autopista" NO se carga como carrera_min 7.

        Rosales, La Cabrera y Chicó Alto —barrios que él mismo pidió— están
        ARRIBA de la séptima, entre la carrera 1 y la 7. Medido: 34 avisos en
        sus propios barrios tienen carrera leída menor que 7. Un carrera_min 7
        le borraría justo lo que busca, así que solo se carga el tope por el
        occidente (la autopista, ~carrera 45).
        """
        per = {"carrera_max": 45}
        assert _fuera_del_perimetro(per, {"direccion": "Cll 71 Cr 1"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Cra 5 Calle 81"}) is None
        assert _fuera_del_perimetro(per, {"direccion": "Calle 95 # 71 - 11"})  # Suba, pasada la autopista
