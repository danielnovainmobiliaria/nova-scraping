"""Actualización automática (lo corre GitHub Actions en la nube, sin abrir la app).

Trae las publicaciones nuevas de Instagram, las lee con IA y las guarda en la base
de datos en la nube. Como el scraping es incremental, solo trae lo nuevo desde la
última vez (barato). Las llaves vienen de los Secrets de GitHub Actions.
"""
from src import config, db, extractor, scraper


def main() -> None:
    if not config.DATABASE_URL:
        raise SystemExit("Falta DATABASE_URL: este job debe escribir en la base de la nube.")
    print("== Actualización automática de Nova Scraping ==", flush=True)
    db.init_db()
    scraper.scrapear_cuentas(config.leer_cuentas(), log=print)
    extractor.extraer_pendientes(log=print)
    print(f"Listo. Total de inmuebles en la base: {db.contar_posts()}", flush=True)


if __name__ == "__main__":
    main()
