#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты имени и пути папки рейса (ШАГ «Папка на рейс»).

Проверяется ядро раскладки готовых документов:
core/contracts/paths.py — contract_folder_name() и resolve_contract_folder().

Зачем это отдельный шаг: раньше все договоры падали в output/ вперемешку, и
найти нужный рейс было нельзя. Теперь имя папки считается из ОДНИХ И ТЕХ ЖЕ
полей данных (водитель, маршрут, дата договора), поэтому договор
Экспедиторства и зеркалённая в Формику / Логистикс заявка оказываются в одной
папке безо всякой связи между окнами.

Проверяется:
  * формат имени: <Фамилия_И.О.>_<маршрут>_<ДД.ММ.ГГГГ>;
  * водитель: три слова → «Фамилия_И.И.», два → «Фамилия_И.», одно →
    фамилия, пусто → «Без_водителя»;
  * маршрут: стрелки → дефис, обрезка до 40 символов, санитайзинг;
  * дата: ISO → ДД.ММ.ГГГГ, мусор и пусто → сегодня;
  * папка создаётся, существующая ПЕРЕИСПОЛЬЗУЕТСЯ (без суффикса «_2»).

Все данные синтетические, реальных ПДн нет. Папки создаются во временной
папке и удаляются за тестом.
"""

import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from core.contracts.paths import (
    ROUTE_LIMIT,
    contract_folder_name,
    contract_output_path,
    resolve_contract_folder,
)

#: Ожидаемое имя папки целиком: буквы, цифры, точка, дефис, подчёркивание.
SAFE_NAME = re.compile(r"^[\w.\-]+$", re.UNICODE)

TODAY = datetime.now().strftime("%d.%m.%Y")

#: Шаблон «Без_водителя...»: столько подчёркиваний даёт сам шаблон имени.
NO_DRIVER = "Без_водителя"


@pytest.fixture
def output_dir():
    """Корень вывода теста: временная папка, удаляется после проверки."""
    folder = Path(tempfile.mkdtemp(prefix="pt_paths_"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# Имя папки
# ─────────────────────────────────────────────────────────────

def test_folder_name_full_data():
    """Полные данные: фамилия с двумя инициалами, маршрут, дата ISO."""
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Чехов — Санкт-Петербург", "date": "2026-10-08"},
    }

    assert contract_folder_name(data) == "Иванов_И.И._Чехов-Санкт-Петербург_08.10.2026"


def test_folder_name_two_words_driver():
    """Два слова в ФИО — одна инициала, третьей буквы не выдумывается."""
    data = {
        "driver": {"full_name": "Петров Пётр"},
        "contract": {"route": "Москва-Тверь", "date": "2026-10-08"},
    }

    assert contract_folder_name(data) == "Петров_П._Москва-Тверь_08.10.2026"


def test_folder_name_empty_driver():
    """Водителя нет — папка всё равно есть: «Без_водителя»."""
    data = {
        "driver": {"full_name": ""},
        "contract": {"route": "Москва-Тверь", "date": "2026-10-08"},
    }

    assert contract_folder_name(data) == "Без_водителя_Москва-Тверь_08.10.2026"


def test_folder_name_empty_route():
    """Маршрута нет — имя короче на одну часть, лишних подчёркиваний нет."""
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "", "date": "2026-10-08"},
    }

    name = contract_folder_name(data)
    assert name == "Иванов_И.И._08.10.2026"
    assert "__" not in name


def test_folder_name_long_route_truncated():
    """
    Длинный маршрут обрезается до 40 символов.

    Полный текст маршрута есть в самом договоре: в имени папки он только
    мешает. Обрезка идёт ДО замены недопустимых символов — иначе «/» успел бы
    стать подчёркиванием и вылезти за границу.
    """
    route = "Москва/Тверь - Санкт-Петербург и обратно длинный маршрут"
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": route, "date": "2026-10-08"},
    }

    name = contract_folder_name(data)
    route_part = name.split("_", 2)[2].rsplit("_", 1)[0]

    assert len(route_part) == ROUTE_LIMIT
    assert route_part == "Москва_Тверь-Санкт-Петербург_и_обратно_д"
    assert "длинный" not in name


def test_folder_name_arrow_normalized():
    """Стрелки и тире маршрута становятся дефисом."""
    for route in (
        "Чехов — Санкт-Петербург",
        "Чехов → Санкт-Петербург",
        "Чехов – Санкт-Петербург",
    ):
        data = {
            "driver": {"full_name": "Иванов Иван Иванович"},
            "contract": {"route": route, "date": "2026-10-08"},
        }
        assert "_Чехов-Санкт-Петербург_" in contract_folder_name(data), route


def test_folder_name_one_word_driver():
    """Одно слово — только фамилия: инициал брать неоткуда."""
    data = {
        "driver": {"full_name": "Иванов"},
        "contract": {"route": "Москва-Тверь", "date": "2026-10-08"},
    }

    assert contract_folder_name(data) == "Иванов_Москва-Тверь_08.10.2026"


def test_folder_name_keeps_only_safe_characters():
    """В имени папки не остаётся запрещённых в Windows символов."""
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {
            "route": 'Москва: Тверь? <тест> | "кавычки" \\ слэш',
            "date": "2026-10-08",
        },
    }

    name = contract_folder_name(data)
    assert SAFE_NAME.match(name)
    assert not set(name) & set(':*?"<>|\\/')


def test_folder_name_empty_input_gives_folder():
    """Пустой вход (None, {}, пустые блоки) не роняет сборку имени."""
    for data in (None, {}, {"driver": None, "contract": None},
                 {"driver": {}, "contract": {}}, "мусор"):
        name = contract_folder_name(data)
        assert name
        assert SAFE_NAME.match(name)
        assert name.endswith(TODAY)
        assert NO_DRIVER in name


@pytest.mark.parametrize("bad_date", ["", "мусор", "2026-02-30", None, "2026-13-45"])
def test_folder_name_bad_date_is_today(bad_date):
    """
    Дата не разобралась — берётся сегодняшняя, папка всё равно создаётся.

    В параметризации — даты, которых НЕ существует: 30 февраля и 45-й месяц.
    Раньше здесь стояло «08.10.2026» — валидная дата, просто вчерашняя: тест
    проходил ровно один день (в день, когда разбираемая дата совпадала с
    сегодняшней) и падал сам по себе при смене дня. Проверяться должна
    ИМЕННО неразобранная дата, а не вчерашняя.
    """
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Москва-Тверь", "date": bad_date},
    }

    assert contract_folder_name(data) == f"Иванов_И.И._Москва-Тверь_{TODAY}"


def test_folder_name_takes_first_ten_characters_of_date():
    """Дата со временем («2026-10-08T09:00:00») читается как дата."""
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Москва-Тверь", "date": "2026-10-08T09:00:00"},
    }

    assert contract_folder_name(data).endswith("_08.10.2026")


# ─────────────────────────────────────────────────────────────
# Папка: создание и переиспользование
# ─────────────────────────────────────────────────────────────

def test_resolve_contract_folder_creates(output_dir):
    """Папки нет — она создаётся на диске, путь отдан наружу."""
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Чехов — Санкт-Петербург", "date": "2026-10-08"},
    }

    folder = resolve_contract_folder(output_dir, data)

    assert folder.is_dir()
    assert folder.name == "Иванов_И.И._Чехов-Санкт-Петербург_08.10.2026"
    assert folder.parent == output_dir


def test_resolve_contract_folder_reuses_existing(output_dir):
    """
    Папка с таким именем уже есть — используется она, без суффикса «_2».

    Два документа одного рейса ОБЯЗАНЫ лежать рядом: именно ради этого
    папка и заводится по ключу данных.
    """
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Чехов — Санкт-Петербург", "date": "2026-10-08"},
    }

    first = resolve_contract_folder(output_dir, data)
    (first / "Договор-заявка.docx").write_text("договор", encoding="utf-8")

    second = resolve_contract_folder(output_dir, data)

    assert second == first
    assert (second / "Договор-заявка.docx").exists(), "файл первого документа пропал"
    assert sorted(path.name for path in output_dir.iterdir()) == [first.name]


def test_resolve_contract_folder_different_drivers(output_dir):
    """Разные водители — разные папки, даже при общем маршруте и дате."""
    route = {"route": "Москва-Тверь", "date": "2026-10-08"}
    first = resolve_contract_folder(output_dir, {
        "driver": {"full_name": "Иванов Иван Иванович"}, "contract": dict(route),
    })
    second = resolve_contract_folder(output_dir, {
        "driver": {"full_name": "Петров Пётр Петрович"}, "contract": dict(route),
    })

    assert first != second
    assert first.is_dir() and second.is_dir()
    assert len(list(output_dir.iterdir())) == 2


def test_resolve_contract_folder_empty_data_creates_fallback(output_dir):
    """Совсем пустые данные: папка «Без_водителя_<сегодня>», а не ошибка."""
    folder = resolve_contract_folder(output_dir, {})

    assert folder.is_dir()
    assert folder.name == f"{NO_DRIVER}_{TODAY}"


def test_resolve_contract_folder_given_the_trip_folder_itself(output_dir):
    """
    Папкой вывода указана сама папка рейса — в себя она не вкладывается.

    Так бывает, когда правят готовый документ «на месте»: выбранная папка —
    уже папка рейса. Вложенная «Иванов_И.И._…/Иванов_И.И._…» развела бы
    документы одного рейса по двум папкам с одинаковым именем.
    """
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Чехов — Санкт-Петербург", "date": "2026-10-08"},
    }
    trip_folder = resolve_contract_folder(output_dir, data)

    assert resolve_contract_folder(trip_folder, data) == trip_folder
    assert resolve_contract_folder(str(trip_folder), data) == trip_folder


# ─────────────────────────────────────────────────────────────
# Полный путь документа
# ─────────────────────────────────────────────────────────────

def test_contract_output_path_puts_file_into_route_folder(output_dir):
    """Путь документа — внутри папки рейса, имя файла не меняется."""
    data = {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {"route": "Чехов — Санкт-Петербург", "date": "2026-10-08"},
    }

    path = Path(contract_output_path(output_dir, "Договор-заявка_1.docx", data))

    assert path.name == "Договор-заявка_1.docx"
    assert path.parent == output_dir / "Иванов_И.И._Чехов-Санкт-Петербург_08.10.2026"
    assert path.parent.is_dir()
    assert path.parent.parent == output_dir
