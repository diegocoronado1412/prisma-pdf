#!/bin/bash
# Instala (o actualiza) PrismaPDF en un Mac. Uso:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/diegocoronado1412/prisma-pdf/main/instalar-mac.sh)"
set -e
REPO="https://github.com/diegocoronado1412/prisma-pdf.git"
DIR="${PRISMA_DIR:-$HOME/PrismaPDF}"
APPS="${PRISMA_APPS:-/Applications}"

echo "==> PrismaPDF: instalando en $DIR"

# 1. Homebrew (si no está)
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "==> Instalando Homebrew (te pedirá la contraseña del Mac)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
  grep -q "brew shellenv" ~/.zprofile 2>/dev/null || \
    echo 'eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"' >> ~/.zprofile
fi

# 2. Python 3.12 y Git
echo "==> Instalando Python y Git"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1 || brew install git
PY="$(brew --prefix python@3.12)/bin/python3.12"

# 3. Código (bajar o actualizar)
if [ -d "$DIR/.git" ]; then
  echo "==> Actualizando el código"
  git -C "$DIR" pull --ff-only
else
  echo "==> Bajando el código"
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

# 4. Librerías
echo "==> Instalando librerías (tarda unos minutos la primera vez)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt pyinstaller

# 5. Datos del OCR
mkdir -p tessdata
for l in spa eng osd; do
  [ -s "tessdata/$l.traineddata" ] || { echo "==> Bajando datos de OCR: $l"; \
    curl -fL# -o "tessdata/$l.traineddata" "https://github.com/tesseract-ocr/tessdata_best/raw/main/$l.traineddata"; }
done

# 6. Crear la app e instalarla
echo "==> Creando la app"
.venv/bin/pyinstaller --noconfirm --windowed --log-level WARN --name PrismaPDF --icon assets/icono.icns \
  --osx-bundle-identifier com.prisma.pdf \
  --add-data "tessdata:tessdata" --add-data "assets:assets" \
  --collect-all pdf2docx --collect-data pptx --collect-data docx PrismaPDF.py
rm -rf "$APPS/PrismaPDF.app"
cp -R dist/PrismaPDF.app "$APPS/"
xattr -cr "$APPS/PrismaPDF.app"

echo "==> Listo: PrismaPDF quedó en $APPS"
[ -n "$PRISMA_NO_OPEN" ] || open "$APPS/PrismaPDF.app"
