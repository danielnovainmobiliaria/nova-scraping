"""Solicitudes que Brokerap deja en la base para que el robot las atienda.

Brokerap no corre Python ni guarda las llaves de Apify/IA: cuando Daniel pide
algo desde la web (actualizar todo, o aprender del motivo de un descarte), la
web deja una marca en `meta` y un workflow de GitHub que revisa cada pocos
minutos hace el trabajo con las llaves que ya viven en los Secrets.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import clientes as mod_clientes
from . import db, matcher

# Claves en la tabla meta. El valor es la fecha-hora ISO en que se pidió;
# vacío = nada pendiente.
CLAVE_ACTUALIZAR = "solicitud_actualizar"
CLAVE_AFINAR = "solicitud_afinar"

# Solo se aprende de motivos recientes: los descartes viejos (de antes de que
# existiera la marca `afinado`) ya fueron procesados en vivo por la app.
DIAS_MOTIVOS_RECIENTES = 7


def limpiar_solicitud(clave: str) -> None:
    db.guardar_meta(clave, "")


def atender_afinaciones(log=print) -> int:
    """Aprende de los motivos de descarte escritos desde Brokerap.

    Replica lo que la app hace en vivo al descartar con motivo: filtros duros
    (interpretar_afinacion + agregar_exclusiones) y preferencias aprendidas
    (aprender_preferencias). Cada motivo procesado queda marcado `afinado` en
    su proceso para no pagar IA dos veces. Devuelve cuántos motivos aprendió.
    """
    from . import config, extractor
    lista = mod_clientes.cargar_guardados()
    corte = (date.today() - timedelta(days=DIAS_MOTIVOS_RECIENTES)).isoformat()
    aprendidos = 0
    hubo_cambios = False
    for c in lista:
        con_motivo_nuevo = False
        for pr in (c.get("procesos") or []):
            obs = (pr.get("observaciones") or "").strip()
            if pr.get("estado") != "descartado" or not obs or pr.get("afinado"):
                continue
            pr["afinado"] = True
            hubo_cambios = True
            if str(pr.get("fecha") or "") < corte:
                continue   # viejo: la app ya lo procesó en vivo en su momento
            if mod_clientes.es_motivo_administrativo(obs):
                continue   # "repetido/vendido" habla del aviso, no del cliente
            if not config.ANTHROPIC_API_KEY:
                pr["afinado"] = False   # sin llave no se pierde: queda pendiente
                continue
            ctx = (f"El inmueble que descartó era: "
                   f"{pr.get('resumen') or '?'} (barrio {pr.get('barrio') or '?'}, "
                   f"{matcher.formato_cop(pr.get('precio')) or 'precio n/d'}). "
                   f"Motivo por el que NO le sirvió: {obs}")
            try:
                af = extractor.interpretar_afinacion(ctx, c)
                if af.get("error"):
                    pr["afinado"] = False
                    continue
                if (af["excluir_barrios"] or af["excluir_palabras"]
                        or af["limites"] or af.get("tipo")):
                    # sobre la copia en memoria; guardar_lista persiste todo junto
                    excl = c.get("exclusiones") or {}
                    for b in af["excluir_barrios"]:
                        if b not in (excl.get("barrios") or []):
                            excl.setdefault("barrios", []).append(b)
                    for p_ in af["excluir_palabras"]:
                        if p_ not in (excl.get("palabras") or []):
                            excl.setdefault("palabras", []).append(p_)
                    if af["limites"]:
                        excl.setdefault("limites", {}).update(af["limites"])
                    if af.get("tipo"):
                        excl["tipo"] = af["tipo"]
                    c["exclusiones"] = excl
                aprendidos += 1
                con_motivo_nuevo = True
                log(f"🧠 {c.get('nombre')}: aprendí del motivo «{obs[:50]}»")
            except Exception as e:  # noqa: BLE001 - un motivo no tumba el resto
                pr["afinado"] = False
                log(f"⚠️ No pude aprender de un motivo de {c.get('nombre')}: {e}")
        if con_motivo_nuevo:
            try:
                señales = (mod_clientes.aprendizajes_cliente(c)
                           + (c.get("comentarios_ia") or []))
                c["preferencias_evitar"] = extractor.aprender_preferencias(señales)
            except Exception:  # noqa: BLE001
                pass
    if hubo_cambios:
        mod_clientes.guardar_lista(lista)
    if aprendidos:
        log(f"🧠 Afinación: {aprendidos} motivo(s) convertidos en filtros.")
    return aprendidos


def atender_comentarios(log=print) -> int:
    """Afinaciones de texto libre escritas en Brokerap (el 🤖 de la app clásica).

    meta `comentarios_pendientes` = [{cliente, texto}]. Para cada uno: filtros
    duros (interpretar_afinacion + exclusiones), se guarda el comentario en la
    ficha y se recalculan las preferencias. Los fallidos quedan en la cola.
    """
    from . import config, extractor
    try:
        cola = json.loads(db.leer_meta("comentarios_pendientes") or "[]")
    except json.JSONDecodeError:
        cola = []
    if not cola:
        return 0
    if not config.ANTHROPIC_API_KEY:
        log("⚠️ Comentarios pendientes sin llave de IA; quedan en cola.")
        return 0
    lista = mod_clientes.cargar_guardados()
    quedan, hechos = [], 0
    for item in cola:
        nombre = (item.get("cliente") or "").strip()
        texto = (item.get("texto") or "").strip()
        c = next((x for x in lista
                  if x.get("nombre", "").lower() == nombre.lower()), None)
        if not c or not texto:
            continue   # cliente borrado o texto vacío: se descarta la entrada
        try:
            af = extractor.interpretar_afinacion(texto, c)
            if af.get("error"):
                quedan.append(item)
                continue
            excl = c.get("exclusiones") or {}
            for b in af["excluir_barrios"]:
                if b not in (excl.get("barrios") or []):
                    excl.setdefault("barrios", []).append(b)
            for p_ in af["excluir_palabras"]:
                if p_ not in (excl.get("palabras") or []):
                    excl.setdefault("palabras", []).append(p_)
            if af["limites"]:
                excl.setdefault("limites", {}).update(af["limites"])
            if af.get("tipo"):
                excl["tipo"] = af["tipo"]
            c["exclusiones"] = excl
            coms = c.get("comentarios_ia") or []
            coms.append(texto)
            c["comentarios_ia"] = coms
            señales = (mod_clientes.aprendizajes_cliente(c) + coms)
            c["preferencias_evitar"] = extractor.aprender_preferencias(señales)
            hechos += 1
            log(f"🤖 {nombre}: afinado con «{texto[:50]}»")
        except Exception as e:  # noqa: BLE001
            quedan.append(item)
            log(f"⚠️ No pude afinar a {nombre}: {e}")
    mod_clientes.guardar_lista(lista)
    db.guardar_meta("comentarios_pendientes", json.dumps(quedan, ensure_ascii=False))
    return hechos


def atender_manuales(log=print) -> int:
    """Inmuebles manuales metidos desde Brokerap (link + descripción).

    meta `manuales_pendientes` = [{descripcion, link}]. Si el link ya está en
    el catálogo se reutilizan sus datos y fotos (cero IA); si no, se lee la
    descripción con la IA. Entra a `inmuebles_manuales` como en la app.
    """
    import hashlib
    from datetime import datetime, timezone
    from . import config, extractor
    from .nucleo import norm_link
    try:
        cola = json.loads(db.leer_meta("manuales_pendientes") or "[]")
    except json.JSONDecodeError:
        cola = []
    if not cola:
        return 0
    try:
        manuales = json.loads(db.leer_meta("inmuebles_manuales") or "[]")
    except json.JSONDecodeError:
        manuales = []
    posts = db.posts_leidos()
    quedan, hechos = [], 0
    hoy_iso = datetime.now(timezone.utc).date().isoformat()
    for item in cola:
        desc = (item.get("descripcion") or "").strip()
        link = (item.get("link") or "").strip()
        nl = norm_link(link)
        if link and any(norm_link(x.get("link", "")) == nl for x in manuales):
            continue   # ya estaba: no duplicar
        scr = next((pp for pp in posts
                    if nl and pp.get("url") and norm_link(pp["url"]) == nl), None)
        try:
            if desc:
                if not config.ANTHROPIC_API_KEY:
                    quedan.append(item)
                    continue
                datos = extractor.interpretar_inmueble(desc)
                texto, fecha = desc, (scr or {}).get("fecha") or hoy_iso
            elif scr:
                campos = ("es_inmueble", "operacion", "tipo", "barrio", "zona",
                          "direccion", "area_m2", "precio", "administracion",
                          "habitaciones", "banos", "parqueaderos", "estrato",
                          "antiguedad_anos", "extras", "resumen")
                datos = {k: scr.get(k) for k in campos}
                datos["es_inmueble"] = True
                texto, fecha = scr.get("caption", ""), scr.get("fecha") or hoy_iso
            else:
                log(f"⚠️ Manual sin descripción y link fuera del catálogo: {link[:50]}")
                continue
            if datos.get("es_inmueble") is False:
                log(f"⚠️ «{desc[:40]}» no parece un inmueble; descartado.")
                continue
            manuales.insert(0, {
                "id": "m_" + hashlib.md5((texto + link).encode("utf-8")).hexdigest()[:16],
                "texto": texto, "link": link, "fecha": fecha, "agregado": hoy_iso,
                "cuenta": (scr or {}).get("cuenta") or "manual",
                "imagen": (scr or {}).get("imagen", ""),
                "media": (scr or {}).get("media") or [],
                "datos": datos,
            })
            hechos += 1
            log(f"🖊️ Inmueble manual leído: {datos.get('resumen') or desc[:50]}")
        except Exception as e:  # noqa: BLE001
            quedan.append(item)
            log(f"⚠️ No pude leer un inmueble manual: {e}")
    db.guardar_meta("inmuebles_manuales", json.dumps(manuales, ensure_ascii=False))
    db.guardar_meta("manuales_pendientes", json.dumps(quedan, ensure_ascii=False))
    return hechos


def atender_importaciones(log=print) -> int:
    """Importaciones de clientes (CSV de Zoho pegado en Brokerap).

    meta `importar_pendientes` = [{textos: [...]}]. Mismo camino de la app:
    interpretar_clientes → filtrar_borrados → fusionar_duplicados. El resumen
    queda en meta `importar_resultado` para mostrarlo en la web.
    """
    from datetime import datetime, timezone
    from . import config, extractor
    try:
        cola = json.loads(db.leer_meta("importar_pendientes") or "[]")
    except json.JSONDecodeError:
        cola = []
    if not cola:
        return 0
    if not config.ANTHROPIC_API_KEY:
        log("⚠️ Importación pendiente sin llave de IA; queda en cola.")
        return 0
    quedan, hechos = [], 0
    for item in cola:
        textos = [t for t in (item.get("textos") or []) if str(t).strip()]
        if not textos:
            continue
        try:
            nuevos = extractor.interpretar_clientes(textos, log=log)
            nuevos, omitidos = mod_clientes.filtrar_borrados(nuevos)
            existentes = mod_clientes.cargar_guardados()
            previos = {mod_clientes._norm_nombre(c.get("nombre", "")) for c in existentes}
            combinados = mod_clientes.fusionar_duplicados(existentes + nuevos)
            mod_clientes.guardar_lista(combinados)
            recien = [c.get("nombre", "") for c in combinados
                      if mod_clientes._norm_nombre(c.get("nombre", "")) not in previos]
            db.guardar_meta("importar_resultado", json.dumps({
                "cuando": datetime.now(timezone.utc).isoformat(),
                "leidos": len(nuevos), "nuevos": recien, "omitidos": omitidos,
                "total": len(combinados),
            }, ensure_ascii=False))
            hechos += len(nuevos)
            log(f"📥 Importados {len(nuevos)} cliente(s); nuevos: {len(recien)}; "
                f"🪦 omitidos: {len(omitidos)}.")
        except Exception as e:  # noqa: BLE001
            quedan.append(item)
            log(f"⚠️ La importación falló (queda en cola): {e}")
    db.guardar_meta("importar_pendientes", json.dumps(quedan, ensure_ascii=False))
    return hechos


def atender_pendientes(log=print) -> int:
    """Atiende TODO lo que Brokerap dejó en cola. Devuelve cuántas cosas hizo."""
    total = 0
    for fn in (atender_importaciones, atender_manuales,
               atender_comentarios, atender_afinaciones):
        try:
            total += fn(log=log)
        except Exception as e:  # noqa: BLE001 - una cola no tumba a las demás
            log(f"⚠️ {fn.__name__}: {e}")
    return total


def solicitudes_pendientes() -> dict[str, str]:
    """{clave: fecha_iso} de lo que Brokerap dejó pedido y sigue sin atender."""
    out = {}
    for clave in (CLAVE_ACTUALIZAR, CLAVE_AFINAR):
        v = db.leer_meta(clave) or ""
        if v.strip():
            out[clave] = v
    return out
