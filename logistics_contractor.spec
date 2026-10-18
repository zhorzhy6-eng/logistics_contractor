# -*- mode: python ; coding: utf-8 -*-
"""
Сборка LogisticsContractor (PyInstaller, --onedir).

Зачем spec, а не строка `pyinstaller ...`
-----------------------------------------
Параметров много, и половина из них — про пути и данные, которые при
сборке легко потерять молча. spec держит их в файле под git: что именно
попадает в сборку, видно в diff, а не в истории консоли.

Что важно знать про раскладку
-----------------------------
PyInstaller 6 в режиме --onedir кладёт ресурсы в `<сборка>/_internal`
(это и есть `sys._MEIPASS`), а .exe — уровнем выше. Программа об этом
знает: пути считаются в core/app_paths.py — ЧИТАЕМОЕ берётся из
`_MEIPASS`, ЗАПИСЫВАЕМОЕ (настройки, база, журналы, договоры) — рядом
с .exe. Поэтому в datas попадают только ресурсы, а `config/settings.json`
и `contracts.db` в сборку НЕ кладутся: это файлы пользователя.

Приватная часть (клиентские типы договоров)
-------------------------------------------
Она лежит отдельной папкой (`E:/Programmy/logistics_contractor_private`)
и в публичный git не входит. В сборку она попадает ЦЕЛИКОМ — как обычные
файлы в `logistics_contractor_private/`: private-модули импортируются в
работе по имени (`core.prompts.<ключ>`, `ui.windows.<ключ>`), и для
импорта нужны настоящие .py на диске, а не архив PYZ. Путь к папке можно
переопределить переменной окружения LOGISTICS_PRIVATE_ROOT.

Сборка:
    pyinstaller --noconfirm --clean logistics_contractor.spec

Результат:
    dist/LogisticsContractor/LogisticsContractor.exe
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ─────────────────────────────────────────────────────────────
# Корни
# ─────────────────────────────────────────────────────────────
#: SPECPATH — папка этого файла (её задаёт сам PyInstaller).
ROOT = Path(SPECPATH).resolve()

#: Приватная часть: переменная окружения главнее расположения по умолчанию.
PRIVATE_ROOT = Path(
    os.environ.get("LOGISTICS_PRIVATE_ROOT") or (ROOT.parent / "logistics_contractor_private")
).resolve()

APP_NAME = "LogisticsContractor"

#: Имя папки, под которым приватная часть лежит в сборке. Совпадает с
#: PRIVATE_DIRNAME в core/private_path.py и с расположением по умолчанию.
PRIVATE_PACKAGE = "logistics_contractor_private"

# ─────────────────────────────────────────────────────────────
# Отбор файлов
# ─────────────────────────────────────────────────────────────
#: Служебные папки, которые в сборке не нужны ни при каком раскладе.
SKIP_DIRS = {
    "__pycache__", ".git", ".github", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".idea", ".vscode", ".venv", "venv", "_tmp",
}

#: Мусор файловой системы.
SKIP_FILES = {"Thumbs.db", ".DS_Store"}

#: Скомпилированные файлы: их всё равно пересоберёт сам код.
SKIP_SUFFIXES = {".pyc", ".pyo"}


def collect_tree(source, destination, skip_names=()):
    """
    Добавляет папку в сборку как данные: список (откуда, куда).

    Файлы перечисляются поштучно, а не через `Tree`: так отбрасываются
    кэши и мусор, а размер сборки не растёт от `__pycache__`.
    """
    source = Path(source)
    if not source.is_dir():
        print("[spec] папка не найдена, пропущена:", source)
        return []

    skip = SKIP_DIRS | set(skip_names)
    collected = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in skip for part in relative.parts):
            continue
        if path.name in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
            continue
        collected.append((str(path), str(Path(destination) / relative.parent)))
    return collected


# ─────────────────────────────────────────────────────────────
# Данные сборки
# ─────────────────────────────────────────────────────────────
datas = []

# Публичные ресурсы: бланки, иконки и сертификаты, правовая база,
# инструкции оператора, файл справочника «Места выгрузок».
datas += collect_tree(ROOT / "templates", "templates")
datas += collect_tree(ROOT / "resources", "resources")
datas += collect_tree(ROOT / "data", "data")

# Конфигурация: список источников правовой базы нужен программе, а
# settings.json — файл пользователя (в сборку не попадает никогда).
for config_name in ("legal_sources.json", "settings.example.json"):
    config_file = ROOT / "config" / config_name
    if config_file.is_file():
        datas.append((str(config_file), "config"))
    else:
        print("[spec] нет файла конфигурации:", config_file)

# Тексты промптов Максимилиана и юридических помощников читаются как файлы.
datas += collect_tree(
    ROOT / "services" / "agent" / "core" / "prompts",
    "services/agent/core/prompts",
)

# Таблицу служебных слов core/agent/words.py обезличиватель читает ФАЙЛОМ
# (не импортом — там объяснено почему), поэтому она нужна рядом с кодом.
words_file = ROOT / "core" / "agent" / "words.py"
if words_file.is_file():
    datas.append((str(words_file), "core/agent"))

# Приватная часть: шаблоны, код, данные. tests/tools/docs в сборку не идут.
if PRIVATE_ROOT.is_dir():
    print("[spec] приватная часть:", PRIVATE_ROOT)
    for folder in ("templates", "core", "ui", "data"):
        datas += collect_tree(
            PRIVATE_ROOT / folder,
            "%s/%s" % (PRIVATE_PACKAGE, folder),
            skip_names=("tests", "tools", "docs", "_rebuild"),
        )
else:
    print("[spec] приватной части нет — собираем только «Экспедиторство»:", PRIVATE_ROOT)

# Словари pymorphy2 (нужны Natasha в обезличивателе): данные пакета, а не код.
datas += collect_data_files("pymorphy2_dicts_ru")

# ─────────────────────────────────────────────────────────────
# Скрытые импорты
# ─────────────────────────────────────────────────────────────
#: Модули, которые PyInstaller не находит статическим анализом.
#:
#: keyring: бэкенды подхватываются по имени (Windows Credential Manager),
#: а win32ctypes тянется уже из бэкенда — без записи здесь ключ не
#: сохранится в собранной программе.
#:
#: docxtpl/docx/openpyxl/PIL/pypdfium2/pytesseract — генерация договоров,
#: чтение PDF и картинок. Их хуки в pyinstaller-hooks-contrib включают
#: данные (например, шаблон python-docx).
#:
#: PyQt5.QtSvg — формат SVG для иконок (без него QIcon вернёт пустую).
hiddenimports = [
    "docxtpl",
    "docx",
    "jinja2",
    "openpyxl",
    "keyring",
    "keyring.backends.Windows",
    "win32ctypes.pywin32.win32cred",
    "win32ctypes.pywin32.pywintypes",
    "requests",
    "PIL",
    "pytesseract",
    "pypdfium2",
    "pypdfium2_raw",
    "bs4",
    "lxml",
    "lxml.etree",
    "PyQt5.QtSvg",
    "natasha",
    "pymorphy2",
    "dawg_python",
    "docopt",
]

#: Динамические импорты САМОГО ПРОЕКТА — их статический анализ не видит.
#:
#: Это не «на всякий случай»: первая же сборка без них дала отказы в
#: самопроверке — `ModuleNotFoundError: core.contracts.expediciya` и
#: `services.agent.tools.tutorial_hints`. Оба модуля импортируются по
#: имени уже в работе:
#:
#:   * типы договоров — реестром (core/contracts/registry.py);
#:   * промпты распознавания — по ключу типа (core/prompts/__init__.py);
#:   * старые имена агента — алиасами Этапа 5 (core/__init__.py);
#:   * модули распознавания — по имени шага (services/recognition/…);
#:   * клиенты API и хранилище секретов — ленивым `__getattr__` в core.
#:
#: Публичные модули перечислены здесь; приватные (`core.contracts.<тип>`,
#: `ui.windows.<тип>`, `core.prompts.<ключ>`) в архив не заворачиваются —
#: они приезжают обычными файлами в `logistics_contractor_private/` и
#: импортируются оттуда.
dynamic_imports = [
    # Типы договоров и их промпты (публичные).
    "core.contracts.expediciya",
    "core.contracts.perevozka",
    "core.prompts.perevozka",
    # Клиенты API и хранилище секретов (ленивые имена в core/__init__.py).
    "infrastructure.api.dadata",
    "infrastructure.api.gigachat",
    "infrastructure.api.tls",
    "infrastructure.secrets.store",
    # Модули распознавания документов (импорт по шагу).
    "services.recognition.fields",
    "services.recognition.import_service",
    "services.recognition.ocr",
    "services.recognition.reader",
    "services.recognition.recognizer",
    # Ядро и образ Максимилиана (алиасы core.agent.*).
    "services.agent.core.agent",
    "services.agent.core.llm",
    "services.agent.core.long_memory",
    "services.agent.core.memory",
    "services.agent.core.prompt",
    "services.agent.persona.emotional",
    "services.agent.persona.personas",
    # Инструменты агента: регистрируются реестром по имени.
    "services.agent.tools.analytics",
    "services.agent.tools.context",
    "services.agent.tools.dadata",
    "services.agent.tools.fill_parser",
    "services.agent.tools.legal",
    "services.agent.tools.legal_check",
    "services.agent.tools.legal_compare",
    "services.agent.tools.legal_hints",
    "services.agent.tools.proactivity",
    "services.agent.tools.proactivity_tools",
    "services.agent.tools.rag",
    "services.agent.tools.registry",
    "services.agent.tools.template_analyzer",
    "services.agent.tools.template_editor",
    "services.agent.tools.tutorial",
    "services.agent.tools.tutorial_hints",
]

hiddenimports += dynamic_imports

#: Публичные пакеты проекта — ЦЕЛИКОМ.
#:
#: Причина та же, что у списка выше, но шире: приватные окна и типы
#: импортируются по имени уже в работе, и их импорты публичного кода
#: статический анализ из `main.py` не видит. Без этого окно типа падало бы
#: у коллеги с `ModuleNotFoundError`, хотя в разработке всё работает.
#: Пакеты проекта небольшие: цена — единицы мегабайт в архиве.
PROJECT_PACKAGES = ("core", "db", "infrastructure", "services", "ui")

public_submodules = []
for _package in PROJECT_PACKAGES:
    public_submodules += collect_submodules(_package)

hiddenimports += public_submodules

#: Что в сборку не идёт: тесты, инструменты разработки и линтер.
excludes = [    "tests",
    "tools",
    "pytest",
    "pytestqt",
    "PyInstaller",
    "IPython",
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
]

# ─────────────────────────────────────────────────────────────
# Сборка
# ─────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(PRIVATE_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX выключен: его нет в системе сборки, а упакованные .exe чаще
    # попадают под ложные срабатывания антивирусов.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=(
        str(ROOT / "resources" / "icon.ico")
        if (ROOT / "resources" / "icon.ico").is_file()
        else None
    ),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

