; Inno Setup script: builds the complete Windows installer for
; Smart Shutdown Hub from the PyInstaller output.
; Build:  iscc scripts\installer.iss
; Output: installer\SmartShutdownHub-Setup-1.0.0.exe

#define MyAppName "Smart Shutdown Hub"
#ifndef MyAppVersion
  ; CI passes /DMyAppVersion=<tag-without-v> on tag pushes.
#define MyAppVersion "1.0.0"
#endif
#define MyAppExeName "SmartShutdownHub.exe"

[Setup]
AppId={{8C6F4E5A-2B7D-4E93-9F1A-5C0DEB2001CA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Smart Shutdown Hub Project
DefaultDirName={autopf}\SmartShutdownHub
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\installer
OutputBaseFilename=SmartShutdownHub-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "..\dist\SmartShutdownHub.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "autostart"; Description: "Iniciar con Windows (minimizado en bandeja)"; GroupDescription: "Opciones:"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SmartShutdownHub"; \
    ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent
