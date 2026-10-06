# AGENTS.md — правила работы над logistics_contractor

> **Этот файл — часть проекта.** Его ведёт пользователь, агент только читает.
> Если агент видит расхождение (например, STATE.md устарел) — сообщает
> в отчёте, но не правит сам.
>
> **В начале каждой новой сессии — прочитать в этом порядке:**
>
> 1. `AGENTS.md` (этот файл) — целиком, § 1–7.
> 2. `docs/STATE.md` — целиком, чтобы понять текущий HEAD, число тестов
>    и что сделано / осталось.
> 3. Промпт текущего шага — короткий, без повторов § 2–5.
>
> Если хотя бы один пункт не выполнен — **СТОП, доложить, не начинать
> работу**.

## 1. Проект

- Рабочая папка: `E:\Prpgrammy\logistics_contractor`
- Сессионный workspace (НЕ проект): `E:\Python\logistics_contractor`
- GitHub: https://github.com/zhorzhy6-eng/logistics_contractor
- Стек: Python 3.14, PyQt5 5.15.11, python-docx, docxtpl, openpyxl==3.1.5,
  pytest, SQLite.
- Назначение: подготовка договоров перевозки / аренды / заявок (5 типов).

## 2. Команды

### Тесты

```bash
pytest -q -o addopts="" --basetemp=tests/_tmp/_pt_agent_new
```

- `-o addopts=""` — сбрасывает `-q` из `pytest.ini`.
- `--basetemp=...` — обязательно: `%TEMP%\pytest-of-Zhorzhy\pytest-current`
  бывает битым симлинком и роняет pytest.
- Перед прогоном **закрыть запущенный экземпляр приложения**
  (`pythonw.exe ...main.py`) — иначе `PermissionError` на `logs/app.log`.

**Qt в тестах:** `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`.

### Запуск приложения

```
Запуск программы.bat
```

## 3. Стиль работы

1. После каждого шага: **pytest → git commit → git push origin main → отчёт →
   СТОП**, дождаться подтверждения.
2. Комментарии в коде — **на русском**.
3. Логи — **без ПДн** (только имена полей, количества, длины).
4. При расхождении с ТЗ — **СТОП, не додумывать**.

### Формат отчёта

```
ШАГ X.Y: <название>
Коммит: <хеш> <сообщение>
Push: git push origin main → OK / FAIL
git status -sb: ## main...origin/main / [ahead N]
Файлы изменены: <список>
Файлы созданы: <список>
Тесты: pytest -o addopts="" → <число> passed
Новые тесты: <файл> → <число> passed
Проверка Экспедиторства: <число> passed
Ключевые проверки: <OK/FAIL по пунктам>
Замечания: <что обнаружено>
Следующий шаг: X.Y+1
```

## 4. Границы

### Always (делай без спроса)

- После каждого шага: pytest → commit → push → отчёт → СТОП.
- Логи без ПДн.
- Комментарии на русском.

### Ask first (спроси перед действием)

- Менять `core/contract_data.py`, `core/validator.py`, `core/pseudonymizer.py`.
- Запускать `tools/make_*.py` (перезапишет шаблоны).
- Добавлять зависимость в `requirements.txt`.

### Never do (не делай никогда)

- Не коммить ПДн (ФИО, паспорта, адреса, телефоны).
- Не трогать `core/contracts/{perevozka,formika,logistiks_rus,arenda_ts}/*`.
- Не использовать `lambda` в `connect` (цикл ссылок → 0xC0000005).
- Не выходить из offscreen `main.py` через `app.quit()` — только через
  `main._do_exit`.
- Не запускать `tools/make_*.py` после фиксации SHA256 в тестах.

## 5. Грабли (проверено на практике)

### 5.1. Qt / Python

- `lambda` в `connect` запрещены — цикл ссылок Python ↔ Qt.
- `main.py` offscreen — выход только через `main._do_exit`.
- Окна типов не закрываются крестиком, а прячутся. Реальное закрытие —
  `force_close()`.

### 5.2. Данные

- `0.0 == falsy` — использовать `"" if v is None else str(v).strip()`.
- `ContractData` теряет ключи при `coerce` — класть поля дважды.
- Промпт ↔ вкладки — разные имена ключей. Проверять стыки тестами.
- `docxtpl` оставляет пробел на месте Jinja-тега — в e2e-тестах `_flatten()`.
- Round-trip Excel: даты — `datetime`, числа — `int/float`. Нормализовать.

### 5.3. Инструменты и файлы

- `tools/make_*.py` не запускать после фиксации SHA256.
- Русские имена плейсхолдеров — точно как в тестах.
- ПДн в git — сразу в `.gitignore`.
- Ловушка openpyxl 3.1.5: `del ws.row_dimensions[row]` сдвигает ключи
  ниже удалённого (KeyError при втором удалении); `ws.delete_rows` не
  двигает merged-диапазоны. Обход в `core/contracts/zayavka/postprocess.py`.

### 5.4. Окружение

- `%TEMP%\pytest-of-Zhorzhy\pytest-current` — битый симлинк.
- Запущенное приложение блокирует `logs/app.log`.

## 6. Структура проекта (кратко)

```
core/                    # доменная логика, без Qt
  contract_data.py       # ContractData — нормализация данных (НЕ трогать)
  validator.py           # общий Validator (НЕ трогать)
  contracts/             # реестр типов + генераторы + валидаторы
    contract_types.py    # ContractType Enum
    registry.py          # ContractTypeRegistry
    factory.py           # GeneratorFactory / ValidatorFactory
    base_generator.py    # BaseContractGenerator + PostprocessStep
    base_validator.py    # BaseValidator
    perevozka/ formika/ logistiks_rus/ arenda_ts/ zayavka/
  prompts/               # промпты распознавания по типам
  dates.py               # единый парсер дат
  address_utils.py       # разбор адресов
  audit.py, trace.py     # логи без ПДн

db/                      # SQLite + логирование запросов
ui/                      # PyQt5: окна, вкладки, виджеты
  windows/               # окна типов (по подпакету на тип)
templates/               # DOCX/XLSX-шаблоны (только читать)
tools/                   # make_*_template.py, make_golden.py
tests/                   # pytest
docs/                    # ARCHITECTURE.md, ADR, STATE.md
```

## 7. Ссылки

- Архитектура: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Решения (ADR): [`docs/adr/`](docs/adr/)
- Текущее состояние: [`docs/STATE.md`](docs/STATE.md)