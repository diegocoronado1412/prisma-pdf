; Instalador de Prisma PDF (Inno Setup 6)
#define AppName "Prisma PDF"
#define AppVersion "1.0"

[Setup]
AppId={{6C1E3F4A-2B7D-4E8F-9A10-5D2C7B8E9F01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Prisma PDF
DefaultDirName={autopf}\Prisma PDF
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\PrismaPDF.exe
SetupIconFile=assets\icono.ico
OutputBaseFilename=PrismaPDF-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "openwith"; Description: "Agregar «Abrir con Prisma PDF» a los archivos PDF"; GroupDescription: "Archivos PDF:"

[Files]
Source: "dist\PrismaPDF\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PrismaPDF.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PrismaPDF.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\PrismaPDF.Document"; ValueType: string; ValueData: "Documento PDF"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\PrismaPDF.Document\DefaultIcon"; ValueType: string; ValueData: "{app}\PrismaPDF.exe,0"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\PrismaPDF.Document\shell\open\command"; ValueType: string; ValueData: """{app}\PrismaPDF.exe"" ""%1"""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "PrismaPDF.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith

[Run]
Filename: "{app}\PrismaPDF.exe"; Description: "Abrir Prisma PDF"; Flags: nowait postinstall skipifsilent
