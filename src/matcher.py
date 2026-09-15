"""Motor de cruce: empareja publicaciones con clientes.

La idea clave NO es buscar coincidencias exactas, sino SIMILARES:
- Una opción un poco más cara o más barata que el presupuesto puede servir.
- Una opción un poco más grande o más pequeña que el metraje pedido también.

Por eso el cruce usa "bandas de tolerancia" y un puntaje que baja de forma
gradual a medida que la opción se aleja del ideal, en vez de descartarla de
golpe. Así la broker ve todas las opciones razonables y decide ella.
"""
from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

# Flexibilidad por defecto (se puede ajustar desde la app con un deslizador).
#
# 0.10 desde el 2026-09-14. Estaba en 0.20 y Daniel lo cortó a la mitad:
# "quiero que la lectura de cada inmueble sea más rigurosa, creo que estamos
# siendo demasiado flexibles y eso está haciendo que la herramienta no funcione
# como realmente debe funcionar". Con 20%%, a alguien con techo de 800 millones
# le salían inmuebles de 930. Medido sobre sus 2.795 posts y 23 clientes: con
# umbral 80 y estos márgenes las coincidencias bajan de 1.290 a 741 y ningún
# cliente se queda sin nada.
FLEX_PRECIO = 0.10
FLEX_AREA = 0.10

# Piso de presupuesto: margen del 20% hacia ABAJO (0.80 = muestra desde el 80%
# del presupuesto). Más barato que eso se considera otro segmento y no se muestra.
PISO_PRECIO = 0.80

# Mapa básico barrio → zona/localidad de Bogotá. Sirve para emparejar aunque
# el cliente pida una "zona" y el post mencione un barrio (o viceversa).
# Puedes ampliarlo con el tiempo.
BARRIO_A_ZONA = {
    # ── Chapinero ──
    "chico": "chapinero", "chico norte": "chapinero", "chico reservado": "chapinero",
    "chico navarra": "chapinero", "chico museo": "chapinero", "parque del chico": "chapinero",
    "el nogal": "chapinero", "nogal": "chapinero", "rosales": "chapinero",
    "los rosales": "chapinero", "la cabrera": "chapinero", "cabrera": "chapinero",
    "quinta camacho": "chapinero", "chapinero alto": "chapinero", "chapinero central": "chapinero",
    "el retiro": "chapinero", "el refugio": "chapinero", "refugio": "chapinero",
    "la porciuncula": "chapinero", "el lago": "chapinero", "lago gaitan": "chapinero",
    "el virrey": "chapinero", "virrey": "chapinero", "antiguo country": "chapinero",
    "emaus": "chapinero", "bellavista": "chapinero", "bosque calderon": "chapinero",
    "marly": "chapinero", "sucre": "chapinero", "la salle": "chapinero",
    "granada": "chapinero", "el castillo": "chapinero", "la esperanza": "chapinero",
    # ── Usaquén ──
    "cedritos": "usaquen", "santa barbara": "usaquen", "santa barbara alta": "usaquen",
    "santa barbara occidental": "usaquen", "la carolina": "usaquen", "country club": "usaquen",
    "san patricio": "usaquen", "usaquen": "usaquen", "multicentro": "usaquen",
    "unicentro": "usaquen", "santa ana": "usaquen", "santa ana occidental": "usaquen",
    "bella suiza": "usaquen", "molinos norte": "usaquen", "cedro golf": "usaquen",
    "cedro bolivar": "usaquen", "cedro narvaez": "usaquen", "contador": "usaquen",
    "la calleja": "usaquen", "lisboa": "usaquen", "toberin": "usaquen",
    "santa paula": "usaquen", "santa bibiana": "usaquen", "navarra": "usaquen",
    "el pedregal": "usaquen", "san gabriel": "usaquen", "ginebra": "usaquen",
    "rincon del chico": "usaquen", "sierras del moral": "usaquen", "la uribe": "usaquen",
    "torca": "usaquen", "verbenal": "usaquen", "santa teresa": "usaquen",
    "cerros de los alpes": "usaquen", "bosque medina": "usaquen", "bosque de pinos": "usaquen",
    "la sonora": "usaquen", "las margaritas": "usaquen", "escuela de caballeria": "usaquen",
    # ── Suba / Noroccidente ──
    "el batan": "suba", "niza": "suba", "niza norte": "suba", "la alhambra": "suba",
    "alhambra": "suba", "pasadena": "suba", "puente largo": "suba", "prado veraniego": "suba",
    "mazuren": "suba", "colina campestre": "suba", "la colina": "suba", "san jose de bavaria": "suba",
    "britalia": "suba", "cantalejo": "suba", "villa elisa": "suba", "las villas": "suba",
    "iberia": "suba", "granada norte": "suba", "victoria norte": "suba", "potosi": "suba",
    "provenza": "suba", "estoril": "suba", "colinas de suba": "suba", "monaco": "suba",
    "la floresta": "suba", "pontevedra": "suba", "morato": "suba", "san nicolas": "suba",
    # ── Barrios Unidos / Occidente cercano ──
    "la castellana": "barrios unidos", "polo club": "barrios unidos", "el polo": "barrios unidos",
    "los andes": "barrios unidos", "la patria": "barrios unidos", "rionegro": "barrios unidos",
    "santa sofia": "barrios unidos", "alcazares": "barrios unidos",
    "nicolas de federman": "teusaquillo", "la esmeralda": "teusaquillo",
    "pablo vi": "teusaquillo", "campin": "teusaquillo", "quinta paredes": "teusaquillo",
    # ── Salitre / Fontibón ──
    "salitre": "fontibon", "ciudad salitre": "fontibon", "salitre oriental": "fontibon",
    "salitre occidental": "fontibon", "modelia": "fontibon", "hayuelos": "fontibon",
    "capellania": "fontibon", "el recuerdo": "fontibon", "corferias": "fontibon",
    # ── Teusaquillo / Centro ──
    "teusaquillo": "teusaquillo", "la soledad": "teusaquillo", "park way": "teusaquillo",
    "galerias": "teusaquillo", "palermo": "teusaquillo", "santa teresita": "teusaquillo",
    "armenia": "teusaquillo", "la magdalena": "teusaquillo",
    "la macarena": "santa fe", "centro internacional": "santa fe", "la candelaria": "santa fe",
    "san diego": "santa fe", "bosque izquierdo": "santa fe",
    # ── Municipios vecinos (para NO confundirlos con Bogotá) ──
    "chia": "chia", "cajica": "cajica", "cota": "cota", "la calera": "la calera",
    "sopo": "sopo", "guarne": "fuera de bogota",
}


def formato_cop(valor) -> str:
    """Formatea un monto en pesos al estilo colombiano: $1'700.000.000.

    Puntos para los miles y apóstrofo en el primer separador (millones/miles de millón).
    """
    try:
        n = int(round(float(valor)))
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    s = f"{n:,}".replace(",", ".")
    if n >= 1_000_000 and "." in s:  # solo desde un millón se usa el apóstrofo
        i = s.index(".")
        s = s[:i] + "'" + s[i + 1:]
    return "$" + s


def _norm(texto: str | None) -> str:
    """Normaliza texto para comparar (minúsculas, sin tildes ni símbolos)."""
    if not texto:
        return ""
    t = texto.lower().strip()
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    for a, b in reemplazos.items():
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9 ]", " ", t).strip()


def _alternativas(extra: str) -> set[str]:
    """Un extra puede ofrecer ALTERNATIVAS: "terraza o balcón" vale con cualquiera.

    Daniel (2026-09-14): "con rodolfo velazquez es claro que necesita terraza o
    balcón". Antes extras era una lista plana comparada por intersección, así
    que no había forma de decir "cualquiera de estas dos" — o se pedían las dos
    o se pedía una sola y la otra no contaba.

    Se acepta como lo escribe un humano: "terraza o balcón", "terraza/balcón",
    "terraza|balcón".
    """
    # Se separa ANTES de normalizar: _norm borra la barra, y "terraza/balcón"
    # llegaba convertido en el extra único "terraza balcon".
    crudo = str(extra or "").replace("/", "|").replace(" o ", "|").replace(" O ", "|")
    partes = [_norm(t) for t in crudo.split("|")]
    return {t for t in partes if t}


# Cómo se escribe DE VERDAD cada extra en los avisos de Bogotá. Nadie escribe
# "club_house" ni "family_room": escribe "salón comunal" y "estar de TV". Sin
# esta tabla, releer el caption solo encontraba la palabra clave literal, que
# es justo la que no aparece nunca.
#
# extractor.SINONIMOS_EXTRA es la MISMA tabla, en el idioma del prompt: lo que
# allá se le enseña a la IA es exactamente lo que acá se busca en el texto.
# (Vive duplicada por lo mismo que OBLIGATORIOS_VALIDOS más abajo: el motor de
# cruce no puede importar el extractor, que arrastra la librería `anthropic`.
# tests/test_extras_vocabulario.py compara las dos listas para que no se
# separen.)
#
# Las tres trampas de abajo salieron de LEER los avisos, no de suponer:
#  · "zona social" NO es club house. Son 277 avisos y en casi todos es la
#    sala-comedor del propio apartamento ("amplia zona social", "balcón en la
#    zona social"). Aceptarla habría inflado club_house de 494 a 673 avisos
#    con puro falso positivo.
#  · "patio" NO es jardín. De los 52 avisos que dicen "patio", 29 son "patio
#    de ropas" o "patio interior" — la zona de lavandería.
#  · "sala de estar" NO es family room: es la sala. De los 127 avisos con la
#    palabra "estar", solo sirven "estar de TV" y "estar de alcobas".
SINONIMOS_EXTRA = {
    "jardin": ["jardin", "jardines", "antejardin", "jardin privado",
               "zona verde privada"],
    "piscina": ["piscina", "piscinas"],
    "club_house": ["club house", "clubhouse", "salon social", "salon comunal",
                   "salon de eventos", "zonas humedas"],
    "gimnasio": ["gimnasio", "gym"],
    "ascensor": ["ascensor", "ascensores"],
    "family_room": ["family room", "sala de tv", "salon de tv", "estar de tv",
                    "estar de alcobas", "sala familiar"],
    "exterior": ["exterior", "apartamento exterior", "vista exterior"],
    "iluminado": ["iluminado", "iluminada", "luminoso", "luminosa",
                  "iluminacion natural", "luz natural", "mucha luz",
                  "buena luz", "bien iluminado"],
}

# Extras DESCRIPTIVOS: que el aviso NO los diga no prueba que el inmueble no
# los tenga. Son la regla de oro de Daniel aplicada al vocabulario — "hay datos
# que son difíciles de ver en descripciones [...] eso es algo que yo puedo
# revisar"— frente a "si no tiene terraza y el cliente sí o sí necesita
# terraza, no debería salir".
#
# La diferencia no es de opinión, está medida sobre los 1.475 avisos con
# descripción de verdad (caption de 400 caracteres o más), contando con los
# mismos sinónimos de arriba (que es como los cuenta el motor):
#   · "exterior" lo dicen 422 (28,6%) y "interior" 135 (9,2%): el 63,9% no dice
#     ni lo uno ni lo otro. Si callar significara "interior", casi dos tercios
#     del mercado de Chicó y Rosales serían interiores. No lo son.
#   · "iluminado" lo dicen 448 (30,4%) y "oscuro"/"oscura"/"poca luz"/"sin luz"
#     lo dice CERO. Un broker jamás escribe que un apartamento es oscuro; que
#     no diga que es iluminado no es un dato, es silencio.
# Por eso estos extras SUMAN cuando aparecen y no restan cuando faltan: el
# aviso sale con una advertencia para que Daniel lo confirme, igual que ya
# pasa con el piso. Cargarlos como extras normales era catastrófico: medido,
# Claudia García pasaba de 24 coincidencias a 0 y Ana Alba de 9 a 0.
EXTRAS_SUAVES = {"exterior", "iluminado"}


def _formas_extra(alternativa: str) -> set[str]:
    """Todas las maneras de escribir una alternativa (su clave + sus sinónimos)."""
    formas = {alternativa}
    for clave, sinos in SINONIMOS_EXTRA.items():
        if alternativa == _norm(clave):
            formas |= {_norm(x) for x in sinos}
    return {f for f in formas if f}


def es_extra_suave(extra: str) -> bool:
    """¿Es un extra DESCRIPTIVO (de los que callar no prueba nada)?

    Solo si TODAS sus alternativas lo son: "terraza o exterior" tiene una parte
    dura (terraza), así que se trata como duro y sí puede restar.
    """
    alts = _alternativas(extra)
    suaves = {_norm(e) for e in EXTRAS_SUAVES}
    return bool(alts) and alts <= suaves


def _con_plurales(forma: str) -> set[str]:
    """Todas las formas de escribir un sinónimo en singular y en plural.

    En español el plural se le pega a TODAS las palabras del grupo: "salón
    social" se escribe "salones sociales", no "salón socials". El motor solo
    probaba a pluralizar el final, así que los sinónimos de dos palabras solo
    se encontraban en singular.

    Lo que costaba, medido sobre los 2.551 avisos vivos: 14 anuncian el club
    house del conjunto en plural ("el conjunto cuenta con gym, salones
    sociales", "2 salones comunales y parqueadero de visitantes") y no los
    encontraba ninguno. Con Luisa Galindo, que tiene "piscina o club_house"
    como OBLIGATORIO, perder un aviso no es perderle puntos: es que no
    aparece — justo lo que Daniel no quiere que pase con lo que el aviso SÍ
    dice.
    """
    palabras = forma.split()
    if not palabras:
        return set()
    # Solo el singular completo y el plural completo. Las mezclas ("salones
    # social") no las escribe nadie, y probar todas las combinaciones
    # multiplicaría las búsquedas por aviso sin ganar un solo caso.
    # Las palabras de 1 o 2 letras se dejan quietas: son conectores ("de") o
    # siglas ("tv"), que en plural no cambian.
    plural = " ".join(p + ("s" if p[-1:] in "aeiou" else "es") if len(p) > 2 else p
                      for p in palabras)
    # El plural de la última palabra también, y no por simetría: 3 avisos dicen
    # "family rooms" (en inglés solo se pluraliza el final), y la regla española
    # de arriba los perdía.
    return {forma, forma + "s", forma + "es", plural}


def _extra_cumplido(extra: str, post: dict[str, Any]) -> bool:
    """¿El aviso trae ALGUNA de las alternativas de este extra?

    Mira primero la lista `extras` que armó el extractor y, si ahí no está,
    RELEE EL TEXTO del aviso. Esa segunda pasada importa porque desde el
    2026-09-14 un extra obligatorio que falta DESCARTA el inmueble: medido
    sobre los 2.795 avisos de Daniel, el extractor se comía la terraza o el
    balcón en 10 casos que el texto sí mencionaba ("terraza bbq", "balcones
    amplios"). Sin este respaldo, esos 10 se perderían sin que nadie se entere.

    Desde el mismo día la relectura usa SINONIMOS_EXTRA, y por eso ahora
    funciona con el vocabulario nuevo: los 2.551 avisos vivos se leyeron ANTES
    de que existieran "piscina" o "club_house", así que su lista `extras` jamás
    los va a traer. Todo lo que encuentra de ellos sale de esta relectura del
    caption — que es la razón por la que no hay que reprocesar nada.

    Usa _menciona_de_verdad, así que "sin terraza" no cuenta como terraza.
    """
    post_norm = {_norm(e) for e in (post.get("extras") or [])}
    formas: set[str] = set()
    for alternativa in _alternativas(extra):
        formas |= _formas_extra(alternativa)
    if formas & post_norm:
        return True
    texto = _norm(post.get("caption", "")) + " " + _norm(post.get("resumen", ""))
    if not texto.strip():
        return False
    # Plural incluido: el aviso dice "balcones amplios", no "balcon"; y
    # "salones sociales", no "salon social".
    for a in formas:
        # El piso de 3 deja pasar "gym", que es como la mitad del mercado
        # escribe gimnasio. Son sinónimos curados a mano, no palabras sueltas.
        if len(a) < 3:
            continue
        for forma in _con_plurales(a):
            if _menciona_de_verdad(texto, forma):
                return True
    return False


# Los nombres que son ZONA (localidad), no barrio. Salen del propio mapa de
# arriba, así que crecen solos cuando alguien agrega un barrio nuevo.
_NOMBRES_ZONA = set(BARRIO_A_ZONA.values())


def _es_nombre_de_zona(texto: str) -> bool:
    return _norm(texto) in _NOMBRES_ZONA


def _zona_de(barrio: str) -> str:
    return BARRIO_A_ZONA.get(_norm(barrio), "")


# Criterios que el cliente puede marcar como NO negociables (filtro duro).
OBLIGATORIOS_VALIDOS = ["barrio", "presupuesto", "habitaciones", "banos", "metraje", "extras"]

# Perfil de flexibilidad de cada cliente. Ajusta qué tan acertado debe ser el
# inmueble para que aparezca, SIN tocar los deslizadores globales:
#   - mult: multiplica la tolerancia de precio y metraje (más bajo = más estricto).
#   - piso_extra: sube/baja el piso de precio (estricto rechaza lo muy barato).
#   - score_min: puntaje mínimo propio (estricto solo muestra coincidencias muy altas).
PERFILES_FLEX = {
    "estricto": {"mult": 0.3, "piso_extra": 0.10, "score_min": 80},   # no cede en nada
    "medio":    {"mult": 1.0, "piso_extra": 0.0,  "score_min": 0},    # equilibrado (def.)
    "flexible": {"mult": 2.0, "piso_extra": -0.20, "score_min": 0},   # abierto a más opciones
}
FLEX_VALIDOS = list(PERFILES_FLEX.keys())


def perfil_flex(cliente: dict[str, Any]) -> dict[str, Any]:
    """Devuelve el perfil de flexibilidad del cliente (por defecto 'medio')."""
    clave = str(cliente.get("flexibilidad") or "medio").lower().strip()
    return PERFILES_FLEX.get(clave, PERFILES_FLEX["medio"])


# Muchos brokers editan el caption a "VENDIDO"/"ARRENDADO" en vez de borrar el post.
# (Singular a propósito: "50 inmuebles vendidos" es publicidad, no un aviso tomado.)
_RE_NO_DISPONIBLE = re.compile(
    r"\b(vendido|vendida|arrendado|arrendada|ya no disponible|no disponible|"
    r"negocio cerrado|ya se vendio|ya se arrendo)\b")


def esta_vendido(post: dict[str, Any]) -> bool:
    """True si el aviso ya está marcado como VENDIDO/ARRENDADO (no se ofrece)."""
    if post.get("no_disponible") is True:
        return True
    texto = _norm(post.get("caption", "")) + " " + _norm(post.get("resumen", ""))
    return bool(_RE_NO_DISPONIBLE.search(texto))


# Por encima de este precio, en Bogotá ya es una VENTA; por debajo, un canon de
# ARRIENDO. Sirve para deducir la operación cuando el aviso no la dice.
PISO_VENTA = 100_000_000


def _inferir_operacion(post: dict[str, Any]) -> str:
    """Operación del aviso; si no la dice, la deduce por el precio (canon vs venta)."""
    op = _norm(post.get("operacion"))
    if op in ("venta", "arriendo", "ambos"):
        return op
    precio = post.get("precio")
    if precio:
        return "arriendo" if precio < PISO_VENTA else "venta"
    return ""                  # sin operación ni precio → no hay pista


# Sinónimos que usa la gente (y Zoho) para cada operación.
_OP_SINONIMOS = {
    "compra": "venta", "comprar": "venta", "compra vivienda": "venta", "venta": "venta",
    "alquiler": "arriendo", "alquilar": "arriendo", "renta": "arriendo",
    "rentar": "arriendo", "arriendo": "arriendo", "arrendar": "arriendo",
}


def _op_cliente(cliente_op: str) -> str:
    """Normaliza la operación del cliente ('compra' → 'venta', 'alquiler' → 'arriendo')."""
    cli = _norm(cliente_op)
    return _OP_SINONIMOS.get(cli, cli)


def _operacion_compatible(cliente_op: str, post: dict[str, Any]) -> bool:
    cli = _op_cliente(cliente_op)
    if not cli:
        return True            # el cliente no especificó → todo sirve
    pos = _inferir_operacion(post)
    if not pos or pos == "ambos":
        return True            # el post no dice (ni por precio) o sirve para ambas
    return cli == pos


# Palabras "genéricas" de zona: solas NO identifican un barrio (evita que un aviso
# con zona 'Norte' marque 100% de barrio para cualquier cliente del norte).
_ZONAS_GENERICAS = {"norte", "sur", "centro", "occidente", "oriente", "noroccidente",
                    "nororiente", "bogota", "el", "la", "los", "las", "de", "del", "alto"}


# Apodos de zona que usan los brokers en Bogotá → los barrios reales que abarcan.
# (Fácil de ampliar: agrega otra línea con el apodo y su lista.)
ZONAS_APODO = {
    "las santas": ["Santa Bibiana", "San Patricio", "Santa Paula", "Santa Bárbara"],
}


def _expandir_apodos(nombres: list) -> list:
    """'Las Santas' → además Santa Bibiana, San Patricio, Santa Paula, Santa Bárbara."""
    out = []
    for n in nombres:
        out.append(n)
        exp = ZONAS_APODO.get(_norm(str(n or "")))
        if exp:
            out.extend(exp)
    return out


def _tokens_lugar(texto: str) -> set[str]:
    """Palabras significativas de un nombre de lugar (sin genéricas ni muy cortas)."""
    return {t for t in _norm(texto).split() if len(t) >= 3 and t not in _ZONAS_GENERICAS}


def _lista_lugar(texto: str) -> list[str]:
    """Como _tokens_lugar pero CONSERVANDO EL ORDEN. El orden importa: ver abajo."""
    return [t for t in _norm(texto).split() if len(t) >= 3 and t not in _ZONAS_GENERICAS]


def _mismo_lugar(a: str, b: str) -> bool:
    """¿'a' y 'b' nombran el mismo lugar? Uno tiene que EMPEZAR por el otro.

    'Chicó' vs 'Chicó Reservado' → sí (Chicó Reservado es parte del Chicó).
    'Chía' vs 'Chicó' → no (palabras distintas).
    'Chicó Norte' vs 'Norte' → no ('norte' solo es genérico).

    'Chapinero Alto' vs 'San Luis Chapinero' → NO, y esto es el arreglo del
    2026-09-14. Antes se comparaban CONJUNTOS: {chapinero} ⊆ {san, luis,
    chapinero} daba verdadero, y a Rodolfo —que pidió Chapinero Alto— le
    salían inmuebles de San Luis Chapinero como "barrio coincide". Son barrios
    distintos que apenas comparten una palabra.

    La regla del prefijo separa las dos cosas: un barrio que ESPECIFICA a otro
    empieza por él ("Chicó Norte" empieza por "Chicó"); uno que solo lo
    menciona, no ("San Luis Chapinero" no empieza por "Chapinero").
    """
    la, lb = _lista_lugar(a), _lista_lugar(b)
    if not la or not lb:
        return False
    corto, largo = (la, lb) if len(la) <= len(lb) else (lb, la)
    return largo[:len(corto)] == corto


def _match_ubicacion(cliente: dict[str, Any], post: dict[str, Any]) -> tuple[float, str]:
    """Puntaje 0..1 de coincidencia de ubicación y una explicación."""
    barrios_cliente = cliente.get("barrios") or []
    zona_cliente = cliente.get("zona") or ""
    if not barrios_cliente and not zona_cliente:
        return 1.0, "sin restricción de zona"

    barrios_cliente = _expandir_apodos(barrios_cliente)   # "las santas" → sus 4 barrios
    post_barrio = post.get("barrio") or ""
    post_zona = post.get("zona") or ""
    post_dir = post.get("direccion") or ""
    # OJO CON LO QUE CUENTA COMO BARRIO (arreglo del 2026-09-14):
    #
    #  · La zona DEDUCIDA del barrio (_zona_de) ya no entra. Entraba, y la zona
    #    de Virrey y de Chicó Norte es "chapinero": un cliente que pidió
    #    "Chapinero Alto" recibía esos inmuebles con puntaje PLENO y el cartel
    #    "barrio coincide".
    #  · La zona DECLARADA por el aviso solo entra si no es el nombre de una
    #    localidad. Los brokers le ponen "Chapinero" a media ciudad —el código
    #    ya lo advertía más abajo—, pero también usan el campo zona para poner
    #    un barrio de verdad ("Rosales", "Chicó"), y esos sí deben contar.
    #
    # Una zona no es un barrio: el paso 1b de abajo la trata como lo que es
    # (0.75 y con advertencia).
    candidatos_post = _expandir_apodos(
        [post_barrio, post_dir] + ([] if _es_nombre_de_zona(post_zona) else [post_zona]))

    # 1) ¿coincide algún barrio pedido con lo que dice el post? (palabras completas)
    #    Se revisan TODOS los barrios pedidos ANTES de conformarse con "misma zona".
    mejor = 0.0
    for b in barrios_cliente:
        nb = _norm(b)
        if not nb:
            continue
        for c in candidatos_post:
            nc = _norm(c)
            if not nc:
                continue
            if _mismo_lugar(nb, nc):
                return 1.0, f"barrio coincide: {b}"
            # La dirección puede CONTENER el barrio ("Calle 94 con 11, El Chicó").
            if c is post_dir and _tokens_lugar(nb) and _tokens_lugar(nb) <= _tokens_lugar(nc):
                return 1.0, f"barrio coincide: {b}"
            mejor = max(mejor, fuzz.token_sort_ratio(nb, nc) / 100.0)

    # 1b) Ningún barrio pedido coincide. Si SABEMOS en qué barrio está el aviso
    # y no es de los pedidos, se descarta.
    #
    # Daniel (2026-09-15): "si el barrio no responde a los barrios que puse en
    # cada cliente, no lo muestres, veo que pongo barrios como nogal, cabrera,
    # virrey y me salen opciones en rosales, si bien son cerca son barrios
    # diferentes y esto me hace perder el tiempo".
    #
    # Antes esto valía 0.40 ("misma zona, otro barrio — verifícalo"). Bajarlo de
    # 0.75 a 0.40 ya los había hundido en el orden, pero seguían apareciendo, y
    # el trabajo de descartarlos uno por uno seguía siendo suyo. El barrio es
    # DATO VERIFICABLE, como la terraza: si el aviso dice en cuál está y no es
    # el pedido, no hay nada que verificar.
    #
    # Se exige reconocerlo en NUESTRO mapa de barrios antes de descartar: si el
    # aviso trae un nombre que no conocemos, puede ser un alias de uno pedido y
    # se sigue de largo (abajo lo atrapan la zona o el parecido de escritura).
    # Un "barrio" pedido que en realidad es el nombre de una localidad
    # ("Chapinero", "Usaquén") no es un barrio: pedirlo es pedir la zona entera,
    # y entonces cualquier barrio de adentro sirve.
    barrios_reales = [b for b in barrios_cliente if not _es_nombre_de_zona(b)]
    zonas_pedidas = {_norm(b) for b in barrios_cliente if _es_nombre_de_zona(b)}
    if zona_cliente:
        zonas_pedidas.add(_norm(zona_cliente))
    # Las localidades donde caen los barrios pedidos (Santa Bárbara → usaquén).
    zonas_de_lo_pedido = zonas_pedidas | {_zona_de(b) for b in barrios_cliente if _zona_de(b)}

    # ¿En qué barrio dice el aviso que está, si lo reconocemos?
    #
    # Solo se mira el campo BARRIO. El campo `zona` se usa para sumar (si trae un
    # barrio de verdad, el paso 1 lo aprovecha) pero nunca para descartar: los
    # avisos lo llenan mal. Caso real: un apartamento de Chicó con score 83
    # traía zona="Usaquén"; leer eso como "el barrio es Usaquén" lo borraba,
    # cuando la dirección (Calle 92 con Carrera 12) y el resumen decían Chicó.
    barrio_post = ""
    if _norm(post_barrio):
        if _es_nombre_de_zona(post_barrio):
            # Es una LOCALIDAD en la casilla del barrio. Solo contradice si es
            # una localidad distinta de donde están los barrios pedidos:
            # "Usaquén" habiendo pedido Santa Bárbara no dice nada nuevo;
            # "Cota" habiendo pedido Rosales es otro municipio.
            if _norm(post_barrio) not in zonas_de_lo_pedido:
                barrio_post = post_barrio
        elif _zona_de(post_barrio):
            barrio_post = post_barrio

    if barrios_reales and barrio_post and _zona_de(barrio_post) not in zonas_pedidas:
        return -1.0, f"queda en {barrio_post}, y no es de los barrios que pediste"

    # Cae en la misma zona pero no sabemos el barrio exacto: sigue valiendo poco.
    zona_post_real = _zona_de(post_barrio) or _norm(post_zona)
    for b in barrios_cliente:
        if _zona_de(b) and _zona_de(b) == zona_post_real:
            return 0.40, f"misma zona ({_zona_de(b)}), otro barrio — verifícalo"

    # 2) coincidencia por zona pedida (nivel zona: nunca cuenta como barrio exacto)
    #
    # La zona pedida puede venir por el campo `zona` o escrita en la lista de
    # barrios ("Chapinero"). Las dos cuentan: escribir la localidad en la
    # casilla de barrios es pedir la localidad entera, y antes eso daba 0 —
    # el inmueble salía castigado por estar justo donde se pidió.
    for z in ([zona_cliente] if zona_cliente else []) + [b for b in barrios_cliente if _es_nombre_de_zona(b)]:
        nz_tokens = _tokens_lugar(z) or {_norm(z)}
        for c in [_zona_de(post_barrio), post_zona, post_barrio]:
            nc = _norm(c)
            if nc and (nz_tokens & (_tokens_lugar(nc) or {nc}) or _norm(z) == nc):
                if barrios_reales:
                    # Pidió barrios concretos y solo cuadra la zona general: señal débil.
                    return 0.5, f"solo coincide la zona general ({z}) — barrio distinto"
                return 0.85, f"zona coincide: {z}"

    # 3) Parecido de escritura (typos): umbral alto para no confundir lugares distintos.
    if mejor >= 0.88:
        return mejor * 0.7, "ubicación parecida (verifícala)"
    return 0.0, "ubicación no coincide"


def _factor_precio(precio: float, presupuesto: float, flex: float, piso: float
                   ) -> tuple[float, str, bool]:
    """Compara precio vs presupuesto tratándolo como un RANGO.

    Devuelve (factor 0..1, explicación, es_a_favor); factor negativo => descartar.
    - Por encima del presupuesto: pasa con menos puntaje hasta el límite de flex.
    - Demasiado por encima (> flex): se descarta.
    - Demasiado por debajo (< piso del presupuesto): otro segmento → se descarta.
    """
    rel = precio / presupuesto  # 1.0 = justo en el presupuesto
    if rel > 1.0 + flex:
        return -1.0, "demasiado por encima del presupuesto", False
    if rel < piso:
        return -1.0, f"{formato_cop(precio)}: muy por debajo del presupuesto (otro segmento)", False
    if rel > 1.0:
        sobre = rel - 1.0
        factor = 1.0 - 0.7 * (sobre / flex) if flex > 0 else 0.3
        return factor, f"{formato_cop(precio)}: {sobre * 100:.0f}% por encima del presupuesto", False
    return 1.0, f"{formato_cop(precio)} acorde al presupuesto", True


# Si el cliente da solo un mínimo de área, este factor fija un techo razonable
# (evita que "sobre los 120 m²" muestre inmuebles de 400 m²).
AREA_TECHO_IMPLICITO = 1.6


def _rango_area(cliente: dict[str, Any]) -> tuple[float, float]:
    """Rango [lo, hi] de metraje deseado, con techo sensato si falta el máximo."""
    amin = cliente.get("area_min")
    amax = cliente.get("area_max")
    lo = amin if amin else (amax * 0.6 if amax else 0.0)
    hi = amax if amax else (amin * AREA_TECHO_IMPLICITO if amin else 1e9)
    return lo, hi


def _factor_area(area: float, lo: float, hi: float, flex: float
                 ) -> tuple[float, str, bool]:
    """Compara metraje vs rango [lo, hi] deseado de forma flexible."""
    if lo <= area <= hi:
        return 1.0, f"{area:g} m² dentro del rango", True
    # Distancia relativa por fuera del rango.
    if area < lo and lo > 0:
        d = (lo - area) / lo
        lado = "por debajo"
    else:
        d = (area - hi) / hi
        lado = "por encima"
    if d <= flex:
        factor = 1.0 - 0.7 * (d / flex)
        return factor, f"{area:g} m²: {d * 100:.0f}% {lado} del rango", False
    return 0.0, f"{area:g} m² fuera del rango", False


def _falla_obligatorio(cliente: dict[str, Any], post: dict[str, Any]) -> str | None:
    """Si el cliente marcó criterios NO negociables y el inmueble los incumple
    (con dato conocido), devuelve cuál falló; si no, None (pasa).
    """
    oblig = set(cliente.get("obligatorios") or [])
    if not oblig:
        return None
    if "habitaciones" in oblig:
        hm, h = cliente.get("habitaciones_min"), post.get("habitaciones")
        if hm and h is not None and h < hm:
            return "habitaciones"
    if "banos" in oblig:
        bm, b = cliente.get("banos_min"), post.get("banos")
        if bm and b is not None and b < bm:
            return "baños"
    if "presupuesto" in oblig:
        pr, pc = cliente.get("presupuesto_max"), post.get("precio")
        if pr and pc:
            # En arriendo, el costo real incluye la administración.
            if _inferir_operacion(post) == "arriendo":
                pc += post.get("administracion") or 0
            if pc > pr:
                return "presupuesto"
    if "metraje" in oblig:
        a = post.get("area_m2")
        if a and (cliente.get("area_min") or cliente.get("area_max")):
            lo, hi = _rango_area(cliente)
            if not (lo <= a <= hi):
                return "metraje"
    if "barrio" in oblig:
        # Solo se exige si el aviso TRAE dato de ubicación (desconocido no es incumplir).
        if post.get("barrio") or post.get("zona") or post.get("direccion"):
            p_ubi, _ = _match_ubicacion(cliente, post)
            if p_ubi < 0.8:
                return "barrio/zona"
    if "extras" in oblig:
        # ANTES esto no descartaba, solo restaba 25 puntos, con el argumento de
        # que un caption que no menciona el parqueadero no prueba que no exista.
        # Daniel lo vio al revés y tiene razón (2026-09-14): "me está mostrando
        # opciones que no tienen ni lo uno ni lo otro, y peor aun, ni se
        # mencionan en la descripción [...] para no perder el tiempo y no
        # perder dinero". Si lo marcó OBLIGATORIO, obligatorio es: un aviso que
        # no lo menciona no se muestra. Lo que se pierde son avisos mudos que
        # quizá sí lo tenían; lo que se gana es no revisarlos uno por uno.
        # …pero SOLO con los extras duros. Un extra suave ("exterior",
        # "iluminado") marcado obligatorio no puede descartar, porque su
        # ausencia en el texto no es un dato: 0 de 1.475 avisos descriptivos
        # dicen que un inmueble es oscuro. Descartar por eso sería inventarse
        # el defecto. Esos van con advertencia y los verifica Daniel.
        falta = [e for e in (cliente.get("extras") or [])
                 if str(e).strip() and not es_extra_suave(e)
                 and not _extra_cumplido(e, post)]
        if falta:
            return "extras: " + ", ".join(falta)
    return None


_PALABRAS_VIEJO = ["para remodelar", "para remodelacion", "remodelacion total", "antiguo",
                   "clasico", "anticuado", "muy viejo", "para restaurar"]

# Familias de tipo de inmueble: para no mezclar apartamentos con casas/locales.
_FAMILIA_TIPO = {
    "apartamento": "apto", "apartaestudio": "apto", "penthouse": "apto",
    "duplex": "apto", "loft": "apto", "aparta estudio": "apto",
    "casa": "casa", "casa campestre": "casa", "casa lote": "casa",
    "local": "comercial", "oficina": "comercial", "bodega": "comercial",
    "consultorio": "comercial", "lote": "lote", "finca": "finca",
}


def _familia_tipo(t: str | None) -> str:
    return _FAMILIA_TIPO.get(_norm(t), _norm(t or ""))


def _tipo_compatible(deseado: str | None, post_tipo: str | None) -> bool:
    """¿El tipo del inmueble es de la misma familia que la que busca el cliente?"""
    if not deseado or not post_tipo:
        return True                       # si falta el dato, no descartamos
    return _familia_tipo(deseado) == _familia_tipo(post_tipo)


def _antiguedad_estimada(post: dict[str, Any]) -> tuple[float | None, bool]:
    """Estima (años de construido, es_viejo) del dato extraído o del texto del aviso.

    años = número si se conoce (o se menciona en el texto), si no None.
    es_viejo = True si el texto sugiere que es antiguo / para remodelar.
    """
    a = post.get("antiguedad_anos")
    try:
        if a is not None:
            a = float(a)
            return (a if a >= 0 else None), False
    except (TypeError, ValueError):
        pass
    texto = _norm(post.get("caption", "")) + " " + _norm(post.get("resumen", ""))
    m = re.search(r"(\d{1,3})\s*anos?\s*(de\s+)?(construido|construccion|antiguedad|de uso|uso)", texto)
    if m:
        return float(m.group(1)), False
    es_viejo = any(w in texto for w in _PALABRAS_VIEJO)
    return None, es_viejo


_ORDINALES_PISO = {
    "primer": 1, "primero": 1, "segundo": 2, "tercer": 3, "tercero": 3,
    "cuarto": 4, "quinto": 5, "sexto": 6, "septimo": 7, "octavo": 8,
    "noveno": 9, "decimo": 10,
}

# "piso 6", "piso no 6"
_RE_PISO_NUM = re.compile(r"\bpiso\s*(?:n[o°º]?\s*)?(\d{1,2})\b")
# "6to piso", "6 piso" — pero NO "de 2 pisos" (eso es un dúplex, no el piso 2).
_RE_PISO_NUM2 = re.compile(r"(?<!de )\b(\d{1,2})\s*(?:do|ro|to|mo|vo|er|°|º)?\s*piso\b")
_RE_PISO_ORD = re.compile(r"\b(" + "|".join(_ORDINALES_PISO) + r")\s+piso\b")
_RE_PISO_ALTO = re.compile(r"\b(penthouse|pent\s*house|ultimo piso|pent-house)\b")


def piso_del_post(post: dict[str, Any]) -> int | None:
    """En qué piso está el inmueble, o None si el aviso no lo dice.

    Solo el 7% de los avisos lo menciona (medido sobre los 2.795 de Daniel),
    así que None es el caso NORMAL, no un error. Quien llame decide qué hacer
    con la ignorancia — y la respuesta correcta nunca es descartar.
    """
    directo = post.get("piso")
    if isinstance(directo, (int, float)) and 0 < directo < 60:
        return int(directo)

    texto = _norm(post.get("caption", "")) + " " + _norm(post.get("resumen", ""))
    if not texto.strip():
        return None
    # Un penthouse o "último piso" es alto por definición, sin saber el número.
    if _RE_PISO_ALTO.search(texto):
        return 99
    for rx in (_RE_PISO_NUM, _RE_PISO_NUM2):
        m = rx.search(texto)
        if m:
            n = int(m.group(1))
            if 0 < n < 60:
                return n
    m = _RE_PISO_ORD.search(texto)
    if m:
        return _ORDINALES_PISO[m.group(1)]
    return None


def _menciona_de_verdad(texto: str, nw: str) -> bool:
    """¿El texto menciona la palabra SIN negarla?

    'amoblado opcional', 'amoblado o sin amoblar' o 'no es primer piso' NO cuentan:
    el aviso ofrece justamente la alternativa que el cliente quiere. Solo anula
    cuando hay al menos una mención afirmativa de la palabra excluida."""
    for mt in re.finditer(rf"\b{re.escape(nw)}\b", texto):
        antes = texto[max(0, mt.start() - 22):mt.start()]
        despues = texto[mt.end():mt.end() + 22]
        if re.search(r"\b(sin|no|no es|nada de|tampoco)\s*$", antes):
            continue
        if re.search(r"^\s*(opcional|o sin|y sin|o no)\b", despues):
            continue
        # "amoblado/sin amoblar", "amoblado - sin muebles": la alternativa negada
        # viene justo después con la MISMA raíz de palabra u otra equivalente.
        raiz = re.escape(nw.split()[0][:5])
        if re.search(rf"^\s*(sin|no)\s+{raiz}", despues):
            continue
        return True
    return False


_TIPOS_NO_VIVIENDA = {"oficina", "local", "bodega", "consultorio", "lote",
                      "parqueadero", "oficinas", "local comercial"}


# ── PERÍMETRO DE CALLES ───────────────────────────────────────────────────
#
# Bogotá está en cuadrícula: las CALLES crecen hacia el norte y las CARRERAS
# hacia el occidente. Por eso media ficha de Daniel tiene el área de búsqueda
# escrita como dos rangos de números —"calle 60 a la 106", "entre carrera 8 y
# 10"— y hasta hoy el motor no los leía: 5 clientes tenían su perímetro escrito
# en las notas y no filtraba nada.
#
# CUÁNTO SE PUEDE VERIFICAR (medido el 2026-09-14 sobre los 2.795 avisos):
#   · 1.055 avisos (38%) traen algo en `direccion`.
#   · De esos, este lector saca calle o carrera en 926 → 33% del inventario
#     (36% de los 2.565 que son inmueble).
#   · Los otros 1.869 no se pueden ubicar en la cuadrícula: o no hay dirección,
#     o dice "Edificio Prato", "Frente al Gun Club", "CEDRITOS".
# Con dos tercios del inventario mudo, el perímetro NO puede exigirse siempre.
# Vale la regla de oro de Daniel: se descarta lo que el aviso DICE que está
# fuera; lo que no se alcanza a leer sale con advertencia y él lo confirma.
#
# SOLO SE LEE EL CAMPO `direccion`, NUNCA EL CAPTION. Medido: de los 1.740
# avisos sin dirección, solo 14 nombran una calle en el texto, y los 14 la
# nombran como REFERENCIA, no como domicilio ("cerca a la Calle 140", "conecta
# con la Calle 26", "a minutos de la Calle 109"). Leer el caption ubicaría un
# apartamento de Cedritos en la calle 140 y uno de Suba en el aeropuerto.
#
# LO QUE ESTE LECTOR NO CUBRE, A PROPÓSITO (todo esto devuelve "no se pudo
# leer", así que pasa con advertencia en vez de descartarse por error):
#   · Diagonales y transversales: van en diagonal, no caen en un número de
#     calle ni de carrera. Son 33 avisos; 29 quedan sin leer y los otros 4 se
#     aprovechan solo por la vía que cruzan ("Tv 57 con Calle 104B" → calle 104).
#   · Avenidas con nombre (Boyacá, Suba, NQS, Circunvalar, Autopista): habría
#     que inventar un mapa de equivalencias y una equivalencia mal puesta
#     descarta inmuebles buenos. "Avenida Calle 26" y "Avenida Carrera 30" sí
#     se leen, porque ahí el número viene dicho.
#   · Las carreras "este" (Cra 1 Este, Cra 4 Este): están al oriente de la
#     carrera 1, en otra numeración; no se pueden comparar con un rango normal.
#     La calle de esa misma dirección sí se lee.
#   · Los perímetros escritos en numeración SUR. Los 5 clientes que lo tienen
#     buscan en el norte, así que una dirección "Calle 84 Sur" se considera
#     fuera de un rango de calles del norte (son 29 avisos, todos del sur de
#     verdad: Usme, Ciudad Bolívar, Madelena, Rafael Uribe).
_VIA_CALLE = r"(?:avenida calle|av\.? calle|calle|clle|cll|cl|ac)"
_VIA_CRA = (r"(?:avenida (?:carrera|cra|kra|kr)|av\.? (?:carrera|cra|cr|kr)|"
            r"carrera|carr|cra|kra|cr|kr|ak)")
# El número de la vía puede traer letra y "bis": "94A", "127C bis", "74 Bis".
_NUM_VIA = r"(\d{1,3})\s*(?:[a-h]\b)?\s*(?:bis\b)?"
_RE_DIR_CALLE = re.compile(r"\b" + _VIA_CALLE + r"\s*" + _NUM_VIA)
_RE_DIR_CRA = re.compile(r"\b" + _VIA_CRA + r"\s*" + _NUM_VIA)
# El número que sigue a la vía principal es el de la vía CRUZADA: en
# "Calle 94 # 11-30" el 11 es la carrera; en "Carrera 8 #170-52" el 170 es la
# calle. _norm ya borró el '#', así que aquí solo queda el "con" o nada.
_RE_DIR_CRUCE = re.compile(r"^\s*(?:sur|este)?\s*(?:con|nro|no|n)?\s*(\d{1,3})(?!\d)")
# Números que existen en Bogotá. Fuera de esto es basura de lectura (un
# "Cra 159", el "# 00-20" de un edificio) y se trata como dato que no se pudo leer.
_CALLE_MAX = 300
_CARRERA_MAX = 130


def direccion_del_post(post: dict[str, Any]) -> tuple[int | None, int | None, bool]:
    """(calle, carrera, es_del_sur) de la dirección del aviso.

    Cada número es None cuando el aviso no permite leerlo, que es el caso
    NORMAL: dos de cada tres avisos no tienen dirección utilizable. Quien
    llame decide qué hacer con la ignorancia — y nunca es descartar.
    """
    t = _norm(post.get("direccion"))
    if not t:
        return None, None, False
    # El "sur" tiene que ir PEGADO al número de la vía ("calle 84 sur",
    # "# 39I - 81 Sur", "diagonal 78 bis sur"): es la única marca que descarta
    # sin haber leído la cuadrícula, así que no puede dispararla el nombre de
    # un edificio del norte ("Mirador del Sur", "Parque Sur"). Medido: las 29
    # direcciones con "sur" de los 2.795 avisos la traen pegada al número —las
    # 29 se siguen detectando— y todas son del sur de verdad (Usme, Ciudad
    # Bolívar, Madelena, Rafael Uribe).
    sur = bool(re.search(r"(?:\d\s*[a-z]?|\bbis)\s*sur\b", t))
    # "Cra 1 Este" está al oriente de la carrera 1: otra numeración, no se compara.
    hay_este = bool(re.search(r"\beste\b", t))

    mc = _RE_DIR_CALLE.search(t)
    mk = _RE_DIR_CRA.search(t)
    calle = int(mc.group(1)) if mc else None
    carrera = int(mk.group(1)) if mk else None
    # Un número imposible ("Cra 159", el "# 00-20" de un edificio) delata una
    # dirección mal escrita: se descarta LA LECTURA ENTERA, porque el número
    # cruzado que la acompaña tampoco es de fiar.
    if calle is not None and not (1 <= calle <= _CALLE_MAX):
        return None, None, sur
    if carrera is not None and not (1 <= carrera <= _CARRERA_MAX):
        return None, None, sur
    # Solo una vía nombrada: el número que viene justo después es el de la cruzada.
    if mc and not mk:
        m = _RE_DIR_CRUCE.match(t[mc.end():])
        carrera = int(m.group(1)) if m else None
    elif mk and not mc:
        m = _RE_DIR_CRUCE.match(t[mk.end():])
        calle = int(m.group(1)) if m else None

    if calle is not None and not (1 <= calle <= _CALLE_MAX):
        calle = None
    if carrera is not None and (hay_este or not (1 <= carrera <= _CARRERA_MAX)):
        carrera = None
    return calle, carrera, sur


def _perimetro(cliente: dict[str, Any]) -> dict[str, Any]:
    """El perímetro del cliente, esté en la raíz de exclusiones o bajo 'limites'."""
    exc = cliente.get("exclusiones") or {}
    per = exc.get("perimetro") or (exc.get("limites") or {}).get("perimetro")
    return per if isinstance(per, dict) else {}


def _fuera_del_perimetro(per: dict[str, Any], post: dict[str, Any]) -> str | None:
    """Motivo por el que la dirección del aviso queda fuera del perímetro, o None.

    None significa DOS cosas distintas y a propósito: "está dentro" y "no se
    pudo leer". Las dos se muestran; la segunda sale marcada con advertencia
    desde evaluar(), porque es una dirección que Daniel confirma en un minuto.
    """
    if not per:
        return None
    calle, carrera, sur = direccion_del_post(post)
    c_min, c_max = per.get("calle_min"), per.get("calle_max")
    k_min, k_max = per.get("carrera_min"), per.get("carrera_max")
    if (c_min or c_max) and sur:
        # Los perímetros vienen escritos en numeración del norte; una "Calle 84
        # Sur" está a media ciudad de distancia, no en la calle 84.
        return "dirección en el sur de la ciudad (el perímetro es del norte)"
    if calle is not None:
        if c_min and calle < c_min:
            return f"calle {calle} (tu perímetro empieza en la {c_min:g})"
        if c_max and calle > c_max:
            return f"calle {calle} (tu perímetro llega hasta la {c_max:g})"
    if carrera is not None:
        if k_min and carrera < k_min:
            return f"carrera {carrera} (tu perímetro empieza en la {k_min:g})"
        if k_max and carrera > k_max:
            return f"carrera {carrera} (tu perímetro llega hasta la {k_max:g})"
    return None


def _falla_exclusion(cliente: dict[str, Any], post: dict[str, Any]) -> str | None:
    """Filtro DURO por comentarios del broker: barrios o palabras que anulan el inmueble.

    Devuelve el motivo si el inmueble debe anularse, o None si pasa.
    """
    exc = cliente.get("exclusiones") or {}
    # Los topes numéricos que aprende la IA desde Brokerap se guardan anidados
    # bajo "limites", pero aquí se leían en la raíz: NINGUNO se aplicaba. La
    # tarjeta #1 de Juan Camilo era un edificio de 22 años con su regla en 10.
    # Se leen de los dos sitios; la raíz manda si están en ambos.
    exc = {**(exc.get("limites") or {}), **exc}
    barrios_x = _expandir_apodos(exc.get("barrios") or [])
    palabras_x = exc.get("palabras") or []
    if barrios_x:
        # REGLA DE ORO: si el aviso está en un barrio que el cliente PIDE, ninguna
        # exclusión de barrio/zona lo puede anular (la IA a veces excluye la localidad
        # entera —"Chapinero"— y eso mataría los propios barrios del cliente).
        deseados = _expandir_apodos(cliente.get("barrios") or [])
        lugares_post = [post.get("barrio") or "", post.get("zona") or ""]
        en_barrio_pedido = any(
            _mismo_lugar(_norm(b), _norm(pl))
            for b in deseados for pl in lugares_post if pl)
        # Tiro directo: si la exclusión nombra EXACTAMENTE el barrio del aviso
        # ('Chicó Navarra'), anula aunque el cliente pida uno parecido ('Chicó').
        # Solo se protege si la exclusión es igual o MÁS amplia que un barrio
        # pedido ('Chapinero' no puede anular al que pide 'Chapinero Alto').
        tokens_post = _tokens_lugar(_norm(post.get("barrio") or ""))
        for b in barrios_x:
            tb = _tokens_lugar(_norm(b))
            protege = any(tb <= _tokens_lugar(_norm(d)) for d in deseados)
            if tb and tokens_post and tb <= tokens_post and not protege:
                return f"barrio excluido: {b}"
        if not en_barrio_pedido:
            candidato = " ".join(_norm(x) for x in (
                post.get("barrio"), post.get("zona"), post.get("direccion"),
                _zona_de(post.get("barrio") or "")))
            for b in barrios_x:
                nb = _norm(b)
                if nb and nb in candidato:
                    return f"barrio excluido: {b}"
    if palabras_x:
        texto_cap = _norm(post.get("caption", ""))
        texto_res = _norm(post.get("resumen", ""))
        lugar = " ".join(_norm(post.get(k) or "") for k in ("barrio", "zona", "direccion"))
        for w in palabras_x:
            nw = _norm(w)
            if not nw or len(nw) < 4:
                continue
            # Palabras de zona genéricas ('norte', 'sur'…) solo aplican a la UBICACIÓN
            # del aviso, no a todo el texto ('iluminación norte' no debe anular).
            if nw in _ZONAS_GENERICAS:
                if _menciona_de_verdad(lugar, nw):
                    return f"contiene «{w}» (excluido por ti)"
                continue
            # El caption es la fuente original: si ahí la palabra solo aparece negada
            # ('amoblado/sin amoblar'), el resumen de la IA no puede anular el aviso.
            if re.search(rf"\b{re.escape(nw)}\b", texto_cap):
                if _menciona_de_verdad(texto_cap, nw):
                    return f"contiene «{w}» (excluido por ti)"
            elif _menciona_de_verdad(texto_res, nw):
                return f"contiene «{w}» (excluido por ti)"
    # Perímetro de calles y carreras ("calle 60 a la 106, entre carrera 8 y 10").
    # Mismo trato que el piso: descarta SOLO si la dirección se pudo leer y queda
    # fuera. Se lee en 33% de los avisos; en el otro 67% pasa con advertencia.
    fuera = _fuera_del_perimetro(_perimetro(cliente), post)
    if fuera:
        return f"fuera de tu perímetro: {fuera}"

    # Topes numéricos duros pedidos por el broker.
    area = post.get("area_m2")
    if exc.get("area_max") and area and area > exc["area_max"]:
        return f"{area:g} m²: por encima del tope ({exc['area_max']:g} m²)"
    if exc.get("area_min") and area and area < exc["area_min"]:
        return f"{area:g} m²: por debajo del mínimo ({exc['area_min']:g} m²)"
    precio = post.get("precio")
    if exc.get("precio_max") and precio:
        # REGLA DE ORO del presupuesto: un tope aprendido jamás puede quedar POR
        # DEBAJO del presupuesto declarado — mataría el margen de negociación
        # (+20%) que el presupuesto ya contempla. Un tope así se IGNORA y el
        # precio lo juzga el presupuesto normal; un tope explícito por encima
        # del presupuesto sí se respeta tal cual.
        tope = exc["precio_max"]
        pmax_cli = cliente.get("presupuesto_max")
        if not (pmax_cli and tope < pmax_cli):
            if precio > tope:
                return f"precio por encima del tope ({formato_cop(tope)})"
    habs = post.get("habitaciones")
    if exc.get("habitaciones_min") and habs is not None and habs < exc["habitaciones_min"]:
        return f"menos de {exc['habitaciones_min']:g} habitaciones"
    banos = post.get("banos")
    if exc.get("banos_min") and banos is not None and banos < exc["banos_min"]:
        return f"menos de {exc['banos_min']:g} baños"
    # Tope de habitaciones (ej. "solo 2, nada de 3").
    if exc.get("habitaciones_max") and habs is not None and habs > exc["habitaciones_max"]:
        return f"{habs:g} habitaciones (pediste máx {exc['habitaciones_max']:g})"
    # Tipo de inmueble (ej. busca apartamento → fuera casas/locales).
    deseado_tipo = exc.get("tipo") or cliente.get("tipo")
    tipo_post = _norm(str(post.get("tipo") or "")) or None
    # Sin tipo declarado, el cliente busca VIVIENDA: oficinas, locales y demás
    # comercial quedan fuera por defecto (si un día alguien busca oficina,
    # se le pone tipo 'oficina' en la ficha y entra normal).
    if not deseado_tipo and tipo_post in _TIPOS_NO_VIVIENDA:
        return f"es {tipo_post} (el cliente busca vivienda)"
    if deseado_tipo and tipo_post and not _tipo_compatible(deseado_tipo, tipo_post):
        return f"es {tipo_post} (buscas {deseado_tipo})"
    # Antigüedad: pediste algo nuevo (tope de años de construido).
    # PISO. Daniel (2026-09-14): "que entienda que si se busca un piso alto no
    # debería mostrar ni un segundo ni tercer piso".
    #
    # Se descarta SOLO cuando el aviso dice en qué piso está. Lo dice el 7% de
    # las veces, así que exigirlo siempre dejaría fuera casi todo el inventario
    # — y él mismo puso la regla: lo que el aviso no alcanza a decir lo revisa
    # a mano, lo que sí dice se respeta.
    piso_min = exc.get("piso_min")
    piso_max = exc.get("piso_max")
    if piso_min or piso_max:
        piso = piso_del_post(post)
        if piso is not None:
            if piso_min and piso < piso_min:
                return f"piso {piso} (pediste del {piso_min:g} hacia arriba)"
            if piso_max and piso != 99 and piso > piso_max:
                return f"piso {piso} (pediste hasta el {piso_max:g})"

    amax = exc.get("antiguedad_max")
    if amax is not None:
        anos, es_viejo = _antiguedad_estimada(post)
        if anos is not None and anos > amax:
            return f"{anos:g} años de construido (pediste máx {amax:g})"
        if anos is None and es_viejo:
            return "el aviso sugiere que es antiguo / para remodelar"
    return None


def _ajuste_preferencias(cliente: dict[str, Any], post: dict[str, Any]
                         ) -> tuple[int, list[str]]:
    """Penaliza inmuebles parecidos a lo que el cliente ya descartó.

    Usa lo aprendido en cliente['preferencias_evitar'] (palabras + extras).
    """
    prefs = cliente.get("preferencias_evitar") or {}
    palabras = prefs.get("palabras") or []
    req_extras = prefs.get("extras") or []
    texto = _norm(post.get("caption", "")) + " " + _norm(post.get("resumen", ""))
    razones: list[str] = []
    pen = 0
    # Los barrios que el cliente SÍ pide nunca pueden castigarlo: la IA a veces
    # aprende "antiguo" de un descarte y eso golpea a "Antiguo Country", que es
    # justo uno de los barrios pedidos.
    pedidos = " ".join(_norm(b) for b in (cliente.get("barrios") or []))
    for w in palabras:
        nw = _norm(w)
        if not nw or len(nw) < 4:
            continue
        if nw in pedidos:
            continue
        # _menciona_de_verdad en vez de 'nw in texto': con substring suelto,
        # "antiguo" pegaba dentro de "Antiguo Country" y "sin terraza" contaba
        # como terraza. El resto del archivo ya usaba esta función; aquí no.
        if _menciona_de_verdad(texto, nw):
            pen += 15
            razones.append(f"a este cliente no le gustó algo así: «{w}»")
    for ex in req_extras:
        # Lo aprendido también entiende alternativas y relee el texto del aviso.
        if not _extra_cumplido(ex, post):
            pen += 20
            razones.append(f"no menciona {ex} (lo pidió tras descartar otro)")
    return min(pen, 45), razones


def evaluar(cliente: dict[str, Any], post: dict[str, Any],
            flex_precio: float = FLEX_PRECIO,
            flex_area: float = FLEX_AREA,
            piso_precio: float = PISO_PRECIO) -> dict[str, Any] | None:
    """Evalúa qué tan SIMILAR es un post a lo que pide un cliente.

    Devuelve None solo cuando hay un choque de fondo (operación distinta,
    precio absurdamente por encima del presupuesto, o muchísimas menos
    habitaciones de las pedidas). En el resto de casos devuelve un puntaje
    0-100 con las razones a favor y en contra.
    """
    razones_ok: list[str] = []
    razones_no: list[str] = []
    puntaje = 0.0
    peso_total = 0.0

    # ── Flexibilidad del cliente: ajusta las tolerancias a su perfil ──
    perfil = perfil_flex(cliente)
    flex_precio = min(0.60, flex_precio * perfil["mult"])
    flex_area = min(0.60, flex_area * perfil["mult"])
    piso_precio = min(0.98, max(0.0, piso_precio + perfil["piso_extra"]))

    # ── Operación: este sí es un choque de fondo (arriendo ≠ venta) ──
    if not _operacion_compatible(cliente.get("operacion", ""), post):
        return None

    # ── Filtros NO negociables (lo que el cliente marcó como obligatorio) ──
    if _falla_obligatorio(cliente, post):
        return None

    # ── Exclusiones duras pedidas por el broker (ej. "nada después de la 100") ──
    if _falla_exclusion(cliente, post):
        return None

    # ── Presupuesto (peso 30, flexible) ──────────────────────
    presupuesto = cliente.get("presupuesto_max")
    precio = post.get("precio")
    es_estricto = str(cliente.get("flexibilidad") or "medio").lower().strip() == "estricto"
    # En ARRIENDO el costo real del cliente es canon + administración.
    admin = post.get("administracion") or 0
    precio_total = precio
    # El aviso dice "ambos" en 142 casos (5%): ahí manda lo que busca el
    # CLIENTE, no lo que declare el aviso, o la administración se pierde.
    busca_arriendo = _op_cliente(cliente.get("operacion", "")) == "arriendo"
    if precio and admin and (busca_arriendo or _inferir_operacion(post) == "arriendo"):
        precio_total = precio + admin
    peso_total += 30
    if presupuesto and precio:
        # Si el broker dio un MÍNIMO explícito ("de 2.700 a 3.600"), ese manda
        # sobre el piso automático del 80%.
        pmin = cliente.get("presupuesto_min")
        piso_efectivo = (pmin / presupuesto) if pmin else piso_precio
        factor, razon, ok = _factor_precio(precio_total, presupuesto, flex_precio,
                                           piso_efectivo)
        if factor < 0:
            return None  # fuera del rango (muy caro o muy barato)
        if precio_total != precio:
            razon = (f"{formato_cop(precio)} + {formato_cop(admin)} admin = "
                     f"{formato_cop(precio_total)} — " + razon.split(": ")[-1])
        puntaje += 30 * factor
        (razones_ok if ok else razones_no).append(razon)
    elif presupuesto and not precio:
        if es_estricto:
            return None                # estricto: sin precio no hay cómo confiar
        puntaje += 30 * 0.35           # sin precio no se puede verificar el presupuesto
        razones_no.append("⚠️ el aviso no indica precio (no se pudo verificar presupuesto)")
    else:
        puntaje += 30  # el cliente no puso presupuesto → no penaliza
    if presupuesto and precio and not admin and (busca_arriendo or _inferir_operacion(post) == "arriendo"):
        razones_no.append("sin dato de administración (confírmala)")

    # ── Habitaciones (peso 12, EXACTAS por defecto) ──────────
    # "Quiere 2 habitaciones" = 2 (ni 3 ni 4). Si el cliente acepta un rango
    # ("2 o 3"), se usa habitaciones_max. Solo el perfil FLEXIBLE tolera ±1.
    habs_min = cliente.get("habitaciones_min")
    habs_max = (cliente.get("habitaciones_max")
                or (cliente.get("exclusiones") or {}).get("habitaciones_max"))
    habs = post.get("habitaciones")
    peso_total += 12
    if habs_min and habs is not None:
        lo = habs_min
        hi = habs_max if habs_max else habs_min   # sin rango explícito → exacto
        if lo <= habs <= hi:
            puntaje += 12
            razones_ok.append(f"{habs:g} habitaciones" +
                              (f" (pediste {lo:g}-{hi:g})" if hi > lo else ""))
        elif perfil["mult"] > 1.0 and (habs == lo - 1 or habs == hi + 1):
            puntaje += 12 * 0.5
            lado = "menos" if habs < lo else "más"
            razones_no.append(f"{habs:g} habitaciones (1 {lado} de lo pedido)")
        else:
            return None  # fuera de lo pedido: no se muestra
    elif habs_min and habs is None:
        puntaje += 12 * 0.5
        razones_no.append("el post no indica habitaciones")
    else:
        puntaje += 12

    # ── Ubicación (peso 25) ──────────────────────────────────
    p_ubi, razon_ubi = _match_ubicacion(cliente, post)
    # Negativo = el aviso dice estar en un barrio que no es de los pedidos.
    # Se descarta, igual que un precio fuera del margen.
    if p_ubi < 0:
        return None
    puntaje += p_ubi * 25
    peso_total += 25
    # Solo una coincidencia de barrio de verdad va como razón A FAVOR. Las de
    # zona (0.40) y las de "ubicación parecida" traen la palabra "verifícalo"
    # y salían en la columna de lo bueno: 273 de 892 filas del radar decían
    # "verifícalo" entre las razones a favor.
    (razones_ok if p_ubi >= 0.85 else razones_no).append(razon_ubi)
    # Si pidió zonas concretas y el inmueble no pega NADA con ellas, es un mal match.
    ubicacion_fallo = bool((cliente.get("barrios") or cliente.get("zona")) and p_ubi < 0.3)

    # ── Metraje (peso 20, flexible) ──────────────────────────
    area = post.get("area_m2")
    a_min = cliente.get("area_min")
    a_max = cliente.get("area_max")
    peso_total += 20
    if area and (a_min or a_max):
        lo, hi = _rango_area(cliente)
        # Metraje desproporcionado (ej. 400 m² para quien pide ~120): otro producto.
        if hi < 1e9 and area > hi * 1.5:
            return None
        factor, razon, ok = _factor_area(area, lo, hi, flex_area)
        if factor == 0.0:
            return None   # fuera del margen de metraje (igual de firme que el presupuesto)
        puntaje += 20 * factor
        (razones_ok if ok else razones_no).append(razon)
    elif (a_min or a_max) and not area:
        if es_estricto:
            return None                # estricto: sin metraje no hay cómo confiar
        puntaje += 20 * 0.4            # sin dato: crédito parcial, no completo
        razones_no.append("⚠️ el aviso no indica metraje (no se pudo verificar)")
    else:
        puntaje += 20

    # ── Baños (peso 8, permite 1 menos) ──────────────────────
    banos = post.get("banos")
    banos_min = cliente.get("banos_min")
    peso_total += 8
    if banos_min and banos is not None:
        if banos >= banos_min:
            puntaje += 8
            razones_ok.append(f"{banos:g} baños (pedías {banos_min:g}+)")
        elif banos >= banos_min - 1:
            puntaje += 8 * 0.5
            razones_no.append(f"{banos:g} baños (1 menos de lo pedido)")
        else:
            razones_no.append(f"solo {banos:g} baños (pedías {banos_min:g}+)")
    elif banos_min and banos is None:
        puntaje += 8 * 0.5             # sin dato: crédito parcial, no completo
        razones_no.append("el aviso no indica baños")
    else:
        puntaje += 8

    # Si pidió un piso concreto y el aviso no lo dice, se muestra pero avisado:
    # es de las cosas que él verifica en dos minutos con una llamada.
    exc_piso = (cliente.get("exclusiones") or {})
    if exc_piso.get("piso_min") or exc_piso.get("piso_max"):
        _p = piso_del_post(post)
        if _p is None:
            razones_no.append("⚠️ el aviso no dice en qué piso está (confírmalo)")
        elif _p == 99:
            razones_ok.append("último piso / penthouse")
        else:
            razones_ok.append(f"piso {_p}")

    # Perímetro: lo que quedaba fuera ya se descartó arriba, así que aquí solo
    # falta decirle si la dirección se pudo verificar o no. Dos de cada tres
    # avisos no traen dirección utilizable: si no se avisa, él no tiene forma
    # de saber cuáles revisar a mano.
    # Se responde EJE POR EJE, solo por los que el cliente puso: decir "dentro
    # de tu perímetro" por un eje que él no limitó es prometerle una
    # verificación que nunca se hizo. Pasaba de verdad: a María, que solo tiene
    # tope de calle (hasta la 95), el aviso "Carrera 106 con Cra 23" —de donde
    # no se puede leer ninguna calle— le salía como "dentro de tu perímetro
    # (carrera 106)", y está en Fontibón, a media ciudad de Rosales.
    _per = _perimetro(cliente)
    if _per:
        _calle, _carrera, _ = direccion_del_post(post)
        _pide_calle = bool(_per.get("calle_min") or _per.get("calle_max"))
        _pide_cra = bool(_per.get("carrera_min") or _per.get("carrera_max"))
        _vistos = ([f"calle {_calle}"] if _pide_calle and _calle is not None else []) + \
                  ([f"carrera {_carrera}"] if _pide_cra and _carrera is not None else [])
        _faltan = (["la calle"] if _pide_calle and _calle is None else []) + \
                  (["la carrera"] if _pide_cra and _carrera is None else [])
        if _vistos:
            razones_ok.append("dentro de tu perímetro (" + " con ".join(_vistos) + ")")
        if _faltan:
            # 2 de cada 3 avisos no traen dirección utilizable: sin este aviso
            # él no tiene forma de saber cuáles le toca revisar a mano.
            razones_no.append("⚠️ el aviso no dice " + " ni ".join(_faltan) +
                              " (no se pudo verificar tu perímetro)")

    # ── Extras (peso 15) ─────────────────────────────────────
    extras_cliente = [e for e in (cliente.get("extras") or []) if str(e).strip()]
    extras_post = set(post.get("extras") or [])
    # Se parten en dos: los DUROS pesan y pueden restar; los SUAVES solo suman.
    # Sin esta división, agregarle "iluminado" a la ficha de Claudia García la
    # dejaba en 0 coincidencias (tenía 24), porque pedir un extra y no verlo
    # cuesta los 15 puntos del bloque MÁS el multiplicador de 0,7 del final.
    extras_suaves = [e for e in extras_cliente if es_extra_suave(e)]
    extras_cliente = [e for e in extras_cliente if e not in extras_suaves]
    peso_total += 15
    if extras_cliente:
        presentes = [e for e in extras_cliente if _extra_cumplido(e, post)]
        faltantes = [e for e in extras_cliente if e not in presentes]
        puntaje += 15 * (len(presentes) / len(extras_cliente))
        if presentes:
            razones_ok.append("incluye: " + ", ".join(sorted(presentes)))
        if faltantes:
            razones_no.append("no menciona: " + ", ".join(sorted(faltantes)))
        # Los extras OBLIGATORIOS ya no llegan hasta aquí: los corta
        # _falla_obligatorio antes de puntuar nada. Esto es solo para los
        # deseables, que sí restan sin descartar.
        pen_extras_oblig = 0
    else:
        puntaje += 15
        pen_extras_oblig = 0

    score = round(100 * puntaje / peso_total) if peso_total else 0
    score = max(0, score - pen_extras_oblig)

    # Datos incompletos: un aviso que no dice ni precio, ni metraje, ni habitaciones
    # no puede rankear como uno completo. Tope al puntaje + etiqueta para verificar.
    faltantes_nucleo = sum([
        1 if (presupuesto and not precio) else 0,
        1 if ((a_min or a_max) and not area) else 0,
        1 if (habs_min and habs is None) else 0,
    ])
    if faltantes_nucleo >= 2:
        score = min(score, 60)
        razones_no.append("⚠️ datos incompletos: verifícalo antes de enviarlo")

    # Fuera de las zonas pedidas: castigo fuerte para que no se cuele (ej. Cota).
    if ubicacion_fallo:
        score = round(score * 0.4)
        razones_no.append("⚠️ fuera de las zonas que pediste")

    # Pidió características y el aviso no trae NI UNA.
    #
    # Los pesos suman 110 y el umbral es 80, así que fallar extras por completo
    # costaba 15 de 110 —unos 13 puntos— y el inmueble salía igual: medido, 177
    # de 317 tarjetas de clientes que pidieron extras no traían ninguno, y
    # llegaban a 86. Restar no alcanza cuando lo que falla es justo lo que el
    # cliente pidió; por eso multiplica, igual que la ubicación.
    if extras_cliente and not presentes:
        score = round(score * 0.7)
        razones_no.append("⚠️ no trae ninguna de las características que pediste")

    # Extras SUAVES: suman un empujón cuando el aviso los dice y no castigan
    # cuando calla. Es el mismo trato que ya recibe el piso unas líneas arriba
    # —se muestra avisado— y por la misma razón: Daniel lo confirma en dos
    # minutos, y descartarlo a ciegas le esconde inmuebles que sí le sirven.
    if extras_suaves:
        vistos = [e for e in extras_suaves if _extra_cumplido(e, post)]
        if vistos:
            score = min(100, score + 4 * len(vistos))
            razones_ok.append("el aviso dice: " + ", ".join(sorted(vistos)))
        for e in extras_suaves:
            if e not in vistos:
                razones_no.append(f"⚠️ el aviso no dice si es {e} (confírmalo)")

    # Aprendizaje: baja el puntaje si se parece a lo que el cliente ya descartó.
    pen, razones_pref = _ajuste_preferencias(cliente, post)
    if pen:
        score = max(0, score - pen)
        razones_no.extend(f"🧠 {r}" for r in razones_pref)

    return {
        "score": score,
        "post": post,
        "razones_ok": razones_ok,
        "razones_no": razones_no,
    }


def cruzar(clientes: list[dict[str, Any]], posts: list[dict[str, Any]],
           score_minimo: int = 50,
           flex_precio: float = FLEX_PRECIO,
           flex_area: float = FLEX_AREA,
           piso_precio: float = PISO_PRECIO) -> dict[str, list[dict[str, Any]]]:
    """Cruza todos los clientes contra todos los posts.

    Devuelve un diccionario {nombre_cliente: [matches ordenados por score]}.
    flex_precio/flex_area/piso_precio controlan qué tan flexible es la comparación.
    """
    resultado: dict[str, list[dict[str, Any]]] = {}
    for cliente in clientes:
        # Los clientes 'estrictos' exigen un puntaje mínimo más alto (solo lo muy acertado).
        piso_score = max(score_minimo, perfil_flex(cliente)["score_min"])
        matches = []
        for post in posts:
            if not post.get("es_inmueble", True) or esta_vendido(post):
                continue
            ev = evaluar(cliente, post, flex_precio, flex_area, piso_precio)
            if ev and ev["score"] >= piso_score:
                matches.append(ev)
        matches.sort(key=lambda m: m["score"], reverse=True)
        resultado[cliente["nombre"]] = matches
    return resultado
