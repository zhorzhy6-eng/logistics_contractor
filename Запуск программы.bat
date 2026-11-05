@echo off
setlocal
cd /d "%~dp0"

rem Data root is the SAME as the installed program:
rem base, settings, logs and contracts live in %APPDATA%\LogisticsContractor.
rem Without this line a source run uses tests\_tmp\_appdata (core\data_paths.py)
rem and shows an EMPTY database.
set "LOGISTICS_DATA_ROOT=%APPDATA%\LogisticsContractor"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit /b
)

where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "" /D "%~dp0" pythonw.exe "%~dp0main.py"
    exit /b
)

where pyw.exe >nul 2>&1
if not errorlevel 1 (
    start "" /D "%~dp0" pyw.exe "%~dp0main.py"
    exit /b
)

chcp 65001 >nul
echo Python не найден. Установите Python и добавьте его в PATH.
pause
exit /b 1
