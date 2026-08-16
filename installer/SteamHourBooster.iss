#define MyAppName "Steam Hour Booster"
#define MyAppPublisher "jayden"
#define MyAppURL "https://github.com/ja7den2006/Steam-Hour-Booster"
#define MyAppExeName "SteamHourBooster.exe"

#define EnvAppVersion GetEnv("APP_VERSION")
#if EnvAppVersion == ""
#define MyAppVersion "0.1.0"
#else
#define MyAppVersion EnvAppVersion
#endif

#define EnvBuildDir GetEnv("APP_BUILD_DIR")
#if EnvBuildDir == ""
#define SourceDir "..\dist\SteamHourBooster"
#else
#define SourceDir EnvBuildDir
#endif

#define EnvOutputDir GetEnv("INSTALLER_OUTPUT_DIR")
#if EnvOutputDir == ""
#define OutputDir "..\dist\installer"
#else
#define OutputDir EnvOutputDir
#endif

[Setup]
AppId={{F7E1A46F-4B61-4AB5-B814-44614764E024}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\Steam Hour Booster
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=SteamHourBooster-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
#if FileExists("..\tmp\steam_icon.ico")
SetupIconFile=..\tmp\steam_icon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
