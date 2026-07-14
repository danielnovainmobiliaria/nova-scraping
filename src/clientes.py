"""Lectura de la lista de clientes desde Excel.

Convierte cada fila de la hoja de cálculo en un diccionario con los
requerimientos del cliente, listo para el motor de cruce.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from . import db

# Los clientes se guardan en la base de datos (db.py), que puede ser local
# (SQLite) o en la nube (Postgres), para que no se pierdan al reiniciar.

# Columnas esperadas en el Excel (ver plantilla_clientes.xlsx).
COLUMNAS = [
    "nombre", "operacion", "barrios", "zona", "area_min", "area_max",
    "presupuesto_max", "habitaciones_min", "banos_min", "extras",
    "perimetro", "notas",
]


def _lista(valor: Any) -> list[str]:
    """Convierte 'estudio, terraza' → ['estudio', 'terraza']."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return []
    return [x.strip() for x in str(valor).replace(";", ",").split(",") if x.strip()]


def _numero(valor: Any) -> float | None:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        # Acepta "1.500.000", "1500000" o "120 m2".
        limpio = "".join(c for c in str(valor) if c.isdigit())
        return float(limpio) if limpio else None
    except ValueError:
        return None


def cargar_clientes(ruta: str | Path) -> list[dict[str, Any]]:
    """Lee el Excel de clientes y devuelve la lista de requerimientos."""
    df = pd.read_excel(ruta)
    df.columns = [str(c).strip().lower() for c in df.columns]

    def _vacio(v: Any) -> bool:
        return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "nan")

    clientes: list[dict[str, Any]] = []
    for _, fila in df.iterrows():
        nombre = str(fila.get("nombre", "")).strip()
        if not nombre or nombre.lower() == "nan":
            continue
        # El backup de la app exporta la columna 'presupuesto' (texto formateado);
        # acéptala como respaldo de 'presupuesto_max' para que restaurar no lo pierda.
        presupuesto = fila.get("presupuesto_max")
        if _vacio(presupuesto):
            presupuesto = fila.get("presupuesto")
        clientes.append(
            {
                "nombre": nombre,
                "telefono": "".join(ch for ch in str(fila.get("telefono", "")) if ch.isdigit()),
                # 'compra' → 'venta', 'alquiler' → 'arriendo' (como venga de Zoho).
                "operacion": {"compra": "venta", "comprar": "venta", "alquiler": "arriendo",
                              "alquilar": "arriendo", "renta": "arriendo",
                              "arrendar": "arriendo"}.get(
                    str(fila.get("operacion", "")).strip().lower(),
                    str(fila.get("operacion", "")).strip().lower()),
                "prioridad": (str(fila.get("prioridad", "")).strip().lower()
                              if str(fila.get("prioridad", "")).strip().lower()
                              in ("alta", "media", "baja") else "media"),
                "flexibilidad": (str(fila.get("flexibilidad", "")).strip().lower()
                                 if str(fila.get("flexibilidad", "")).strip().lower()
                                 in ("estricto", "medio", "flexible") else "medio"),
                "barrios": _lista(fila.get("barrios")),
                "zona": str(fila.get("zona", "")).strip(),
                "area_min": _numero(fila.get("area_min")),
                "area_max": _numero(fila.get("area_max")),
                "presupuesto_max": _numero(presupuesto),
                "habitaciones_min": _numero(fila.get("habitaciones_min")),
                "habitaciones_max": _numero(fila.get("habitaciones_max")),
                "banos_min": _numero(fila.get("banos_min")),
                "extras": [e.lower() for e in _lista(fila.get("extras"))],
                "obligatorios": [o.lower().strip() for o in _lista(fila.get("obligatorios"))],
                "perimetro": str(fila.get("perimetro", "")).strip(),
                "notas": str(fila.get("notas", "")).strip(),
            }
        )
    return clientes


# ── Almacén de clientes del formulario (data/clientes.json) ───

# Campos del CRM (seguimiento) con sus valores por defecto.
# valor_cierre = canon mensual (arriendo) o precio de venta final (venta).
# comision = comisión final (ajustable, porque todo es negociable).
CRM_CAMPOS = {
    "estado": "activo", "prioridad": "media", "visitas": 0,
    "inmuebles_enviados": [], "notas_crm": "",
    "valor_cierre": 0, "comision": 0,
    # ids de inmuebles ya enviados o descartados (compatibilidad / set para ocultar).
    "ids_enviados": [], "ids_descartados": [],
    # Embudo de seguimiento: cada inmueble que se mueve entra aquí con su estado y notas.
    "procesos": [],
    # Lo que la IA aprendió que el cliente EVITA (de los descartados):
    # {"palabras": [...], "extras": [...]}
    "preferencias_evitar": {},
    # Comentarios libres del broker para afinar la búsqueda de este cliente.
    "comentarios_ia": [],
    # Filtros DUROS deducidos de los comentarios: anulan inmuebles que no cumplen.
    # {"barrios": [...], "palabras": [...]}
    "exclusiones": {},
    # Inmuebles que el broker asignó a dedo (por link) a este cliente.
    "asignados": [],
    # Búsqueda manual: cuándo visitó el broker cada fuente PARA este cliente.
    # {"@perfil": "2026-07-10", "https://portal...": "2026-07-08"}
    "visitas_fuentes": {},
}

# Estados del embudo de seguimiento de cada inmueble enviado a un cliente.
ESTADOS_PROCESO = ["enviado", "agendado", "visitado", "descartado", "cerrado"]


def _con_crm(cliente: dict[str, Any]) -> dict[str, Any]:
    """Asegura que el cliente tenga los campos de seguimiento (CRM)."""
    for campo, defecto in CRM_CAMPOS.items():
        if campo not in cliente:
            if isinstance(defecto, list):
                cliente[campo] = []
            elif isinstance(defecto, dict):
                cliente[campo] = {}
            else:
                cliente[campo] = defecto
    # Fecha de inclusión (para el semáforo de antigüedad del cliente).
    if not cliente.get("creado"):
        cliente["creado"] = date.today().isoformat()
    return cliente


def set_preferencias_evitar(nombre: str, prefs: dict[str, Any]) -> None:
    """Guarda lo que la IA aprendió que el cliente evita."""
    actualizar_crm(nombre, {"preferencias_evitar": prefs})


def cargar_guardados() -> list[dict[str, Any]]:
    """Lee los clientes guardados (base de datos), garantizando los campos del CRM."""
    return [_con_crm(c) for c in db.leer_clientes()]


def fusionar_crm(nuevos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conserva los datos de CRM (estado, visitas, enviados) al reescribir clientes.

    Cuando se guardan los REQUERIMIENTOS de un cliente, no queremos borrar su
    seguimiento. Esta función copia los campos CRM del cliente previo (por nombre).
    """
    previos = {c.get("nombre", "").lower(): c for c in cargar_guardados()}
    for c in nuevos:
        anterior = previos.get(c.get("nombre", "").lower())
        for campo, defecto in CRM_CAMPOS.items():
            if anterior and campo in anterior:
                c[campo] = anterior[campo]
            elif campo not in c:
                c[campo] = [] if isinstance(defecto, list) else defecto
    return nuevos


def actualizar_crm(nombre: str, cambios: dict[str, Any]) -> None:
    """Actualiza los campos de seguimiento (estado, visitas, etc.) de un cliente."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            c.update(cambios)
    guardar_lista(lista)


def marcar_inmueble_enviado(nombre: str, inmueble: str, post_id: str = "") -> None:
    """Marca un inmueble como ENVIADO a un cliente (lo oculta de sus coincidencias)."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            enviados = c.get("inmuebles_enviados") or []
            if inmueble not in enviados:
                enviados.append(inmueble)
            c["inmuebles_enviados"] = enviados
            ids = c.get("ids_enviados") or []
            if post_id and post_id not in ids:
                ids.append(post_id)
            c["ids_enviados"] = ids
    guardar_lista(lista)


def descartar_inmueble(nombre: str, post_id: str) -> None:
    """Marca un inmueble como DESCARTADO para un cliente (lo oculta sin enviarlo)."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            ids = c.get("ids_descartados") or []
            if post_id and post_id not in ids:
                ids.append(post_id)
            c["ids_descartados"] = ids
    guardar_lista(lista)


# ── Embudo de seguimiento (procesos) ─────────────────────────

def ids_en_proceso(cliente: dict[str, Any]) -> set[str]:
    """Todos los ids de inmuebles que ya están 'movidos' para un cliente (se ocultan)."""
    ids = {pr.get("post_id") for pr in (cliente.get("procesos") or [])}
    ids |= set(cliente.get("ids_enviados") or [])
    ids |= set(cliente.get("ids_descartados") or [])
    return {i for i in ids if i}


def agregar_proceso(nombre: str, proceso: dict[str, Any]) -> None:
    """Mete un inmueble al embudo de seguimiento de un cliente (si no estaba)."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            procs = c.get("procesos") or []
            if not any(pr.get("post_id") == proceso.get("post_id") for pr in procs):
                procs.append(proceso)
            c["procesos"] = procs
    guardar_lista(lista)


def actualizar_proceso(nombre: str, post_id: str, cambios: dict[str, Any]) -> None:
    """Cambia el estado u observaciones de un inmueble en seguimiento."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            for pr in (c.get("procesos") or []):
                if pr.get("post_id") == post_id:
                    pr.update(cambios)
    guardar_lista(lista)


def quitar_proceso(nombre: str, post_id: str) -> None:
    """Saca un inmueble del seguimiento (vuelve a aparecer en coincidencias)."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            c["procesos"] = [pr for pr in (c.get("procesos") or [])
                             if pr.get("post_id") != post_id]
            c["ids_enviados"] = [i for i in (c.get("ids_enviados") or []) if i != post_id]
            c["ids_descartados"] = [i for i in (c.get("ids_descartados") or []) if i != post_id]
    guardar_lista(lista)


# Motivos de descarte que hablan del AVISO y no de los gustos del cliente
# (repetido, ya enviado, ya vendido…). No deben volverse filtros ni aprendizaje.
_RE_MOTIVO_ADMIN = re.compile(
    r"repetid|duplicad|dos veces|mismo (que|de|inmueble|apartamento|apto)|"
    r"otra publicacion|otro (broker|perfil|aviso|post)|otra cuenta|"
    r"ya (lo|la|se lo|se la) (envie|envio|mande|mando|vio|vi)|ya envi|ya mand|"
    r"ya (esta|fue) (vendid|arrendad)|ya se (vendio|arrendo)|no disponible")


def es_motivo_administrativo(texto: str) -> bool:
    """True si el motivo de descarte es logístico (repetido/enviado/vendido),
    no una preferencia del cliente. Esos motivos solo ocultan, no filtran."""
    t = str(texto or "").lower()
    for a, b in {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}.items():
        t = t.replace(a, b)
    return bool(_RE_MOTIVO_ADMIN.search(t))


def aprendizajes_cliente(cliente: dict[str, Any]) -> list[str]:
    """Observaciones de los inmuebles DESCARTADOS (lo que NO le gustó al cliente)."""
    notas = []
    for pr in (cliente.get("procesos") or []):
        if pr.get("estado") == "descartado" and pr.get("observaciones"):
            if es_motivo_administrativo(pr["observaciones"]):
                continue
            notas.append(pr["observaciones"].strip())
    return notas


def marcar_visita_fuente(nombre: str, fuente: str, fecha: str) -> None:
    """Apunta que el broker visitó una fuente buscando para un cliente."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            v = c.get("visitas_fuentes") or {}
            v[fuente] = fecha
            c["visitas_fuentes"] = v
    guardar_lista(lista)


def asignar_inmueble(nombre: str, entrada: dict[str, Any]) -> None:
    """Asigna a dedo un inmueble (por link) a un cliente. No repite links."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            asig = c.get("asignados") or []
            if not any((a.get("link") or "").strip() == (entrada.get("link") or "").strip()
                       for a in asig):
                asig.append(entrada)
            c["asignados"] = asig
    guardar_lista(lista)


def agregar_comentario_ia(nombre: str, comentario: str) -> None:
    """Guarda un comentario libre del broker para afinar la búsqueda de un cliente."""
    comentario = (comentario or "").strip()
    if not comentario:
        return
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            coms = c.get("comentarios_ia") or []
            coms.append(comentario)
            c["comentarios_ia"] = coms
    guardar_lista(lista)


def agregar_exclusiones(nombre: str, barrios=None, palabras=None, limites=None, tipo=None) -> None:
    """Suma filtros DUROS (barrios, palabras, topes numéricos y tipo) a un cliente, sin duplicar."""
    barrios = barrios or []
    palabras = palabras or []
    limites = limites or {}
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            exc = c.get("exclusiones") or {}
            exc["barrios"] = list(dict.fromkeys((exc.get("barrios") or []) + barrios))
            exc["palabras"] = list(dict.fromkeys((exc.get("palabras") or []) + palabras))
            for k, v in limites.items():            # topes: gana el último valor indicado
                if v is not None:
                    exc[k] = v
            if tipo:
                exc["tipo"] = tipo
            c["exclusiones"] = exc
    guardar_lista(lista)


def limpiar_exclusiones(nombre: str) -> None:
    """Quita TODOS los filtros duros de un cliente (vuelven a aparecer esos inmuebles)."""
    lista = cargar_guardados()
    for c in lista:
        if c.get("nombre", "").lower() == nombre.lower():
            c["exclusiones"] = {}
    guardar_lista(lista)


def guardar_lista(clientes: list[dict[str, Any]]) -> None:
    db.guardar_clientes(clientes)


def _completitud(c: dict[str, Any]) -> int:
    """Cuenta cuántos campos del cliente tienen información (para elegir el más completo)."""
    n = 0
    for v in c.values():
        if isinstance(v, list):
            n += len(v)
        elif v not in (None, "", 0):
            n += 1
    return n


def _unir_listas(la: list, lb: list) -> list:
    """Une dos listas sin duplicar, soportando listas de fichas (dicts).

    'procesos' es una lista de dicts (no hashables): se une por post_id.
    Las listas de textos/números se unen conservando el orden.
    """
    if any(isinstance(x, dict) for x in [*la, *lb]):
        vistos: dict[str, Any] = {}
        for x in [*la, *lb]:
            clave = (x.get("post_id") or json.dumps(x, sort_keys=True, ensure_ascii=False)
                     if isinstance(x, dict) else str(x))
            vistos.setdefault(clave, x)
        return list(vistos.values())
    return list(dict.fromkeys([*la, *lb]))


def _fusionar_dos(base: dict[str, Any], otro: dict[str, Any]) -> dict[str, Any]:
    """Fusiona 'otro' dentro de 'base' (base es el más completo y manda en empates)."""
    out = dict(base)
    for k, v in otro.items():
        actual = out.get(k)
        if isinstance(actual, list) or isinstance(v, list):
            la = actual if isinstance(actual, list) else ([] if actual in (None, "") else [actual])
            lb = v if isinstance(v, list) else ([] if v in (None, "") else [v])
            out[k] = _unir_listas(la, lb)              # une sin duplicar (soporta fichas CRM)
        elif k in ("notas", "notas_crm"):
            partes = [str(x).strip() for x in (actual, v) if x and str(x).strip()]
            out[k] = " | ".join(dict.fromkeys(partes))
        elif isinstance(actual, dict) or isinstance(v, dict):
            da = actual if isinstance(actual, dict) else {}
            dv = v if isinstance(v, dict) else {}
            out[k] = {**dv, **da}                       # base manda en empates
        elif actual in (None, "", 0) and v not in (None, "", 0):
            out[k] = v                                  # base no tenía el dato → lo toma de otro
    return out


def _norm_nombre(nombre: str) -> str:
    """Normaliza un nombre para comparar (minúsculas, sin tildes NI signos).

    'Alberto, Nessim' y 'Alberto Nessim' son la misma persona.
    """
    t = str(nombre or "").lower().strip()
    for a, b in {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}.items():
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(t.split())


def _norm_tel(tel: str) -> str:
    """Deja solo dígitos del teléfono (últimos 10) para comparar."""
    d = "".join(ch for ch in str(tel or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def fusionar_duplicados(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une clientes que son el mismo (mismo nombre SIN tildes/mayúsculas, o mismo teléfono).

    Toma el más completo como base, une listas (barrios, extras, enviados) sin duplicar
    y concatena las notas. Así, venga de Zoho o de un alta manual, no se duplican.
    """
    out: list[dict[str, Any]] = []
    for c in lista:
        nk, tk = _norm_nombre(c.get("nombre", "")), _norm_tel(c.get("telefono", ""))
        encontrado = None
        for g in out:
            if (nk and nk == _norm_nombre(g.get("nombre", ""))) or \
               (tk and tk == _norm_tel(g.get("telefono", ""))):
                encontrado = g
                break
        if encontrado is None:
            out.append(dict(c))
        else:
            base, otro = ((encontrado, c) if _completitud(encontrado) >= _completitud(c)
                          else (dict(c), encontrado))
            out[out.index(encontrado)] = _fusionar_dos(base, otro)
    return out


def agregar_o_actualizar(cliente: dict[str, Any]) -> None:
    """Agrega un cliente nuevo o reemplaza uno existente con el mismo nombre."""
    lista = cargar_guardados()
    lista = [c for c in lista if c.get("nombre", "").lower() != cliente["nombre"].lower()]
    lista.append(cliente)
    guardar_lista(lista)


def eliminar(nombre: str) -> None:
    lista = [c for c in cargar_guardados()
             if c.get("nombre", "").lower() != nombre.lower()]
    borrados = [c for c in cargar_guardados()
                if c.get("nombre", "").lower() == nombre.lower()]
    guardar_lista(lista)
    # Lápida: recuerda que se borró a propósito, para que una re-importación
    # (CSV de Zoho / Excel de respaldo) no lo reviva sin querer.
    _guardar_borrados(nombres_borrados() | {_norm_nombre(nombre)})
    # Papelera: copia COMPLETA del cliente (requerimientos + CRM + procesos),
    # por si el broker se arrepiente o el cliente vuelve a escribir.
    if borrados:
        try:
            from . import db
            pap = json.loads(db.leer_meta("clientes_papelera") or "[]")
            pap = [x for x in pap
                   if _norm_nombre(x.get("nombre", "")) != _norm_nombre(nombre)]
            pap.insert(0, {**borrados[0], "_borrado_el": date.today().isoformat()})
            db.guardar_meta("clientes_papelera", json.dumps(pap[:20], ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass


def nombres_borrados() -> set[str]:
    """Nombres (normalizados) de clientes que el broker borró a propósito."""
    try:
        from . import db
        return set(json.loads(db.leer_meta("clientes_borrados") or "[]"))
    except Exception:  # noqa: BLE001
        return set()


def _guardar_borrados(nombres: set[str]) -> None:
    try:
        from . import db
        db.guardar_meta("clientes_borrados", json.dumps(sorted(nombres),
                                                        ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def revivir(nombre: str) -> None:
    """Quita la lápida (cuando el broker RE-CREA al cliente a propósito)."""
    b = nombres_borrados()
    n = _norm_nombre(nombre)
    if n in b:
        b.discard(n)
        _guardar_borrados(b)


def papelera() -> list[dict[str, Any]]:
    """Clientes borrados recientemente (máx. 20), con todo su historial."""
    try:
        from . import db
        return json.loads(db.leer_meta("clientes_papelera") or "[]")
    except Exception:  # noqa: BLE001
        return []


def restaurar_de_papelera(nombre: str) -> bool:
    """Revive un cliente desde la papelera con TODO su historial."""
    from . import db
    pap = papelera()
    objetivo = next((x for x in pap
                     if _norm_nombre(x.get("nombre", "")) == _norm_nombre(nombre)), None)
    if objetivo is None:
        return False
    objetivo = {k: v for k, v in objetivo.items() if k != "_borrado_el"}
    lista = cargar_guardados()
    if not any(_norm_nombre(c.get("nombre", "")) == _norm_nombre(nombre) for c in lista):
        lista.append(_con_crm(objetivo))
        guardar_lista(lista)
    revivir(nombre)
    db.guardar_meta("clientes_papelera", json.dumps(
        [x for x in pap if _norm_nombre(x.get("nombre", "")) != _norm_nombre(nombre)],
        ensure_ascii=False))
    return True


def filtrar_borrados(nuevos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Separa de una importación los clientes que el broker borró antes.

    Devuelve (los_que_pasan, nombres_omitidos). Si el cliente está VIVO en la
    lista actual, pasa siempre (es una actualización, no una resurrección)."""
    vivos = {_norm_nombre(c.get("nombre", "")) for c in cargar_guardados()}
    b = nombres_borrados()
    dentro: list[dict[str, Any]] = []
    fuera: list[str] = []
    for c in nuevos:
        n = _norm_nombre(c.get("nombre", ""))
        if n in b and n not in vivos:
            fuera.append(c.get("nombre", ""))
        else:
            dentro.append(c)
    return dentro, fuera
