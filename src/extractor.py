"""Lectura inteligente de captions con Claude.

Toma el texto libre de un caption de Instagram (con emojis, abreviaturas y
jerga inmobiliaria colombiana) y lo convierte en datos estructurados:
operación, barrio, metraje, precio, habitaciones, baños, extras, etc.
"""
from __future__ import annotations

import json
from typing import Any

import anthropic

from . import config, db

# Lista cerrada de "extras" para que siempre se normalicen igual y el cruce
# con los clientes sea confiable.
EXTRAS_VALIDOS = [
    "estudio",
    "terraza",
    "balcon",
    "cuarto_servicio",
    "deposito",
    "parqueadero",
    "vista",
    "remodelado",
    "amoblado",
    "chimenea",
    "duplex",
    "penthouse",
]

# Criterios que pueden marcarse como NO negociables.
OBLIGATORIOS_VALIDOS = ["barrio", "presupuesto", "habitaciones", "banos", "metraje", "extras"]

# Perfil de flexibilidad del cliente.
FLEX_VALIDOS = ["estricto", "medio", "flexible"]

SYSTEM_PROMPT = f"""Eres un asistente experto en el mercado inmobiliario de Bogotá, Colombia.
Recibes el caption (texto) de una publicación de Instagram de un broker y extraes
la información del inmueble en formato JSON estricto.

Devuelve ÚNICAMENTE un objeto JSON válido (sin texto adicional, sin ```), con estas claves:

{{
  "es_inmueble": true|false,        // true solo si es la oferta de UN inmueble concreto
  "operacion": "arriendo"|"venta"|"ambos"|null,
  "tipo": "apartamento"|"casa"|"apartaestudio"|"local"|"oficina"|"otro"|null,
  "barrio": string|null,            // nombre del barrio tal como aparece
  "zona": string|null,              // localidad o sector (ej. Chapinero, Usaquén, Norte)
  "direccion": string|null,         // dirección si aparece (ej. "Calle 100 # 15-20")
  "area_m2": number|null,           // metraje en metros cuadrados
  "precio": number|null,            // valor en pesos COP, como número entero sin puntos
  "administracion": number|null,    // valor de administración en COP si aparece
  "habitaciones": number|null,
  "banos": number|null,
  "parqueaderos": number|null,
  "estrato": number|null,
  "extras": [string],               // SOLO valores de esta lista: {EXTRAS_VALIDOS}
  "resumen": string                 // una frase corta describiendo el inmueble
}}

Reglas:
- Si un dato no aparece, usa null (o lista vacía para "extras").
- Interpreta abreviaturas: "alcobas"/"hab"/"habs" = habitaciones; "mts2"/"m2"/"mtrs" = area_m2;
  "parq"/"gj" = parqueaderos; "admin" = administracion.
- Precios: "1.500" o "1500" en contexto de arriendo suele ser millones → conviértelo a pesos
  (1.500.000). "$450M" o "450 millones" en venta = 450000000. Usa tu criterio del mercado bogotano.
- "operacion": si el texto no lo dice explícito, dedúcelo por el precio. Un canon MENSUAL
  (millones: ej. $3 a $40 millones) es "arriendo"; un precio de cientos o miles de millones
  (ej. $450M, $1.800.000.000) es "venta". Deja null SOLO si no hay ninguna pista.
- "cuarto de servicio"/"alcoba de servicio"/"zona de ropas con baño" → "cuarto_servicio".
- Si el post NO es un inmueble específico (es publicidad genérica, motivacional, etc.),
  pon "es_inmueble": false y el resto en null.
- No inventes datos que no estén en el texto.
"""


def _extraer_uno(client: anthropic.Anthropic, caption: str) -> dict[str, Any]:
    """Llama a Claude para extraer los datos de un solo caption."""
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": caption.strip()[:4000]}],
    )
    texto = msg.content[0].text.strip()
    # Por si el modelo envuelve la respuesta en ```json … ```
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{") : texto.rfind("}") + 1]
    datos = json.loads(texto)
    # Filtra extras a la lista válida por seguridad.
    datos["extras"] = [e for e in datos.get("extras", []) if e in EXTRAS_VALIDOS]
    return datos


def extraer_pendientes(log=print) -> int:
    """Procesa todos los posts de la caché que aún no tienen extracción.

    Devuelve cuántos captions se procesaron.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "Falta ANTHROPIC_API_KEY en el archivo .env. "
            "Consíguela en https://console.anthropic.com/settings/keys"
        )

    pendientes = db.posts_sin_extraer()
    if not pendientes:
        log("No hay captions nuevos por leer.")
        return 0

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    procesados = 0
    for fila in pendientes:
        try:
            datos = _extraer_uno(client, fila["caption"])
            db.guardar_extraccion(fila["id"], datos)
            procesados += 1
            log(f"Leído post de @{fila['cuenta']} ({procesados}/{len(pendientes)})")
        except Exception as e:  # noqa: BLE001 - no queremos que un post tumbe todo
            log(f"  ⚠️ No se pudo leer un post de @{fila['cuenta']}: {e}")
    log(f"Listo. Se leyeron {procesados} captions.")
    return procesados


# ── Interpretación de clientes desde texto libre ─────────────

SYSTEM_CLIENTES = f"""Eres un asistente experto en el mercado inmobiliario de Bogotá, Colombia.
Recibes la descripción en TEXTO LIBRE del requerimiento de UN cliente (puede traer
abreviaturas, jerga y datos desordenados) y la conviertes en JSON estricto.

Devuelve ÚNICAMENTE un objeto JSON válido (sin texto extra, sin ```), con estas claves:

{{
  "nombre": string|null,            // nombre del cliente si aparece
  "telefono": string|null,          // teléfono/celular si aparece (solo dígitos)
  "operacion": "arriendo"|"venta"|null,
  "barrios": [string],              // barrios de interés mencionados
  "zona": string|null,              // localidad o sector (ej. Chapinero, Norte)
  "presupuesto_max": number|null,   // en pesos COP, número entero sin puntos
  "area_min": number|null,          // metros cuadrados
  "area_max": number|null,          // metros cuadrados
  "habitaciones_min": number|null,
  "banos_min": number|null,
  "extras": [string],               // SOLO valores de: {EXTRAS_VALIDOS}
  "obligatorios": [string],         // criterios NO negociables. SOLO de:
                                    // ["barrio","presupuesto","habitaciones","banos","metraje","extras"]
  "flexibilidad": "estricto"|"medio"|"flexible",  // qué tan exigente es el cliente
  "notas": string|null              // cualquier detalle adicional relevante
}}

Reglas de interpretación (mercado bogotano):
- Precios abreviados: "12M", "12 mill", "12 millones" = 12000000.
  "$450M" o "450 millones" (venta) = 450000000.
  "1.900", "1900 millones", "$1.900M" (venta) = 1900000000.
  En ARRIENDO, montos de 1 a 40 suelen ser millones ("arriendo 12M" = 12000000).
  Usa tu criterio del mercado para distinguir arriendo vs venta.
- "2 alcobas"/"2 habs"/"2 hab"/"2 dormitorios" = habitaciones_min 2.
- "mts2"/"m2"/"mtrs"/"metros" = área. "mín 60 m2" -> area_min 60.
  "entre 60 y 90 m2" -> area_min 60, area_max 90. "máx 120" -> area_max 120.
- "cuarto de servicio"/"alcoba de servicio" -> "cuarto_servicio".
- "obligatorios": agrega un criterio si el texto lo marca como OBLIGATORIO / "sí o sí" /
  "indispensable" / "solo"/"únicamente". Mapea: "solo en Chicó"->barrio; "máx/no más de X"->
  presupuesto; "mínimo 3 hab sí o sí"->habitaciones; "obligatorio 2 baños"->banos;
  "mínimo 80 m2 indispensable"->metraje; "con parqueadero sí o sí"->extras. Si nada es
  obligatorio, deja la lista vacía.
- "flexibilidad": qué tan exigente es el cliente con cumplir sus requerimientos.
  "estricto" si NO cede / es muy exigente / "tiene que ser exacto" / "no se mueve de" /
  "solo lo que cumpla todo"; "flexible" si es abierto / "lo que aparezca por la zona" /
  "flexible en precio/metraje" / "abierto a opciones"; si no se nota, usa "medio".
- Operación: si el cliente quiere COMPRAR ("compra", "comprar") -> operacion="venta"
  (busca inmuebles EN venta). Si quiere ARRENDAR/alquilar -> "arriendo". Si no lo dice
  pero el presupuesto es de cientos o miles de millones, asume "venta"; si es de pocos
  millones (mensuales), asume "arriendo".
- "MM" = millones (10MM = 10000000). El apóstrofo es separador ("1'900.000.000" = 1900000000).
- Rangos de presupuesto ("800M-900M", "11M-14M"): usa el valor MÁXIMO como presupuesto_max
  y menciona el rango en "notas".
- Si el presupuesto viene como PRECIO POR METRO ("15MM-18MM precio Mt2"), usa el valor
  SUPERIOR del rango por m² (ej. 18MM) y multiplícalo por el área para estimar el
  presupuesto_max total. Como el precio por m² es aproximado, conviene ser generoso
  (puedes tomar el tope o un poco más). Aclara en "notas" que fue un cálculo y deja el
  rango original.
- Si la ZONA se describe por calles/carreras (un perímetro, ej. "entre calle 100 y 82" o
  "Calle 72 - 100 - séptima - autopista"): el barrio CASI SIEMPRE se menciona en las
  publicaciones, así que deduce e incluye en "barrios" los nombres REALES de los barrios de
  Bogotá que caen en ese sector (ej. Chicó, El Nogal, Quinta Camacho, La Cabrera, Rosales,
  Chapinero Alto, El Retiro, Cedritos, Santa Bárbara, La Carolina). NO inventes nombres
  genéricos (como "Barrio Antiguo") ni te quedes solo en la localidad si puedes nombrar
  barrios específicos. Copia además el perímetro textual en "notas".
- IGNORA datos administrativos que NO describen el inmueble: teléfono, status, fechas,
  horas, URLs, correos, nombre del asesor/referente. NO los pongas en "notas".
- Si un dato no aparece, usa null (o lista vacía para barrios/extras). NO inventes.
- Si no hay nombre, deja "nombre" en null.
"""


def interpretar_clientes(textos: list[str], log=print) -> list[dict[str, Any]]:
    """Convierte descripciones en texto libre a clientes con formato estructurado.

    Cada elemento de 'textos' es la descripción de un cliente (una fila del archivo).
    Devuelve la lista de clientes en el formato que usa la app.
    Requiere ANTHROPIC_API_KEY.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "Falta la llave de Claude (ANTHROPIC_API_KEY) para interpretar con IA."
        )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resultado: list[dict[str, Any]] = []
    total = len([t for t in textos if t and t.strip()])
    for i, texto in enumerate(textos):
        if not texto or not texto.strip():
            continue
        try:
            msg = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=600,
                system=SYSTEM_CLIENTES,
                messages=[{"role": "user", "content": texto.strip()[:3000]}],
            )
            t = msg.content[0].text.strip()
            if t.startswith("```"):
                t = t.strip("`")
                t = t[t.find("{") : t.rfind("}") + 1]
            datos = json.loads(t)
        except Exception as e:  # noqa: BLE001 - una fila mala no debe tumbar todo
            log(f"  ⚠️ No se pudo interpretar una fila: {e}")
            continue

        # Normalización al formato de la app.
        datos["extras"] = [e for e in datos.get("extras", []) if e in EXTRAS_VALIDOS]
        datos["obligatorios"] = [o for o in (datos.get("obligatorios") or []) if o in OBLIGATORIOS_VALIDOS]
        _fx = str(datos.get("flexibilidad") or "medio").lower().strip()
        datos["flexibilidad"] = _fx if _fx in FLEX_VALIDOS else "medio"
        datos["barrios"] = datos.get("barrios") or []
        datos["perimetro"] = ""
        datos["telefono"] = "".join(ch for ch in str(datos.get("telefono") or "") if ch.isdigit())
        if not datos.get("nombre"):
            datos["nombre"] = f"Cliente {len(resultado) + 1}"
        resultado.append(datos)
        log(f"Interpretado: {datos['nombre']} ({len(resultado)}/{total})")

    log(f"Listo. Se interpretaron {len(resultado)} cliente(s).")
    return resultado


SYSTEM_TEXTO_LIBRE = f"""Eres un asistente inmobiliario en Bogotá. Recibes un texto libre que puede
contener UNO O VARIOS clientes (pegado de WhatsApp, correo, notas, una lista, etc.).

Devuelve ÚNICAMENTE un ARRAY JSON válido (sin texto extra, sin ```), con un objeto por cada
cliente que encuentres, con estas claves:

[{{
  "nombre": string|null, "telefono": string|null,
  "operacion": "arriendo"|"venta"|null,
  "barrios": [string], "zona": string|null,
  "presupuesto_max": number|null, "area_min": number|null, "area_max": number|null,
  "habitaciones_min": number|null, "banos_min": number|null,
  "extras": [string], "obligatorios": [string],
  "flexibilidad": "estricto"|"medio"|"flexible", "notas": string|null
}}]

"extras" SOLO de: {EXTRAS_VALIDOS}. "obligatorios" SOLO de: {OBLIGATORIOS_VALIDOS} (criterios
NO negociables que el texto marque como "obligatorio"/"sí o sí"/"indispensable"/"solo").
"flexibilidad": "estricto" si el cliente NO cede / es muy exigente; "flexible" si es abierto a
más opciones; "medio" si no se nota.
Reglas (mercado bogotano): "12M"/"12 millones"=12000000; "MM"=millones; "$450M" en venta=450000000;
"1.900.000.000" tal cual. Rangos ("800M-900M","11M-14M"): usa el MÁXIMO. "comprar"/"compra" ->
operacion "venta"; "arrendar"/"arriendo" -> "arriendo". "2 alcobas/habs/dormitorios"=habitaciones;
"mts2/m2/metros"=área. "cuarto de servicio" -> "cuarto_servicio". Si la zona viene por calles/carreras,
deduce los barrios reales de Bogotá de ese sector. Teléfono solo dígitos.
Si hay un solo cliente, devuelve un array con un solo objeto. NO inventes clientes que no estén.
"""


def interpretar_texto_libre(texto: str, log=print) -> list[dict[str, Any]]:
    """Interpreta un texto libre (uno o varios clientes) y devuelve la lista estructurada."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("Falta la llave de Claude (ANTHROPIC_API_KEY).")
    if not texto or not texto.strip():
        return []
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=2000,
        system=SYSTEM_TEXTO_LIBRE,
        messages=[{"role": "user", "content": texto.strip()[:8000]}],
    )
    t = msg.content[0].text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("[") : t.rfind("]") + 1]
    try:
        datos = json.loads(t)
    except json.JSONDecodeError:
        return []
    if isinstance(datos, dict):
        datos = [datos]

    resultado: list[dict[str, Any]] = []
    for d in datos:
        if not isinstance(d, dict):
            continue
        d["extras"] = [e for e in (d.get("extras") or []) if e in EXTRAS_VALIDOS]
        d["obligatorios"] = [o for o in (d.get("obligatorios") or []) if o in OBLIGATORIOS_VALIDOS]
        _fx = str(d.get("flexibilidad") or "medio").lower().strip()
        d["flexibilidad"] = _fx if _fx in FLEX_VALIDOS else "medio"
        d["barrios"] = d.get("barrios") or []
        d["perimetro"] = ""
        d["telefono"] = "".join(ch for ch in str(d.get("telefono") or "") if ch.isdigit())
        if not d.get("nombre"):
            d["nombre"] = f"Cliente {len(resultado) + 1}"
        resultado.append(d)
        log(f"Interpretado: {d['nombre']} ({len(resultado)})")
    log(f"Listo. Se encontraron {len(resultado)} cliente(s).")
    return resultado


# ── Aprender qué evita un cliente (de los inmuebles descartados) ─────

SYSTEM_PREFERENCIAS = f"""Eres un asistente inmobiliario en Bogotá. Te doy señales sobre un cliente:
(a) observaciones de por qué RECHAZÓ inmuebles, y/o (b) comentarios del BROKER afinando la
búsqueda (ej. "los resultados están muy lejos, busco algo más moderno y con vista", "no quiero
primer piso", "que siempre tenga parqueadero"). Tu tarea es deducir qué EVITAR y qué priorizar
en sus próximas búsquedas.

Devuelve ÚNICAMENTE un objeto JSON válido (sin texto extra), con estas claves:
{{
  "palabras": [string],   // palabras o frases CORTAS en minúsculas que, si aparecen en la
                          // descripción de un inmueble, indican que probablemente NO le
                          // gustará (ej. "para remodelar", "primer piso", "interior",
                          // "oscuro", "ruidoso", "sin ascensor"). Máximo 8.
  "extras": [string]      // características que AHORA debería tener sí o sí, deducidas de lo
                          // que rechazó. SOLO de esta lista: {EXTRAS_VALIDOS}.
                          // Ej: rechazó "sin parqueadero" -> ["parqueadero"]; rechazó
                          // "viejo/para remodelar" -> ["remodelado"].
}}
Reglas: incluye solo lo que se deduzca claramente. Si no hay nada claro, usa listas vacías.
"""


SYSTEM_AFINACION = """Eres un asistente inmobiliario experto en la geografía de Bogotá, Colombia.
El broker está revisando las coincidencias de un cliente y da una instrucción para LIMPIAR
(anular) los inmuebles que NO cumplen.

Devuelve ÚNICAMENTE un objeto JSON válido (sin texto extra, sin ```), con estas claves:
{
  "excluir_barrios": [string],  // barrios/sectores a EXCLUIR por completo. Si el broker pone un
                                // límite geográfico, EXPÁNDELO a los barrios reales que quedan
                                // FUERA. En Bogotá, a MAYOR número de calle = más al NORTE.
                                // "nada después de la calle 100" / "no más arriba de la 100"
                                //   (al norte de la 100) -> ["Santa Bárbara","Cedritos",
                                //   "La Carolina","Country Club","Usaquén","Multicentro",
                                //   "San Patricio","Unicentro","Toberín","Cedro"]
                                // "solo del Chicó hacia el sur" -> excluye lo que esté al norte.
  "excluir_palabras": [string], // frases que, si aparecen en el aviso, lo anulan por completo
                                // (ej. "primer piso", "para remodelar", "remate", "permuta").
  "limites": {                  // TOPES numéricos que anulan lo que se pase. null si no aplica.
    "area_max": number|null,    // "nada por encima de 160 m2" / "máx 160 metros" -> 160
    "area_min": number|null,    // "mínimo 80 m2" / "nada menor a 80" -> 80
    "precio_max": number|null,  // tope de precio en pesos COP. "que no pase de 1.800 millones"
                                //   -> 1800000000 ; "máx 12M" (arriendo) -> 12000000
    "habitaciones_min": number|null,  // "al menos 3 habitaciones" -> 3
    "banos_min": number|null    // "mínimo 2 baños" -> 2
  },
  "resumen": string            // frase corta en español de lo que entendiste y vas a anular.
}
Reglas: incluye SOLO exclusiones CLARAS que el broker pide quitar. Para "limites", llena solo
los topes que el broker mencione y deja el resto en null. "metros"/"m2"/"mts" = área;
distingue área (decenas/cientos) de precio (millones/miles de millones). Si el comentario es una
preferencia suave (no una orden de excluir), deja listas vacías y límites en null. No inventes
barrios que no existan. Usa nombres reales de barrios de Bogotá.
"""


def interpretar_afinacion(comentario: str, cliente: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convierte una instrucción del broker en filtros DUROS (barrios/palabras a anular)."""
    vacio = {"excluir_barrios": [], "excluir_palabras": [], "limites": {}, "resumen": ""}
    if not config.ANTHROPIC_API_KEY or not (comentario or "").strip():
        return vacio
    contexto = ""
    if cliente:
        barrios = ", ".join(cliente.get("barrios") or []) or "—"
        contexto = f"\n\n(Contexto del cliente — barrios de interés: {barrios}; zona: {cliente.get('zona') or '—'})"
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        msg = client.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=500,
            system=SYSTEM_AFINACION,
            messages=[{"role": "user", "content": (comentario.strip() + contexto)[:3000]}],
        )
        t = msg.content[0].text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            t = t[t.find("{"): t.rfind("}") + 1]
        datos = json.loads(t)
    except Exception:  # noqa: BLE001
        return vacio

    def _num(v):
        try:
            n = float(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    lim_in = datos.get("limites") or {}
    limites = {k: _num(lim_in.get(k)) for k in
               ("area_max", "area_min", "precio_max", "habitaciones_min", "banos_min")}
    limites = {k: v for k, v in limites.items() if v is not None}
    return {
        "excluir_barrios": [str(b).strip() for b in datos.get("excluir_barrios", []) if str(b).strip()][:30],
        "excluir_palabras": [str(p).lower().strip() for p in datos.get("excluir_palabras", []) if str(p).strip()][:15],
        "limites": limites,
        "resumen": str(datos.get("resumen") or "").strip(),
    }


def aprender_preferencias(observaciones: list[str]) -> dict[str, Any]:
    """De las observaciones de inmuebles descartados, deduce qué evitar."""
    if not config.ANTHROPIC_API_KEY or not observaciones:
        return {"palabras": [], "extras": []}
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    texto = "Observaciones de inmuebles que el cliente rechazó:\n- " + "\n- ".join(observaciones)
    try:
        msg = client.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=400,
            system=SYSTEM_PREFERENCIAS,
            messages=[{"role": "user", "content": texto[:3000]}],
        )
        t = msg.content[0].text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            t = t[t.find("{") : t.rfind("}") + 1]
        datos = json.loads(t)
    except Exception:  # noqa: BLE001
        return {"palabras": [], "extras": []}
    return {
        "palabras": [str(p).lower().strip() for p in datos.get("palabras", []) if str(p).strip()][:8],
        "extras": [e for e in datos.get("extras", []) if e in EXTRAS_VALIDOS],
    }
