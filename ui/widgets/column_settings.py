#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Состав колонок таблиц: какие колонки показывать (ШАГ FIX-6, часть B3).

Зачем отдельный модуль. Таблиц «Перевозимые авто» в проекте пять, и у
каждой свой состав колонок. Показывать их все сразу незачем: оператору
нужны то VIN с маркой, то тип ТС, то год выпуска. Здесь живёт описание
колонки (`ColumnSpec`), чтение/запись выбора (QSettings) и раскладка
колонок по позициям таблицы.

Как это устроено в таблице: КОЛОНКИ НЕ УДАЛЯЮТСЯ, а ПРЯЧУТСЯ
(`QTableView.setColumnHidden`). Скрытая колонка остаётся в модели —
номер колонки не меняется, данные в ней сохраняются, а `get_data()`
читает их как обычно. Удалять и пересобирать колонки было бы дороже:
сдвинулись бы все номера, а вместе с ними — делегаты, комбобоксы и
сохранённая раскладка.

Ключ QSettings — один на таблицу (`storage_key`); скрытые колонки
хранятся списком ключей. Хранилище то же, что у раскладки ширин
(`ui/widgets/table_helpers.py::_settings`): INI-файл в настройках
пользователя, тесты перенаправляют его одной фикстурой.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from PyQt5.QtWidgets import QTableWidget

logger = logging.getLogger("ui.widgets.column_settings")

#: Префикс ключей QSettings этого модуля.
SETTINGS_PREFIX = "ui/columns/"


@dataclass(frozen=True)
class ColumnSpec:
    """Одна колонка таблицы: ключ, заголовок и признак обязательности."""

    key: str
    title: str
    required: bool = False
    default_visible: bool = True

    @property
    def optional(self) -> bool:
        """True, если колонку можно скрыть (обязательные не скрываются)."""
        return not self.required


def make_specs(
    columns: Iterable[Union[ColumnSpec, Tuple[str, str], Tuple[str, str, bool]]],
) -> Tuple[ColumnSpec, ...]:
    """
    Приводит описание колонок к кортежу ColumnSpec.

    Принимает и готовые ColumnSpec, и короткие пары («ключ», «Заголовок»),
    и тройки («ключ», «Заголовок», обязательная): так таблица описывает
    свои колонки одной строкой на колонку.
    """
    specs: List[ColumnSpec] = []
    for column in columns:
        if isinstance(column, ColumnSpec):
            specs.append(column)
            continue

        key, title = column[0], column[1]
        required = bool(column[2]) if len(column) > 2 else False
        specs.append(ColumnSpec(key=str(key), title=str(title), required=required))
    return tuple(specs)


def column_keys(specs: Sequence[ColumnSpec], *, visible_only: bool = False,
                hidden: Iterable[str] = ()) -> List[str]:
    """Ключи колонок: все или только видимые (для тестов и диагностики)."""
    hidden_set = set(hidden)
    if not visible_only:
        return [spec.key for spec in specs]
    return [spec.key for spec in specs if spec.key not in hidden_set]


def optional_keys(specs: Sequence[ColumnSpec]) -> List[str]:
    """Ключи необязательных колонок — из них состоит меню выбора."""
    return [spec.key for spec in specs if spec.optional]


def default_hidden(specs: Sequence[ColumnSpec]) -> List[str]:
    """Колонки, скрытые по умолчанию (необязательные с default_visible=False)."""
    return [spec.key for spec in specs if spec.optional and not spec.default_visible]


def load_hidden(table: QTableWidget, storage_key: str,
                specs: Sequence[ColumnSpec]) -> List[str]:
    """
    Скрытые колонки таблицы: из QSettings, иначе — по умолчанию.

    Битый или устаревший список (колонки с таким ключом больше нет)
    отбрасывается молча: интерфейс не должен падать из-за настроек.
    """
    if not storage_key:
        return default_hidden(specs)

    from ui.widgets.table_helpers import _settings  # локально: без цикла импортов

    stored = _settings().value(SETTINGS_PREFIX + storage_key)
    if stored is None:
        return default_hidden(specs)

    if isinstance(stored, str):
        stored = [stored] if stored else []
    if not isinstance(stored, (list, tuple)):
        logger.warning(
            "Состав колонок: значение %r не разобрано (ключ=%s)", stored, storage_key
        )
        return default_hidden(specs)

    known = {spec.key for spec in specs}
    valid = [str(item) for item in stored if str(item) in known]
    unknown = [str(item) for item in stored if str(item) not in known]
    if unknown:
        logger.debug(
            "Состав колонок: в настройках есть неизвестные ключи %s (ключ=%s)",
            unknown, storage_key,
        )
    return valid


def save_hidden(table: QTableWidget, storage_key: str, hidden: Iterable[str]) -> None:
    """Записывает список скрытых колонок в QSettings."""
    if not storage_key:
        return

    from ui.widgets.table_helpers import _settings  # локально: без цикла импортов

    settings = _settings()
    settings.setValue(SETTINGS_PREFIX + storage_key, [str(key) for key in hidden])
    settings.sync()
    logger.debug(
        "Состав колонок сохранён: ключ=%s, скрыто=%s", storage_key, len(list(hidden))
    )


def visible_headers(table: QTableWidget) -> List[str]:
    """
    Заголовки ВИДИМЫХ колонок — то, что оператор видит в шапке таблицы.

    Для тестов и диагностики: скрытые колонки в списке не участвуют.
    """
    return [
        table.horizontalHeaderItem(index).text()
        for index in range(table.columnCount())
        if not table.isColumnHidden(index)
        and table.horizontalHeaderItem(index) is not None
    ]


def resolve_selection(
    specs: Sequence[ColumnSpec],
    checked: Dict[str, bool],
    required_keys: Iterable[str],
) -> List[str]:
    """
    Считает список скрытых колонок по галочкам меню.

    Обязательные колонки скрыть нельзя: даже если галочку как-то сняли,
    колонка остаётся видимой. Возвращается список СКРЫТЫХ ключей — в том
    виде, в каком он ложится в настройки.
    """
    required = set(required_keys)
    hidden: List[str] = []
    for spec in specs:
        if spec.key in required:
            continue
        if not checked.get(spec.key, True):
            hidden.append(spec.key)
    return hidden
