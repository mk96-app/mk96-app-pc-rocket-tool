; Inno Setup script: per-user installer (no admin rights) for the PyInstaller bundle.
; build.ps1 passes AppVersion, SourceDir (PyInstaller output) and OutputDir.
; Keep AppMutexName equal to single_instance.MUTEX_NAME and RunValueName equal to
; autostart.RUN_VALUE_NAME.

#define AppName "MK96 PC Rocket Tool"
#define AppExe "mk96-app-pc-rocket-tool.exe"
#define AppMutexName "mk96-app-pc-rocket-tool"
#define RunValueName "MK96 PC Rocket Tool"

[Setup]
AppId={{E4330B9E-8639-4931-ACAC-46348CAF5B8C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Markes96
AppPublisherURL=https://github.com/mk96-app/mk96-app-pc-rocket-tool
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Detects the running app (named mutex) and asks to close it before updating files.
AppMutex={#AppMutexName}
CloseApplications=yes
SetupIconFile=generated\app.ico
UninstallDisplayIcon={app}\{#AppExe}
OutputDir={#OutputDir}
OutputBaseFilename=mk96-app-pc-rocket-tool-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "autostart"; Description: "Iniciar {#AppName} al iniciar sesión en Windows"; GroupDescription: "Opciones:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Straight in Start > All apps, no subfolder (Windows 11 convention for single-app products).
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value the app's "Iniciar con Windows" checkbox writes, so both stay in agreement.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#RunValueName}"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// The autostart value may also have been created from the app's settings, which the
// [Registry] section does not track: remove it on uninstall in every case. The user's
// config, sounds and Credential Manager entries are kept on purpose.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#RunValueName}');
end;
