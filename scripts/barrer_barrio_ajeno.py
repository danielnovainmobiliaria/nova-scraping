"""Saca del radar las coincidencias cuyo barrio no es de los que pidió el cliente.

Daniel (2026-09-15): "de una vez haz un barrido con la búsqueda actual, si no
responde el barrio, borra esos inmuebles".

El motor ya quedó estricto (`_match_ubicacion` devuelve negativo cuando el aviso
dice estar en un barrio conocido que no es de los pedidos), pero eso solo aplica
a los cruces NUEVOS. Las filas que ya están en la tabla `radar` se calcularon
con la regla vieja, y ahí siguen hasta la próxima corrida completa del robot.
Esto las revisa una por una con la regla nueva.

Usa el MISMO `_match_ubicacion` que el motor, no una regla aparte: si mañana la
regla cambia, este barrido cambia con ella y no hay dos criterios que se
contradigan.

    python3 scripts/barrer_barrio_ajeno.py           # solo informa
    python3 scripts/barrer_barrio_ajeno.py --borrar  # borra de verdad
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, ".")

from sqlalchemy import text  # noqa: E402

from src import db  # noqa: E402
from src.matcher import _match_ubicacion  # noqa: E402


def fichas_por_nombre() -> dict[str, dict[str, Any]]:
    return {c.get("nombre", ""): c for c in db.leer_clientes()}


def filas_del_radar() -> list[dict[str, Any]]:
    """Cada cruce con lo que el motor ve del aviso para juzgar la ubicación.

    El barrio se toma de `posts` y no de `radar`: la tabla del radar solo guarda
    el barrio, y para decidir bien hacen falta también la zona y la dirección
    (hay avisos que ponen el barrio de verdad en el campo zona).
    """
    with db._conn() as con:
        res = con.execute(text("""
            SELECT r.cliente, r.post_id, r.score, r.barrio AS barrio_radar,
                   p.datos_json
              FROM radar r
              LEFT JOIN posts p ON p.id = r.post_id
        """))
        filas = []
        for cliente, post_id, score, barrio_radar, datos in res:
            d = {}
            if datos:
                try:
                    d = json.loads(datos)
                except Exception:  # noqa: BLE001 - un json roto no detiene el barrido
                    d = {}
            filas.append({
                "cliente": cliente, "post_id": post_id, "score": score,
                "post": {
                    "barrio": d.get("barrio") or barrio_radar or "",
                    "zona": d.get("zona") or "",
                    "direccion": d.get("direccion") or "",
                },
            })
        return filas


def main() -> int:
    borrar = "--borrar" in sys.argv
    fichas = fichas_por_nombre()
    filas = filas_del_radar()

    sobran: list[tuple[str, str]] = []
    porque: dict[str, list[str]] = defaultdict(list)
    sin_ficha = 0

    for f in filas:
        cliente = fichas.get(f["cliente"])
        if not cliente:
            sin_ficha += 1
            continue
        p, razon = _match_ubicacion(cliente, f["post"])
        if p < 0:
            sobran.append((f["cliente"], f["post_id"]))
            porque[f["cliente"]].append(f"{f['post']['barrio']} (score {f['score']})")

    print(f"Cruces en el radar: {len(filas)}")
    if sin_ficha:
        print(f"  ({sin_ficha} sin ficha de cliente: no se tocan)")
    print(f"Con barrio ajeno:   {len(sobran)}")
    print()
    for nombre in sorted(porque, key=lambda n: -len(porque[n])):
        pedidos = ", ".join(fichas[nombre].get("barrios") or []) or "(sin barrios)"
        print(f"  {nombre}: {len(porque[nombre])} de sobra")
        print(f"      pidió: {pedidos}")
        cuenta: dict[str, int] = defaultdict(int)
        for b in porque[nombre]:
            cuenta[b.split(" (score")[0]] += 1
        print("      le salían: " + ", ".join(
            f"{b}×{n}" for b, n in sorted(cuenta.items(), key=lambda kv: -kv[1])))

    if not borrar:
        print("\n(informe solamente — para borrar: --borrar)")
        return 0

    if not sobran:
        print("\nNada que borrar.")
        return 0

    with db._conn() as con:
        for cliente, post_id in sobran:
            con.execute(
                text("DELETE FROM radar WHERE cliente = :c AND post_id = :p"),
                {"c": cliente, "p": post_id},
            )
    print(f"\nBorrados {len(sobran)} cruces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
