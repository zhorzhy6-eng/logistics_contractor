# rebuild.ps1 — пересборка exe и установщика одной командой.
#
# Использование:  .\rebuild.ps1
#
# Что делает, по шагам:
#   1/7  тесты unit;
#   2/7  тесты integration;
#   3/7  тесты ui;
#   4/7  тесты раскладки (новые файлы тестов обязаны попасть в стражи
#        раскладки — иначе они молча не собираются);
#   5/7  PyInstaller по logistics_contractor.spec → dist\LogisticsContractor;
#   6/7  Inno Setup по installer.iss → installer\LogisticsContractor_Setup_*.exe;
#   7/7  проверка результата и путь к установщику.
#
# Останавливается на первом сбое: собирать установщик из красных тестов
# нельзя — коллега получит сломанную программу.
#
# Про Inno Setup: в installer.iss нужны директивы Inno Setup 7
# (SetupArchitecture, x64compatible), поэтому скрипт ищет СНАЧАЛА 7, затем 6
# (на 6 этот .iss не соберётся — поиск 6 оставлен на случай, если файл
# когда-нибудь упростят). Свой путь к ISCC.exe можно задать переменной
# окружения ISCC_PATH.
#
# Про данные: база, настройки, журналы и готовые договоры лежат в
# %APPDATA%\LogisticsContractor (core/data_paths.py) — установщик их не
# трогает, обновление поверх старой версии данные не теряет.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$step = 0
function Write-Step([string]$Title) {
    $script:step++
    Write-Host ""
    Write-Host "═══ $($script:step)/7: $Title ═══" -ForegroundColor Cyan
}

# ─────────────────────────────────────────────────────────────
# 0. Проверки перед сборкой
# ─────────────────────────────────────────────────────────────

if (-not (Test-Path "logistics_contractor.spec")) {
    throw "Нет logistics_contractor.spec — запускать из корня проекта"
}
if (-not (Test-Path "installer.iss")) {
    throw "Нет installer.iss — запускать из корня проекта"
}

# Запущенная программа держит logs\app.log и мешает сборке (PermissionError).
$running = @(Get-Process -Name "LogisticsContractor" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    throw "Программа запущена (pid: $($running.Id -join ', ')) — закройте её и повторите"
}

# Экземпляр из исходников (pythonw main.py) держит журналы в папке данных:
# тесты упадут на занятом файле. Не убиваем — предупреждаем.
$pythonw = @(Get-Process -Name "pythonw" -ErrorAction SilentlyContinue)
if ($pythonw.Count -gt 0) {
    Write-Host "⚠ Запущены процессы pythonw (pid: $($pythonw.Id -join ', '))." `
        -ForegroundColor Yellow
    Write-Host "  Если это ваш main.py — закройте его, иначе тесты упадут" `
        -ForegroundColor Yellow
    Write-Host "  на занятом файле журнала." -ForegroundColor Yellow
}

# ─────────────────────────────────────────────────────────────
# 1/4. Тесты
# ─────────────────────────────────────────────────────────────

$basetemp = "tests/_tmp/_pt_build"

Write-Step "Тесты: unit"
pytest tests/unit/ -o addopts="" -q --basetemp="$basetemp`_unit"
if ($LASTEXITCODE -ne 0) { throw "Тесты unit упали (код $LASTEXITCODE)" }

Write-Step "Тесты: integration"
pytest tests/integration/ -o addopts="" -q --basetemp="$basetemp`_int"
if ($LASTEXITCODE -ne 0) { throw "Тесты integration упали (код $LASTEXITCODE)" }

Write-Step "Тесты: ui"
pytest tests/ui/ -o addopts="" -q --basetemp="$basetemp`_ui"
if ($LASTEXITCODE -ne 0) { throw "Тесты ui упали (код $LASTEXITCODE)" }

Write-Step "Тесты: раскладка"
pytest tests/test_tests_layout.py -o addopts="" -q --basetemp="$basetemp`_layout"
if ($LASTEXITCODE -ne 0) { throw "Тесты раскладки упали (код $LASTEXITCODE)" }

# ─────────────────────────────────────────────────────────────
# 5/7. PyInstaller
# ─────────────────────────────────────────────────────────────

Write-Step "PyInstaller"
pyinstaller --noconfirm --clean logistics_contractor.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller упал (код $LASTEXITCODE)" }

$exe = "dist\LogisticsContractor\LogisticsContractor.exe"
if (-not (Test-Path $exe)) {
    throw "PyInstaller не создал $exe"
}
$exeSize = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "exe: $exe ($exeSize МБ)" -ForegroundColor Green

# ─────────────────────────────────────────────────────────────
# 6/7. Inno Setup
# ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "═══ 6/7: Inno Setup ═══" -ForegroundColor Cyan
$iscc = $env:ISCC_PATH
if (-not $iscc) {
    $candidates = @(
        "C:\Program Files\Inno Setup 7\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    throw "Inno Setup не найден (нет ISCC.exe). Установите Inno Setup 7 или задайте ISCC_PATH"
}
Write-Host "ISCC: $iscc"
& $iscc "installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup упал (код $LASTEXITCODE)" }

# ─────────────────────────────────────────────────────────────
# 7/7. Результат
# ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "═══ 7/7: Готово ═══" -ForegroundColor Green

$installer = Get-ChildItem "installer\LogisticsContractor_Setup_*.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $installer) {
    throw "Установщик не найден в installer\"
}

$installerSize = [math]::Round($installer.Length / 1MB, 1)
Write-Host "Установщик: $($installer.FullName) ($installerSize МБ)" -ForegroundColor Green
Write-Host "Данные пользователя остаются в %APPDATA%\LogisticsContractor и при обновлении не теряются." `
    -ForegroundColor Green
