; Inno Setup script for WhatsApp Forensicator.
; Build:  ISCC.exe /DAppVersion=1.0.0 installer\wa_forensicator.iss
; (build_installer.bat and .github/workflows/release.yml both do this.)
;
; Expects, relative to the repo root:
;   dist\WhatsAppForensicator\   <- PyInstaller output (pyinstaller wa_forensicator.spec)
;   bin\platform-tools\          <- adb (scripts\fetch_tools.ps1 downloads it)
;   bin\tesseract\               <- Tesseract OCR  (scripts\fetch_tools.ps1 copies it)

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppName "WhatsApp Forensicator"
#define AppExe  "WhatsAppForensicator.exe"

[Setup]
AppId={{6F0B3C52-8E4A-4D7B-9A21-5C1E7D93B4A8}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Keshav (Cyber Octopus)
AppPublisherURL=https://github.com/Keshav-gehlot
AppSupportURL=https://github.com/Keshav-gehlot

; Per-user install (no admin prompt). This matters: the app writes
; .ocr_config.json next to the exe, and Program Files is not writable
; without admin rights.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Show the legal/ethics notice and licence before installing.
InfoBeforeFile=..\docs\LEGAL_AND_ETHICS.md
LicenseFile=..\LICENSE

OutputDir=..\dist-installer
OutputBaseFilename=WhatsAppForensicator-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; The PyInstaller app. Anything already staged under bin/models in dist is
; skipped here and re-added explicitly below so we control exactly what ships.
Source: "..\dist\WhatsAppForensicator\*"; DestDir: "{app}"; \
  Excludes: ".ocr_config.json,bin\*,models\*"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

; adb (Android platform-tools, Apache-2.0)
Source: "..\bin\platform-tools\*"; DestDir: "{app}\bin\platform-tools"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

; Tesseract OCR, minus training tools / man pages / unused Java jars.
Source: "..\bin\tesseract\*"; DestDir: "{app}\bin\tesseract"; \
  Excludes: "*.html,*.jar,tessdata\*.traineddata,tessdata\script\*,ambiguous_words.exe,classifier_tester.exe,cntraining.exe,combine_lang_model.exe,combine_tessdata.exe,dawg2wordlist.exe,lstmeval.exe,lstmtraining.exe,merge_unicharsets.exe,mftraining.exe,set_unicharset_properties.exe,shapeclustering.exe,text2image.exe,unicharset_extractor.exe,wordlist2dawg.exe,tesseract-uninstall.exe,winpath.exe"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

; Tesseract language data: English + orientation detection only (all 124 languages is ~340 MB).
Source: "..\bin\tesseract\tessdata\eng.traineddata"; DestDir: "{app}\bin\tesseract\tessdata"; Flags: ignoreversion
Source: "..\bin\tesseract\tessdata\osd.traineddata"; DestDir: "{app}\bin\tesseract\tessdata"; Flags: ignoreversion

; Optional face models (not in git; included only if present at build time).
Source: "..\models\*"; DestDir: "{app}\models"; \
  Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist

Source: "..\LICENSE";                   DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";                 DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\LEGAL_AND_ETHICS.md";  DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExe}"
Name: "{group}\Legal and ethics";     Filename: "{app}\docs\LEGAL_AND_ETHICS.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Created by the app at runtime, so the uninstaller doesn't know about it.
Type: files; Name: "{app}\.ocr_config.json"
