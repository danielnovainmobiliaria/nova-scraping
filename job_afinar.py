"""Atiende la solicitud LIVIANA de Brokerap: aprender de motivos de descarte
y republicar el Radar. No scrapea nada (no gasta Apify): solo unas llamadas
pequeñas de IA por los motivos nuevos, y el re-cálculo local del cruce.
"""
from src import config, db, radar, solicitudes


def main() -> None:
    if not config.DATABASE_URL:
        raise SystemExit("Falta DATABASE_URL: este job debe escribir en la base de la nube.")
    print("== Afinación rápida (pedida desde Brokerap) ==", flush=True)
    db.init_db()
    solicitudes.limpiar_solicitud(solicitudes.CLAVE_AFINAR)
    try:
        solicitudes.atender_afinaciones(log=print)
    except Exception as e:  # noqa: BLE001 - el radar se republica igual
        print(f"⚠️ La afinación falló: {e}", flush=True)
    radar.publicar_radar(log=print)


if __name__ == "__main__":
    main()
