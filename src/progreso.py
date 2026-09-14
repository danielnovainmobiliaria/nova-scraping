"""Progreso de la actualización, para que Brokerap lo pueda dibujar.

El robot ya cuenta lo que hace por consola con `print`, pero esa consola vive
en GitHub Actions y Daniel no la ve: desde la web, pulsar "Actualizar todo" era
esperar sin saber si faltaban dos minutos o treinta.

Aquí se escribe el mismo relato en la tabla `meta`, que sí lee Brokerap:

  progreso_actualizar   -> {"paso", "total", "etiqueta", "empezado", "visto"}
  duraciones_actualizar -> [minutos de las últimas 10 corridas]

Con las duraciones pasadas la web calcula cuánto falta. La primera vez no hay
con qué estimar y simplemente no muestra tiempo, que es mejor que inventarlo.

Nada de esto puede tumbar una corrida: si la base no responde al marcar un
paso, se sigue de largo. El progreso es un lujo, los inmuebles no.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src import db

CLAVE = "progreso_actualizar"
CLAVE_DURACIONES = "duraciones_actualizar"

# El orden es el de job_actualizar.main(). Si cambia allá, cambia aquí.
PASOS: list[tuple[str, str]] = [
    ("instagram", "Leyendo Instagram"),
    ("ia", "Interpretando las publicaciones"),
    ("portales", "Recorriendo los portales"),
    ("colas", "Atendiendo lo que dejaste pedido"),
    ("radar", "Cruzando con tus clientes"),
    ("cierre", "Cerrando y limpiando"),
]

_CLAVES = [c for c, _ in PASOS]


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _escribir(datos: dict) -> None:
    try:
        db.guardar_meta(CLAVE, json.dumps(datos, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 - el progreso jamás tumba la corrida
        print(f"(progreso: no se pudo anotar: {e})", flush=True)


def empezar() -> None:
    """Marca el arranque. Llamar una vez, al principio de la corrida."""
    _escribir({
        "paso": 0,
        "total": len(PASOS),
        "etiqueta": "Arrancando",
        "empezado": _ahora(),
        "visto": _ahora(),
    })


def marcar(clave: str) -> None:
    """Anota que empezó el paso `clave` (uno de PASOS)."""
    if clave not in _CLAVES:
        return
    i = _CLAVES.index(clave)
    anterior = {}
    try:
        anterior = json.loads(db.leer_meta(CLAVE) or "{}")
    except Exception:  # noqa: BLE001
        anterior = {}
    _escribir({
        "paso": i + 1,
        "total": len(PASOS),
        "etiqueta": PASOS[i][1],
        # Si se perdió el arranque, se usa ahora: mejor una barra sin tiempo
        # que una barra que diga que lleva corriendo desde 1970.
        "empezado": anterior.get("empezado") or _ahora(),
        "visto": _ahora(),
    })


def terminar() -> None:
    """Borra el progreso y guarda cuánto duró, para estimar la próxima."""
    minutos = None
    try:
        actual = json.loads(db.leer_meta(CLAVE) or "{}")
        empezado = actual.get("empezado")
        if empezado:
            ini = datetime.fromisoformat(empezado)
            minutos = max(1, round((datetime.now(timezone.utc) - ini).total_seconds() / 60))
    except Exception:  # noqa: BLE001
        minutos = None

    try:
        if minutos:
            try:
                previas = json.loads(db.leer_meta(CLAVE_DURACIONES) or "[]")
            except Exception:  # noqa: BLE001
                previas = []
            previas = ([minutos] + [d for d in previas if isinstance(d, int)])[:10]
            db.guardar_meta(CLAVE_DURACIONES, json.dumps(previas))
        # Vaciar en vez de borrar: guardar_meta es un upsert y no hay un
        # borrar_meta; la web trata la cadena vacía como "no hay progreso".
        db.guardar_meta(CLAVE, "")
    except Exception as e:  # noqa: BLE001
        print(f"(progreso: no se pudo cerrar: {e})", flush=True)
