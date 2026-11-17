; Inno Setup script. Build: iscc /DAppVersion=0.1.0 installer.iss  (after PyInstaller)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B1F6A0E-3B7E-4B5B-9E5B-7C0D2A4F8C11}
AppName=Mouse Battery Overlay
AppVersion={#AppVersion}
AppPublisher=Mouse Battery Overlay
DefaultDirName={autopf}\MouseBatteryOverlay
DefaultGroupName=Mouse Battery Overlay
PrivilegesRequired=lowest
OutputDir=dist-installer
OutputBaseFilename=MouseBatteryOverlay-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\MouseBatteryOverlay.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=MouseBatteryOverlay
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "autostart"; Description: "Start with Windows"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "dist\MouseBatteryOverlay\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Mouse Battery Overlay"; Filename: "{app}\MouseBatteryOverlay.exe"
Name: "{autodesktop}\Mouse Battery Overlay"; Filename: "{app}\MouseBatteryOverlay.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MouseBatteryOverlay"; ValueData: """{app}\MouseBatteryOverlay.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\MouseBatteryOverlay.exe"; Description: "Launch Mouse Battery Overlay"; Flags: nowait postinstall skipifsilent
