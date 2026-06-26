"""Crea la plantilla de Excel para tu lista de clientes.

Ejecuta:  python3 generar_plantilla.py
Genera el archivo  data/plantilla_clientes.xlsx  con las columnas correctas
y dos filas de ejemplo. Llénalo, guárdalo como  data/clientes.xlsx  y úsalo
en la app.
"""
from __future__ import annotations

import pandas as pd

from src import config
from src.sample_data import CLIENTES_DEMO


def main() -> None:
    filas = []
    for c in CLIENTES_DEMO:
        filas.append(
            {
                "nombre": c["nombre"],
                "operacion": c["operacion"],
                "barrios": ", ".join(c["barrios"]),
                "zona": c["zona"],
                "area_min": c["area_min"],
                "area_max": c["area_max"],
                "presupuesto_max": c["presupuesto_max"],
                "habitaciones_min": c["habitaciones_min"],
                "banos_min": c["banos_min"],
                "extras": ", ".join(c["extras"]),
                "perimetro": c["perimetro"],
                "notas": c["notas"],
            }
        )
    df = pd.DataFrame(filas)
    ruta = config.DATA_DIR / "plantilla_clientes.xlsx"
    df.to_excel(ruta, index=False)
    print(f"✅ Plantilla creada en: {ruta}")
    print("   Llénala con tus clientes y guárdala como data/clientes.xlsx")


if __name__ == "__main__":
    main()
