; Instalador de Ruida Vision (Inno Setup 6). Se compila en el PC de Windows:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\RuidaVision.iss
; La version la pasa build_windows.bat con /DAPPVER=..., que la lee de
; ruidavision\__init__.py. A mano no se cambia nada aqui dentro.

#ifndef APPVER
  #define APPVER "0.0"
#endif

#define MiApp "Ruida Vision"

[Setup]
AppId={{7C1E0B2A-9E4D-4A2E-9B4C-3F1D2A5B6C77}
AppName={#MiApp}
AppVersion={#APPVER}
AppPublisher=JesPezz
DefaultDirName={autopf}\{#MiApp}
DefaultGroupName={#MiApp}
; Sin espacio en el nombre: esto va a una URL de descarga y el
; actualizador la tiene que poder pedir bien.
OutputBaseFilename=instalar_RuidaVision_{#APPVER}
OutputDir=..\dist
; lzma2/max y no ultra64: la diferencia de tamano son unos pocos MB y
; ultra64 tarda una eternidad en 200 MB, que es justo lo que hace que el
; build se quede a medias sin que se entere nadie.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
; Puerto 50207: si hay otra copia abierta, el panel no responde.
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\RuidaVision.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "spanish"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un icono en el escritorio"; \
  GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "..\dist\RuidaVision\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; El calibrado NO va a {app}: en Archivos de programa no se puede escribir sin
; ser administrador, y el calibrado lo escribe la app cada vez que se calibra.
; Va a los datos del usuario, que es donde la app lo busca.
Source: "..\calib.json"; DestDir: "{localappdata}\{#MiApp}"; \
  Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\{#MiApp}"; Filename: "{app}\RuidaVision.exe"
Name: "{group}\Desinstalar {#MiApp}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MiApp}"; Filename: "{app}\RuidaVision.exe"; Tasks: escritorio

[UninstallDelete]
Type: files; Name: "{localappdata}\{#MiApp}\*.log"

[Run]
Filename: "{app}\RuidaVision.exe"; Description: "Abrir {#MiApp}"; \
  Flags: nowait postinstall skipifsilent
