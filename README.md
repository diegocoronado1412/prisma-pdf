# PrismaPDF

Editor de PDF para Windows y Mac: editar texto (también en PDF escaneados), convertir a Word, Excel,
PowerPoint e imágenes, OCR en español e inglés, unir, dividir, comprimir, proteger con contraseña y más.

## Instalar

### Mac
Abre **Terminal** y pega:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/diegocoronado1412/prisma-pdf/main/instalar-mac.sh)"
```

Instala lo necesario (Homebrew, Python y Git, si faltan), crea la app y la deja en **Aplicaciones**.
Puede pedir la contraseña del Mac. La primera vez tarda unos 10 minutos.

### Windows
Abre **PowerShell** y pega:

```powershell
irm https://raw.githubusercontent.com/diegocoronado1412/prisma-pdf/main/instalar-windows.ps1 | iex
```

Instala lo necesario (Python, Git e Inno Setup, si faltan), crea el programa y abre su instalador.

## Actualizar
Vuelve a pegar el mismo comando: baja la versión nueva y rehace la app.
