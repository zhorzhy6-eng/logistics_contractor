#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Каталог юридических правил: загрузка, поиск, проверка значения.

Зачем модуль
------------
Юридическая помощь Максимилиана состоит из пяти уровней, и первый из них —
проверка формы по нормам права. Нормы не должны жить в коде: их читает
юрист, а не программист. Поэтому правила лежат в JSON
(data/knowledge/legal/rules.json), а здесь — только чтение, разбор и
проверка значения.

Три правила каталога
--------------------
1. **Норма без источника не правило.** У каждого правила есть source
   (акт и статья), source_file (файл правовой базы) и quote — ДОСЛОВНАЯ
   цитата из этого файла. Тест сверяет цитату с файлом
   (tests/unit/core/test_legal_rules.py::test_quote_is_found_in_source_file),
   поэтому выдумать норму нельзя: цитаты в тексте закона не найдётся.
2. **Справочная норма — не проверка.** rule_type: reference описывает
   норму для объяснений (describe_rule), но форму по ней не проверяют:
   у неё нет поля формы.
3. **Нечего проверить — не нарушение.** Пустое поле или нечитаемое
   значение дают checked: False, а не ложное «нарушено»: оператор не
   должен получать замечание из-за незаполненного поля, которого в форме
   нет вовсе.

Типы правил
-----------
* exact     — значение должно быть равно value (ставка НДС);
* max       — не больше value;
* min       — не меньше value;
* required  — значение должно быть заполнено (для числа — больше нуля);
* validity  — дата в поле не должна быть в прошлом;
* reference — норма без проверки формы.

Модуль без Qt и без сети: его проверяют unit-тестами.
"""

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import app_paths

logger = logging.getLogger("core.legal_rules")

#: Переменная окружения с путём к каталогу правил (нужна тестам).
ENV_RULES_PATH = "LOGISTICS_LEGAL_RULES"

#: Типы правил, которые умеет проверять check_rule.
CHECK_TYPES = ("exact", "max", "min", "required", "validity")

#: Признаки «без НДС»: для правила НДС это допустимое значение, а не ошибка.
#: Одиночный «0» здесь НЕ ищется подстрокой: иначе «20%» тоже считалось бы
#: нулевой ставкой (проверено пробой — ставка 20% проходила как «без НДС»).
VAT_FREE_WORDS = ("без ндс", "б/ндс", "безндс", "не облагается")

#: Кэш каталога: файл читается один раз на процесс.
_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
#: Откуда прочитан кэш (путь): смена пути в тестах сбрасывает кэш.
_CACHE_PATH: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Пути и загрузка
# ─────────────────────────────────────────────────────────────

def project_root() -> Path:
    """
    Корень ЧТЕНИЯ ресурсов: в разработке — корень проекта, в .exe — папка
    ресурсов сборки (`_MEIPASS`).

    Отсюда ищется правовая база (`data/knowledge/legal/`). Раньше корень
    считался от `__file__`, и в собранном приложении указывал в архив
    PyInstaller, где никаких текстов актов нет (см. core/app_paths.py).
    """
    return app_paths.resource_path()


def default_rules_path() -> Path:
    """Путь к каталогу правил по умолчанию."""
    env_value = os.environ.get(ENV_RULES_PATH, "").strip()
    if env_value:
        return Path(env_value)
    return project_root() / "data" / "knowledge" / "legal" / "rules.json"


def load_rules(path: Optional[Any] = None, force: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Загружает каталог правил: имя правила → описание.

    Служебные ключи (_comment, _version, _note_*) в ответ не попадают: это
    пояснения для человека, а не правила.

    Ошибка чтения не роняет программу: возвращается пустой каталог, а в
    журнал пишется причина. Пустой каталог честно означает «проверять
    нечем» — инструмент так и скажет оператору.

    :param path: свой файл правил (тесты); None — файл проекта;
    :param force: перечитать даже из кэша.
    """
    global _CACHE, _CACHE_PATH

    # Активный каталог — тот, что уже загружен: get_rule и check_rule не
    # должны внезапно перечитывать файл проекта, если тест или проба
    # загрузили свой. Сбросить активный каталог можно reset_rules().
    if path is None and _CACHE is not None and not force:
        return _CACHE

    rules_path = Path(path) if path else default_rules_path()
    key = str(rules_path)

    if _CACHE is not None and _CACHE_PATH == key and not force:
        return _CACHE

    if not rules_path.is_file():
        logger.error("Каталог юридических правил не найден: %s", rules_path)
        _CACHE, _CACHE_PATH = {}, key
        return _CACHE

    try:
        raw = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.error("Каталог юридических правил не прочитан: %s", error)
        _CACHE, _CACHE_PATH = {}, key
        return _CACHE

    if not isinstance(raw, dict):
        logger.error("Каталог юридических правил должен быть объектом JSON")
        _CACHE, _CACHE_PATH = {}, key
        return _CACHE

    rules: Dict[str, Dict[str, Any]] = {}
    for name, body in raw.items():
        if str(name).startswith("_"):
            continue
        if not isinstance(body, dict):
            logger.warning("Правило %r пропущено: описание не объект", name)
            continue
        rule = dict(body)
        rule.setdefault("rule_type", "reference")
        rule.setdefault("severity", "warning")
        rule.setdefault("source", "")
        rule.setdefault("source_file", "")
        rule.setdefault("explanation", "")
        rule.setdefault("applies_to", [])
        rule.setdefault("name", str(name))
        rule.setdefault("title", str(rule.get("name") or name))
        rules[str(name)] = rule

    if not rules:
        logger.warning("Каталог юридических правил пуст: %s", rules_path)

    _CACHE, _CACHE_PATH = rules, key
    logger.info("Юридических правил загружено: %s", len(rules))
    return _CACHE


def reset_rules() -> None:
    """Забывает загруженный каталог (тесты, смена файла правил)."""
    global _CACHE, _CACHE_PATH
    _CACHE, _CACHE_PATH = None, None


def rule_names() -> Tuple[str, ...]:
    """Имена всех правил в порядке файла."""
    return tuple(load_rules())


# ─────────────────────────────────────────────────────────────
# Доступ к правилам
# ─────────────────────────────────────────────────────────────

def get_rule(name: str) -> Optional[Dict[str, Any]]:
    """Одно правило по имени (None — такого нет)."""
    return load_rules().get(str(name or "").strip())


def rules_for_type(contract_type: str) -> Dict[str, Dict[str, Any]]:
    """
    Правила для типа договора.

    У правила есть applies_to — список типов. Пустой список или пустой тип
    договора означают «правило общее»: лучше проверить лишнее, чем
    промолчать о норме, которая в этой форме тоже действует.

    :param contract_type: ключ типа (perevozka, arenda_ts…); пусто — все.
    """
    key = str(contract_type or "").strip().casefold()
    result: Dict[str, Dict[str, Any]] = {}
    for name, rule in load_rules().items():
        types = [str(item).strip().casefold() for item in rule.get("applies_to") or []]
        if not key or not types or key in types:
            result[name] = rule
    return result


def field_paths(rule: Dict[str, Any]) -> List[str]:
    """Пути поля формы, где может лежать значение правила."""
    paths: List[str] = []
    primary = str(rule.get("field") or "").strip()
    if primary:
        paths.append(primary)
    for alias in rule.get("field_aliases") or []:
        alias_text = str(alias or "").strip()
        if alias_text and alias_text not in paths:
            paths.append(alias_text)
    return paths


def resolve_field(values: Dict[str, Any],
                  rule: Dict[str, Any]) -> Tuple[str, Any]:
    """
    Поле формы со значением правила.

    Перебираются основное имя поля и синонимы: у «Экспедиторства» маршрут
    лежит в contract_tab.route, у клиентских типов — в route_tab.route,
    а правило одно.

    :return: (путь, значение); путь пустой — поля в форме нет.
    """
    for path in field_paths(rule):
        if path in (values or {}):
            return path, values[path]
    return "", None


def source_path(rule: Dict[str, Any]) -> Optional[Path]:
    """
    Файл правовой базы, откуда взята норма.

    Нужен тесту «цитата есть в источнике» и диалогу правовой базы: оператор
    должен иметь возможность открыть сам акт.
    """
    name = str(rule.get("source_file") or "").strip()
    if not name:
        return None
    return project_root() / "data" / "knowledge" / "legal" / "text" / name


def quote_found_in_source(rule: Dict[str, Any]) -> bool:
    """
    Есть ли цитата правила в его файле-источнике дословно.

    Пустой источник или цитата — False: правило без подтверждения не
    считается юридическим.
    """
    quote = str(rule.get("quote") or "").strip()
    path = source_path(rule)
    if not quote or path is None or not path.is_file():
        return False
    try:
        return quote in path.read_text(encoding="utf-8")
    except OSError:
        return False


# ─────────────────────────────────────────────────────────────
# Разбор значений
# ─────────────────────────────────────────────────────────────

def as_number(value: Any) -> Optional[float]:
    """
    Число из значения формы: «22%» → 22.0, «45» → 45.0, 22 → 22.0.

    None — значение не число (пустое, текст, «без НДС»): такое правило
    помечается «не проверено», а не «нарушено».
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    # Убираем знак процента и неразрывный пробел: в форме ставка — «22%».
    text = text.replace("%", "").replace("\u00a0", " ").strip()
    try:
        return float(text)
    except ValueError:
        return None


def as_date(value: Any) -> Optional[date]:
    """
    Дата из значения формы: строка ISO («2026-10-17»), datetime или date.

    None — дата не читается. Понимаются форматы %Y-%m-%d, %d.%m.%Y и
    российская запись с временем.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_blank(value: Any) -> bool:
    """Пустое ли значение (None, пустая строка, 0 у числа — «не заполнено»)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    return not str(value).strip()


def is_vat_free(value: Any) -> bool:
    """
    Похоже ли значение на «без НДС» (для правила ставки НДС).

    Отдельно проверяется числовой ноль: «0%» — это тоже «без НДС».
    Подстрокой ноль не ищем: «20%» иначе проходило бы как нулевая ставка.
    """
    text = str(value or "").strip().casefold()
    if not text:
        return False
    if any(word in text for word in VAT_FREE_WORDS):
        return True
    number = as_number(text)
    return number is not None and abs(number) < 1e-9


def format_date(value: Any) -> str:
    """Дата в русской записи (17.10.2026); нечитаемая — как есть."""
    parsed = as_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else str(value or "").strip()


# ─────────────────────────────────────────────────────────────
# Проверка значения
# ─────────────────────────────────────────────────────────────

def _result(rule_name: str, rule: Dict[str, Any], *,
            ok: bool, checked: bool, message: str = "",
            current: Any = None) -> Dict[str, Any]:
    """Ответ проверки в одном виде (его читают инструменты и диалог)."""
    return {
        "rule": rule_name,
        "name": rule.get("name") or rule_name,
        "ok": bool(ok),
        "checked": bool(checked),
        "rule_type": rule.get("rule_type") or "reference",
        "severity": rule.get("severity") or "warning",
        "source": rule.get("source") or "",
        "field": str(rule.get("field") or ""),
        "unit": rule.get("unit") or "",
        "current": current,
        "required": rule.get("value"),
        "message": message,
        "quote": rule.get("quote") or "",
        "explanation": rule.get("explanation") or "",
    }


def check_rule(name: str, value: Any) -> Dict[str, Any]:
    """
    Проверяет значение по правилу.

    :param name: имя правила из каталога;
    :param value: значение поля формы;
    :return: словарь проверки (см. _result). Нарушение — ok: False и
        заполненный message; «проверить нечем» — checked: False.

    Пример: check_rule("vat_rate", "20%") → нарушение с текстом
    «Значение 20%, а по НК РФ ст. 164 п. 3 — 22%.»
    """
    rule_name = str(name or "").strip()
    rule = get_rule(rule_name)
    if rule is None:
        return {
            "rule": rule_name, "ok": False, "checked": False,
            "rule_type": "unknown", "severity": "warning",
            "source": "", "field": "", "unit": "", "current": value,
            "required": None, "quote": "", "explanation": "",
            "message": f"правила «{rule_name}» нет в каталоге",
        }

    rule_type = str(rule.get("rule_type") or "reference")
    if rule_type == "reference":
        return _result(
            rule_name, rule, ok=True, checked=False, current=value,
            message="справочная норма: поле формы по ней не проверяется",
        )

    if rule_type not in CHECK_TYPES:
        return _result(
            rule_name, rule, ok=True, checked=False, current=value,
            message=f"тип правила «{rule_type}» проверять не умеем",
        )

    if rule_type == "required":
        return _check_required(rule_name, rule, value)
    if rule_type == "validity":
        return _check_validity(rule_name, rule, value)
    return _check_number(rule_name, rule, value, rule_type)


def _check_required(rule_name: str, rule: Dict[str, Any],
                    value: Any) -> Dict[str, Any]:
    """Значение заполнено (для числа — больше нуля)."""
    if not is_blank(value):
        return _result(rule_name, rule, ok=True, checked=True, current=value)

    what = str(rule.get("explanation") or rule.get("name") or rule_name).strip()
    message = (
        f"Поле «{rule.get('field') or rule_name}» не заполнено. "
        f"{what} Источник: {rule.get('source')}."
    )
    return _result(rule_name, rule, ok=False, checked=True,
                   message=" ".join(message.split()), current=value)


def _check_validity(rule_name: str, rule: Dict[str, Any],
                    value: Any) -> Dict[str, Any]:
    """Дата в поле не в прошлом (срок действия документа)."""
    parsed = as_date(value)
    if parsed is None:
        return _result(rule_name, rule, ok=True, checked=False, current=value,
                       message="дата не читается — проверка срока не выполнена")

    today = date.today()
    if parsed >= today:
        return _result(rule_name, rule, ok=True, checked=True, current=value)

    days = (today - parsed).days
    message = (
        f"Срок действия истёк {format_date(parsed)} ({days} дн. назад). "
        f"{str(rule.get('explanation') or '').strip()} "
        f"Источник: {rule.get('source')}."
    )
    return _result(rule_name, rule, ok=False, checked=True,
                   message=" ".join(message.split()), current=value)


def _check_number(rule_name: str, rule: Dict[str, Any], value: Any,
                  rule_type: str) -> Dict[str, Any]:
    """Числовое сравнение: exact / max / min."""
    if is_blank(value):
        return _result(rule_name, rule, ok=True, checked=False, current=value,
                       message="значение не заполнено — сравнивать не с чем")

    if rule_type == "exact" and rule.get("allow_free") and is_vat_free(value):
        # «Без НДС» — не ошибка: ставка просто не применяется.
        return _result(rule_name, rule, ok=True, checked=True, current=value,
                       message="без НДС — ставка не применяется")

    current = as_number(value)
    required = as_number(rule.get("value"))
    if current is None or required is None:
        return _result(rule_name, rule, ok=True, checked=False, current=value,
                       message="значение не число — проверить не удалось")

    unit = str(rule.get("unit") or "").strip()

    def show(number: float) -> str:
        return f"{number:g}" + (unit if unit else "")

    if rule_type == "exact":
        ok = abs(current - required) < 1e-9
        message = "" if ok else (
            f"Значение {show(current)}, а по {rule.get('source')} — "
            f"{show(required)}. {str(rule.get('explanation') or '').strip()}"
        )
    elif rule_type == "max":
        ok = current <= required + 1e-9
        message = "" if ok else (
            f"Значение {show(current)}, а по {rule.get('source')} — не больше "
            f"{show(required)}. {str(rule.get('explanation') or '').strip()}"
        )
    else:  # min
        ok = current >= required - 1e-9
        message = "" if ok else (
            f"Значение {show(current)}, а по {rule.get('source')} — не меньше "
            f"{show(required)}. {str(rule.get('explanation') or '').strip()}"
        )

    return _result(rule_name, rule, ok=ok, checked=True,
                   message=" ".join(message.split()), current=value)


# ─────────────────────────────────────────────────────────────
# Человеческое описание
# ─────────────────────────────────────────────────────────────

def describe_rule(name: str) -> str:
    """
    Человеческое описание правила: что требует, откуда и дословно.

    Формат — для диалога и ответа оператору::

        Ставка НДС, %: основная ставка — 22%.
        Источник: НК РФ ст. 164 п. 3 (2000-08-05-117-fz-nk-rf-ch-2.txt).
        «Налогообложение производится по налоговой ставке 22 процента…»
        Проверяется поле: contract_tab.vat_rate.

    Неизвестное правило — короткая строка «правила нет»: молчать нельзя,
    оператор спросил именно про него.
    """
    rule = get_rule(name)
    if rule is None:
        return f"Правила «{name}» в каталоге нет."

    parts: List[str] = []
    #: title — человеческое название правила («Ставка НДС»). Ключ каталога
    #: (vat_rate) в тексте для оператора выглядел бы машинным.
    title = str(rule.get("title") or rule.get("name") or name)
    unit = str(rule.get("unit") or "").strip()
    heading = title if title[:1].isupper() else title.capitalize()
    if unit:
        heading += f", {unit}"
    parts.append(f"{heading}: {str(rule.get('explanation') or '').strip()}")
    parts.append(f"Источник: {rule.get('source') or 'не указан'}"
                 f" ({rule.get('source_file') or 'файл не указан'}).")
    quote = str(rule.get("quote") or "").strip()
    if quote:
        parts.append(f"«{quote}»")
    paths = field_paths(rule)
    if paths:
        parts.append("Проверяется поле: " + ", ".join(paths) + ".")
    else:
        parts.append("Поле формы не проверяется: норма справочная.")
    return "\n".join(item for item in parts if item)


def describe_rules(names: Iterable[str]) -> str:
    """Описание нескольких правил подряд (для диалога «что проверяем»)."""
    texts = [describe_rule(name) for name in names]
    return "\n\n".join(text for text in texts if text)


__all__ = [
    "CHECK_TYPES",
    "ENV_RULES_PATH",
    "as_date",
    "as_number",
    "check_rule",
    "default_rules_path",
    "describe_rule",
    "describe_rules",
    "field_paths",
    "format_date",
    "get_rule",
    "is_blank",
    "is_vat_free",
    "load_rules",
    "project_root",
    "quote_found_in_source",
    "reset_rules",
    "resolve_field",
    "rule_names",
    "rules_for_type",
    "source_path",
]
