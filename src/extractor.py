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
  "operacion": "arriendo"|"venta"|null,
  "barrios": [string],              // barrios de interés mencionados
  "zona": string|null,              // localidad o sector (ej. Chapinero, Norte)
  "presupuesto_max": number|null,   // en pesos COP, número entero sin puntos
  "area_min": number|null,          // metros cuadrados
  "area_max": number|null,          // metros cuadrados
  "habitaciones_min": number|null,
  "banos_min": number|null,
  "extras": [string],               // SOLO valores de: {EXTRAS_VALIDOS}
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
        datos["barrios"] = datos.get("barrios") or []
        datos["perimetro"] = ""
        if not datos.get("nombre"):
            datos["nombre"] = f"Cliente {len(resultado) + 1}"
        resultado.append(datos)
        log(f"Interpretado: {datos['nombre']} ({len(resultado)}/{total})")

    log(f"Listo. Se interpretaron {len(resultado)} cliente(s).")
    return resultado


# ── Aprender qué evita un cliente (de los inmuebles descartados) ─────

SYSTEM_PREFERENCIAS = f"""Eres un asistente inmobiliario en Bogotá. Te doy las observaciones de
por qué un cliente RECHAZÓ varios inmuebles. Tu tarea es deducir qué EVITAR en sus próximas
búsquedas.

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
