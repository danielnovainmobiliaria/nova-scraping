"""Crea la plantilla de Excel para tu lista de clientes.

Ejecuta:  python3 generar_plantilla.py
Genera el archivo  data/plantilla_clientes.xlsx  con las columnas correctas
y dos filas de ejemplo. Llénalo, guárdalo como  data/clientes.xlsx  y úsalo
en la app.
"""
from __future__ import annotations

import pandas as pd

from src import config

# Filas de ejemplo (puedes borrarlas y poner tus clientes reales).
EJEMPLOS = [
    {
        "nombre": "Ana Ejemplo", "telefono": "3001234567", "operacion": "venta",
        "flexibilidad": "medio", "barrios": "Chicó, El Nogal", "zona": "Chapinero",
        "area_min": 90, "area_max": 130, "presupuesto_max": 1800000000,
        "habitaciones_min": 2, "banos_min": 2, "extras": "estudio, parqueadero",
        "obligatorios": "barrio, habitaciones", "perimetro": "", "notas": "No cede en zona.",
    },
    {
        "nombre": "Carlos Ejemplo", "telefono": "3019876543", "operacion": "arriendo",
        "flexibilidad": "flexible", "barrios": "Cedritos, Santa Bárbara", "zona": "Usaquén",
        "area_min": 60, "area_max": 90, "presupuesto_max": 4000000,
        "habitaciones_min": 2, "banos_min": 1, "extras": "balcon",
        "obligatorios": "", "perimetro": "", "notas": "Abierto a opciones cercanas.",
    },
]


def main() -> None:
    df = pd.DataFrame(EJEMPLOS)
    ruta = config.DATA_DIR / "plantilla_clientes.xlsx"
    df.to_excel(ruta, index=False)
    print(f"✅ Plantilla creada en: {ruta}")
    print("   Llénala con tus clientes y guárdala como data/clientes.xlsx")


if __name__ == "__main__":
    main()
