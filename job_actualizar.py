"""Actualización automática (lo corre GitHub Actions en la nube, sin abrir la app).

Trae las publicaciones nuevas de Instagram, las lee con IA y las guarda en la base
de datos en la nube. Como el scraping es incremental, solo trae lo nuevo desde la
última vez (barato). Las llaves vienen de los Secrets de GitHub Actions.
"""
from datetime import date

from src import config, db, extractor, limpieza, scraper, scraper_portales


def main() -> None:
    if not config.DATABASE_URL:
        raise SystemExit("Falta DATABASE_URL: este job debe escribir en la base de la nube.")
    print("== Actualización automática de Nova Scraping ==", flush=True)
    db.init_db()

    # Cada fuente va por separado: si Instagram falla, los portales igual corren
    # (y viceversa). Solo se marca el día en rojo si TODO falló.
    errores: list[str] = []

    try:
        cuentas = config.leer_cuentas()
        # Cadencia adaptativa: cuentas sin publicar hace 10+ días van en corrida
        # APARTE cada 2 días. OJO: separadas para que su corte viejo no arrastre
        # el 'onlyPostsNewerThan' de las activas (eso fue lo que disparó el
        # gasto en julio con las privadas).
        import json as _json
        try:
            _leidas = set(_json.loads(db.leer_meta("ultimas_lecturas") or "{}"))
        except Exception:  # noqa: BLE001
            _leidas = set()
        _ult_pub = {}
        for p in db.posts_leidos():
            cta, f = p.get("cuenta"), str(p.get("fecha") or "")[:10]
            if cta and f and f > _ult_pub.get(cta, ""):
                _ult_pub[cta] = f
        hoy_iso = date.today().isoformat()

        def _dias_sin_publicar(cta):
            f = _ult_pub.get(cta)
            if not f:
                return None
            return (date.today() - date.fromisoformat(f)).days

        dormidas = [c for c in cuentas
                    if c in _leidas                      # ya leída antes (no es nueva)
                    and (_dias_sin_publicar(c) or 0) > 10]
        activas = [c for c in cuentas if c not in dormidas]
        scraper.scrapear_cuentas(activas, log=print)
        if dormidas:
            if date.today().toordinal() % 2 == 0:
                print(f"😴 Corrida aparte de {len(dormidas)} cuenta(s) dormidas "
                      "(sin publicar hace 10+ días)…", flush=True)
                scraper.scrapear_cuentas(dormidas, log=print)
            else:
                print(f"😴 {len(dormidas)} cuenta(s) dormidas descansan hoy "
                      "(se revisan cada 2 días).", flush=True)
    except Exception as e:  # noqa: BLE001
        errores.append(f"Instagram: {e}")
        print(f"⚠️ Problema con Instagram (los portales igual se intentan): {e}", flush=True)

    try:
        # Modo LOTE: nadie espera al robot → tarifa del 50% en la lectura IA.
        extractor.extraer_pendientes(log=print, lote=True)
    except Exception as e:  # noqa: BLE001
        errores.append(f"Lectura IA: {e}")
        print(f"⚠️ Problema leyendo captions con IA: {e}", flush=True)

    portales = config.leer_portales()
    # La VENTA se mueve lento: sus búsquedas corren un día sí, un día no
    # (los arriendos, que vuelan, corren a diario). Ahorra ~la mitad de Apify.
    if date.today().toordinal() % 2:
        descansan = [u for u in portales
                     if "venta" in u.lower() or "for-sale" in u.lower()]
        if descansan:
            print(f"↔️ Hoy descansan {len(descansan)} búsqueda(s) de VENTA "
                  "(corren mañana; los arriendos van a diario).", flush=True)
            portales = [u for u in portales
                        if "venta" not in u.lower() and "for-sale" not in u.lower()]
    if portales:
        try:
            scraper_portales.scrapear_portales(portales, log=print)
        except Exception as e:  # noqa: BLE001
            errores.append(f"Portales: {e}")
            print(f"⚠️ Problema leyendo portales: {e}", flush=True)

    try:
        from src import radar
        radar.publicar_radar(log=print)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Radar: {e}", flush=True)

    try:
        limpieza.purgar_no_comercializables(log=print)
    except Exception as e:  # noqa: BLE001 - la limpieza jamás tumba el día
        print(f"⚠️ La limpieza de vencidos falló: {e}", flush=True)

    print(f"Listo. Total de inmuebles en la base: {db.contar_posts()}", flush=True)
    if errores:
        print("Resumen de problemas del día: " + " | ".join(errores), flush=True)
        fuentes_intentadas = 2 + (1 if portales else 0)
        if len(errores) >= fuentes_intentadas:
            raise SystemExit(1)   # todo falló → que GitHub marque el run en rojo


if __name__ == "__main__":
    main()
