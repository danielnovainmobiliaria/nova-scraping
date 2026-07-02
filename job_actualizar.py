"""Actualización automática (lo corre GitHub Actions en la nube, sin abrir la app).

Trae las publicaciones nuevas de Instagram, las lee con IA y las guarda en la base
de datos en la nube. Como el scraping es incremental, solo trae lo nuevo desde la
última vez (barato). Las llaves vienen de los Secrets de GitHub Actions.
"""
from src import config, db, extractor, scraper, scraper_portales


def main() -> None:
    if not config.DATABASE_URL:
        raise SystemExit("Falta DATABASE_URL: este job debe escribir en la base de la nube.")
    print("== Actualización automática de Nova Scraping ==", flush=True)
    db.init_db()

    # Cada fuente va por separado: si Instagram falla, los portales igual corren
    # (y viceversa). Solo se marca el día en rojo si TODO falló.
    errores: list[str] = []

    try:
        scraper.scrapear_cuentas(config.leer_cuentas(), log=print)
    except Exception as e:  # noqa: BLE001
        errores.append(f"Instagram: {e}")
        print(f"⚠️ Problema con Instagram (los portales igual se intentan): {e}", flush=True)

    try:
        extractor.extraer_pendientes(log=print)
    except Exception as e:  # noqa: BLE001
        errores.append(f"Lectura IA: {e}")
        print(f"⚠️ Problema leyendo captions con IA: {e}", flush=True)

    portales = config.leer_portales()
    if portales:
        try:
            scraper_portales.scrapear_portales(portales, log=print)
        except Exception as e:  # noqa: BLE001
            errores.append(f"Portales: {e}")
            print(f"⚠️ Problema leyendo portales: {e}", flush=True)

    print(f"Listo. Total de inmuebles en la base: {db.contar_posts()}", flush=True)
    if errores:
        print("Resumen de problemas del día: " + " | ".join(errores), flush=True)
        fuentes_intentadas = 2 + (1 if portales else 0)
        if len(errores) >= fuentes_intentadas:
            raise SystemExit(1)   # todo falló → que GitHub marque el run en rojo


if __name__ == "__main__":
    main()
