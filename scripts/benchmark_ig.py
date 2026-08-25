"""Banco de pruebas: ¿hay un scraper de Instagram más barato e igual de bueno?

Corre en GitHub Actions (ahí viven las llaves). Compara el actor actual contra
alternativas del marketplace de Apify: precio por resultado, cobertura de campos
que la herramienta necesita, y cantidad de posts devueltos en 5 cuentas reales.
"""
from __future__ import annotations

import json
import os
import urllib.request

from apify_client import ApifyClient

ACTUAL = "apify/instagram-scraper"
CUENTAS_PRUEBA = ["belainmobiliaria", "piqbrokers", "areainmobiliaria2022",
                  "hook_realestate", "realestate.angelabril"]
CAMPOS_NECESARIOS = ["caption", "timestamp", "url", "ownerUsername"]


def precios_del_marketplace() -> list[dict]:
    req = urllib.request.Request(
        "https://api.apify.com/v2/store?search=instagram%20scraper&limit=20&sortBy=popularity")
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["items"]
    actores = []
    for a in data:
        p = a.get("currentPricingInfo") or {}
        actores.append({
            "actor": f"{a.get('username')}/{a.get('name')}",
            "modelo": p.get("pricingModel"),
            "precio_por_1000": (float(p.get("pricePerUnitUsd") or 0) * 1000
                                if p.get("pricingModel") == "PAY_PER_RESULT" else None),
            "mensual": p.get("trialMinutes") and None or (p.get("pricePerUnitUsd")
                       if p.get("pricingModel") == "FLAT_PRICE_PER_MONTH" else None),
            "usuarios": (a.get("stats") or {}).get("totalUsers"),
        })
    return actores


def correr(cliente: ApifyClient, actor: str, run_input: dict) -> tuple[int, dict, float]:
    run = cliente.actor(actor).call(run_input=run_input, timeout_secs=600)
    if not run or not run.default_dataset_id:
        return 0, {}, 0.0
    items = list(cliente.dataset(run.default_dataset_id).iterate_items())
    utiles = [i for i in items if not i.get("error")]
    cobertura = {c: sum(1 for i in utiles if i.get(c)) for c in CAMPOS_NECESARIOS}
    usd = 0.0
    try:
        rd = cliente.run(run.id).get()
        usd = float((rd.model_dump() if hasattr(rd, "model_dump") else dict(rd)
                     ).get("usage_total_usd") or 0)
    except Exception:
        pass
    return len(utiles), cobertura, usd


def main() -> None:
    print("== PRECIOS DEL MARKETPLACE (instagram scrapers, por popularidad) ==")
    candidatos = []
    for a in precios_del_marketplace():
        linea = (f"  {a['actor']:45} {str(a['modelo']):22} "
                 f"{'$%.2f/1000' % a['precio_por_1000'] if a['precio_por_1000'] else '':12} "
                 f"usuarios: {a['usuarios']}")
        print(linea)
        if (a["modelo"] == "PAY_PER_RESULT" and a["precio_por_1000"]
                and a["actor"] != ACTUAL and (a["usuarios"] or 0) > 500):
            candidatos.append(a)
    candidatos.sort(key=lambda x: x["precio_por_1000"])
    finalistas = [c["actor"] for c in candidatos[:2]]
    print(f"\nFinalistas más baratos: {finalistas}")

    cliente = ApifyClient(os.environ["APIFY_TOKEN"])
    run_input = {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in CUENTAS_PRUEBA],
        "resultsType": "posts", "resultsLimit": 8,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }
    print(f"\n== CORRIDAS DE PRUEBA (5 cuentas, ≤8 posts c/u) ==")
    for actor in [ACTUAL] + finalistas:
        try:
            n, cob, usd = correr(cliente, actor, run_input)
            print(f"  {actor:45} posts: {n:3}  ${usd:.3f}  cobertura: {cob}")
        except Exception as e:
            print(f"  {actor:45} ❌ incompatible/fallo: {str(e)[:90]}")
    print("\nVEREDICTO: comparar $/posts reales y cobertura de campos; si un "
          "finalista iguala cobertura con menor costo real, vale migrar (requiere "
          "adaptar _normalizar a su formato).")


if __name__ == "__main__":
    main()
