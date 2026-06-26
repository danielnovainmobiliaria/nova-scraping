"""Datos de demostración (modo demo).

Permiten probar TODO el flujo de cruce sin pagar Apify ni Claude todavía.
Son captions inventados pero realistas y clientes de ejemplo.
"""
from __future__ import annotations

from typing import Any

# Publicaciones de ejemplo, ya "leídas" (como las dejaría Claude).
POSTS_DEMO: list[dict[str, Any]] = [
    {
        "id": "demo1", "cuenta": "arriendos_chapinero",
        "url": "https://instagram.com/p/demo1", "imagen": "",
        "fecha": "2026-06-20",
        "caption": "🏡 Hermoso apto en El Nogal, 3 alcobas, 2 baños, 95 mts2, "
                   "estudio independiente y balcón. Arriendo $5.500.000 + admin. ¡Escríbenos!",
        "es_inmueble": True, "operacion": "arriendo", "tipo": "apartamento",
        "barrio": "El Nogal", "zona": "Chapinero", "direccion": None,
        "area_m2": 95, "precio": 5500000, "administracion": 800000,
        "habitaciones": 3, "banos": 2, "parqueaderos": 1, "estrato": 6,
        "extras": ["estudio", "balcon"], "resumen": "Apto 3 alcobas en El Nogal",
    },
    {
        "id": "demo2", "cuenta": "inmobiliaria_norte",
        "url": "https://instagram.com/p/demo2", "imagen": "",
        "fecha": "2026-06-18",
        "caption": "VENTA Cedritos 🌳 Apartamento remodelado 70m2, 2 habitaciones, "
                   "2 baños, parqueadero y depósito. $420.000.000. Estrato 4.",
        "es_inmueble": True, "operacion": "venta", "tipo": "apartamento",
        "barrio": "Cedritos", "zona": "Usaquen", "direccion": None,
        "area_m2": 70, "precio": 420000000, "administracion": None,
        "habitaciones": 2, "banos": 2, "parqueaderos": 1, "estrato": 4,
        "extras": ["remodelado", "parqueadero", "deposito"],
        "resumen": "Apto 2 habs en Cedritos en venta",
    },
    {
        "id": "demo3", "cuenta": "arriendos_chapinero",
        "url": "https://instagram.com/p/demo3", "imagen": "",
        "fecha": "2026-06-22",
        "caption": "Apartaestudio en Chapinero Alto, 1 alcoba, 1 baño, 38 m2, "
                   "amoblado, vista a los cerros. Arriendo $2.300.000 incluido admin.",
        "es_inmueble": True, "operacion": "arriendo", "tipo": "apartaestudio",
        "barrio": "Chapinero Alto", "zona": "Chapinero", "direccion": None,
        "area_m2": 38, "precio": 2300000, "administracion": None,
        "habitaciones": 1, "banos": 1, "parqueaderos": 0, "estrato": 4,
        "extras": ["amoblado", "vista"], "resumen": "Apartaestudio amoblado en Chapinero",
    },
    {
        "id": "demo4", "cuenta": "inmobiliaria_norte",
        "url": "https://instagram.com/p/demo4", "imagen": "",
        "fecha": "2026-06-15",
        "caption": "Santa Bárbara 🔝 Apto de lujo 140 m2, 3 hab + servicio, 3 baños, "
                   "terraza, 2 parqueaderos. Venta $850M.",
        "es_inmueble": True, "operacion": "venta", "tipo": "apartamento",
        "barrio": "Santa Barbara", "zona": "Usaquen", "direccion": None,
        "area_m2": 140, "precio": 850000000, "administracion": None,
        "habitaciones": 3, "banos": 3, "parqueaderos": 2, "estrato": 6,
        "extras": ["terraza", "cuarto_servicio", "parqueadero"],
        "resumen": "Apto de lujo en Santa Bárbara",
    },
    {
        "id": "demo5", "cuenta": "promo_general",
        "url": "https://instagram.com/p/demo5", "imagen": "",
        "fecha": "2026-06-21",
        "caption": "¡Feliz lunes! En nuestra inmobiliaria te ayudamos a encontrar "
                   "el hogar de tus sueños. Agenda tu asesoría 📲",
        "es_inmueble": False, "operacion": None, "tipo": None,
        "barrio": None, "zona": None, "direccion": None,
        "area_m2": None, "precio": None, "administracion": None,
        "habitaciones": None, "banos": None, "parqueaderos": None, "estrato": None,
        "extras": [], "resumen": "Publicación promocional, no es un inmueble",
    },
]

# Clientes de ejemplo.
CLIENTES_DEMO: list[dict[str, Any]] = [
    {
        "nombre": "Familia Gómez", "operacion": "arriendo",
        "barrios": ["El Nogal", "Rosales", "Chicó"], "zona": "Chapinero",
        "area_min": 80, "area_max": 120, "presupuesto_max": 6000000,
        "habitaciones_min": 3, "banos_min": 2,
        "extras": ["estudio"], "perimetro": "", "notas": "Tienen 2 hijos",
    },
    {
        "nombre": "Andrés Ríos", "operacion": "venta",
        "barrios": ["Cedritos", "Santa Bárbara"], "zona": "Usaquen",
        "area_min": 60, "area_max": 90, "presupuesto_max": 500000000,
        "habitaciones_min": 2, "banos_min": 2,
        "extras": ["parqueadero"], "perimetro": "", "notas": "Primera vivienda",
    },
    {
        "nombre": "Laura (inversión)", "operacion": "arriendo",
        "barrios": [], "zona": "Chapinero",
        "area_min": None, "area_max": 45, "presupuesto_max": 2500000,
        "habitaciones_min": 1, "banos_min": 1,
        "extras": ["amoblado"], "perimetro": "", "notas": "Busca para rentar a corto plazo",
    },
]
