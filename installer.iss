; ─────────────────────────────────────────────────────────────
; Установщик Logistics Contractor (Inno Setup 7)
;
; Собирается ПОСЛЕ PyInstaller:
;   1) pyinstaller --noconfirm --clean logistics_contractor.spec
;   2) "C:\Program Files\Inno Setup 7\ISCC.exe" installer.iss
;
; Результат: installer\LogisticsContractor_Setup_v1.4.1.exe
; Или одной командой: .\rebuild.ps1 (тесты → PyInstaller → Inno Setup).
;
; ⚠️ Нужен именно Inno Setup 7: директивы SetupArchitecture там новая, а
; значение x64 в ArchitecturesInstallIn64BitMode ЗАПРЕЩЕНО (вместо него
; x64compatible). На Inno Setup 6 этот файл не соберётся — там вместо
; SetupArchitecture=x64 ставится ArchitecturesInstallIn64BitMode=x64.
;
; ГДЕ ДАННЫЕ ПОЛЬЗОВАТЕЛЯ (важно для обновления)
; ----------------------------------------------
; База (contracts.db), настройки (config\settings.json), журналы (logs\) и
; готовые договоры (output\) с версии 1.4.1 лежат НЕ в {app}, а в
; %APPDATA%\LogisticsContractor (см. core/data_paths.py). Установщик эту
; папку не создаёт, не копирует и не удаляет — ни при установке, ни при
; удалении программы. Поэтому обновление поверх старой версии данные не
; теряет. Старые данные из {app} программа при первом запуске сама
; копирует в %APPDATA% (core/data_paths.py::migrate_old_data).
;
; Почему установка ПОЛЬЗОВАТЕЛЬСКАЯ (PrivilegesRequired=lowest)
; ------------------------------------------------------------
; Рядом с .exe остаются изменяемые файлы, которые программа пишет в работе:
; правовая база (db\legal_metadata.db), сертификаты и отчёт самопроверки
; (selftest.txt). В «Program Files» обычный пользователь писать не может,
; поэтому установка идёт в {autopf} режима «только для меня» —
; %LOCALAPPDATA%\Programs\LogisticsContractor. Заодно не нужны права
; администратора: коллега запускает установщик двойным щелчком.
; ─────────────────────────────────────────────────────────────

[Setup]
AppName=Logistics Contractor
AppVersion=1.4.1
AppVerName=Logistics Contractor 1.4.1
AppPublisher=Сергей
AppPublisherURL=https://github.com/zhorzhy6-eng/logistics_contractor
AppSupportURL=https://github.com/zhorzhy6-eng/logistics_contractor
DefaultDirName={autopf}\LogisticsContractor
DefaultGroupName=Logistics Contractor
DisableProgramGroupPage=yes
; per-user: без запроса прав администратора и с правом записи рядом с .exe
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=LogisticsContractor_Setup_v1.4.1
SetupIconFile=resources\icon.ico
UninstallDisplayIcon={app}\LogisticsContractor.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: checkedonce

[Files]
; Вся папка сборки: .exe, _internal с библиотеками и ресурсами.
; Данных пользователя здесь нет: база, настройки, журналы и договоры лежат
; в %APPDATA%\LogisticsContractor и в установку не входят.
Source: "dist\LogisticsContractor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Logistics Contractor"; Filename: "{app}\LogisticsContractor.exe"
Name: "{group}\Удалить Logistics Contractor"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Logistics Contractor"; Filename: "{app}\LogisticsContractor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LogisticsContractor.exe"; Description: "Запустить программу"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Удаляются только производные файлы, оставшиеся в папке программы.
; Данные пользователя в %APPDATA%\LogisticsContractor не удаляются НИКОГДА:
; этой папки нет в [UninstallDelete], и Inno Setup её не знает вовсе.
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
