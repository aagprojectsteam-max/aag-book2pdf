#ifndef AppVersion
  #define AppVersion "1.3.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\..\dist\windows\bundle\AAG-Book2PDF"
#endif
#ifndef OutputPath
  #define OutputPath "..\..\dist\windows"
#endif
[Setup]
AppId={{563C061D-13A8-499B-9724-DC814D12494E}
AppName=AAG Book2PDF
AppVersion={#AppVersion}
AppPublisher=AAG
DefaultDirName={localappdata}\Programs\AAG Book2PDF
DefaultGroupName=AAG Book2PDF
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#OutputPath}
OutputBaseFilename=AAG-Book2PDF-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\.build\windows-icon.ico
UninstallDisplayIcon={app}\AAG-Book2PDF.exe
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; Flags: unchecked
Name: "bookassociation"; Description: "Add AAG Book2PDF to Open with for .book files"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AAG Book2PDF"; Filename: "{app}\AAG-Book2PDF.exe"
Name: "{userdesktop}\AAG Book2PDF"; Filename: "{app}\AAG-Book2PDF.exe"; Tasks: desktopicon

[Registry]
; Only our unique ProgID/capabilities are removed on uninstall. Never edit UserChoice.
Root: HKCU; Subkey: "Software\Classes\AAG.Book2PDF.Book"; ValueType: string; ValueName: ""; ValueData: "AAG Book2PDF Book"; Flags: uninsdeletekey; Tasks: bookassociation
Root: HKCU; Subkey: "Software\Classes\AAG.Book2PDF.Book\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\AAG-Book2PDF.exe"",0"; Tasks: bookassociation
Root: HKCU; Subkey: "Software\Classes\AAG.Book2PDF.Book\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\AAG-Book2PDF.exe"" ""%1"""; Tasks: bookassociation
Root: HKCU; Subkey: "Software\Classes\.book\OpenWithProgids"; ValueType: string; ValueName: "AAG.Book2PDF.Book"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: bookassociation
Root: HKCU; Subkey: "Software\Classes\Applications\AAG-Book2PDF.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "AAG Book2PDF"; Flags: uninsdeletekey; Tasks: bookassociation
Root: HKCU; Subkey: "Software\Classes\Applications\AAG-Book2PDF.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\AAG-Book2PDF.exe"" ""%1"""; Tasks: bookassociation
Root: HKCU; Subkey: "Software\Classes\Applications\AAG-Book2PDF.exe\SupportedTypes"; ValueType: string; ValueName: ".book"; ValueData: ""; Tasks: bookassociation
Root: HKCU; Subkey: "Software\AAG\Book2PDF\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "AAG Book2PDF"; Flags: uninsdeletekey; Tasks: bookassociation
Root: HKCU; Subkey: "Software\AAG\Book2PDF\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Read and recover scanned .book files"; Tasks: bookassociation
Root: HKCU; Subkey: "Software\AAG\Book2PDF\Capabilities\FileAssociations"; ValueType: string; ValueName: ".book"; ValueData: "AAG.Book2PDF.Book"; Tasks: bookassociation
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "AAG Book2PDF"; ValueData: "Software\AAG\Book2PDF\Capabilities"; Flags: uninsdeletevalue; Tasks: bookassociation

[Run]
Filename: "{app}\AAG-Book2PDF.exe"; Description: "Open AAG Book2PDF"; Flags: nowait postinstall skipifsilent
Filename: "ms-settings:defaultapps"; Description: "Choose the default app for .book files in Windows Settings"; Flags: shellexec nowait postinstall skipifsilent unchecked; Tasks: bookassociation
