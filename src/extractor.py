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
