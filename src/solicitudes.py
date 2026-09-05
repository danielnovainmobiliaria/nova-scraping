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


def solicitudes_pendientes() -> dict[str, str]:
    """{clave: fecha_iso} de lo que Brokerap dejó pedido y sigue sin atender."""
    out = {}
    for clave in (CLAVE_ACTUALIZAR, CLAVE_AFINAR):
        v = db.leer_meta(clave) or ""
        if v.strip():
            out[clave] = v
    return out
