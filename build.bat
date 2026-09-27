@echo off
echo ====================================
echo Сборка приложения «Умный конструктор договоров»
echo ====================================
echo.

REM Проверяем наличие Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10+
    pause
    exit /b 1
)

REM Устанавливаем зависимости
echo [1/4] Установка зависимостей...
pip install -r requirements.txt

REM Собираем приложение
echo [2/4] Сборка приложения...
pyinstaller --noconfirm --clean ^
    --name "LogisticsContractor" ^
    --windowed ^
    --icon "resources\icons\app.ico" ^
    --add-data "templates;templates" ^
    --add-data "config;config" ^
    --add-data "resources;resources" ^
    --add-data "db;db" ^
    --add-data "core;core" ^
    --add-data "ui;ui" ^
    main.py

if %errorlevel% neq 0 (
    echo [ОШИБКА] Сборка завершилась с ошибкой
    pause
    exit /b 1
)

echo.
echo [3/4] Сборка успешно завершена!
echo Файл находится в: dist\LogisticsContractor\LogisticsContractor.exe
echo.

REM Создаём установщик (опционально)
echo [4/4] Запуск Inno Setup (опционально)... 
echo Для создания установщика используйте Inno Setup Compiler:
echo iscc installer_script.iss
echo.

echo Готово!
pause