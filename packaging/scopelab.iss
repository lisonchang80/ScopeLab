; scopelab.iss — Inno Setup 6 安裝腳本。
;
; 把 PyInstaller 產物 packaging\dist\ScopeLab\ 整包成 ScopeLab-Setup.exe。
; 每使用者安裝（免系統管理員 / 不跳 UAC），最符合「越容易安裝越好」。
;
; 編譯（需先裝 Inno Setup 6，提供 ISCC.exe）：
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\scopelab.iss
; 產物：packaging\dist\ScopeLab-Setup.exe
;
; 前置：先成功跑 packaging\build_exe.ps1 產生 dist\ScopeLab\ScopeLab.exe。

#define AppName "ScopeLab"
#define AppVer "0.1.0"
#define AppPublisher "ScopeLab"
#define AppExe "ScopeLab.exe"

[Setup]
AppId={{7C4E9A21-3F8B-4D1E-A6C2-9B5E1F3D72A8}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#AppPublisher}
; 每使用者安裝到 %LOCALAPPDATA%\Programs\ScopeLab → 免 admin
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; 安裝腳本與來源都在 packaging\ 下；輸出到 packaging\dist
SourceDir=.
OutputDir=dist
OutputBaseFilename=ScopeLab-Setup
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "附加捷徑："

[Files]
; PyInstaller onedir 整包（使用者資料另存 %LOCALAPPDATA%\ScopeLab，不在安裝夾內）
Source: "dist\ScopeLab\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\解除安裝 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即啟動 {#AppName}"; Flags: nowait postinstall skipifsilent

; 註：解除安裝「不」刪除使用者資料 %LOCALAPPDATA%\ScopeLab（標註專案 / 訓練結果 / 匯出視為
;     使用者資料）。如需清除請手動刪該資料夾。
