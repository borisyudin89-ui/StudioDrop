; Установщик StudioDrop для Windows (без прав администратора, в папку пользователя)
#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{8F3C1A52-6B7D-4E19-A0C4-5D2E7B91F3A6}
AppName=StudioDrop
AppVersion={#AppVersion}
AppPublisher=StudioDrop
DefaultDirName={localappdata}\Programs\StudioDrop
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=installer_out
OutputBaseFilename=StudioDrop-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\StudioDrop.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no

#if FileExists(CompilerPath + "Languages\Russian.isl")
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Files]
Source: "dist\StudioDrop\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\StudioDrop"; Filename: "{app}\StudioDrop.exe"
Name: "{autodesktop}\StudioDrop"; Filename: "{app}\StudioDrop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StudioDrop.exe"; Description: "Запустить StudioDrop"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{userappdata}\Microsoft\Windows\SendTo\StudioDrop.cmd"
Type: files; Name: "{userappdata}\Microsoft\Windows\Start Menu\Programs\Startup\StudioDrop.cmd"

[Code]
procedure KillOld();
var rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM StudioDrop.exe', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(800);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillOld();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  KillOld();
  Result := True;
end;
