"""Lectura inteligente de captions con Claude.

Toma el texto libre de un caption de Instagram (con emojis, abreviaturas y
jerga inmobiliaria colombiana) y lo convierte en datos estructurados:
operación, barrio, metraje, precio, habitaciones, baños, extras, etc.
"""
from __future__ import annotations

import json
import re
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
  "no_disponible": true|false,      // true si dice VENDIDO/ARRENDADO/"ya no disponible"
                                    // (muchos brokers editan el caption en vez de borrar)
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
  "antiguedad_anos": number|null,   // años de construido. "para estrenar"/"sobre planos"/
                                    // "obra nueva"/"proyecto nuevo" = 0; "X años de construido" = X
  "extras": [string],               // SOLO valores de esta lista: {EXTRAS_VALIDOS}
  "resumen": string                 // una frase corta describiendo el inmueble
}}

Reglas:
- Si un dato no aparece, usa null (o lista vacía para "extras").
- Interpreta abreviaturas: "alcobas"/"hab"/"habs" = habitaciones; "mts2"/"m2"/"mtrs" = area_m2;
  "parq"/"gj" = parqueaderos; "admin" = administracion.
- Precios: "1.500" o "1500" en contexto de arriendo suele ser millones → conviértelo a pesos
  (1.500.000). "$450M" o "450 millones" en venta = 450000000. En contexto de VENTA, un "$1.900"
  o "2.300" abreviado suele ser MILES de millones (1.900 = 1900000000). El apóstrofo es separador
  ("1'900.000.000" = 1900000000). "MM" = millones. Usa tu criterio del mercado bogotano.
- "operacion": si el texto no lo dice explícito, dedúcelo por el precio. Un canon MENSUAL
  (millones: ej. $3 a $40 millones) es "arriendo"; un precio de cientos o miles de millones
  (ej. $450M, $1.800.000.000) es "venta". Deja null SOLO si no hay ninguna pista.
- "cuarto de servicio"/"alcoba de servicio"/"zona de ropas con baño" → "cuarto_servicio".
- "antiguedad_anos": años de construido si se menciona o se deduce. "para estrenar"/"a estrenar"/
  "sobre planos"/"obra nueva"/"proyecto nuevo"/"preventa" = 0; "remodelado" NO es lo mismo que nuevo
  (déjalo null salvo que diga los años); "X años de construido/antigüedad" = X; si no hay pista, null.
- Si el post NO es un inmueble específico (es publicidad genérica, motivacional, etc.),
  pon "es_inmueble": false y el resto en null.
- No inventes datos que no estén en el texto.
"""


# ── Dieta de tokens: recorta murallas de hashtags y emojis repetidos ──
_RE_HASHTAG = re.compile(r"#\w+")
_RE_MURALLA = re.compile(r"(?:#\w+[\s.,]*){2,}")
_RE_EMOJI_REP = re.compile(
    r"([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]\uFE0F?)\1{2,}")


def _dieta(texto: str) -> str:
    """Adelgaza el caption antes de mandarlo a la IA: quita murallas de hashtags
    (conservando las primeras etiquetas, que a veces traen barrio/operación) y
    emojis repetidos. Mismos datos, ~20-30% menos tokens."""
    t = texto or ""
    tags = _RE_HASHTAG.findall(t)
    if len(tags) > 5:
        t = _RE_MURALLA.sub(" ", t)
        t += "\nEtiquetas: " + " ".join(x[1:] for x in tags[:8])
    t = _RE_EMOJI_REP.sub(r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ── Lectura en GRUPO: varios avisos por llamada (instrucciones se pagan 1 vez) ──
SYSTEM_LOTE = SYSTEM_PROMPT + """

Si recibes VARIOS avisos separados por '=== AVISO N ===', devuelve ÚNICAMENTE un
ARRAY JSON con un objeto por aviso, EN EL MISMO ORDEN (AVISO 1 → posición 0).
Sin texto fuera del array. Si un aviso no es un inmueble, su objeto lleva
"es_inmueble": false."""

TAM_GRUPO = 8    # avisos por llamada (equilibrio costo/robustez)


def _params_grupo(captions: list[str]) -> dict[str, Any]:
    """Parámetros de la llamada agrupada (los usa el modo normal Y el modo lote)."""
    cuerpo = "\n\n".join(f"=== AVISO {i + 1} ===\n{_dieta(c)[:3000]}"
                          for i, c in enumerate(captions))
    return {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": min(8000, 500 + 700 * len(captions)),
        # El caché de prompt hace que las instrucciones cuesten ~10% tras la 1ª llamada.
        "system": [{"type": "text", "text": SYSTEM_LOTE,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": cuerpo}],
    }


def _parsear_grupo(texto: str, n: int) -> list[dict[str, Any] | None]:
    """Convierte la respuesta del grupo en una lista de n resultados (None = falló)."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`")
    ini, fin = t.find("["), t.rfind("]")
    if ini < 0 or fin <= ini:
        return [None] * n
    try:
        arr = json.loads(t[ini:fin + 1])
    except json.JSONDecodeError:
        return [None] * n
    out: list[dict[str, Any] | None] = []
    for d in arr[:n]:
        try:
            d["extras"] = [e for e in (d.get("extras") or []) if e in EXTRAS_VALIDOS]
            out.append(_validar_precio(d))
        except Exception:  # noqa: BLE001
            out.append(None)
    out.extend([None] * (n - len(out)))
    return out


def _extraer_grupo(client: anthropic.Anthropic, captions: list[str]
                   ) -> list[dict[str, Any] | None]:
    """Lee un grupo de captions en UNA llamada. None por aviso que no se pudo."""
    msg = client.messages.create(**_params_grupo(captions))
    if msg.stop_reason == "max_tokens":
        return [None] * len(captions)
    return _parsear_grupo(msg.content[0].text, len(captions))


def _extraer_uno(client: anthropic.Anthropic, caption: str) -> dict[str, Any]:
    """Llama a Claude para extraer los datos de un solo caption."""
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _dieta(caption)[:4000]}],
    )
    texto = msg.content[0].text.strip()
    # Por si el modelo envuelve la respuesta en ```json … ```
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{") : texto.rfind("}") + 1]
    datos = json.loads(texto)
    # Filtra extras a la lista válida por seguridad.
    datos["extras"] = [e for e in (datos.get("extras") or []) if e in EXTRAS_VALIDOS]
    return _validar_precio(datos)


def interpretar_inmueble(texto: str) -> dict[str, Any]:
    """Interpreta la descripción de UN inmueble (texto libre) a datos estructurados.

    Sirve para el panel manual: el broker pega lo que vio y la IA lo entiende.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("Falta la llave de Claude (ANTHROPIC_API_KEY) para interpretar.")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _extraer_uno(client, texto)


def extraer_pendientes(log=print, lote: bool = False) -> int:
    """Procesa todos los posts de la caché que aún no tienen extracción.

    Con lote=True usa la Batches API (50% de descuento) — para el robot diario,
    donde nadie está esperando. Devuelve cuántos captions se procesaron.
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
    grupos = [pendientes[i:i + TAM_GRUPO] for i in range(0, len(pendientes), TAM_GRUPO)]

    if lote and len(pendientes) >= TAM_GRUPO:
        try:
            return _extraer_por_lote(client, grupos, len(pendientes), log)
        except Exception as e:  # noqa: BLE001 - el lote nunca puede costar el día
            log(f"⚠️ El modo lote falló ({e}); sigo en modo normal.")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    procesados = 0
    # Grupos de captions en paralelo: las instrucciones se pagan 1 vez por grupo
    # (no por aviso) y el caché de prompt abarata el resto.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futuros = {pool.submit(_extraer_grupo, client,
                               [f["caption"] for f in g]): g for g in grupos}
        for fut in as_completed(futuros):
            grupo = futuros[fut]
            try:
                resultados = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"  ⚠️ Grupo completo falló ({e}); reintento uno a uno.")
                resultados = [None] * len(grupo)
            for fila, datos in zip(grupo, resultados):
                if datos is None:      # rescate individual del aviso que falló
                    try:
                        datos = _extraer_uno(client, fila["caption"])
                    except Exception as e:  # noqa: BLE001
                        log(f"  ⚠️ No se pudo leer un post de @{fila['cuenta']}: {e}")
                        continue
                db.guardar_extraccion(fila["id"], datos)
                procesados += 1
            log(f"Leídos {procesados}/{len(pendientes)} captions…")
    log(f"Listo. Se leyeron {procesados} captions.")
    return procesados


def _extraer_por_lote(client: anthropic.Anthropic, grupos, total: int, log) -> int:
    """Modo LOTE (Batches API): mismas lecturas a MITAD de precio.

    Pensado para el robot de la madrugada, donde nadie espera la respuesta.
    Espera hasta ~40 min; si el lote no termina, se cancela y el que llama
    hace el trabajo en modo normal.
    """
    import time

    requests = [{"custom_id": f"g{i}",
                 "params": _params_grupo([f["caption"] for f in g])}
                for i, g in enumerate(grupos)]
    batch = client.messages.batches.create(requests=requests)
    log(f"📦 Lote enviado ({len(requests)} grupos, {total} captions) — tarifa 50%…")

    inicio = time.time()
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        if time.time() - inicio > 40 * 60:
            try:
                client.messages.batches.cancel(batch.id)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("el lote no terminó en 40 min")
        time.sleep(20)

    procesados = 0
    por_id = {f"g{i}": g for i, g in enumerate(grupos)}
    fallidos: list[dict] = []
    for entrada in client.messages.batches.results(batch.id):
        grupo = por_id.get(entrada.custom_id) or []
        if entrada.result.type == "succeeded":
            msg = entrada.result.message
            resultados = ([None] * len(grupo) if msg.stop_reason == "max_tokens"
                          else _parsear_grupo(msg.content[0].text, len(grupo)))
        else:
            resultados = [None] * len(grupo)
        for fila, datos in zip(grupo, resultados):
            if datos is None:
                fallidos.append(fila)
                continue
            db.guardar_extraccion(fila["id"], datos)
            procesados += 1
    for fila in fallidos:      # rescate individual (pocos, a tarifa normal)
        try:
            db.guardar_extraccion(fila["id"], _extraer_uno(client, fila["caption"]))
            procesados += 1
        except Exception as e:  # noqa: BLE001
            log(f"  ⚠️ No se pudo leer un post de @{fila['cuenta']}: {e}")
    log(f"📦 Lote listo: {procesados}/{total} captions a mitad de tarifa.")
    return procesados


# ── Lectura de páginas de portales (varios inmuebles por página) ─────

SYSTEM_PORTAL = f"""Eres un asistente inmobiliario en Bogotá. Recibes el TEXTO de una página de
un portal o sitio web inmobiliario (puede listar VARIOS inmuebles, o ser la ficha de uno solo).
Extrae TODOS los inmuebles concretos que encuentres.

Devuelve ÚNICAMENTE un ARRAY JSON válido (sin texto extra, sin ```), un objeto por inmueble:
[{{
  "operacion": "arriendo"|"venta"|null,
  "no_disponible": true|false,      // true si el aviso dice VENDIDO/ARRENDADO/no disponible
  "tipo": "apartamento"|"casa"|"apartaestudio"|"local"|"oficina"|"otro"|null,
  "barrio": string|null, "zona": string|null, "direccion": string|null,
  "area_m2": number|null, "precio": number|null, "administracion": number|null,
  "habitaciones": number|null, "banos": number|null, "parqueaderos": number|null,
  "estrato": number|null, "antiguedad_anos": number|null,
  "extras": [string],               // SOLO de: {EXTRAS_VALIDOS}
  "url": string|null,               // link directo al inmueble si aparece en el texto
  "publicado_hace_dias": number|null, // SI la página dice hace cuánto se publicó el aviso:
                                    // "Publicado hoy"=0; "ayer"=1; "hace 3 días"=3;
                                    // "hace 2 semanas"=14; "hace 3 meses"=90. Si no aparece, null.
  "resumen": string                 // una frase corta describiendo el inmueble
}}]

Reglas:
- Precios en pesos COP como número entero sin puntos. "$450M"/"450 millones"=450000000;
  "1.900.000.000" tal cual; canon de arriendo en millones (ej. 3.500.000).
  En VENTA, abreviaturas sin sufijo tipo "$1.900" o "2.300" suelen ser MILES de millones
  (1.900 = 1900000000); "$1.900M"/"1900 millones" = 1900000000. El apóstrofo es separador
  ("1'900.000.000" = 1900000000). "MM" = millones.
- "publicado_hace_dias": SOLO si el texto lo dice explícitamente; NO lo inventes.
- "operacion": si no es explícita, dedúcela por el precio (millones = arriendo; cientos/miles
  de millones = venta).
- "antiguedad_anos": "para estrenar"/"sobre planos"/"obra nueva"=0; "X años"=X; si no, null.
- Ignora menús, filtros, banners, anuncios y texto que no describa un inmueble concreto.
- Si no hay ningún inmueble, devuelve un array vacío []. NO inventes datos.
"""


def _validar_precio(d: dict[str, Any], fuente: str = "", log=None) -> dict[str, Any]:
    """Descarta precios imposibles para la operación (protege el cruce de datos malos).

    Una 'venta' de $1.900.000 (mal leída de '$1.900') contaminaría el matching: mejor
    dejar el precio vacío que guardar un dato 1000 veces menor/mayor.
    """
    precio, op = d.get("precio"), (d.get("operacion") or "")
    if not precio:
        return d
    sospechoso = (op == "venta" and precio < 100_000_000) or \
                 (op == "arriendo" and precio > 100_000_000)
    if sospechoso:
        if log:
            log(f"  ⚠️ precio sospechoso en {fuente or 'aviso'} ({op}: {precio:,}) → se deja vacío")
        d["precio"] = None
    return d


def _trozos_pagina(texto: str, tam: int = 13000, solape: int = 600) -> list[str]:
    """Parte el texto largo de una página en trozos con solape (corta en párrafos)."""
    texto = texto.strip()
    if len(texto) <= tam + solape:
        return [texto]
    trozos: list[str] = []
    i = 0
    while i < len(texto) and len(trozos) < 8:      # techo de trozos por página (costo)
        fin = min(len(texto), i + tam)
        corte = fin
        if fin < len(texto):
            salto = texto.rfind("\n\n", i + int(tam * 0.6), fin)
            if salto > i:
                corte = salto
        trozos.append(texto[i:corte])
        if corte >= len(texto):
            break
        i = max(corte - solape, i + 1)
    return trozos


def _extraer_trozo(client: anthropic.Anthropic, trozo: str, fuente: str, log=print
                   ) -> list[dict[str, Any]]:
    """Extrae los inmuebles de UN trozo de página (con rescate de JSON cortado)."""
    try:
        msg = client.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=8000,
            system=SYSTEM_PORTAL,
            messages=[{"role": "user", "content": trozo}],
        )
        if getattr(msg, "stop_reason", "") == "max_tokens":
            log(f"  ⚠️ {fuente}: respuesta cortada; se conservan los inmuebles completos.")
        t = msg.content[0].text.strip()
        if t.startswith("```"):
            t = t.strip("`")
        if "[" in t:
            t = t[t.find("["):]
        try:
            datos = json.loads(t if t.rstrip().endswith("]") else t[:t.rfind("]") + 1])
        except json.JSONDecodeError:
            corte = t.rfind("},")
            if corte <= 0:
                raise
            datos = json.loads(t[:corte + 1] + "]")
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ No se pudo leer una parte de {fuente}: {e}")
        return []
    return datos if isinstance(datos, list) else [datos]


def extraer_inmuebles_pagina(texto: str, fuente: str = "", log=print) -> list[dict[str, Any]]:
    """Lee el texto COMPLETO de una página de portal (por trozos, sin perder avisos)."""
    if not config.ANTHROPIC_API_KEY or not (texto or "").strip():
        return []
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    trozos = _trozos_pagina(texto)
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for trozo in trozos:
        for d in _extraer_trozo(client, trozo, fuente, log):
            if not isinstance(d, dict):
                continue
            # Dedup dentro de la página (el solape puede repetir un aviso).
            clave = (d.get("url") or
                     f"{d.get('barrio')}|{d.get('precio')}|{d.get('area_m2')}|{d.get('habitaciones')}")
            if clave in vistos:
                continue
            vistos.add(clave)
            d["es_inmueble"] = True
            d["extras"] = [e for e in (d.get("extras") or []) if e in EXTRAS_VALIDOS]
            salida.append(_validar_precio(d, fuente, log))
    return salida


# ── Interpretación de clientes desde texto libre ─────────────

SYSTEM_CLIENTES = f"""Eres un asistente experto en el mercado inmobiliario de Bogotá, Colombia.
Recibes la descripción en TEXTO LIBRE del requerimiento de UN cliente (puede traer
abreviaturas, jerga y datos desordenados) y la conviertes en JSON estricto.

Devuelve ÚNICAMENTE un objeto JSON válido (sin texto extra, sin ```), con estas claves:

{{
  "nombre": string|null,            // nombre del cliente si aparece
  "telefono": string|null,          // teléfono/celular si aparece (solo dígitos)
  "operacion": "arriendo"|"venta"|null,
  "tipo": "apartamento"|"casa"|"apartaestudio"|"penthouse"|"local"|"oficina"|null,  // tipo que busca
  "barrios": [string],              // barrios de interés mencionados
  "zona": string|null,              // localidad o sector (ej. Chapinero, Norte)
  "presupuesto_max": number|null,   // en pesos COP, número entero sin puntos
  "area_min": number|null,          // metros cuadrados
  "area_max": number|null,          // metros cuadrados
  "habitaciones_min": number|null,
  "habitaciones_max": number|null,  // ver regla de habitaciones abajo
  "banos_min": number|null,
  "extras": [string],               // SOLO valores de: {EXTRAS_VALIDOS}
  "obligatorios": [string],         // criterios NO negociables. SOLO de:
                                    // ["barrio","presupuesto","habitaciones","banos","metraje","extras"]
  "flexibilidad": "estricto"|"medio"|"flexible",  // qué tan exigente es el cliente
  "prioridad": "alta"|"media"|"baja",  // urgencia del cliente (ver regla abajo)
  "notas": string|null              // cualquier detalle adicional relevante
}}

Reglas de interpretación (mercado bogotano):
- Precios abreviados: "12M", "12 mill", "12 millones" = 12000000.
  "$450M" o "450 millones" (venta) = 450000000.
  "1.900", "1900 millones", "$1.900M" (venta) = 1900000000.
  En ARRIENDO, montos de 1 a 40 suelen ser millones ("arriendo 12M" = 12000000).
  Usa tu criterio del mercado para distinguir arriendo vs venta.
- Habitaciones (IMPORTANTE, sé exacto): "2 alcobas/habs/dormitorios" = quiere DOS →
  habitaciones_min 2 Y habitaciones_max 2. "2 o 3" / "2-3" → min 2, max 3.
  "mínimo 3" / "3 o más" / "3+" → min 3, max 5. Nunca dejes max en null si hay dato.
- "mts2"/"m2"/"mtrs"/"metros" = área. "mín 60 m2" -> area_min 60.
  "entre 60 y 90 m2" -> area_min 60, area_max 90. "máx 120" -> area_max 120.
- "cuarto de servicio"/"alcoba de servicio" -> "cuarto_servicio".
- "obligatorios": palabras SUAVES como "preferible", "preferiblemente", "idealmente",
  "ojalá", "sería bueno", "le gustaría" JAMÁS generan obligatorios (son deseos: van en
  extras/notas y suman puntaje, no filtran).
- "obligatorios": SOLO si el texto usa palabras EXPLÍCITAS de exigencia: "sí o sí",
  "indispensable", "obligatorio", "innegociable", "solo"/"únicamente". OJO: pedir
  "3 habitaciones" o "mínimo 100 m2" NO es obligatorio por sí solo (eso ya lo filtra el
  buscador); en la duda deja la lista VACÍA. Mapea: "solo en Chicó"->barrio;
  "mínimo 3 hab sí o sí"->habitaciones; "80 m2 indispensable"->metraje.
- "prioridad": "alta" si el texto sugiere urgencia ("tiene afán", "urgente", "necesita ya",
  "se muda pronto", "entrega su apto", "responde rápido"); "baja" si está "explorando"/"sin
  afán"/"para el otro año"; si no se nota, "media".
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
- Apodos de zona en Bogotá: "las santas" = los barrios Santa Bibiana, San Patricio,
  Santa Paula y Santa Bárbara → inclúyelos TODOS en "barrios".
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
        datos["extras"] = [e for e in (datos.get("extras") or []) if e in EXTRAS_VALIDOS]
        datos["obligatorios"] = [o for o in (datos.get("obligatorios") or []) if o in OBLIGATORIOS_VALIDOS]
        _fx = str(datos.get("flexibilidad") or "medio").lower().strip()
        datos["flexibilidad"] = _fx if _fx in FLEX_VALIDOS else "medio"
        _pr = str(datos.get("prioridad") or "media").lower().strip()
        datos["prioridad"] = _pr if _pr in ("alta", "media", "baja") else "media"
        datos["tipo"] = (str(datos.get("tipo") or "").lower().strip() or None)
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
  "tipo": "apartamento"|"casa"|"apartaestudio"|"penthouse"|"local"|"oficina"|null,
  "barrios": [string], "zona": string|null,
  "presupuesto_max": number|null, "area_min": number|null, "area_max": number|null,
  "habitaciones_min": number|null, "habitaciones_max": number|null, "banos_min": number|null,
  "extras": [string], "obligatorios": [string],
  "flexibilidad": "estricto"|"medio"|"flexible",
  "prioridad": "alta"|"media"|"baja", "notas": string|null
}}]

"extras" SOLO de: {EXTRAS_VALIDOS}. "obligatorios" SOLO de: {OBLIGATORIOS_VALIDOS} — ÚNICAMENTE si el texto usa palabras
explícitas de exigencia ("sí o sí"/"indispensable"/"obligatorio"/"solo"); pedir "3 hab" o
"mínimo 100 m2" NO cuenta. En la duda, lista VACÍA.
"flexibilidad": "estricto" si el cliente NO cede / es muy exigente; "flexible" si es abierto a
más opciones; "medio" si no se nota. "prioridad": "alta" si hay urgencia ("afán", "urgente",
"necesita ya", "se muda pronto"); "baja" si "sin afán"/"explorando"; si no se nota, "media".
Reglas (mercado bogotano): "12M"/"12 millones"=12000000; "MM"=millones; "$450M" en venta=450000000;
"1.900.000.000" tal cual. Rangos ("800M-900M","11M-14M"): usa el MÁXIMO. "comprar"/"compra" ->
operacion "venta"; "arrendar"/"arriendo" -> "arriendo". Habitaciones EXACTAS: "2 alcobas/habs"= min 2 y max 2; "2 o 3"= min 2 max 3; "mínimo 3"/"3+"= min 3 max 5;
"mts2/m2/metros"=área. "cuarto de servicio" -> "cuarto_servicio". Si la zona viene por calles/carreras,
deduce los barrios reales de Bogotá de ese sector. Teléfono solo dígitos.
Apodo bogotano: "las santas" = Santa Bibiana, San Patricio, Santa Paula y Santa Bárbara (ponlos todos).
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
        _pr = str(d.get("prioridad") or "media").lower().strip()
        d["prioridad"] = _pr if _pr in ("alta", "media", "baja") else "media"
        d["tipo"] = (str(d.get("tipo") or "").lower().strip() or None)
        d["barrios"] = d.get("barrios") or []
        d["perimetro"] = ""
        d["telefono"] = "".join(ch for ch in str(d.get("telefono") or "") if ch.isdigit())
        if not d.get("nombre"):
            d["nombre"] = f"Cliente {len(resultado) + 1}"
        resultado.append(d)
        log(f"Interpretado: {d['nombre']} ({len(resultado)})")
    log(f"Listo. Se encontraron {len(resultado)} cliente(s).")
    return resultado


# ── Edición de un cliente existente con lenguaje natural ─────

SYSTEM_EDICION = f"""Eres un asistente inmobiliario en Bogotá. El broker quiere EDITAR un cliente
existente. Recibes el cliente ACTUAL (JSON) y la instrucción del broker en texto libre.

Devuelve ÚNICAMENTE un objeto JSON con SOLO los campos que deben CAMBIAR (omite los demás):
{{{{
  "nombre": string, "telefono": string, "operacion": "arriendo"|"venta",
  "barrios": [string],              // la lista COMPLETA como debe quedar. Si dice "agrega X",
                                    // incluye X junto a los barrios actuales; si dice "los
                                    // barrios son X, Y", reemplaza.
  "zona": string, "presupuesto_max": number,
  "area_min": number, "area_max": number,
  "habitaciones_min": number, "habitaciones_max": number, "banos_min": number,
  "extras": [string],               // lista COMPLETA final, SOLO de: {EXTRAS_VALIDOS}
  "obligatorios": [string],         // SOLO de {OBLIGATORIOS_VALIDOS} y SOLO con exigencia
                                    // explícita ("sí o sí"/"indispensable")
  "flexibilidad": "estricto"|"medio"|"flexible",
  "prioridad": "alta"|"media"|"baja",
  "notas": string                   // SOLO el texto NUEVO que haya que agregar a las notas
}}}}

Reglas de interpretación (las mismas del mercado bogotano): "12M"=12000000; "1.500 millones" o
"1.500M"=1500000000; apóstrofo separador (1'500.000.000); "2 habitaciones"= min 2 y max 2
(EXACTO); "2 o 3"= min 2 max 3; "mínimo 3"= min 3 max 5; "las santas"= barrios Santa Bibiana,
San Patricio, Santa Paula y Santa Bárbara; "tiene afán/urgente"= prioridad alta; "sin afán"=
baja; "no cede/exigente"= flexibilidad estricto; "abierto"= flexible.
NO incluyas campos que la instrucción no menciona. Si no hay nada claro que cambiar, devuelve {{{{}}}}.
"""


def interpretar_edicion(texto: str, cliente: dict[str, Any]) -> dict[str, Any]:
    """Convierte una instrucción libre en los CAMPOS a cambiar de un cliente existente."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("Falta la llave de Claude (ANTHROPIC_API_KEY).")
    if not (texto or "").strip():
        return {}
    actual = {k: cliente.get(k) for k in (
        "nombre", "telefono", "operacion", "barrios", "zona", "presupuesto_max",
        "area_min", "area_max", "habitaciones_min", "habitaciones_max", "banos_min",
        "extras", "obligatorios", "flexibilidad", "prioridad")}
    actual["notas"] = str(cliente.get("notas") or "")[:300]
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=700,
        system=SYSTEM_EDICION,
        messages=[{"role": "user", "content":
                   f"CLIENTE ACTUAL:\n{json.dumps(actual, ensure_ascii=False)}\n\n"
                   f"INSTRUCCIÓN DEL BROKER:\n{texto.strip()[:3000]}"}],
    )
    t = msg.content[0].text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"): t.rfind("}") + 1]
    try:
        cambios = json.loads(t)
    except json.JSONDecodeError:
        return {}
    if not isinstance(cambios, dict):
        return {}
    # Normalización defensiva (mismas listas cerradas de siempre).
    if "extras" in cambios:
        cambios["extras"] = [e for e in (cambios.get("extras") or []) if e in EXTRAS_VALIDOS]
    if "obligatorios" in cambios:
        cambios["obligatorios"] = [o for o in (cambios.get("obligatorios") or [])
                                   if o in OBLIGATORIOS_VALIDOS]
    if "flexibilidad" in cambios:
        _fx = str(cambios["flexibilidad"] or "").lower().strip()
        if _fx not in FLEX_VALIDOS:
            cambios.pop("flexibilidad")
        else:
            cambios["flexibilidad"] = _fx
    if "prioridad" in cambios:
        _pr = str(cambios["prioridad"] or "").lower().strip()
        if _pr not in ("alta", "media", "baja"):
            cambios.pop("prioridad")
        else:
            cambios["prioridad"] = _pr
    if "telefono" in cambios:
        cambios["telefono"] = "".join(ch for ch in str(cambios["telefono"] or "")
                                      if ch.isdigit())
    return cambios


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
    "habitaciones_max": number|null,  // "solo 2 habitaciones"/"exactamente 2"/"nada de 3" -> 2
    "banos_min": number|null,   // "mínimo 2 baños" -> 2
    "antiguedad_max": number|null  // años máximos de construido. "quiere algo nuevo/a estrenar"
                                // -> pocos años (ej. 5); "máximo 6 años de construido" -> 6;
                                // "nada viejo" -> ~10. Interpreta la INTENCIÓN aunque venga con
                                // doble negación ("no quiere nada que no sea nuevo" = quiere nuevo).
  },
  "tipo": "apartamento"|"casa"|"apartaestudio"|"penthouse"|"local"|"oficina"|null,
                                // tipo de inmueble que SÍ busca, si el broker lo aclara.
                                // "solo apartamentos"/"nada de casas" -> "apartamento";
                                // "solo casas" -> "casa". null si no lo menciona.
  "resumen": string            // frase corta en español de lo que entendiste y vas a anular.
}
Si el broker DESCARTA un inmueble de referencia por ser "muy grande/pequeño/caro/barato", usa
los datos de ese inmueble como límite, pero PON EL TOPE UN POCO POR DENTRO para que ese mismo
inmueble y los similares queden fuera. Ej: descartó uno de 300 m² por muy grande -> area_max 290;
descartó uno de $2.500M por muy caro -> precio_max ~2.400.000.000.
EXCEPCIÓN con el precio: si el motivo solo dice que el inmueble SE PASA DEL PRESUPUESTO del
cliente ("presupuesto máximo 7.500.000", "está por encima de lo que tiene", "muy caro para su
presupuesto"), NO crees precio_max: la ficha del cliente ya tiene su presupuesto y el sistema
le da un margen de negociación del +20% por encima. Un tope nuevo mataría ese margen. Solo crea
precio_max si el broker fija un tope DISTINTO y explícito ("no le muestres nada de más de X"),
y JAMÁS por debajo del presupuesto declarado del cliente.
Apodo bogotano: "las santas" = Santa Bibiana, San Patricio, Santa Paula y Santa Bárbara.
PROHIBIDO: jamás incluyas en "excluir_barrios" un barrio que el cliente PIDE (te paso su
contexto), ni el nombre de la localidad que contiene sus barrios (si pide Chicó, NO excluyas
"Chapinero"; si pide Santa Bárbara, NO excluyas "Usaquén"). Sé quirúrgico: excluye solo los
barrios ESPECÍFICOS que el broker rechaza.
Si el motivo del descarte NO habla de los gustos del cliente sino de un hecho del AVISO
("está repetido", "duplicado", "es el mismo de otro broker", "ya se lo envié", "ya está
vendido/arrendado", "ya no está disponible"), NO crees NINGÚN filtro: devuelve listas vacías,
límites en null y tipo null.
Cifras aproximadas NO son topes exactos: "alrededor de/unos/cerca de X m²" -> area_min = 0.8*X
(ej. "busca algo alrededor de los 250 metros" -> area_min 200, jamás 240 ni 250). Solo usa la
cifra tal cual si el broker dice "mínimo X" o "nada menor a X".
Reglas: incluye SOLO exclusiones CLARAS que el broker pide quitar. Para "limites", llena solo
los topes que el broker mencione y deja el resto en null. "metros"/"m2"/"mts" = área;
distingue área (decenas/cientos) de precio (millones/miles de millones). Si el comentario es una
preferencia suave (no una orden de excluir), deja listas vacías y límites en null. No inventes
barrios que no existan. Usa nombres reales de barrios de Bogotá.
"""


def interpretar_afinacion(comentario: str, cliente: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convierte una instrucción del broker en filtros DUROS (barrios/palabras a anular)."""
    vacio = {"excluir_barrios": [], "excluir_palabras": [], "limites": {}, "tipo": None, "resumen": ""}
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
        # Falla técnica (API caída, sin crédito, JSON malo): distinta de "nada que excluir",
        # para que la interfaz avise en vez de fingir éxito.
        return {**vacio, "error": True}

    def _num(v):
        try:
            n = float(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    lim_in = datos.get("limites") or {}
    limites = {k: _num(lim_in.get(k)) for k in
               ("area_max", "area_min", "precio_max", "habitaciones_min", "habitaciones_max",
                "banos_min", "antiguedad_max")}
    limites = {k: v for k, v in limites.items() if v is not None}
    tipo = str(datos.get("tipo") or "").lower().strip() or None
    return {
        "excluir_barrios": [str(b).strip() for b in datos.get("excluir_barrios", []) if str(b).strip()][:30],
        "excluir_palabras": [str(p).lower().strip() for p in datos.get("excluir_palabras", []) if str(p).strip()][:15],
        "limites": limites,
        "tipo": tipo,
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
        "extras": [e for e in (datos.get("extras") or []) if e in EXTRAS_VALIDOS],
    }
