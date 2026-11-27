# Instala (o actualiza) PrismaPDF en Windows. Uso, en PowerShell:
#   irm https://raw.githubusercontent.com/diegocoronado1412/prisma-pdf/main/instalar-windows.ps1 | iex
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # descargas mucho más rápidas
$Repo = "https://github.com/diegocoronado1412/prisma-pdf.git"
$Dir = if ($env:PRISMA_DIR) { $env:PRISMA_DIR } else { Join-Path $HOME "PrismaPDF" }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Need($id, $test) {
    if (-not (& $test)) {
        Write-Host "==> Instalando $id"
        winget install -e --id $id --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
    }
}
function Find-Python {
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Path $c) { return $c }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
    }
    return $null
}

Write-Host "==> PrismaPDF: instalando en $Dir"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "Falta 'winget'. Instala 'App Installer' desde la Microsoft Store y vuelve a correr esto."
}

# 1. Programas necesarios
Need "Python.Python.3.12" { [bool](Find-Python) }
Need "Git.Git" { [bool](Get-Command git -ErrorAction SilentlyContinue) }
$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
Need "JRSoftware.InnoSetup" { Test-Path $Iscc }
$Py = Find-Python
if (-not $Py) { throw "No se encontró Python 3.12. Cierra y abre PowerShell y vuelve a correr el comando." }

# 2. Código (bajar o actualizar)
if (Test-Path (Join-Path $Dir ".git")) {
    Write-Host "==> Actualizando el código"
    git -C $Dir pull --ff-only
} else {
    Write-Host "==> Bajando el código"
    git clone $Repo $Dir
}
Set-Location $Dir

# 3. Librerías
Write-Host "==> Instalando librerías (tarda unos minutos la primera vez)"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $Py -m venv .venv }
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt pywin32 pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de librerías." }

# 4. Datos del OCR
New-Item -ItemType Directory -Force tessdata | Out-Null
foreach ($l in "spa", "eng", "osd") {
    $f = "tessdata\$l.traineddata"
    if (-not (Test-Path $f) -or (Get-Item $f).Length -eq 0) {
        Write-Host "==> Bajando datos de OCR: $l"
        Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_best/raw/main/$l.traineddata" -OutFile $f
    }
}

# 5. Crear el programa y el instalador
Write-Host "==> Creando el programa"
& .venv\Scripts\pyinstaller.exe --noconfirm --windowed --log-level WARN --name PrismaPDF --icon assets/icono.ico `
    --add-data "tessdata;tessdata" --add-data "assets;assets" `
    --collect-all pdf2docx --collect-data pptx --collect-data docx `
    --hidden-import win32com.client --hidden-import pythoncom PrismaPDF.py
if ($LASTEXITCODE -ne 0) { throw "Falló al crear el programa." }
& $Iscc /Q installer.iss
if ($LASTEXITCODE -ne 0) { throw "Falló al crear el instalador." }

Write-Host "==> Listo. Abriendo el instalador de PrismaPDF"
Start-Process ".\Output\PrismaPDF-Setup.exe"
