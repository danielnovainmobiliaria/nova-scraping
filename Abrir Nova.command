#!/bin/bash
# Doble clic en este archivo abre la app Nova Scraping en el navegador.
# Va a la carpeta donde está este archivo:
cd "$(dirname "$0")"

# Si por alguna razón no existe el entorno, lo crea e instala todo:
if [ ! -d ".venv" ]; then
  echo "Preparando por primera vez (esto puede tardar un par de minutos)..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi

echo ""
echo "==================================================="
echo "  Abriendo Nova Scraping en tu navegador..."
echo "  (No cierres esta ventana negra mientras la uses.)"
echo "  Para cerrar la app: cierra esta ventana."
echo "==================================================="
echo ""

streamlit run app.py
