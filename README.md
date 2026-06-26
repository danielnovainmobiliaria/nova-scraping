# 🏙️ Nova Scraping

Herramienta para **cruzar inmuebles publicados en Instagram con tu lista de clientes**.

Lee los captions de las publicaciones recientes de cuentas de brokers, extrae
con IA las características del inmueble (operación, barrio, metraje, precio,
habitaciones, baños, extras) y te muestra qué publicaciones encajan con cada
cliente para que las compartas.

---

## ¿Qué hace, paso a paso?

1. **Fuentes** → defines qué cuentas de Instagram seguir (editable cuando quieras).
2. **Scraping (Apify)** → trae los posts de los **últimos 30 días**.
3. **Lectura (Claude)** → cada caption se convierte en datos estructurados.
4. **Clientes (Excel)** → tu lista con los criterios de cada interesado.
5. **Cruce** → empareja publicaciones ↔ clientes con un puntaje de coincidencia.
6. **App web** → ves los matches, filtras y generas un texto listo para WhatsApp.

> 💡 **Modo Demo:** puedes probar todo el cruce **sin pagar nada y sin llaves**,
> usando datos de ejemplo. Ideal para ver cómo funciona antes de invertir.

---

## Instalación (una sola vez)

Abre la **Terminal** dentro de esta carpeta y ejecuta:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configurar las llaves (solo para el modo Real)

1. Copia el archivo de ejemplo:
   ```bash
   cp .env.example .env
   ```
2. Abre `.env` y pega tus llaves:
   - **APIFY_TOKEN** → https://console.apify.com/account/integrations
   - **ANTHROPIC_API_KEY** → https://console.anthropic.com/settings/keys

> El modo **Demo** no necesita ninguna llave.

## Preparar tu lista de clientes

```bash
python3 generar_plantilla.py
```

Esto crea `data/plantilla_clientes.xlsx`. Llénalo con tus clientes y guárdalo
como `data/clientes.xlsx` (o súbelo directamente en la app).

### Columnas del Excel

| Columna | Ejemplo | Notas |
|---|---|---|
| nombre | Familia Gómez | obligatorio |
| operacion | arriendo / venta | |
| barrios | El Nogal, Rosales | separados por coma |
| zona | Chapinero | localidad o sector |
| area_min | 80 | m² |
| area_max | 120 | m² |
| presupuesto_max | 6000000 | en pesos, sin puntos |
| habitaciones_min | 3 | |
| banos_min | 2 | |
| extras | estudio, terraza | ver lista abajo |
| perimetro | (opcional) | ver nota |
| notas | Tienen 2 hijos | libre |

**Extras válidos:** estudio, terraza, balcon, cuarto_servicio, deposito,
parqueadero, vista, remodelado, amoblado, chimenea, duplex, penthouse.

## Usar la app

```bash
source .venv/bin/activate
streamlit run app.py
```

Se abre en el navegador. Empieza en **modo Demo** para familiarizarte.

---

## Configurar las cuentas de Instagram

Edítalas desde la app (pestaña *Fuentes*) o directamente en
`config/cuentas.txt` (una cuenta por línea).

## Costos aproximados (modo Real)

- **Apify**: plan desde ~5 USD/mes; el scraper de Instagram cobra por uso.
- **Claude (Haiku)**: centavos de dólar por cientos de captions (cada post se
  lee una sola vez gracias a la caché local).

## Notas y límites

- El **perímetro entre calles y carreras** está en versión básica: el cruce
  fuerte es por barrio/zona. Mejorar el perímetro requiere geolocalización
  (ver con el equipo técnico para una segunda fase).
- El scraping de Instagram opera en una zona gris frente a los términos de
  Instagram; usar un servicio pago (Apify) es lo más estable.
- La caché vive en `data/nova.db`. Bórrala si quieres empezar de cero.
