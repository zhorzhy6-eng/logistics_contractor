# AGENTS.md — правила работы над logistics_contractor

> **Этот файл — часть проекта.** Его ведёт пользователь. Если агент видит
> расхождение — сообщает в отчёте, но `AGENTS.md` не правит (см. § 4).
>
> **В начале каждой новой сессии — прочитать в этом порядке:**
>
> 1. `AGENTS.md` (этот файл) — целиком, § 1–8.
> 2. `docs/STATE.md` — целиком (HEAD, тесты, что сделано / осталось).
> 3. Промпт текущего шага.
>
> Если хотя бы один пункт не выполнен — **СТОП, доложить, не начинать
> работу**.

## 1. Проект

- Рабочая папка: `E:\Programmy\logistics_contractor`
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
- **Два pytest с общим `--basetemp` одновременно запускать нельзя.**

**Qt в тестах:** `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`.

### Запуск приложения

```
Запуск программы.bat
```

## 3. Стиль работы

1. После каждого шага: **pytest → обновить `docs/STATE.md` → git add →
   git commit → git push origin main → отчёт → СТОП**.
2. Комментарии в коде — **на русском**.
3. Логи — **без ПДн** (только имена полей, количества, длины).
4. При расхождении с ТЗ — **СТОП, не додумывать**.
5. `docs/STATE.md` **ведёт агент** — обновляет в конце каждого шага.
   `AGENTS.md` **ведёт пользователь** — агент его не правит.

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
STATE.md обновлён: HEAD=<хеш>, tests=<число>, шаг=<статус>
Следующий шаг: X.Y+1
```

## 4. Границы

### Always (делай без спроса)

- После каждого шага: pytest → обновить `docs/STATE.md` → commit → push →
  отчёт → СТОП.
- Логи без ПДн.
- Комментарии на русском.
- **Вести `docs/STATE.md`** (см. § 8): HEAD, число тестов, статус шага,
  следующий шаг, новые замечания.
- Коммитить `docs/STATE.md` **вместе** с изменениями шага (одним коммитом)
  или отдельным — на выбор агента, но **запушить до отчёта**.

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
- **Не править `AGENTS.md`** — его ведёт пользователь.
- Не удалять и не переименовывать `docs/STATE.md` — только обновлять
  содержимое по § 8.

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
- **openpyxl 3.1.5, удаление строк:** `del ws.row_dimensions[row]`
  сдвигает ключи ниже удалённого (KeyError при втором удалении);
  `ws.delete_rows` не двигает merged-диапазоны. Обход в
  `core/contracts/zayavka/postprocess.py`: `_drop_row_heights` +
  `_shift_merged_ranges`.
- **openpyxl, запись в merged-ячейку:** писать можно только в левый
  верхний угол. В генераторе Хавалов — `_anchor_cell`.
- **`_guard_same_file`:** правка поля в тот же день и в ту же папку
  падает с `ZayavkaTemplateError` — исходный файл не затирается.
- **Имя файла Хавалов зависит только от даты заявки:**
  `output/Заявка_Хавалы_<дата ISO>.xlsx`.
- **`STATE.md` содержит HEAD коммита, в котором он же коммитится.**
  Если делать `--amend`, HEAD меняется, а внутри файла остаётся старый —
  push отклоняется. Решение: не пиши HEAD = будущий коммит; пиши HEAD
  **предыдущего** коммита, а актуальный HEAD — из `git rev-parse HEAD`.

### 5.4. Окружение

- `%TEMP%\pytest-of-Zhorzhy\pytest-current` — битый симлинк.
- Запущенное приложение блокирует `logs/app.log`.
- **Два pytest с общим `--basetemp` одновременно — нельзя.**
- **Путь проекта:** `E:\Programmy\logistics_contractor` (был
  `E:\Prpgrammy\logistics_contractor`). При переименовании папки git
  может ругаться `dubious ownership` — лечится
  `git config --global --add safe.directory E:/Programmy/logistics_contractor`.

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

## 8. `docs/STATE.md` — ведёт агент

### Что обновлять в конце каждого шага

1. **Шапка файла:**
   - `**Обновлено:**` — сегодняшняя дата (YYYY-MM-DD).
   - `**HEAD:**` — хеш коммита **только что закрытого шага** и его название.
   - `**Тестов:**` — итог **последнего полного прогона**.
   - `**Upstream:**` — `## main...origin/main` (или `[ahead N]`, если push
     ещё не сделан — но push должен быть сделан до отчёта).

2. **Раздел «Что сделано»:**
   - закрытый шаг перевести из `🚧` в `✅`, добавить одну строку с коммитом
     и числом новых тестов;
   - если шаг добавил новый файл — упомянуть его.

3. **Раздел «Что осталось»:**
   - убрать закрытый шаг;
   - если следующий шаг уже понятен — оставить `⬜`.

4. **Раздел «Следующий шаг»:**
   - заголовок с номером следующего шага;
   - краткий контекст (2–4 строки).

5. **Раздел «Известные расхождения / замечания»:**
   - **добавить** новые ловушки;
   - **удалить** потерявшие актуальность;
   - **не дублировать** `AGENTS.md` § 5 — там ссылка.

### Чего НЕ делать

- Не менять структуру разделов.
- Не удалять закрытые шаги из «Что сделано».
- Не оставлять `docs/STATE.md` в состоянии `[ahead N]` в отчёте —
  push должен быть сделан до того, как отчёт показан пользователю.

### Порядок действий в конце шага

1. Прогнать полный `pytest` — зафиксировать число.
2. Обновить `docs/STATE.md` по § 8.
3. `git add` — все изменённые файлы шага + `docs/STATE.md`.
4. `git commit -m "3.1.E.N: <название> + STATE.md"`.
5. `git push origin main`.
6. `git status -sb` — убедиться, что `## main...origin/main`.
7. Отчёт пользователю.

**Если на шаге 5 push упал (`rejected`) — СТОП, доложить, не разрешать
конфликт самостоятельно.**

**Особое:** не пиши в `docs/STATE.md` HEAD = будущий коммит. Пиши HEAD
**предыдущего** коммита. Актуальный HEAD всегда можно узнать:
`git rev-parse HEAD`. Иначе получится порочный круг: amend меняет хеш,
а внутри файла остаётся старый — push отклоняется.