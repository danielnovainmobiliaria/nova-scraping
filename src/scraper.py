"""Descarga de publicaciones de Instagram usando Apify.

Usa el actor oficial "apify/instagram-scraper", que recibe una lista de
usuarios y devuelve sus publicaciones recientes con el caption, la fecha,
la URL y la imagen de portada.

Documentación del actor: https://apify.com/apify/instagram-scraper
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from apify_client import ApifyClient

from . import config, db

ACTOR_ID = "apify/instagram-scraper"


def _fecha_corte_iso() -> str:
    """Piso de 30 días: la ventana de inmuebles 'activos' que nos interesa."""
    corte = datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)
    return corte.date().isoformat()


def _lecturas_por_cuenta() -> dict[str, str]:
    """Última fecha de lectura EXITOSA de cada cuenta (para no perder posts)."""
    try:
        return json.loads(db.leer_meta("ultimas_lecturas") or "{}")
    except json.JSONDecodeError:
        return {}


def _cutoff_incremental(cuentas: list[str]) -> str:
    """Desde qué fecha pedir publicaciones para NO repetir lo ya descargado.

    Usa la última lectura EXITOSA más ANTIGUA entre las cuentas: si una cuenta
    estuvo restringida unos días, al recuperarse la ventana retrocede lo necesario
    para no perder sus posts (antes el corte era global y esos posts se perdían).
    Nunca pasa del piso de 30 días.
    """
    piso = (datetime.now(timezone.utc) - timedelta(days=config.DIAS_RECIENTES)).date()
    lecturas = _lecturas_por_cuenta()
    fechas = []
    for c in cuentas:
        f = lecturas.get(c)
        if not f:
            return piso.isoformat()   # alguna cuenta nunca leída → ventana completa
        try:
            fechas.append(date.fromisoformat(f))
        except ValueError:
            return piso.isoformat()
    if not fechas:
        return piso.isoformat()
    desde = min(fechas) - timedelta(days=config.DIAS_SOLAPE)
    return max(piso, desde).isoformat()


CUARENTENA_DIAS = 7    # una cuenta marcada privada se reintenta a los 7 días


def _privadas_en_cuarentena() -> tuple[dict, set]:
    """({usuario: fecha_marca}, {usuarios en cuarentena vigente}).

    Formato dict con fecha: una falla PASAJERA de Instagram ya no condena a la
    cuenta para siempre (eso congeló 35 cuentas sanas en agosto). A los 7 días
    se reintenta; si vuelve a fallar se re-marca, si lee bien se auto-sana."""
    try:
        crudo = json.loads(db.leer_meta("cuentas_restringidas") or "{}")
    except json.JSONDecodeError:
        crudo = {}
    if isinstance(crudo, list):   # formato viejo (lista) → reintentar todas ya
        crudo = {config._solo_usuario(u): "2000-01-01" for u in crudo}
    hoy = datetime.now(timezone.utc).date()
    vigentes = set()
    for u, f in crudo.items():
        try:
            if (hoy - date.fromisoformat(str(f))).days < CUARENTENA_DIAS:
                vigentes.add(u)
        except ValueError:
            pass
    return crudo, vigentes


def _buckets_por_lectura(cuentas: list[str]) -> list[tuple[str, list[str]]]:
    """Agrupa las cuentas por qué tan atrasada está su última lectura.

    Cada grupo corre APARTE con su propio corte: una cuenta nueva o rezagada
    paga SU ventana, sin arrastrar el corte de las que están al día (ese
    arrastre re-compró un mes completo dos veces, en julio y en agosto)."""
    lecturas = _lecturas_por_cuenta()
    hoy = datetime.now(timezone.utc).date()
    frescas, rezagadas, nunca = [], [], []
    for c in cuentas:
        f = lecturas.get(c)
        try:
            dias = (hoy - date.fromisoformat(str(f))).days if f else None
        except ValueError:
            dias = None
        if dias is None:
            nunca.append(c)
        elif dias <= 3:
            frescas.append(c)
        else:
            rezagadas.append(c)
    grupos = []
    if frescas:
        grupos.append(("al día", frescas))
    if rezagadas:
        grupos.append(("rezagadas", rezagadas))
    if nunca:
        grupos.append(("nuevas", nunca))
    return grupos


def scrapear_cuentas(cuentas: list[str], log=print) -> int:
    """Trae los posts recientes de las cuentas y los guarda en la caché.

    Devuelve cuántos posts nuevos se guardaron.
    Requiere APIFY_TOKEN configurado en el .env.
    """
    if not config.APIFY_TOKEN:
        raise RuntimeError(
            "Falta APIFY_TOKEN en el archivo .env. "
            "Consíguelo en https://console.apify.com/account/integrations"
        )
    if not cuentas:
        log("No hay cuentas configuradas en config/cuentas.txt")
        return 0

    cliente = ApifyClient(config.APIFY_TOKEN)

    marcas, en_cuarentena = _privadas_en_cuarentena()
    _saltadas = [c for c in cuentas if c in en_cuarentena]
    if _saltadas:
        log(f"🔒 {len(_saltadas)} cuenta(s) privadas en cuarentena se saltan "
            f"(se reintentan a los {CUARENTENA_DIAS} días): "
            + ", ".join("@" + c for c in _saltadas[:6])
            + (" y más" if len(_saltadas) > 6 else ""))
        cuentas = [c for c in cuentas if c not in en_cuarentena]
    if not cuentas:
        log("Todas las cuentas están en cuarentena; nada que scrapear.")
        return 0

    hoy = datetime.now(timezone.utc).date().isoformat()
    nuevos_total = 0
    fallidas_hoy: set[str] = set()
    leidas_ok: set[str] = set()

    # Instagram exige proxies residenciales y enlaces de perfil (directUrls),
    # de lo contrario bloquea la lectura ("Empty or private data").
    for etiqueta, grupo in _buckets_por_lectura(cuentas):
        corte = _cutoff_incremental(grupo)
        primera = etiqueta == "nuevas"
        run_input: dict[str, Any] = {
            "directUrls": [f"https://www.instagram.com/{u}/" for u in grupo],
            "resultsType": "posts",
            "resultsLimit": 100 if primera else config.MAX_POSTS_POR_CUENTA,
            "onlyPostsNewerThan": corte,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        log(f"📷 Grupo «{etiqueta}»: {len(grupo)} cuenta(s), trayendo desde {corte}…")
        run = cliente.actor(ACTOR_ID).call(run_input=run_input)
        if run is None or not run.default_dataset_id:
            raise RuntimeError("Apify no devolvió resultados. Revisa tu plan o las cuentas.")
        nuevos = 0
        fallidas_grupo: set[str] = set()
        for item in cliente.dataset(run.default_dataset_id).iterate_items():
            if item.get("error"):  # perfil restringido/privado: Instagram lo bloquea
                u = config._solo_usuario(item.get("inputUrl") or item.get("url") or "")
                if u:
                    fallidas_grupo.add(u)
                continue
            post = _normalizar(item)
            if post is None:
                continue
            if db.guardar_post(post):
                nuevos += 1
        nuevos_total += nuevos
        fallidas_hoy |= fallidas_grupo
        leidas_ok |= {c for c in grupo if c not in fallidas_grupo}
        log(f"   «{etiqueta}»: {nuevos} publicaciones nuevas.")

    # Cuarentena: las fallas de hoy se marcan con fecha; las que leyeron bien SANAN.
    for u in fallidas_hoy:
        marcas[u] = hoy
    for u in leidas_ok:
        marcas.pop(u, None)
    db.guardar_meta("cuentas_restringidas", json.dumps(marcas, ensure_ascii=False))

    db.guardar_meta("ultimo_scrape", hoy)
    # Última lectura exitosa POR CUENTA.
    lecturas = _lecturas_por_cuenta()
    lecturas.update({c: hoy for c in leidas_ok})
    db.guardar_meta("ultimas_lecturas", json.dumps(lecturas, ensure_ascii=False))

    if fallidas_hoy:
        log(f"⚠️ {len(fallidas_hoy)} cuenta(s) no se dejaron leer (quedan en "
            f"cuarentena {CUARENTENA_DIAS} días).")
    log(f"Listo. Se guardaron {nuevos_total} publicaciones nuevas.")
    return nuevos_total


def _media(item: dict[str, Any]) -> list[dict[str, str]]:
    """Lista de archivos descargables del post (videos y fotos), sin duplicar.

    Maneja posts de video, fotos sueltas y carruseles (Sidecar).
    """
    media: list[dict[str, str]] = []
    vistos: set[str] = set()

    def agregar(tipo: str, url: str | None) -> None:
        if url and url not in vistos:
            vistos.add(url)
            media.append({"tipo": tipo, "url": url})

    # Video principal
    if item.get("videoUrl"):
        agregar("video", item["videoUrl"])
    # Carrusel: cada elemento puede ser foto o video
    hijos = item.get("childPosts") or []
    if hijos:
        for hijo in hijos:
            if hijo.get("videoUrl"):
                agregar("video", hijo["videoUrl"])
            else:
                agregar("foto", hijo.get("displayUrl"))
    else:
        for img in (item.get("images") or []):
            agregar("foto", img)
    # Respaldo: la imagen de portada
    if not media:
        agregar("foto", item.get("displayUrl"))
    return media


def _normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convierte un item crudo de Apify al formato que usa nuestra caché."""
    post_id = item.get("id") or item.get("shortCode")
    caption = item.get("caption") or ""
    if not post_id or not caption.strip():
        return None

    return {
        "id": str(post_id),
        "cuenta": item.get("ownerUsername", ""),
        "url": item.get("url", ""),
        "caption": caption,
        # YYYY-MM-DD; si Apify no trae la fecha, usa hoy (antes quedaba '' e invisible).
        "fecha": (item.get("timestamp") or "")[:10]
                 or datetime.now(timezone.utc).date().isoformat(),
        "imagen": item.get("displayUrl", ""),
        "media": _media(item),
    }
