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
# 0.20 = margen del 20% definido por Daniel: acepta hasta 20% por encima del
# presupuesto o por fuera del rango de metraje, con puntaje que baja gradualmente.
FLEX_PRECIO = 0.20
FLEX_AREA = 0.20

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


def _mismo_lugar(a: str, b: str) -> bool:
    """¿'a' y 'b' nombran el mismo lugar? Compara por PALABRAS completas.

    'Chicó' vs 'Chicó Reservado' → sí (chico ⊆ {chico, reservado}).
    'Chía' vs 'Chicó' → no (palabras distintas; antes el fuzzy los confundía).
    'Chicó Norte' vs 'Norte' → no ('norte' solo es genérico).
    """
    ta, tb = _tokens_lugar(a), _tokens_lugar(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


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
    candidatos_post = _expandir_apodos(
        [post_barrio, post_zona, post_dir, _zona_de(post_barrio)])

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

    # 1b) Ningún barrio exacto: ¿al menos cae en la misma zona REAL?
    # OJO: la zona real del post sale de NUESTRO mapa de barrios (Santa Bárbara →
    # Usaquén), no de lo que el aviso declare (a veces le ponen "Chapinero" a todo).
    zona_post_real = _zona_de(post_barrio) or _norm(post_zona)
    for b in barrios_cliente:
        if _zona_de(b) and _zona_de(b) == zona_post_real:
            # Zona correcta pero OTRO barrio: se muestra con advertencia, nunca como pleno.
            return 0.75, f"misma zona ({_zona_de(b)}), otro barrio — verifícalo"

    # 2) coincidencia por zona pedida (nivel zona: nunca cuenta como barrio exacto)
    if zona_cliente:
        nz_tokens = _tokens_lugar(zona_cliente) or {_norm(zona_cliente)}
        for c in [_zona_de(post_barrio), post_zona, post_barrio]:
            nc = _norm(c)
            if nc and (nz_tokens & (_tokens_lugar(nc) or {nc}) or _norm(zona_cliente) == nc):
                if barrios_cliente:
                    # Pidió barrios concretos y solo cuadra la zona general: señal débil.
                    return 0.5, f"solo coincide la zona general ({zona_cliente}) — barrio distinto"
                return 0.85, f"zona coincide: {zona_cliente}"

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
    # OJO: "extras" obligatorios NO anulan aquí: que un caption no mencione el
    # parqueadero no prueba que no exista. Se penaliza fuerte en evaluar() en su lugar.
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


def _falla_exclusion(cliente: dict[str, Any], post: dict[str, Any]) -> str | None:
    """Filtro DURO por comentarios del broker: barrios o palabras que anulan el inmueble.

    Devuelve el motivo si el inmueble debe anularse, o None si pasa.
    """
    exc = cliente.get("exclusiones") or {}
    barrios_x = _expandir_apodos(exc.get("barrios") or [])
    palabras_x = exc.get("palabras") or []
    if barrios_x:
        candidato = " ".join(_norm(x) for x in (
            post.get("barrio"), post.get("zona"), post.get("direccion"),
            _zona_de(post.get("barrio") or "")))
        for b in barrios_x:
            nb = _norm(b)
            if nb and nb in candidato:
                return f"barrio excluido: {b}"
    if palabras_x:
        texto = _norm(post.get("caption", "")) + " " + _norm(post.get("resumen", ""))
        lugar = " ".join(_norm(post.get(k) or "") for k in ("barrio", "zona", "direccion"))
        for w in palabras_x:
            nw = _norm(w)
            if not nw or len(nw) < 4:
                continue
            # Palabras de zona genéricas ('norte', 'sur'…) solo aplican a la UBICACIÓN
            # del aviso, no a todo el texto ('iluminación norte' no debe anular).
            objetivo = lugar if nw in _ZONAS_GENERICAS else texto
            if re.search(rf"\b{re.escape(nw)}\b", objetivo):
                return f"contiene «{w}» (excluido por ti)"
    # Topes numéricos duros pedidos por el broker.
    area = post.get("area_m2")
    if exc.get("area_max") and area and area > exc["area_max"]:
        return f"{area:g} m²: por encima del tope ({exc['area_max']:g} m²)"
    if exc.get("area_min") and area and area < exc["area_min"]:
        return f"{area:g} m²: por debajo del mínimo ({exc['area_min']:g} m²)"
    precio = post.get("precio")
    if exc.get("precio_max") and precio and precio > exc["precio_max"]:
        return f"precio por encima del tope ({formato_cop(exc['precio_max'])})"
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
    if deseado_tipo and post.get("tipo") and not _tipo_compatible(deseado_tipo, post.get("tipo")):
        return f"es {post.get('tipo')} (buscas {deseado_tipo})"
    # Antigüedad: pediste algo nuevo (tope de años de construido).
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
    for w in palabras:
        nw = _norm(w)
        if nw and nw in texto:
            pen += 15
            razones.append(f"a este cliente no le gustó algo así: «{w}»")
    extras_post = set(post.get("extras") or [])
    for ex in req_extras:
        if ex not in extras_post:
            pen += 8
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
    if precio and admin and _inferir_operacion(post) == "arriendo":
        precio_total = precio + admin
    peso_total += 30
    if presupuesto and precio:
        factor, razon, ok = _factor_precio(precio_total, presupuesto, flex_precio, piso_precio)
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
    if presupuesto and precio and not admin and _inferir_operacion(post) == "arriendo":
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
    puntaje += p_ubi * 25
    peso_total += 25
    (razones_ok if p_ubi >= 0.6 else razones_no).append(razon_ubi)
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

    # ── Extras (peso 15) ─────────────────────────────────────
    extras_cliente = set(cliente.get("extras") or [])
    extras_post = set(post.get("extras") or [])
    peso_total += 15
    if extras_cliente:
        presentes = extras_cliente & extras_post
        faltantes = extras_cliente - extras_post
        puntaje += 15 * (len(presentes) / len(extras_cliente))
        if presentes:
            razones_ok.append("incluye: " + ", ".join(sorted(presentes)))
        if faltantes:
            razones_no.append("no menciona: " + ", ".join(sorted(faltantes)))
        # Extras marcados como OBLIGATORIOS: castigo fuerte si no se mencionan (no se
        # anula del todo: que el caption no los liste no prueba que no existan).
        if faltantes and "extras" in set(cliente.get("obligatorios") or []):
            pen_extras_oblig = 25
            razones_no.append("⚠️ no menciona algo OBLIGATORIO ("
                              + ", ".join(sorted(faltantes)) + ") — confírmalo antes de enviar")
        else:
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
