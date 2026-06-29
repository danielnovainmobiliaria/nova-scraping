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
# 0.15 = acepta hasta 15% por encima del presupuesto o fuera del rango de metraje,
# con un puntaje que baja gradualmente.
FLEX_PRECIO = 0.15
FLEX_AREA = 0.15

# Piso de presupuesto: por debajo de este % del presupuesto, el inmueble se
# considera de otro segmento y NO se muestra (0.70 = 70%). Evita mostrar, por
# ejemplo, un apto de $6.5M a un cliente con presupuesto de $12M.
PISO_PRECIO = 0.70

# Mapa básico barrio → zona/localidad de Bogotá. Sirve para emparejar aunque
# el cliente pida una "zona" y el post mencione un barrio (o viceversa).
# Puedes ampliarlo con el tiempo.
BARRIO_A_ZONA = {
    "chico": "chapinero", "chico norte": "chapinero", "el nogal": "chapinero",
    "rosales": "chapinero", "la cabrera": "chapinero", "quinta camacho": "chapinero",
    "chapinero alto": "chapinero", "el retiro": "chapinero",
    "cedritos": "usaquen", "santa barbara": "usaquen", "santa bárbara": "usaquen",
    "la carolina": "usaquen", "country club": "usaquen", "san patricio": "usaquen",
    "usaquen": "usaquen", "multicentro": "usaquen", "unicentro": "usaquen",
    "el batan": "suba", "niza": "suba", "la alhambra": "suba", "pasadena": "suba",
    "salitre": "fontibon", "ciudad salitre": "fontibon", "modelia": "fontibon",
    "teusaquillo": "teusaquillo", "la soledad": "teusaquillo", "park way": "teusaquillo",
    "galerias": "teusaquillo", "galerías": "teusaquillo",
    "la macarena": "santa fe", "centro internacional": "santa fe",
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
    "estricto": {"mult": 0.3, "piso_extra": 0.20, "score_min": 80},   # no cede en nada
    "medio":    {"mult": 1.0, "piso_extra": 0.0,  "score_min": 0},    # equilibrado (def.)
    "flexible": {"mult": 2.0, "piso_extra": -0.20, "score_min": 0},   # abierto a más opciones
}
FLEX_VALIDOS = list(PERFILES_FLEX.keys())


def perfil_flex(cliente: dict[str, Any]) -> dict[str, Any]:
    """Devuelve el perfil de flexibilidad del cliente (por defecto 'medio')."""
    clave = str(cliente.get("flexibilidad") or "medio").lower().strip()
    return PERFILES_FLEX.get(clave, PERFILES_FLEX["medio"])


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


def _operacion_compatible(cliente_op: str, post: dict[str, Any]) -> bool:
    cli = _norm(cliente_op)
    if not cli:
        return True            # el cliente no especificó → todo sirve
    pos = _inferir_operacion(post)
    if not pos or pos == "ambos":
        return True            # el post no dice (ni por precio) o sirve para ambas
    return cli == pos


def _match_ubicacion(cliente: dict[str, Any], post: dict[str, Any]) -> tuple[float, str]:
    """Puntaje 0..1 de coincidencia de ubicación y una explicación."""
    barrios_cliente = cliente.get("barrios") or []
    zona_cliente = cliente.get("zona") or ""
    if not barrios_cliente and not zona_cliente:
        return 1.0, "sin restricción de zona"

    post_barrio = post.get("barrio") or ""
    post_zona = post.get("zona") or ""
    post_dir = post.get("direccion") or ""
    candidatos_post = [post_barrio, post_zona, post_dir, _zona_de(post_barrio)]

    # 1) ¿coincide algún barrio pedido con lo que dice el post?
    mejor = 0.0
    for b in barrios_cliente:
        nb = _norm(b)
        if not nb:
            continue
        for c in candidatos_post:
            nc = _norm(c)
            if not nc:
                continue
            if nb in nc or nc in nb:
                return 1.0, f"barrio coincide: {b}"
            mejor = max(mejor, fuzz.partial_ratio(nb, nc) / 100.0)
        # ¿el barrio pedido cae en la misma zona que el post?
        if _zona_de(b) and _zona_de(b) == _norm(post_zona):
            return 0.8, f"misma zona ({post_zona})"

    # 2) coincidencia por zona pedida
    if zona_cliente:
        nz = _norm(zona_cliente)
        for c in [post_zona, _zona_de(post_barrio), post_barrio]:
            if nz and nz in _norm(c):
                return 0.85, f"zona coincide: {zona_cliente}"

    if mejor >= 0.75:
        return mejor * 0.7, "ubicación parecida"
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


def _factor_area(area: float, a_min: float | None, a_max: float | None, flex: float
                 ) -> tuple[float, str, bool]:
    """Compara metraje vs rango pedido de forma flexible."""
    lo = a_min if a_min else 0.0
    hi = a_max if a_max else 1e9
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
        if pr and pc and pc > pr:
            return "presupuesto"
    if "metraje" in oblig:
        a, amin, amax = post.get("area_m2"), cliente.get("area_min"), cliente.get("area_max")
        if a and (amin or amax) and not ((amin or 0) <= a <= (amax or 1e9)):
            return "metraje"
    if "barrio" in oblig:
        p_ubi, _ = _match_ubicacion(cliente, post)
        if p_ubi < 0.8:
            return "barrio/zona"
    if "extras" in oblig:
        if set(cliente.get("extras") or []) - set(post.get("extras") or []):
            return "extras"
    return None


def _falla_exclusion(cliente: dict[str, Any], post: dict[str, Any]) -> str | None:
    """Filtro DURO por comentarios del broker: barrios o palabras que anulan el inmueble.

    Devuelve el motivo si el inmueble debe anularse, o None si pasa.
    """
    exc = cliente.get("exclusiones") or {}
    barrios_x = exc.get("barrios") or []
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
        for w in palabras_x:
            nw = _norm(w)
            if nw and nw in texto:
                return f"contiene «{w}» (excluido por ti)"
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
    peso_total += 30
    if presupuesto and precio:
        factor, razon, ok = _factor_precio(precio, presupuesto, flex_precio, piso_precio)
        if factor < 0:
            return None  # fuera del rango (muy caro o muy barato)
        puntaje += 30 * factor
        (razones_ok if ok else razones_no).append(razon)
    elif presupuesto and not precio:
        puntaje += 30 * 0.5
        razones_no.append("el post no indica precio")
    else:
        puntaje += 30  # el cliente no puso presupuesto → no penaliza

    # ── Habitaciones (peso 12, permite 1 menos) ──────────────
    habs_min = cliente.get("habitaciones_min")
    habs = post.get("habitaciones")
    peso_total += 12
    if habs_min and habs is not None:
        if habs >= habs_min:
            puntaje += 12
            razones_ok.append(f"{habs:g} habitaciones (pedías {habs_min:g}+)")
        elif habs >= habs_min - 1:
            puntaje += 12 * 0.5
            razones_no.append(f"{habs:g} habitaciones (1 menos de lo pedido)")
        else:
            return None  # muchas menos habitaciones: no es similar
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

    # ── Metraje (peso 20, flexible) ──────────────────────────
    area = post.get("area_m2")
    a_min = cliente.get("area_min")
    a_max = cliente.get("area_max")
    peso_total += 20
    if area and (a_min or a_max):
        factor, razon, ok = _factor_area(area, a_min, a_max, flex_area)
        puntaje += 20 * factor
        (razones_ok if ok else razones_no).append(razon)
    else:
        puntaje += 20
        if (a_min or a_max) and not area:
            razones_no.append("el post no indica metraje")

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
    else:
        puntaje += 15

    score = round(100 * puntaje / peso_total) if peso_total else 0

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
            if not post.get("es_inmueble", True):
                continue
            ev = evaluar(cliente, post, flex_precio, flex_area, piso_precio)
            if ev and ev["score"] >= piso_score:
                matches.append(ev)
        matches.sort(key=lambda m: m["score"], reverse=True)
        resultado[cliente["nombre"]] = matches
    return resultado
