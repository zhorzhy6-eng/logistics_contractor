@echo off
rem Сохранение секретного ключа DaData в системное хранилище Windows (keyring).
rem Файл записан в кодировке CP866 - cmd.exe читает его в текущей кодовой странице.
chcp 866 > nul
cd /d "%~dp0"

where python > nul 2>&1
if errorlevel 1 (
    echo.
    echo Python не найден в PATH.
    echo Установите Python 3 и при установке отметьте "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

python set_dadata_secret.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    if "%~1"=="--check" (
        echo Секретный ключ DaData в системном хранилище не найден.
    ) else (
        echo Не удалось сохранить секретный ключ DaData. Проверьте сообщения выше.
    )
)

echo.
pause
exit /b %RC%
