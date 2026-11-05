#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Справочник салонов («Места выгрузок»): чтение файла и первичная заливка.

Графы файла ищутся по ЗАГОЛОВКАМ, а не по номерам (порядок колонок у
заказчика может меняться). Файл лежит в data/, контакты из него не читаются:
в справочнике им места нет, а телефоны и e-mail — персональные данные.

Заливка запускается при инициализации базы (db/database.py::init_database →
salons.load_if_empty). Путь передаётся параметром: фасад берёт его из своего
модуля, где тесты подменяют `salons_xlsx_path`.
"""

import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Tuple

from core import app_paths
from core.address_utils import split_salon_name
from db.connection import get_connection
from db.crud.addresses import import_addresses_from_list

logger = logging.getLogger("db.salons")

#: Файл справочника салонов («Места выгрузок»), который раскладывается по
#: заголовкам граф. Лежит в data/ — там же, где остальные входные файлы.
SALONS_XLSX_NAME = "spravochnik_mest_vygruzki.xlsx"

#: Ключевые слова заголовков файла справочника салонов → имена полей.
#: Сравнение идёт по подстроке в шапке (регистр не важен): «КОД (второй)»
#: и «КОД» — одна и та же графа, а «Адрес доставки автомобилей» находится
#: по слову «Адрес доставки».
#:
#: «Наименование» принимается наравне с «Юр. Лицо»: у файлов операторов
#: встречается шапка «Наименование салона». Голый «Адрес» обрабатывается
#: отдельно (_is_address_header), чтобы не спутать его с «Юридическим
#: адресом» — тот в файле «Места выгрузок» идёт раньше адреса доставки.
SALON_COLUMN_KEYS = (
    ("salon_code", ("код",)),
    ("salon_inn", ("инн",)),
    ("salon_name", ("юр. лицо", "юридическое лицо", "наименование")),
    ("salon_city", ("город",)),
    ("address", ("адрес доставки",)),
)

#: Слова, по которым «Адрес» — это НЕ адрес доставки (юридический/почтовый).
ADDRESS_HEADER_STOP_WORDS = ("юридическ", "почтов", "регистрац", "жительства")

#: Ключевые слова по полю — чтобы не собирать словарь на каждый заголовок.
_SALON_COLUMN_KEYWORDS = dict(SALON_COLUMN_KEYS)

#: Графы, без которых файл не считается справочником салонов: одного
#: «Адреса» мало — иначе старый одноколоночный файл перехватывался бы
#: этим путём вместо запасного чтения первой колонки.
_SALON_FIELDS = {"salon_name", "salon_code", "salon_inn", "salon_city"}


def salons_xlsx_path() -> str:
    """
    Путь к файлу справочника салонов в папке data/.

    Читается из РЕСУРСОВ сборки (core/app_paths.py): в .exe файл приезжает
    вместе с программой в `_MEIPASS`, а не рядом с модулем.
    """
    return str(app_paths.resource_path(os.path.join("data", SALONS_XLSX_NAME)))


# ─────────────────────────────────────────────────────────────
# Чтение файла
# ─────────────────────────────────────────────────────────────

def _normalize_header(value: Any) -> str:
    """Заголовок графы без регистра, «ё» и лишних пробелов."""
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .:;")


def _is_address_header(text: str) -> bool:
    """
    Это заголовок адреса доставки?

    «Адрес» и «Адрес салона» — да; «Юридический адрес» и «Почтовый адрес» —
    нет: в файле «Места выгрузок» юридический адрес идёт раньше, и раньше
    он ошибочно забирал бы графу доставки.
    """
    if "адрес доставки" in text:
        return True
    if text == "адрес":
        return True
    if text.startswith("адрес ") or text.endswith(" адрес"):
        return not any(word in text for word in ADDRESS_HEADER_STOP_WORDS)
    return False


def _header_matches(title: Any, field: str) -> bool:
    """Заголовок графы относится к полю справочника салонов?"""
    text = _normalize_header(title)
    if not text:
        return False
    if field == "address":
        return _is_address_header(text)
    return any(keyword in text for keyword in _SALON_COLUMN_KEYWORDS.get(field, ()))


def _find_salon_columns(header: Tuple[Any, ...]) -> Dict[str, int]:
    """
    Индексы граф файла справочника салонов по ключевым словам шапки.

    Возвращает {имя поля: индекс колонки}. Ключ «код» ищется ПЕРВЫМ
    совпадением по порядку граф (в файле две графы КОД — берём левую).
    Если ни одного ключа не найдено, словарь пуст: файл не похож на
    справочник салонов, импортировать его построчно нельзя.
    """
    found: Dict[str, int] = {}
    for index, title in enumerate(header):
        for field, _keywords in SALON_COLUMN_KEYS:
            if field in found:
                continue
            if _header_matches(title, field):
                found[field] = index
                break
    return found


def read_salons_rows(path: str) -> List[Dict[str, str]]:
    """
    Читает файл «Места выгрузок» и отдаёт строки справочника салонов.

    Графы ищутся по ЗАГОЛОВКАМ (первая непустая строка), а не по номерам:
    порядок колонок в файле заказчика может меняться. Понимаются оба
    варианта шапки:

      * полный файл «Места выгрузок»: КОД, ИНН, Юр. Лицо, Город,
        Адрес доставки автомобилей;
      * короткий файл оператора: «Наименование салона» и «Адрес».

    Лишние графы (e-mail, телефоны, комментарии, региональный менеджер) не
    читаются: в справочнике им места нет, а контакты — персональные данные.

    Перед возвратом адрес чистится правилом core.address_utils.split_salon_name:
    наименование, заклеенное в хвост адреса, переезжает в salon_name, а
    адрес остаётся адресом.

    Возвращает список словарей {address, salon_name, salon_code, salon_inn,
    salon_city, date, time_window}. Пустой список — либо файла нет, либо
    шапка не распознана (тогда вызывающий код импорт не делает).
    """
    if not path or not os.path.exists(path):
        logger.info(f"Файл справочника салонов не найден: {path}")
        return []

    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl не установлен — справочник салонов не загружен")
        return []

    try:
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001 — файл может быть занят или битым
        logger.warning(f"Не удалось открыть справочник салонов ({type(e).__name__})")
        return []

    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)

        columns: Dict[str, int] = {}
        for row in rows:
            if row is None or all(value is None or not str(value).strip() for value in row):
                continue
            columns = _find_salon_columns(row)
            break

        if not columns or "address" not in columns:
            logger.warning(
                "В файле справочника салонов не найдена ожидаемая шапка "
                "(Наименование / КОД / ИНН / Город / Адрес) — импорт пропущен"
            )
            return []

        # Одного «Адреса» мало: старый одноколоночный файл должен идти
        # запасным путём чтения первой колонки, а не сюда.
        if not _SALON_FIELDS & set(columns):
            logger.warning(
                "В файле нет ни одной графы салона (Наименование / КОД / ИНН / "
                "Город) — импорт пропущен"
            )
            return []

        def cell(row: Tuple[Any, ...], field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(row):
                return ""
            value = row[index]
            return "" if value is None else str(value).strip()

        result: List[Dict[str, str]] = []
        for row in rows:
            if row is None:
                continue
            address = cell(row, "address")
            if not address:
                continue

            # Наименование из колонки важнее найденного в адресе: колонка
            # заполняется оператором, хвост адреса — наследие выгрузки.
            salon_name = cell(row, "salon_name")
            clean_address, salon_name = split_salon_name(address, salon_name)
            if not clean_address:
                continue

            result.append({
                "address": clean_address,
                "salon_name": salon_name,
                "salon_code": cell(row, "salon_code"),
                "salon_inn": cell(row, "salon_inn"),
                "salon_city": cell(row, "salon_city"),
                "date": "",
                "time_window": "",
            })

        return result
    finally:
        try:
            workbook.close()
        except Exception:  # noqa: BLE001 — закрытие не должно ломать импорт
            pass


# ─────────────────────────────────────────────────────────────
# Первичная заливка справочника
# ─────────────────────────────────────────────────────────────

def load_if_empty(path: str) -> int:
    """
    Первый запуск: если справочник салонов ещё не залит, читает файл.

    «Не залит» — это два случая:
      * таблица выгрузок пуста (чистая база) — заливается всё;
      * записи есть, но НИ У ОДНОЙ нет кода салона (база старше FIX-2.2:
        адреса набирались вручную или прежним импортом) — тогда файл
        догружает недостающие поля и добавляет записи, которых нет.
        Значения существующих записей не теряются: адрес — ключ уникальности,
        а пустые поля только дополняются (см. import_addresses_from_list).

    Если файла нет или в нём не распознана шапка — тихо ничего не делаем:
    приложение работает и без него, справочник наполняется вручную.

    :param path: путь к файлу справочника «Места выгрузок».
    :return: сколько записей импортировано (0 — импорта не было)
    """
    conn = get_connection()
    try:
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM address_book WHERE point_type = 'unloading'"
            ).fetchone()[0] or 0
        )
        with_code = int(
            conn.execute(
                "SELECT COUNT(*) FROM address_book "
                "WHERE point_type = 'unloading' AND COALESCE(salon_code, '') != ''"
            ).fetchone()[0] or 0
        )
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось проверить справочник выгрузок: {e}")
        return 0
    finally:
        conn.close()

    if total and with_code:
        logger.debug(
            f"Справочник салонов уже загружен ({with_code} записей с кодом)"
        )
        return 0

    rows = read_salons_rows(path)
    if not rows:
        logger.info(
            "Справочник салонов не загружен автоматически "
            "(нет файла или не распознана шапка)"
        )
        return 0

    added = import_addresses_from_list("unloading", rows)
    logger.info(
        f"Загружен справочник салонов: {len(rows)} записей, новых {added} "
        f"(было в базе: {total})"
    )
    return added


__all__ = [
    "SALONS_XLSX_NAME",
    "SALON_COLUMN_KEYS",
    "load_if_empty",
    "read_salons_rows",
    "salons_xlsx_path",
]

