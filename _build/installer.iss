; _build/installer.iss  --  Inno Setup script for CV-Toposheet
;
; STEP 1: Build the exe first:
;           double-click  _build\build_exe.bat
;
; STEP 2: Open this file in Inno Setup → Ctrl+F9
;
; OUTPUT:  _build\CVToposheet_Setup.exe

#define AppName        "CV-Toposheet"
#define AppVersion     "1.0.0"
#define AppPublisher   "Aditya Akash"
#define AppDescription "AI-Powered Historical Toposheet Digitization System"
#define AppURL         "https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps"
#define AppExeName     "CVToposheet.exe"
#define AppCopyright   "Copyright (C) 2026 Aditya Akash. Licensed under GNU GPLv3."
#define DistDir        "dist\CVToposheet"
#define IconFile       "app_icon.ico"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
AppReadMeFile={app}\ABOUT.txt
AppContact=Aditya Akash
AppCopyright={#AppCopyright}
AppComments={#AppDescription}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppDescription}
VersionInfoCopyright={#AppCopyright}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; License shown during install
LicenseFile=LICENSE.txt
; App description shown BEFORE install
InfoBeforeFile=ABOUT.txt
; API keys instructions shown AFTER install
InfoAfterFile=AFTER_INSTALL.txt
; Output
OutputDir=.
OutputBaseFilename=CVToposheet_Setup
; App icon (used on installer wizard, desktop shortcut, Add/Remove Programs)
SetupIconFile={#IconFile}
WizardSmallImageFile=app_icon.png
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ShowLanguageDialog=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";           GroupDescription: "Additional icons:"
Name: "startupentry"; Description: "Launch automatically at &startup (optional)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; Python runtime + all app files (no Python needed on target PC)
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Ship the license and info files inside the app folder too
Source: "LICENSE.txt";       DestDir: "{app}"; Flags: ignoreversion
Source: "ABOUT.txt";         DestDir: "{app}"; Flags: ignoreversion
Source: "AFTER_INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion
; App icon — used by shortcuts
Source: "{#IconFile}";   DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";             Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#IconFile}"; Comment: "{#AppDescription}"
Name: "{group}\About CV-Toposheet";     Filename: "{app}\ABOUT.txt"
Name: "{group}\API Keys Setup Guide";   Filename: "{app}\AFTER_INSTALL.txt"
Name: "{group}\License";                Filename: "{app}\LICENSE.txt"
Name: "{group}\Uninstall {#AppName}";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";       Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#IconFile}"; Comment: "{#AppDescription}"; Tasks: desktopicon

[Run]
; Launch app after install (optional tick-box on final page)
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now (browser will open at http://127.0.0.1:5000)"; \
    Flags: nowait postinstall skipifsilent

[Registry]
; Store app info in registry (shows in Add/Remove Programs details)
Root: HKA; Subkey: "Software\{#AppPublisher}\{#AppName}"; \
    ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; \
    Flags: uninsdeletekey
; Optional auto-startup (only written in non-admin/per-user install mode)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#AppName}"; \
    ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupentry; Check: not IsAdminInstallMode

[UninstallDelete]
; Uncomment below to also remove user data on uninstall:
; Type: filesandordirs; Name: "{app}\results"
; Type: filesandordirs; Name: "{app}\maps"
; Type: filesandordirs; Name: "{app}\logs"
