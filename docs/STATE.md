# STATE — текущее состояние проекта

> Этот файл ведёт агент (см. `AGENTS.md` § 8). Обновляется в конце каждого
> шага. Архитектура — в [`ARCHITECTURE.md`](ARCHITECTURE.md).

**Обновлено:** 2026-10-06
**HEAD:** `2bcc1e9` (3.1.E.A.4: сводные e2e-тесты Хавалов)
**Тестов:** 3841 passed, exit 0
**Upstream:** `## main...origin/main` (синхронизировано)

---

## Что сделано

### Инфраструктура (шаги 0–8 рефакторинга архитектуры контрактов)

- ✅ Реестр типов договоров (`core/contracts/registry.py`,
  `contract_types.py`, `factory.py`).
- ✅ `BaseContractGenerator` + `PostprocessStep`
  (`core/contracts/base_generator.py`).
- ✅ `BaseValidator` (`core/contracts/base_validator.py`).
- ✅ Промпты по типам (`core/prompts/`) с fallback на дефолт GigaChat.
- ✅ Shim `core/contract_generator.py` → `PerevozkaGenerator` для обратной
  совместимости.
- ✅ UI: селектор типа договора в шапке, `WindowManager`, окна типов
  (скрытие вместо закрытия, `force_close()` при выходе).

### Типы договоров

| Тип | Ключ | Состояние |
|---|---|---|
| Экспедиторство (перевозка) | `perevozka` | ✅ полностью (UI + бэкенд + распознавание + golden-тесты) |
| Формика | `formika` | ✅ полностью (UI + бэкенд + промпт + распознавание) |
| Логистикс Рус | `logistiks_rus` | ✅ полностью (UI + бэкенд, ООО / ИП, промпт + распознавание) |
| Разовая аренда | `arenda_ts` | ✅ полностью (UI + бэкенд, ООО / ИП с НДС / ИП без НДС, промпт + распознавание) |
| Хавалы | `zayavka_excel` | 🚧 бэкенд готов, UI в работе |

### Хавалы (ЭТАП 3.1.E) — детально

- ✅ **A.0** — разбор образца `templates/Хавалы_образец.xlsx`.
- ✅ **A.1** — `tools/make_havaly_template.py` +
  `templates/shablon_havaly.xlsx` + `tests/test_havaly_template.py`.
- ✅ **A.2** — промпт `core/prompts/havaly.py` +
  `tests/test_prompts_havaly.py` (161 passed).
- ✅ **A.3** — `ZayavkaExcelGenerator` + `ZayavkaExcelValidator` +
  `TrimVehicleRowsStep` + тесты. Коммит `db3da82`, +176 тестов.
- ✅ **A.4** — сводные e2e-тесты. Коммит `2bcc1e9`, +32 теста.

### Безопасность

- ✅ Ключ GigaChat в системном хранилище (`keyring`).
- ✅ Логи без ПДн (`core/trace.py`, `core/audit.py`).
- ✅ Псевдонимизация `<<PERSON_N>>` — токен неделим.

---

## Что осталось

### Хавалы — бэкенд

- ⬜ **A.5** — финальная проверка бэкенда Хавалов (verification).

### Хавалы — UI

- ⬜ **B.1** — `ui/windows/havaly/data.py` (сборщик с маппингами).
- ⬜ **B.2** — 6 вкладок в `ui/windows/havaly/tabs/`.
- ⬜ **B.3** — окно на реальных вкладках.
- ⬜ **B.4** — финал + README.

### Документация

- ⬜ `docs/adr/` — ADR по ключевым решениям.
- ⬜ `docs/ARCHITECTURE.md` — писать после B.4.

---

## Следующий шаг

**3.1.E.A.5** — финальная проверка бэкенда Хавалов. Только verification,
без правок кода. Проверить: полный pytest, реестр, промпт, валидатор,
публичное API генератора, чтение живого образца, отсутствие ПДн в логах.

---

## Известные расхождения / замечания

- Плейсхолдер `<<PERSON_1>>` для ФИО целиком кладётся в
  `driver_last_name`, имя/отчество остаются пустыми. Токен **не делится**.
- `vat_rate` в бланк не пишется — ячейки нет. В `UNMAPPED_ZAYAVKA_FIELDS`.
- `customer_name`, `carrier_name` не читаются из файла, а подставляются
  константами (напечатаны в бланке).
- При 0 машин общие сведения в файл не попадают (в бланке это колонки
  таблицы). Валидатор предупреждает.
- `TITLE` есть только у Хавалов. У DOCX-типов название берётся из
  реестра (`spec.title`). Это разница контрактов, не поломка.
- `get_prompt("perevozka") is None` — норма, распознавание берёт
  дефолтный GigaChat.
- `_guard_same_file`: правка поля в тот же день в ту же папку падает
  с `ZayavkaTemplateError`. Возможно пересмотреть в B.3 — кандидат в ADR.

Технические ловушки openpyxl 3.1.5 (удаление строк, merged-ячейки) —
см. `AGENTS.md` § 5.3.

---

## Полезные ссылки

- [`../AGENTS.md`](../AGENTS.md) — правила работы.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — архитектура (в работе).
- [`adr/`](adr/) — Architecture Decision Records (в работе).
- GitHub: https://github.com/zhorzhy6-eng/logistics_contractor