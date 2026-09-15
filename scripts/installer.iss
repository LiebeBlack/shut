; Inno Setup script: builds the complete Windows installer for
; Smart Shutdown Hub from the PyInstaller output.
; Build:  iscc scripts\installer.iss
; Output: installer\SmartShutdownHub-Setup-1.0.0.exe

#define MyAppName "Smart Shutdown Hub"
#ifndef MyAppVersion
  ; CI passes /DMyAppVersion=<tag-without-v> or from pyproject.toml
  #define MyAppVersion "1.0.0"
#endif
#define MyAppExeName "SmartShutdownHub.exe"
#define MyAppPublisher "Smart Shutdown Hub"
#define MyAppURL "https://github.com/LiebeBlack/shut"

[Setup]
AppId={{8C6F4E5A-2B7D-4E93-9F1A-5C0DEB2001CA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\SmartShutdownHub
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
OutputDir=..\installer
OutputBaseFilename=SmartShutdownHub-Setup-{#MyAppVersion}
SetupIconFile=..\sshub\gui\assets\sshub.ico
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
WizardSizePercent=100
CloseApplications=yes
CloseApplicationsFilter=SmartShutdownHub.exe
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
spanish.DesktopIconTask=Crear acceso directo en el escritorio
english.DesktopIconTask=Create a desktop shortcut
spanish.AutostartTask=Iniciar con Windows (minimizado en bandeja)
english.AutostartTask=Start with Windows (minimized to system tray)
spanish.LaunchApp=Ejecutar {#MyAppName}
english.LaunchApp=Launch {#MyAppName}
spanish.ShortcutsGroup=Accesos directos:
english.ShortcutsGroup=Shortcuts:
spanish.OptionsGroup=Opciones:
english.OptionsGroup=Options:

[Files]
; The complete application folder (onedir build: exe + _internal libs +
; themes + icon), installed recursively so the app runs without repackaging.
Source: "..\dist\SmartShutdownHub\*"; \
    DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:ShortcutsGroup}"
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "{cm:OptionsGroup}"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SmartShutdownHub"; \
    ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; \
    ValueType: string; ValueName: ""; \
    ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchApp}"; \
    Flags: nowait postinstall skipifsilent
