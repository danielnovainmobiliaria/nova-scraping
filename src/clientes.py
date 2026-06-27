"""Lectura de la lista de clientes desde Excel.

Convierte cada fila de la hoja de cálculo en un diccionario con los
requerimientos del cliente, listo para el motor de cruce.
"""
from __future__ import annotations

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

    clientes: list[dict[str, Any]] = []
    for _, fila in df.iterrows():
        nombre = str(fila.get("nombre", "")).strip()
        if not nombre or nombre.lower() == "nan":
            continue
        clientes.append(
            {
                "nombre": nombre,
                "operacion": str(fila.get("operacion", "")).strip().lower(),
                "barrios": _lista(fila.get("barrios")),
                "zona": str(fila.get("zona", "")).strip(),
                "area_min": _numero(fila.get("area_min")),
                "area_max": _numero(fila.get("area_max")),
                "presupuesto_max": _numero(fila.get("presupuesto_max")),
                "habitaciones_min": _numero(fila.get("habitaciones_min")),
                "banos_min": _numero(fila.get("banos_min")),
                "extras": [e.lower() for e in _lista(fila.get("extras"))],
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
    "estado": "activo", "visitas": 0, "inmuebles_enviados": [], "notas_crm": "",
    "valor_cierre": 0, "comision": 0,
    # ids de inmuebles ya enviados o descartados (para ocultarlos de las coincidencias).
    "ids_enviados": [], "ids_descartados": [],
}


def _con_crm(cliente: dict[str, Any]) -> dict[str, Any]:
    """Asegura que el cliente tenga los campos de seguimiento (CRM)."""
    for campo, defecto in CRM_CAMPOS.items():
        if campo not in cliente:
            cliente[campo] = [] if isinstance(defecto, list) else defecto
    return cliente


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


def _fusionar_dos(base: dict[str, Any], otro: dict[str, Any]) -> dict[str, Any]:
    """Fusiona 'otro' dentro de 'base' (base es el más completo y manda en empates)."""
    out = dict(base)
    for k, v in otro.items():
        actual = out.get(k)
        if isinstance(actual, list) or isinstance(v, list):
            la = actual if isinstance(actual, list) else ([] if actual in (None, "") else [actual])
            lb = v if isinstance(v, list) else ([] if v in (None, "") else [v])
            out[k] = list(dict.fromkeys([*la, *lb]))   # une sin duplicar, conserva orden
        elif k in ("notas", "notas_crm"):
            partes = [str(x).strip() for x in (actual, v) if x and str(x).strip()]
            out[k] = " | ".join(dict.fromkeys(partes))
        elif actual in (None, "", 0) and v not in (None, "", 0):
            out[k] = v                                  # base no tenía el dato → lo toma de otro
    return out


def fusionar_duplicados(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une clientes con el mismo nombre: toma el más completo y fusiona el resto.

    Une listas (barrios, extras, inmuebles_enviados) sin duplicar y concatena las notas.
    """
    grupos: dict[str, list[dict[str, Any]]] = {}
    orden: list[str] = []
    for c in lista:
        key = c.get("nombre", "").strip().lower()
        if key not in grupos:
            grupos[key] = []
            orden.append(key)
        grupos[key].append(c)

    resultado: list[dict[str, Any]] = []
    for key in orden:
        grupo = grupos[key]
        if len(grupo) == 1:
            resultado.append(grupo[0])
            continue
        grupo = sorted(grupo, key=_completitud, reverse=True)  # el más completo primero
        fusionado = dict(grupo[0])
        for otro in grupo[1:]:
            fusionado = _fusionar_dos(fusionado, otro)
        resultado.append(fusionado)
    return resultado


def agregar_o_actualizar(cliente: dict[str, Any]) -> None:
    """Agrega un cliente nuevo o reemplaza uno existente con el mismo nombre."""
    lista = cargar_guardados()
    lista = [c for c in lista if c.get("nombre", "").lower() != cliente["nombre"].lower()]
    lista.append(cliente)
    guardar_lista(lista)


def eliminar(nombre: str) -> None:
    lista = [c for c in cargar_guardados()
             if c.get("nombre", "").lower() != nombre.lower()]
    guardar_lista(lista)
