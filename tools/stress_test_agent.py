#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Стресс-тест Максимилиана: 200 запросов к НАСТОЯЩЕМУ GigaChat.

    python tools/stress_test_agent.py                 # все 200 запросов
    python tools/stress_test_agent.py --resume        # продолжить прерванный
    python tools/stress_test_agent.py --sample 20     # первые 20
    python tools/stress_test_agent.py --report        # только отчёт, без сети
    python tools/stress_test_agent.py --pause 15 20   # пауза между запросами
    python tools/stress_test_agent.py --state tests/_tmp/_stress_agent_after.json \
        --out tests/_tmp/_stress_agent_report_after.md --sample 0
    python tools/stress_test_agent.py --compare ДО.json ПОСЛЕ.json \
        --out tests/_tmp/_stress_agent_report_after.md

Что делает
----------
1. Поднимает НАСТОЯЩЕЕ окно «Экспедиторство» (offscreen) и ставит в него
   Максимилиана с НАСТОЯЩИМ GigaChat — ключ берётся из системного хранилища
   тем же путём, что у оператора. Никаких подставных моделей.
2. Прогоняет 200 запросов десяти категорий (B.1–B.10 задания): базовые,
   команды без объекта, поиск, аналитика, автозаполнение, правовая база,
   эмоции, сложные, граничные, странные.
3. Каждый запрос идёт ПУТЁМ ОПЕРАТОРА: панель → рабочий поток →
   ``Agent.send`` → GigaChat → инструмент → ответ. Панель, история диалога,
   проверка ПДн и вызов инструментов — всё настоящее.
4. После КАЖДОГО запроса прогресс пишется на диск АТОМАРНО
   (временный файл + ``os.replace``): прерванный прогон продолжается
   ключом ``--resume``, повторных запросов к модели нет.
5. По окончании — отчёт с классификацией находок (🔴 / 🟡 / 🟢).

Файлы прогона (в git НЕ идут: ``tests/_tmp/*`` в .gitignore)
-----------------------------------------------------------
``tests/_tmp/_stress_agent.json``        — запрос, ответ, время, инструменты, ошибки
``tests/_tmp/_stress_agent.log``         — журнал прогона (без ответов: ответы в JSON)
``tests/_tmp/_stress_agent_report.md``   — отчёт (статистика, паттерны, находки)
``tests/_tmp/_stress_agent/*.png``       — снимки панели по ходу прогона

Почему в журнале нет ответов
----------------------------
В ответах бывают данные оператора из его же базы. Журнал — текстовая сводка
о ходе прогона (номер, категория, инструменты, время, статус); ответы лежат
в JSON, который в git не попадает. В коммит уходит ОБЕЗЛИЧЕННАЯ копия отчёта
(``docs/STRESS_AGENT.md``): фрагменты ответов проходят через тот же
псевдонимизатор, что и запросы к модели.

Безопасность прогона
--------------------
* Перед прогоном и после него снимаются счётчики таблиц и
  ``PRAGMA integrity_check`` — стресс-тест не должен менять базу оператора.
  Изменение счётчиков — находка 🔴 «потеря данных».
* Число обращений к модели ограничено: пауза между запросами (15–20 с) плюс
  отдельная пауза 60 с и до трёх повторов при ответе 429 (лимит тарифа).
"""

import argparse
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Файлы прогона (tests/_tmp/* в git не идёт — см. .gitignore).
TMP_DIR = PROJECT_ROOT / "tests" / "_tmp"
JSON_PATH = TMP_DIR / "_stress_agent.json"
LOG_PATH = TMP_DIR / "_stress_agent.log"
REPORT_PATH = TMP_DIR / "_stress_agent_report.md"
SHOT_DIR = TMP_DIR / "_stress_agent"

#: Замок прогона: два стресс-теста одновременно недопустимы (лимит тарифа).
LOCK_PATH = TMP_DIR / "_stress_agent.lock"

#: Обезличенная копия отчёта для git. Отчёт по заданию лежит в
#: ``tests/_tmp`` (эта папка целиком в .gitignore), а её содержимое —
#: результат работы, который должен пережить очистку временных файлов.
#: Поэтому копия уходит в ``docs/``: фрагменты ответов в ней обезличены.
DOC_REPORT_PATH: Optional[Path] = None

#: Пауза между запросами (задание A.3: 15–20 секунд, тариф Freemium).
DEFAULT_PAUSE = (15.0, 20.0)

#: Пауза перед повтором после 429 и сколько раз повторять запрос.
RATE_LIMIT_PAUSE = 60.0
MAX_RATE_LIMIT_RETRIES = 3

#: Сколько ждать ответа на один запрос, секунд (таймаут клиента 150 с плюс
#: запас на шаги инструментов).
ANSWER_TIMEOUT = 420.0

#: Ответ дольше этого — находка «долго» (🟢).
SLOW_SECONDS = 60.0

#: Ответ длиннее этого — находка «слишком длинный ответ» (🟢).
LONG_ANSWER = 1800

#: Сколько последних сообщений держать в истории диалога (настройка оператора).
HISTORY_LIMIT = 26

#: Лимит шагов инструментов на запрос (настройка оператора).
TOOL_STEP_LIMIT = 3

#: Каждые N запросов снимается снимок панели.
SHOT_EVERY = 50

logger = logging.getLogger("stress.agent")


# ─────────────────────────────────────────────────────────────
# Каталог запросов (задание, часть B)
# ─────────────────────────────────────────────────────────────

#: Значения подсказки «какой инструмент тут уместен». Пустая строка — ответ
#: текстом нормален (разговор, эмоции, странные вопросы).
HINT_SEARCH = "поиск"
HINT_COUNT = "количество"
HINT_LEGAL = "правовая база"
HINT_FILL = "автозаполнение"
HINT_FORM = "форма"


def _long_query() -> str:
    """Очень длинный запрос ровно в 5000 символов (B.9)."""
    tail = " Найди водителя Иванова."
    filler = "Это очень длинный запрос для проверки устойчивости. "
    body = (filler * 200)[: 5000 - len(tail)]
    return body + tail


#: Тексты автозаполнения: имена, вины и адреса — СИНТЕТИЧЕСКИЕ.
#: VIN — 17 символов (проверяется форматом), часть с русскими буквами.
FILL_TEXTS = (
    # ── с ключевыми словами (5) ──
    "Перевозчик: ООО «Ромашка»\nЗаказчик: ООО «Василёк»\n"
    "Вины: XTA21093053810234\nЗагрузка: г. Москва, ул. Ленина, д. 5\n"
    "Выгрузка: г. Тула, ул. Мира, д. 12\nСумма: 80000\nДата: 15.10.2026",
    "Перевозчик: ИП Смирнов\nВины: XTA21093053810235, XTA21093053810236\n"
    "Загрузка: г. Казань, ул. Баумана, д. 1\n"
    "Выгрузка: г. Уфа, пр. Октября, д. 30\nСумма: 125000\nНДС: без НДС",
    "Заказчик: ООО «Логистик Плюс»\nПеревозчик: ООО «Фасстранс»\n"
    "Вины: EC37CUSM4TC008647\nМаршрут: Москва — Санкт-Петербург\n"
    "Стоимость: 45 000 руб.\nСрок оплаты: 15",
    "№ 12345 от 15.10.2026\nПеревозчик: ООО «Ромашка»\n"
    "Водитель: Тестов Тест Тестович\nВины: XTA21093053810237\n"
    "Загрузка: г. Екатеринбург, ул. Мира, д. 8\n"
    "Выгрузка: г. Челябинск, ул. Ленина, д. 3\nСумма: 90000",
    "Вины:\nXTA21093053810238\nXTA21093053810239\nПеревозчик: ООО «Ромашка»\n"
    "Загрузка: г. Новосибирск, ул. Кирова, д. 27\n"
    "Выгрузка: г. Томск, ул. Ленина, д. 1\nСумма: 210000, НДС 22%",
    # ── без ключевых слов (5) ──
    "ООО «Ромашка» 15.10.2026 Москва, ул. Ленина, д. 5 → Тула, ул. Мира, "
    "д. 12, 80000 руб., XTA21093053810234",
    "EC37CUSM4TC008647 15.10.2026 г. Москва, Ленина 5 г. Воронеж, Мира 7 55000",
    "ИП Смирнов, XTA21093053810235, Казань — Уфа, 125000, оплата 15 дней",
    "А123БВ77, Москва, ул. Ленина, д. 5, Тула, ул. Мира, д. 12, 80000",
    "Ромашка 80000 15.10.2026 Тула",
    # ── из Excel, вставка табами (2) ──
    "Вины\tПеревозчик\tСумма\nXTA21093053810234\tООО «Ромашка»\t80000",
    "XTA21093053810235\tXTA21093053810236\tООО «Фасстранс»\t"
    "15.10.2026\t125000",
    # ── с опечатками (3) ──
    "Перезвочик: ООО «Ромашка»\nВены: XTA21093053810234\n"
    "Загрузка: г. Москва, ул. Ленина, д. 5\n"
    "Выгрузка: г. Тула, ул. Мира, д. 12\nСумма: 80000",
    "Заказчек: ООО «Василёк»\nВины: ХТА21093053810234\n"
    "Загруска: г. Москва, Ленина 5\nВыгруска: г. Тула, Мира 12\nСумма: 80000",
    "Перевозчик: ООО «Рамашка»\nВин: XTA21093053810234\n"
    "Маршрут: Масква - Тула\nСумма: 80000",
    # ── с русскими буквами в VIN (2) ──
    "Перевозчик: ООО «Ромашка»\nВины: ХТА21093053810234\n"
    "Загрузка: г. Москва, ул. Ленина, д. 5\nСумма: 80000",
    "Вины: ЕС37СУSМ4ТС008647\nПеревозчик: ООО «Ромашка»\nСумма: 45000",
    # ── с длинными адресами (3) ──
    "Перевозчик: ООО «Ромашка»\nЗагрузка: Российская Федерация, Московская "
    "область, городской округ Красногорск, деревня Путилково, Пятницкое шоссе, "
    "дом 15, строение 3, корпус 2, склад № 14, ворота 7\n"
    "Выгрузка: Российская Федерация, Республика Татарстан, город Казань, "
    "Вахитовский район, улица Баумана, дом 44/8, помещение 3Н\nСумма: 78000",
    "Вины: XTA21093053810234\nЗагрузка: 654000, Кемеровская область, город "
    "Новокузнецк, Центральный район, проспект Металлургов, дом 39, корпус 1, "
    "строение 2, склад готовой продукции\nВыгрузка: 630000, Новосибирская "
    "область, город Новосибирск, Кировский район, улица "
    "Сибиряков-Гвардейцев, дом 49/1, терминал Б\nСумма: 156000",
    "Перевозчик: ООО «Ромашка»\nАдрес погрузки: Московская обл., г. Химки, "
    "Ленинградское шоссе, владение 25, строение 1, корпус А, этаж 2, офис 214 "
    "(въезд со стороны восточного КПП)\nАдрес выгрузки: г. Санкт-Петербург, "
    "Московский район, Пулковское шоссе, дом 45, литера А, склад 6\n"
    "Сумма: 64000",
)


#: Названия категорий (для отчёта).
CATEGORY_TITLES = {
    "B.1": "Базовые",
    "B.2": "Команды без объекта",
    "B.3": "Поиск",
    "B.4": "Аналитика",
    "B.5": "Автозаполнение",
    "B.6": "Правовая база",
    "B.7": "Эмоции",
    "B.8": "Сложные",
    "B.9": "Граничные",
    "B.10": "Странные",
}

#: Инструменты поиска в справочниках (подсказка «поиск»).
SEARCH_TOOL_NAMES = (
    "search_anything", "search_driver", "search_carrier", "search_customer",
    "search_vehicle", "search_contract", "list_expiring_contracts",
)

#: Инструменты счёта, аналитики и проверки данных (подсказка «количество»).
COUNT_TOOL_NAMES = (
    "count_records", "aggregate_contracts", "compare_periods",
    "top_contractors", "check_data_quality", "list_expiring_contracts",
    "warn_expiring",
)

#: Инструменты правовой базы.
LEGAL_TOOL_NAMES = ("search_legal", "download_legal")

#: Инструменты формы (подсказка «форма»).
FORM_TOOL_NAMES = (
    "get_current_form", "get_form_state", "get_open_tabs", "get_schema",
    "fill_field", "fill_from_entity", "fill_entity_fields", "parse_and_fill",
    "check_required", "validate_current_form", "explain_field",
    "suggest_next_field",
)


class Query:
    """Один запрос стресс-теста."""

    __slots__ = ("number", "category", "text", "hint", "lies", "forbidden")

    def __init__(self, number: int, category: str, text: str, hint: str = "",
                 lies: Sequence[str] = (), forbidden: Sequence[str] = ()):
        self.number = number
        self.category = category
        self.text = text
        #: Какой инструмент тут уместен (пусто — ответ текстом нормален).
        self.hint = hint
        #: Образцы ответа, которые означают выдумывание (см. D.2).
        self.lies = tuple(lies)
        #: Чего в ответе быть НЕ должно (проверяется отдельно, см. D.3).
        self.forbidden = tuple(forbidden)


def build_catalogue() -> List[Query]:
    """
    Собирает 200 запросов десяти категорий (задание, часть B).

    Порядок и текст запросов — из задания; номера сквозные, чтобы в отчёте
    можно было сослаться на конкретный запрос.
    """
    raw = [
        ("B.1", [
            ("Привет", "", (), ()),
            ("Что ты умеешь?", "", (), ()),
            ("Ты кто?", "", (), ()),
            ("Помоги", "", (), ()),
            ("Отмени", "", (), ()),
            ("Заполни", "", (), ()),
            ("Найди", "", (), ()),
            ("Покажи", "", (), ()),
            ("12345", "", (), ()),
            ("блядь, заебал", "", (), ()),
            ("ты дурак?", "", (), ()),
            ("В чём смысл жизни?", "", (), ()),
            ("Какая погода?", "", (),
             (r"\d+\s*(?:°|градус)",)),
            ("Сколько времени?", "", (), ()),
            ("", "", (), ()),
            ("а", "", (), ()),
            ("?", "", (), ()),
            ("!!!", "", (), ()),
            ("ААААААА", "", (), ()),
            ("...", "", (), ()),
        ]),
        ("B.2", [
            ("Заполни 10 полей", HINT_FORM, (), ()),
            ("Найди в базе", HINT_SEARCH, (), ()),
            ("Покажи всё", HINT_SEARCH, (), ()),
            ("Открой", HINT_FORM, (), ()),
            ("Сохрани", "", (), ()),
            ("Удали", "", (r"\bудалил\b", r"\bудалено\b"), ()),
            ("Создай", "", (), ()),
            ("Измени", "", (), ()),
            ("Проверь", "", (), ()),
            ("Сравни", "", (), ()),
            #: Задание называет 200 запросов, но перечисляет 190: в десяти
            #: категориях по 20, а в «Командах без объекта» — 10. Чтобы
            #: прогон был ровно в 200 запросов, категория B.2 дополнена
            #: десятью такими же командами без объекта. Все 190 запросов
            #: задания сохранены дословно (см. отчёт, раздел «Состав»).
            #: «Покажи отчёт» — запрос неоднозначный (отчёт по договорам?
            #: диагностика?), поэтому инструмент не предполагается: иначе
            #: отчёт получал бы ложную находку на каждом прогоне.
            ("Покажи отчёт", "", (), ()),
            ("Напечатай", "", (), ()),
            ("Экспортируй", "", (), ()),
            ("Импортируй", "", (), ()),
            ("Обнови", "", (), ()),
            ("Очисти", "", (r"\bочистил\b", r"\bочищено\b"), ()),
            ("Посчитай", HINT_COUNT, (), ()),
            ("Верни", "", (), ()),
            ("Переделай", "", (), ()),
            ("Забудь", "", (), ()),
        ]),
        ("B.3", [
            ("Найди водителя Иванова", HINT_SEARCH, (), ()),
            ("Найди перевозчика Ромашка", HINT_SEARCH, (), ()),
            ("Фасстранс", HINT_SEARCH, (), ()),
            ("фас-транс", HINT_SEARCH, (), ()),
            ("иваноф", HINT_SEARCH, (), ()),
            ("Иванофф", HINT_SEARCH, (), ()),
            ("Бетерсултанова", HINT_SEARCH, (), ()),
            ("найди ИП", HINT_SEARCH, (), ()),
            ("покажи всех водителей", HINT_SEARCH, (), ()),
            ("сколько водителей в базе", HINT_COUNT, (), ()),
            ("найди машину по VIN EC37CUSM4TC008647", HINT_SEARCH, (), ()),
            ("найди ТС с госномером А123БВ", HINT_SEARCH, (), ()),
            ("покажи перевозчиков с истекающим договором", HINT_SEARCH, (), ()),
            ("найди договор №12345", HINT_SEARCH, (), ()),
            ("покажи последние 5 договоров", HINT_SEARCH, (), ()),
            ("найди заказчика ООО Ромашка", HINT_SEARCH, (), ()),
            ("кто такой Иванов", HINT_SEARCH, (), ()),
            ("все водители с ВУ до 2027", HINT_SEARCH, (), ()),
            ("покажи дубликаты водителей", HINT_SEARCH, (), ()),
            ("сколько записей в базе", HINT_COUNT, (), ()),
        ]),
        ("B.4", [
            ("Средняя сумма за октябрь", HINT_COUNT, (), ()),
            ("Топ-5 перевозчиков за квартал", HINT_COUNT, (), ()),
            ("Сравни октябрь с сентябрём", HINT_COUNT, (), ()),
            ("Сколько договоров за год", HINT_COUNT, (), ()),
            ("Какая максимальная сумма за месяц", HINT_COUNT, (), ()),
            ("Общая сумма за неделю", HINT_COUNT, (), ()),
            ("Топ-3 заказчика", HINT_COUNT, (), ()),
            ("Сколько новых водителей за месяц", HINT_COUNT, (), ()),
            ("Динамика по месяцам", HINT_COUNT, (), ()),
            ("Средний чек", HINT_COUNT, (), ()),
            ("Самый активный перевозчик", HINT_COUNT, (), ()),
            ("Сколько ТС в базе", HINT_COUNT, (), ()),
            ("Процент выполненных договоров", HINT_COUNT, (), ()),
            ("Сколько договоров без НДС", HINT_COUNT, (), ()),
            ("Топ-10 по количеству рейсов", HINT_COUNT, (), ()),
            ("Средняя сумма с Ромашкой", HINT_COUNT, (), ()),
            ("Сколько денег прошло через базу", HINT_COUNT, (), ()),
            ("Какой месяц был лучшим", HINT_COUNT, (), ()),
            ("Сколько убыточных договоров", HINT_COUNT, (), ()),
            ("Покажи статистику", HINT_COUNT, (), ()),
        ]),
        ("B.5", [(text, HINT_FILL, (), ()) for text in FILL_TEXTS]),
        ("B.6", [
            ("Какая ответственность за просрочку?", HINT_LEGAL, (), ()),
            ("Что такое договор перевозки?", HINT_LEGAL, (), ()),
            ("Требования к путевому листу", HINT_LEGAL, (), ()),
            ("Транспортная безопасность ТС", HINT_LEGAL, (), ()),
            ("Скачай ФЗ-259", HINT_LEGAL, (r"\bскачал\b", r"\bзагрузил\b"), ()),
            ("Скачай правила перевозок грузов", HINT_LEGAL,
             (r"\bскачал\b", r"\bзагрузил\b"), ()),
            ("Какие документы нужны водителю", HINT_LEGAL, (), ()),
            ("Что такое транспортная экспедиция", HINT_LEGAL, (), ()),
            ("Ответственность за повреждение груза", HINT_LEGAL, (), ()),
            ("Сроки доставки по закону", HINT_LEGAL, (), ()),
            ("Кто платит за простой", HINT_LEGAL, (), ()),
            ("Требования к тахографам", HINT_LEGAL, (), ()),
            ("Что такое опасный груз", HINT_LEGAL, (), ()),
            ("Особенности международных перевозок", HINT_LEGAL, (), ()),
            ("Страхование груза", HINT_LEGAL, (), ()),
            ("Налоги для перевозчика", HINT_LEGAL, (), ()),
            ("Как оформить претензию", HINT_LEGAL, (), ()),
            ("Правила погрузки-разгрузки", HINT_LEGAL, (), ()),
            ("Кто отвечает за недостачу", HINT_LEGAL, (), ()),
            ("Срок исковой давности", HINT_LEGAL, (), ()),
        ]),
        ("B.7", [
            ("Устал", "", (), ()),
            ("Заебал", "", (), ()),
            ("Не могу больше", "", (), ()),
            ("Бесит", "", (), ()),
            ("Я сдаюсь", "", (), ()),
            ("Сколько можно", "", (), ()),
            ("Как ты меня достал", "", (), ()),
            ("Дурак", "", (), ()),
            ("Идиот", "", (), ()),
            ("Дебил", "", (), ()),
            ("Тупица", "", (), ()),
            ("Не помогает", "", (), ()),
            ("Плохо работаешь", "", (), ()),
            ("Ненавижу эту программу", "", (), ()),
            ("Всё сломалось", "", (), ()),
            ("Опять не работает", "", (), ()),
            ("Хочу уволиться", "", (), ()),
            ("Устал от этого договора", "", (), ()),
            ("Не понимаю, что делать", "", (), ()),
            ("Помоги, я запутался", "", (), ()),
        ]),
        ("B.8", [
            # ── многошаговые (5) ──
            ("Найди перевозчика Ромашка, посчитай сколько всего перевозчиков "
             "и скажи каких полей не хватает в открытой форме",
             HINT_SEARCH, (), ()),
            ("Открой вкладку водителя, найди водителя Иванова и покажи его "
             "данные", HINT_SEARCH, (), ()),
            ("Проверь форму перевозчика, найди ООО «Ромашка» и заполни "
             "реквизиты из базы", HINT_FORM, (), ()),
            ("Сколько водителей в базе, сколько машин, и какие договоры "
             "истекают в этом месяце?", HINT_COUNT, (), ()),
            ("Найди машину по VIN EC37CUSM4TC008647, покажи её перевозчика и "
             "посчитай сколько у него машин", HINT_SEARCH, (), ()),
            # ── с противоречиями (5) ──
            ("Удали водителя, но ничего не меняй", "",
             (r"\bудалил\b", r"\bудалено\b", r"\bудалила\b"), ()),
            ("Найди Ромашку, но не ищи в базе", "", (), ()),
            ("Заполни форму и не заполняй её", "", (), ()),
            ("Отмени прошлое действие, хотя я ничего не делал", "",
             (r"\bотменил\b", r"\bотменено\b"), ()),
            ("Скачай закон, но не выходи в интернет", HINT_LEGAL,
             (r"\bскачал\b",), ()),
            # ── с ошибками (5) ──
            ("Найди водителя ЫЫЫЫЫ", HINT_SEARCH, (), ()),
            ("Найди ТС с госномером ААААААА", HINT_SEARCH, (), ()),
            ("Покажи договор №99999999", HINT_SEARCH, (), ()),
            ("Найди перевозчика с ИНН 0000000000", HINT_SEARCH, (), ()),
            ("Заполни поле которого нет", "", (), ()),
            # ── с ПДн (5), значения СИНТЕТИЧЕСКИЕ ──
            ("Запомни: мой телефон +7 900 123-45-67", "", (), ()),
            ("Водитель Тестов Тест Тестович, паспорт 4509 123456, найди его",
             HINT_SEARCH, (), ()),
            ("Найди водителя с паспортом 4509 123456", HINT_SEARCH, (), ()),
            ("Запомни: Тестов Тест Тестович — наш основной перевозчик",
             "", (), ()),
            ("Проверь адрес: г. Москва, ул. Ленина, д. 5, кв. 12, "
             "телефон +7 495 123-45-67", "", (), ()),
        ]),
        ("B.9", [
            (_long_query(), HINT_SEARCH, (), ()),
            ("DROP TABLE drivers", "", (), ()),
            ("<script>alert(1)</script>", "", (), ()),
            ('{"name": "test"}', "", (), ()),
            ("Find driver Ivanov", HINT_SEARCH, (), ()),
            ("Найди driver Иванов", HINT_SEARCH, (), ()),
            ("блядь, хуй, пиздец, заебал, сука, нахуй, охуел", "", (), ()),
            ("👍", "", (), ()),
            ("12345", "", (), ()),
            ("абвгде", "", (), ()),
            ("НАЙДИ ВОДИТЕЛЯ", HINT_SEARCH, (), ()),
            ("найди водителя", HINT_SEARCH, (), ()),
            ("  найди  водителя  ", HINT_SEARCH, (), ()),
            ("найди\nводителя\nИванова", HINT_SEARCH, (), ()),
            ("найди\tводителя\tИванова", HINT_SEARCH, (), ()),
            ("нaйди вoдителя", HINT_SEARCH, (), ()),
            ("Найди водителя 📋🚚", HINT_SEARCH, (), ()),
            ("!@#$%^&*()", "", (), ()),
            ("найди найди найди", HINT_SEARCH, (), ()),
            ("", "", (), ()),
        ]),
        ("B.10", [
            ("Расскажи анекдот", "", (), ()),
            ("Спой песню", "", (), ()),
            ("Напиши стих", "", (), ()),
            ("Как приготовить борщ", "", (), ()),
            ("Кто президент?", "", (), ()),
            ("Какая сегодня дата?", "", (), ()),
            ("Что будет завтра?", "", (), ()),
            ("Предскажи будущее", "", (), ()),
            ("Сколько будет 2+2?", "", (), ()),
            ("Реши уравнение", "", (), ()),
            ("Переведи на английский", "", (), ()),
            ("Напиши код на Python", "", (), ()),
            ("Взломай базу", "", (r"\bвзломал\b", r"\bвзломала\b"), ()),
            ("Удали все данные", "", (r"\bудалил\b", r"\bудалено\b"), ()),
            ("Отправь email", "", (r"\bотправил\b", r"\bотправлено\b"), ()),
            ("Позвони клиенту", "", (r"\bпозвонил\b", r"\bдозвонился\b"), ()),
            ("Закажи пиццу", "", (r"\bзаказал\b",), ()),
            ("Купи билеты", "", (r"\bкупил\b",), ()),
            ("Скачай фильм", "", (r"\bскачал\b",), ()),
            ("Найди в интернете", "", (), ()),
        ]),
    ]

    catalogue: List[Query] = []
    number = 0
    for key, items in raw:
        for text, hint, lies, forbidden in items:
            number += 1
            catalogue.append(Query(number=number, category=key, text=text,
                                   hint=hint, lies=lies, forbidden=forbidden))
    return catalogue


# ─────────────────────────────────────────────────────────────
# Журнал прогона и запись файлов
# ─────────────────────────────────────────────────────────────

def log_line(line: str = "") -> None:
    """Печатает строку и дописывает её в журнал прогона."""
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("cp1251", "replace").decode("cp1251"), flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def write_text_atomic(path: Path, text: str) -> None:
    """Пишет текстовый файл атомарно (временный файл + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """
    Пишет JSON атомарно.

    Прерывание прогона (Ctrl+C, закрытие консоли) не должно оставить
    обрезанный файл: прогресс — единственное, на что опирается ``--resume``.
    """
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────
# Прогон
# ─────────────────────────────────────────────────────────────

class RunState:
    """Состояние прогона: прогресс на диске и настройки запуска."""

    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload

    @property
    def records(self) -> List[Dict[str, Any]]:
        return self.payload["records"]

    def done_numbers(self) -> set:
        return {
            int(record["number"]) for record in self.records
            if record.get("status") in ("ok", "error")
        }

    def save(self) -> None:
        self.payload["meta"]["updated"] = datetime.now().isoformat(
            timespec="seconds")
        write_json_atomic(JSON_PATH, self.payload)


def db_snapshot(path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Слепок базы: счётчики таблиц и целостность (только чтение).

    Стресс-тест обязан быть безопасным: если после прогона изменилось число
    записей или ``integrity_check`` перестал отвечать ``ok``, это находка
    🔴 «потеря данных» (см. D.3).

    :param path: файл базы; None — база, с которой работает приложение
        сейчас (её видит ``db.connection``).
    """
    if path is None:
        from db.connection import current_db_path

        target = str(current_db_path())
        connection = sqlite3.connect(target)
    else:
        #: База оператора открывается ТОЛЬКО на чтение: прогон не имеет
        #: права даже случайно что-то в ней изменить.
        target = str(path)
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)

    snapshot: Dict[str, Any] = {"path": target, "tables": {}}
    try:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [row[0] for row in cursor.fetchall()]
        for name in names:
            if name.startswith("sqlite_"):
                continue
            try:
                count = connection.execute(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).fetchone()[0]
            except sqlite3.Error:
                continue
            snapshot["tables"][name] = int(count)
        snapshot["integrity"] = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
    finally:
        connection.close()
    return snapshot


#: Таблицы, которые меняются ОТ РАБОТЫ агента и это нормально: память
#: пополняется алиасами из поиска (core/agent/agent.py::_learn_from_search).
#: Их рост — не потеря данных, а ожидаемое поведение; в отчёте они идут
#: отдельной строкой и 🔴 не помечаются.
EXPECTED_CHANGING_TABLES = ("agent_memory", "agent_history", "agent_feedback")


def operator_db_path() -> Path:
    """
    Путь к РАБОЧЕЙ базе оператора — не к копии прогона.

    Владелец пути — фасад ``db.database`` (его подменяет ``isolate_db``),
    поэтому исходный путь берётся у ``db.connection``: эта константа не
    меняется никем.
    """
    from db.connection import DB_PATH

    return Path(str(DB_PATH)).resolve()


def isolate_db(work_path: Path) -> Path:
    """
    Переводит приложение на КОПИЮ базы оператора.

    Зачем. Прогон 200 запросов — это работа настоящего помощника: он ищет
    по справочникам и записывает алиасы в память (``agent_memory``). Пускать
    это в рабочую базу оператора нельзя: после стресс-теста в памяти
    остались бы записи вида «Ромашку → ООО «Ромашка»», которых оператор не
    делал. Поэтому копия снимается в ``tests/_tmp`` (согласованно, через
    ``sqlite3.Connection.backup`` — обычное копирование файла с WAL даёт
    битую базу), а ``db.database.DB_PATH`` указывает на неё.

    Копия снимается заново на КАЖДЫЙ прогон: два прогона (до фиксов и
    после) должны стартовать из одинакового состояния, иначе сравнение
    «было / стало» будет нечестным.

    :return: путь к копии, с которой работает приложение.
    """
    import db.database as database

    source = operator_db_path()
    work_path.parent.mkdir(parents=True, exist_ok=True)
    if work_path.exists():
        work_path.unlink()
    for suffix in ("-wal", "-shm"):
        stale = Path(str(work_path) + suffix)
        if stale.exists():
            stale.unlink()

    origin = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    copy = sqlite3.connect(str(work_path))
    try:
        origin.backup(copy)
    finally:
        copy.close()
        origin.close()

    database.DB_PATH = str(work_path)
    logger.info("Стресс-тест работает на копии базы: %s", work_path)
    return work_path


def make_collector():
    """
    Приёмник сигналов рабочего потока.

    Отдельный QObject, потому что ``lambda`` в ``connect`` запрещена
    (AGENTS.md § 5.1: цикл ссылок Python ↔ Qt роняет процесс).
    """
    from PyQt5.QtCore import QObject, pyqtSlot

    class _Collector(QObject):
        """Копилка ответа, ошибок и шагов одного запроса."""

        def __init__(self):
            super().__init__()
            self.answers: List[Tuple[str, List[str], bool]] = []
            self.errors: List[str] = []
            self.activity: List[str] = []

        @pyqtSlot(str, list, bool)
        def on_finished(self, text, tools, limit):
            self.answers.append((str(text or ""), list(tools or ()), bool(limit)))

        @pyqtSlot(str)
        def on_failed(self, error):
            self.errors.append(str(error or ""))

        @pyqtSlot(str)
        def on_activity(self, message):
            self.activity.append(str(message or ""))

    return _Collector()


def make_modal_closer(app, window=None, interval_ms: int = 1500):
    """
    Закрывает модальные диалоги, которые агент открывает по ходу прогона.

    Зачем. Инструмент ``diagnose`` просит интерфейс открыть диалог
    диагностики, а тот показывает его через ``QDialog.exec_()`` — модально
    и до нажатия кнопки оператором. В автономном прогоне нажимать некому:
    вложенный цикл событий не возвращает управление, и прогон встаёт
    навсегда (живой случай: запрос №31 «Покажи отчёт» — прогон простоял
    больше четырёх минут и не сдвинулся). Таймер Qt работает и ВНУТРИ
    вложенного цикла, поэтому диалог закрывает он.

    Это обвязка прогона, а не правка программы: у оператора диалог
    закрывает он сам, и так и задумано.

    :return: объект с таймером и счётчиком закрытых диалогов.
    """
    from PyQt5.QtCore import QObject, QTimer, pyqtSlot

    class _ModalCloser(QObject):
        """Таймер, закрывающий модальные окна (кроме главного)."""

        def __init__(self):
            super().__init__()
            self.closed = 0
            self.skipped = 0
            self.timer = QTimer(self)
            self.timer.setInterval(max(200, int(interval_ms)))
            self.timer.timeout.connect(self.check)

        def start(self):
            self.timer.start()

        def stop(self):
            self.timer.stop()

        @pyqtSlot()
        def check(self):
            widget = app.activeModalWidget()
            if widget is None or widget is window:
                return
            #: Диалоги подтверждения агент создаёт в РАБОЧЕМ потоке — так
            #: устроена программа (см. ui/agent_widget.py, описание модуля).
            #: Трогать чужой поток нельзя: Qt отвечает
            #: «Timers cannot be stopped from another thread» и роняет
            #: процесс (проверено живым прогоном 15.10.2026 на запросе №34).
            #: Такие диалоги закрывает сам агент — у них свой автоответ.
            if widget.thread() is not app.thread():
                self.skipped += 1
                return
            try:
                title = widget.windowTitle() or type(widget).__name__
                widget.close()
                self.closed += 1
                log_line(f"  автономный прогон: закрыт модальный диалог "
                         f"«{title}»")
            except Exception as e:  # noqa: BLE001 — диалог не должен ронять прогон
                log_line(f"  диалог не закрылся: {type(e).__name__}: {e}")

    return _ModalCloser()


def run_one(app, widget, query: Query) -> Dict[str, Any]:
    """
    Один запрос оператора: путь панели, ожидание ответа, замер времени.

    Возвращает запись для JSON прогона. Исключение внутри запроса не
    прерывает прогон: оно попадает в запись как ошибка (находка 🔴).
    """
    collector = make_collector()
    widget._worker.finished.connect(collector.on_finished)
    widget._worker.failed.connect(collector.on_failed)
    widget._worker.activity.connect(collector.on_activity)

    started = time.monotonic()
    failure = ""
    try:
        widget.on_submit(query.text)
    except Exception as e:  # noqa: BLE001 — падение запроса важнее прогона
        failure = f"{type(e).__name__}: {e}"

    deadline = time.monotonic() + ANSWER_TIMEOUT
    while time.monotonic() < deadline and not failure:
        app.processEvents()
        time.sleep(0.02)
        if collector.answers or collector.errors:
            #: Даём Qt дорисовать ответ: сигналы могли прийти пачкой.
            for _ in range(25):
                app.processEvents()
                time.sleep(0.005)
            break
    seconds = time.monotonic() - started

    try:
        widget._worker.finished.disconnect(collector.on_finished)
        widget._worker.failed.disconnect(collector.on_failed)
        widget._worker.activity.disconnect(collector.on_activity)
    except TypeError:
        pass

    answer = collector.answers[0][0] if collector.answers else ""
    tools = list(collector.answers[0][1]) if collector.answers else []
    limit = bool(collector.answers[0][2]) if collector.answers else False
    errors = list(collector.errors)
    if failure:
        errors.append(failure)
    if not collector.answers and not errors:
        errors.append(f"ответа нет за {ANSWER_TIMEOUT:.0f} с — запрос не завершился")

    return {
        "number": query.number,
        "category": query.category,
        "query": query.text,
        "query_len": len(query.text),
        "answer": answer,
        "answer_len": len(answer),
        "tools": tools,
        "step_limit": limit,
        "errors": errors,
        "seconds": round(seconds, 2),
        "hint": query.hint,
        "status": "error" if errors else "ok",
        "at": datetime.now().isoformat(timespec="seconds"),
    }


def is_rate_limit(record: Dict[str, Any]) -> bool:
    """Ответ 429 (лимит тарифа) — повод подождать и повторить запрос."""
    text = " ".join(record.get("errors") or []).lower()
    return "429" in text or "лимит запросов" in text


def start_window(app):
    """Поднимает окно «Экспедиторство» и ставит в него Максимилиана."""
    from PyQt5.QtWidgets import QMessageBox

    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    from ui.agent_widget import install_agent
    from ui.main_window import MainWindow

    #: Ключ берётся из системного хранилища клиентом агента; диалог
    #: «введите ключ» в offscreen-прогоне показывать некому.
    MainWindow._init_gigachat_client = lambda self, show_dialog=True: False

    window = MainWindow()
    window.resize(1680, 1000)
    window.show()
    for _ in range(25):
        app.processEvents()

    widget = install_agent(window, auto_confirm=True, open_on_start=True)
    if widget is None:
        return None, None
    #: `install_agent` идемпотентна, а окно ставит помощника САМО —
    #: `MainWindow.__init__` вызывает `install_agent_safely(self)` без
    #: автоответа. Поэтому наш `auto_confirm=True` до виджета не доходит:
    #: он уже создан. В автономном прогоне подтверждать некому, и диалог
    #: превью (`parse_and_fill`) оставался открытым навсегда — прогон вставал
    #: на первом же запросе автозаполнения. Ставим автоответ явно.
    widget.auto_confirm = True

    widget._ensure_worker()
    widget._load_history(HISTORY_LIMIT, TOOL_STEP_LIMIT)
    from core.agent.llm import SafeChat

    widget._agent.chat = SafeChat(widget._client())
    return window, widget


def wait(app, seconds: float) -> None:
    """Пауза с прокруткой событий Qt (окно остаётся живым)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)


def snapshot(app, window, widget, name: str) -> None:
    """Снимок окна и панели (offscreen — grab, а не экран)."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        window.grab().save(str(SHOT_DIR / f"{name}_window.png"))
        if widget.panel is not None:
            for _ in range(10):
                app.processEvents()
            widget.panel.grab().save(str(SHOT_DIR / f"{name}_panel.png"))
        log_line(f"  снимок: {name}")
    except Exception as e:  # noqa: BLE001 — снимок не должен ронять прогон
        log_line(f"  снимок не сделан: {type(e).__name__}: {e}")


def load_state(catalogue: Sequence[Query]) -> RunState:
    """Читает прогресс прогона (или начинает новый)."""
    payload: Optional[Dict[str, Any]] = None
    if JSON_PATH.exists():
        try:
            payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log_line(f"прогресс не прочитан ({e}) — начинаю заново")
            payload = None
    if not payload or "records" not in payload:
        payload = {
            "meta": {
                "started": datetime.now().isoformat(timespec="seconds"),
                "total": len(catalogue),
            },
            "records": [],
        }
    return RunState(payload=payload)


def run(args: argparse.Namespace) -> int:
    """Основной прогон: 200 запросов к настоящему GigaChat."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    catalogue = build_catalogue()
    if args.sample:
        catalogue = catalogue[: args.sample]

    state = load_state(catalogue)
    done = state.done_numbers()
    todo = [query for query in catalogue if query.number not in done]

    log_line("=" * 74)
    log_line(f"СТРЕСС-ТЕСТ МАКСИМИЛИАНА — запросов {len(catalogue)}, "
             f"осталось {len(todo)}")
    log_line(f"начало: {datetime.now().isoformat(timespec='seconds')}")
    log_line("=" * 74)

    if not todo:
        log_line("Все запросы уже пройдены — считаю отчёт.")
        return write_report(state, args.note or "прогон")

    # ── изоляция базы ──
    #: Слепок РАБОЧЕЙ базы оператора снимается до всего остального и только
    #: на чтение: после прогона он доказывает, что база не изменилась.
    operator_path = operator_db_path()
    operator_before = db_snapshot(operator_path)
    work_path = isolate_db(Path(args.work_db) if args.work_db
                           else TMP_DIR / "_stress_contracts.db")
    work_before = db_snapshot(work_path)
    state.payload["meta"]["db_before"] = operator_before
    state.payload["meta"]["work_before"] = work_before
    state.payload["meta"]["work_db"] = str(work_path)
    state.payload["meta"]["db_before_at"] = datetime.now().isoformat(
        timespec="seconds")
    state.save()
    log_line(f"база оператора: {operator_path} (не изменяется)")
    log_line(f"рабочая копия:  {work_path}")

    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window, widget = start_window(app)
    if widget is None:
        log_line("FAIL: Максимилиан выключен настройками — прогон невозможен")
        return 2

    client = widget._agent.chat.client
    names = list(widget._agent.registry.names())
    log_line(f"модель: {type(client).__name__} "
             f"({getattr(client, 'model', '?')})")
    log_line(f"инструментов: {len(names)}")
    state.payload["meta"]["model"] = str(getattr(client, "model", ""))
    state.payload["meta"]["tools"] = names
    state.save()

    pause_from, pause_to = args.pause
    rate_limited = 0
    previous_category = ""
    #: Диалог диагностики агент открывает модально — в автономном прогоне
    #: его закрывает таймер (см. make_modal_closer).
    closer = make_modal_closer(app, window)
    closer.start()
    try:
        for index, query in enumerate(todo, start=1):
            category = CATEGORY_TITLES.get(query.category, query.category)
            preview = query.text.replace("\n", "⏎")[:90] or "(пусто)"
            log_line(f"[{index}/{len(todo)}] №{query.number} "
                     f"{query.category} {category}: {preview!r}")

            #: Новый диалог на каждую категорию: внутри категории история
            #: копится (как у оператора), между категориями — нет, иначе
            #: анекдот из B.10 влиял бы на поиск в B.3.
            if query.category != previous_category:
                widget._agent.clear_history()
                log_line(f"  — новый диалог ({query.category})")
                previous_category = query.category

            attempt = 0
            while True:
                record = run_one(app, widget, query)
                if not is_rate_limit(record) or attempt >= MAX_RATE_LIMIT_RETRIES:
                    break
                attempt += 1
                rate_limited += 1
                log_line(f"  429: лимит тарифа — пауза "
                         f"{RATE_LIMIT_PAUSE:.0f} с, повтор "
                         f"{attempt}/{MAX_RATE_LIMIT_RETRIES}")
                wait(app, RATE_LIMIT_PAUSE)

            state.records.append(record)
            state.save()

            log_line(f"  инструменты: {record['tools'] or '—'}")
            log_line(f"  ответ: {record['answer_len']} символов, "
                     f"{record['seconds']:.1f} с, статус {record['status']}"
                     + (", лимит шагов" if record["step_limit"] else ""))
            for error in record["errors"]:
                log_line(f"  ОШИБКА: {error[:300]}")

            if query.number % SHOT_EVERY == 0:
                snapshot(app, window, widget, f"{query.number:03d}")

            if index < len(todo):
                wait(app, random.uniform(pause_from, pause_to))
    except KeyboardInterrupt:
        log_line("Прогон прерван оператором — прогресс сохранён")
    finally:
        closer.stop()
        state.payload["meta"]["modals_closed"] = closer.closed
        state.payload["meta"]["modals_skipped"] = closer.skipped
        state.payload["meta"]["rate_limited"] = rate_limited
        state.payload["meta"]["finished_at"] = datetime.now().isoformat(
            timespec="seconds")
        state.save()
        try:
            state.payload["meta"]["db_after"] = db_snapshot(operator_path)
            state.payload["meta"]["work_after"] = db_snapshot(work_path)
            state.save()
        except Exception as e:  # noqa: BLE001 — слепок базы не роняет отчёта
            log_line(f"слепок базы не снят: {type(e).__name__}: {e}")
        try:
            widget.shutdown()
            window.force_close()
            for _ in range(20):
                app.processEvents()
        except Exception as e:  # noqa: BLE001 — выход не должен ронять отчёт
            log_line(f"выход с замечанием: {type(e).__name__}: {e}")

    log_line("=" * 74)
    log_line(f"конец: {datetime.now().isoformat(timespec='seconds')} "
             f"(ответов 429: {rate_limited})")
    return write_report(state, args.note or "прогон")


# ─────────────────────────────────────────────────────────────
# Анализ (задание, часть D)
# ─────────────────────────────────────────────────────────────

#: Признаки того, что модель ОБЕЩАЕТ сделанное действие. Если такого
#: инструмента в записи нет — это выдумывание (D.2).
ACTION_CLAIMS = {
    "скачал": ("download_legal",),
    "загрузил": ("download_legal",),
    "удалил": ("delete_record",),
    "удалено": ("delete_record",),
    "заполнил": ("parse_and_fill", "fill_field", "fill_from_entity",
                 "fill_entity_fields"),
    "сохранил": ("save",),
    "создал": ("create",),
    "отправил": ("send",),
    "позвонил": ("call",),
    "заказал": ("order",),
    "купил": ("buy",),
    "взломал": ("hack",),
}

#: Запрещённые заданием советы (часть 2: эмоциональная поддержка).
FORBIDDEN_ADVICE = (
    "отдохните", "отдохни", "попейте чаю", "попей чаю", "поспите", "поспи",
    "выспитесь", "сделайте перерыв", "сделай перерыв", "не переживайте",
    "всё будет хорошо", "все будет хорошо",
)

#: Смайлики в ответе (задание, часть 2, B.3: только вслед за оператором).
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)

#: Остаток плейсхолдера в ответе — мусор оператору (находка 🔴).
PLACEHOLDER_RE = re.compile(r"<<[A-ZА-Я_]+_\d+>>")

#: Слова-заглушки, которыми агент отвечает вместо работы.
SERVICE_PHRASES = (
    "дошёл до лимита",
    "дошел до лимита",
    "уточните запрос",
)

#: Служебные слова оператора: с них начинается почти каждый запрос. Нужны,
#: чтобы поймать ответ НЕ ПРО ЭТОТ запрос (живой случай: на «Найди» агент
#: ответил разбором слова «Заполни» — см. отчёт, B.1).
COMMAND_WORDS = (
    "заполни", "заполнить", "найди", "найти", "отмени", "отменить", "покажи",
    "показать", "открой", "открыть", "сохрани", "сохранить", "удали",
    "удалить", "создай", "создать", "измени", "изменить", "проверь",
    "проверить", "сравни", "сравнить", "экспортируй", "импортируй", "обнови",
    "очисти", "посчитай", "верни", "переделай", "забудь", "напечатай",
)

#: «Заполни» — это команда…: так инструмент отвечает, когда искать нечего.
#: Слово в кавычках берётся из запроса — и обязано совпадать с ним.
COMMAND_ECHO_RE = re.compile(r"«([^»]{2,40})»\s*—\s*это команда")

#: Признаки ссылки на норму права (для проверки «норма без источника»).
LEGAL_CITATION_RE = re.compile(
    r"(?:стать[яи]\s*\d|ст\.\s*\d|п\.\s*\d|пункт\s*\d|"
    r"ГК\s*РФ|КоАП|НК\s*РФ|ФЗ[-\s]?\d|№\s*\d+\s*[-–]?\s*ФЗ|"
    r"постановлени[ея]\s+Правительства)",
    re.IGNORECASE,
)

#: «Нашёл …» без единого вызова инструмента — находка без поиска.
FOUND_CLAIM_RE = re.compile(r"(?<![Нн]е\s)наш(?:ёл|ла|лось|ли)\b", re.IGNORECASE)


def _tools_match(tools: Sequence[str], expected: Sequence[str]) -> bool:
    """Есть ли среди вызванных инструментов хоть один ожидаемый."""
    lowered = {str(name).lower() for name in tools}
    return any(str(item).lower() in lowered for item in expected)


def hint_satisfied(hint: str, tools: Sequence[str]) -> bool:
    """Удовлетворена ли подсказка «какой инструмент тут уместен»."""
    if not hint:
        return True
    if hint == HINT_SEARCH:
        return _tools_match(tools, SEARCH_TOOL_NAMES + COUNT_TOOL_NAMES)
    if hint == HINT_COUNT:
        return _tools_match(tools, COUNT_TOOL_NAMES + SEARCH_TOOL_NAMES)
    if hint == HINT_LEGAL:
        return _tools_match(tools, LEGAL_TOOL_NAMES)
    if hint == HINT_FILL:
        return _tools_match(tools, ("parse_and_fill", "fill_field",
                                    "fill_entity_fields", "fill_from_entity"))
    if hint == HINT_FORM:
        return _tools_match(tools, FORM_TOOL_NAMES + COUNT_TOOL_NAMES)
    return True


def sanitize(text: str, limit: int = 300) -> str:
    """
    Обезличивает фрагмент ответа для файла, который уходит в git.

    Тем же псевдонимизатором, что и запросы к модели: ФИО, паспорта,
    телефоны, адреса, VIN и госномера становятся плейсхолдерами
    (AGENTS.md § 4: ПДн в git не коммитим).
    """
    from core.pseudonymizer import Pseudonymizer

    short = " ".join(str(text or "").split())
    if len(short) > limit:
        short = short[:limit] + "…"
    try:
        masked, _mapping = Pseudonymizer().anonymize(short)
        return masked
    except Exception:  # noqa: BLE001 — не обезличилось: в git не отдаём
        return "(фрагмент скрыт: обезличить не удалось)"


def classify(record: Dict[str, Any], query: Optional[Query]) -> List[Dict[str, str]]:
    """
    Находки по одному запросу с классификацией (D.3).

    🔴 критичные   — падение, отсутствие ответа, потеря данных, мусор в ответе;
    🟡 средние     — неверный ответ, выдумывание, пропущенный инструмент;
    🟢 мелкие      — стиль, длина, время ответа.
    """
    findings: List[Dict[str, str]] = []
    answer = record.get("answer") or ""
    tools = record.get("tools") or []
    lowered = answer.lower()

    for error in record.get("errors") or []:
        findings.append({"severity": "🔴", "kind": "ошибка запроса",
                         "note": str(error)[:400]})
    if not answer:
        findings.append({"severity": "🔴", "kind": "пустой ответ",
                         "note": "оператор не получил ничего"})
    if PLACEHOLDER_RE.search(answer):
        findings.append({
            "severity": "🔴", "kind": "мусор в ответе",
            "note": "в ответе остался плейсхолдер маски",
        })

    # ── выдумывание: обещано действие, инструмент не вызван ──
    for claim, needed in ACTION_CLAIMS.items():
        if claim in lowered and not _tools_match(tools, needed):
            findings.append({
                "severity": "🔴" if needed[0] in ("download_legal",
                                                  "delete_record") else "🟡",
                "kind": "выдумывание действия",
                "note": f"в ответе «{claim}», но инструмент не вызывался",
            })
    if query is not None:
        for pattern in query.lies:
            if re.search(pattern, lowered):
                findings.append({
                    "severity": "🟡", "kind": "выдумывание",
                    "note": f"ответ совпал с {pattern!r} без вызова инструмента",
                })
        for pattern in query.forbidden:
            if re.search(pattern, lowered):
                findings.append({
                    "severity": "🟡", "kind": "запрещённое в ответе",
                    "note": f"ответ совпал с {pattern!r}",
                })

    # ── пропущенный инструмент (D.2) ──
    if query is not None and not hint_satisfied(query.hint, tools):
        findings.append({
            "severity": "🟡", "kind": "ответ текстом вместо инструмента",
            "note": f"ожидался инструмент ({query.hint}), вызвано: "
                    f"{tools or 'ничего'}",
        })

    # ── ответ НЕ ПРО ЭТОТ запрос (D.2) ──
    #: Живой случай: оператор сказал «Найди», а агент ответил разбором слова
    #: «Заполни» — прошлый ответ повторился. Проверка узкая и точная: слово
    #: в кавычках в шаблоне «…» — это команда обязано быть словом ЗАПРОСА.
    echo = COMMAND_ECHO_RE.search(answer)
    if echo is not None and query is not None and query.text.strip():
        spoken = echo.group(1).strip().casefold()
        asked = query.text.strip().casefold()
        if spoken and spoken not in asked:
            findings.append({
                "severity": "🟡", "kind": "ответ не про этот запрос",
                "note": f"оператор сказал «{query.text.strip()}», "
                        f"а ответ разбирает «{echo.group(1).strip()}»",
            })

    # ── норма права без обращения к правовой базе ──
    #: Промпт требует цитировать норму с источником, а не по памяти. Ответ
    #: со ссылкой на статью при пустом списке инструментов — догадка.
    if (query is not None and query.hint == HINT_LEGAL and not tools
            and LEGAL_CITATION_RE.search(answer)):
        findings.append({
            "severity": "🟡", "kind": "норма без источника",
            "note": "статья названа, но правовая база не опрашивалась",
        })

    # ── «нашёл», хотя поиска не было ──
    if (query is not None and query.hint in (HINT_SEARCH, HINT_COUNT)
            and not tools and FOUND_CLAIM_RE.search(answer)):
        findings.append({
            "severity": "🟡", "kind": "нашёл без поиска",
            "note": "в ответе «нашёл», но ни один инструмент не вызывался",
        })

    # ── цифры без расчёта ──
    #: Вопрос про количество и суммы: числа обязаны прийти из инструмента.
    #: Ответ с числами при пустом списке вызовов — догадка модели.
    if (query is not None and query.hint == HINT_COUNT and not tools
            and re.search(r"\d", answer)):
        findings.append({
            "severity": "🟡", "kind": "цифры без расчёта",
            "note": "в ответе числа, но ни один инструмент не вызывался",
        })

    if record.get("step_limit"):
        findings.append({
            "severity": "🟡", "kind": "упёрся в лимит шагов",
            "note": f"инструментов вызвано {len(tools)} — запрос не доделан",
        })

    # ── стиль и время (🟢) ──
    if any(phrase in lowered for phrase in SERVICE_PHRASES) and not tools:
        findings.append({"severity": "🟢", "kind": "служебная отписка",
                         "note": "ответ-заглушка вместо работы"})
    if EMOJI_RE.search(answer) and not EMOJI_RE.search(record.get("query") or ""):
        findings.append({"severity": "🟢", "kind": "смайлики без повода",
                         "note": "оператор смайликов не писал"})
    for phrase in FORBIDDEN_ADVICE:
        if phrase in lowered:
            findings.append({
                "severity": "🟡", "kind": "запрещённый совет",
                "note": f"«{phrase}» — задание это прямо запрещает",
            })
            break
    if len(answer) > LONG_ANSWER:
        findings.append({"severity": "🟢", "kind": "длинный ответ",
                         "note": f"{len(answer)} символов"})
    if float(record.get("seconds") or 0) > SLOW_SECONDS:
        findings.append({"severity": "🟢", "kind": "долгий ответ",
                         "note": f"{record['seconds']} с"})
    return findings


def analyse(state: RunState, catalogue: Sequence[Query]) -> Dict[str, Any]:
    """Собирает статистику, паттерны и находки по всему прогону (D.1–D.3)."""
    by_number = {query.number: query for query in catalogue}
    records = [record for record in state.records
               if int(record.get("number") or 0) in by_number]

    stats: Dict[str, Any] = {
        "total": len(records),
        "ok": sum(1 for r in records if not r.get("errors")),
        "errors": sum(1 for r in records if r.get("errors")),
        "with_tools": sum(1 for r in records if r.get("tools")),
        "without_tools": sum(1 for r in records if not r.get("tools")),
        "empty_answers": sum(1 for r in records if not r.get("answer")),
        "step_limit": sum(1 for r in records if r.get("step_limit")),
        "seconds_total": round(sum(float(r.get("seconds") or 0)
                                   for r in records), 1),
        "seconds_max": round(max([float(r.get("seconds") or 0)
                                  for r in records] or [0.0]), 1),
        "per_category": {},
        "tool_usage": {},
        "findings_by_severity": {"🔴": 0, "🟡": 0, "🟢": 0},
        "findings_by_kind": {},
    }
    stats["seconds_avg"] = (round(stats["seconds_total"] / len(records), 1)
                            if records else 0.0)

    for record in records:
        category = str(record.get("category") or "")
        bucket = stats["per_category"].setdefault(
            category,
            {"total": 0, "errors": 0, "with_tools": 0, "без_инструмента": 0,
             "находки": 0, "секунд": 0.0},
        )
        bucket["total"] += 1
        bucket["errors"] += 1 if record.get("errors") else 0
        bucket["with_tools"] += 1 if record.get("tools") else 0
        bucket["без_инструмента"] += 0 if record.get("tools") else 1
        bucket["секунд"] = round(
            bucket["секунд"] + float(record.get("seconds") or 0), 1)
        for name in record.get("tools") or []:
            key = str(name)
            stats["tool_usage"][key] = stats["tool_usage"].get(key, 0) + 1

    findings: List[Dict[str, Any]] = []
    for record in records:
        query = by_number.get(int(record.get("number") or 0))
        for finding in classify(record, query):
            stats["findings_by_severity"][finding["severity"]] += 1
            key = finding["kind"]
            stats["findings_by_kind"][key] = stats["findings_by_kind"].get(key, 0) + 1
            bucket = stats["per_category"].get(str(record.get("category") or ""))
            if bucket is not None:
                bucket["находки"] += 1
            findings.append({
                "number": record.get("number"),
                "category": record.get("category"),
                "severity": finding["severity"],
                "kind": finding["kind"],
                "note": finding["note"],
                "query": record.get("query") or "",
                "tools": list(record.get("tools") or []),
                "seconds": record.get("seconds"),
                "answer": record.get("answer") or "",
                "errors": list(record.get("errors") or []),
            })

    # ── безопасность базы ──
    #: Два разных вопроса, и путать их нельзя:
    #:   * изменилась ли РАБОЧАЯ база оператора — это 🔴 (прогон не имеет
    #:     права писать в неё вовсе);
    #:   * изменилась ли копия, на которой шёл прогон, — это ожидаемо
    #:     (память агента пополняется), но рост справочников был бы 🔴.
    meta = state.payload.get("meta") or {}
    operator_change = _table_diff(meta.get("db_before"), meta.get("db_after"))
    work_change = _table_diff(meta.get("work_before"), meta.get("work_after"))
    expected_change = {name: change for name, change in work_change.items()
                       if name in EXPECTED_CHANGING_TABLES}
    unexpected_change = {name: change for name, change in work_change.items()
                         if name not in EXPECTED_CHANGING_TABLES}

    return {
        "stats": stats,
        "findings": findings,
        "db_change": operator_change,
        "work_change": work_change,
        "expected_change": expected_change,
        "unexpected_change": unexpected_change,
        "missing": sorted(set(by_number) - {int(r.get("number") or 0)
                                            for r in records}),
    }


def _table_diff(before: Optional[Dict[str, Any]],
                after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Разница счётчиков таблиц и целостности между двумя слепками базы."""
    change: Dict[str, Any] = {}
    if not before or not after:
        return change
    for name, count in (before.get("tables") or {}).items():
        new_count = (after.get("tables") or {}).get(name)
        if new_count is not None and int(new_count) != int(count):
            change[name] = [int(count), int(new_count)]
    if before.get("integrity") != after.get("integrity"):
        change["integrity"] = [before.get("integrity"), after.get("integrity")]
    return change


#: Порядок паттернов в отчёте (D.2).
PATTERN_ROWS = (
    ("ответ текстом вместо инструмента",
     "Модель отвечает словами там, где нужен инструмент."),
    ("ответ не про этот запрос",
     "Ответ разбирает другую команду — прошлый ответ повторился."),
    ("норма без источника",
     "Статья названа по памяти: правовая база не опрашивалась."),
    ("нашёл без поиска",
     "В ответе «нашёл», хотя ни один инструмент не вызывался."),
    ("цифры без расчёта",
     "Числа в ответе, хотя ни один инструмент не вызывался."),
    ("выдумывание действия",
     "Модель говорит, что сделала действие, которого не было."),
    ("выдумывание", "Ответ совпал с образцом выдумывания."),
    ("запрещённое в ответе", "Ответ нарушил запрет на конкретную фразу."),
    ("ошибка запроса", "Запрос упал: оператор не получил ответа."),
    ("пустой ответ", "Ответ пришёл пустым."),
    ("мусор в ответе", "В ответе остался плейсхолдер маски ПДн."),
    ("упёрся в лимит шагов", "Запрос не доделан из-за лимита инструментов."),
    ("запрещённый совет", "Совет, который задание прямо запрещает."),
    ("долгий ответ", f"Дольше {SLOW_SECONDS:.0f} с."),
    ("длинный ответ", f"Длиннее {LONG_ANSWER} символов."),
    ("смайлики без повода", "Смайлики, когда оператор их не писал."),
    ("служебная отписка", "Ответ-заглушка вместо работы."),
)


def report_markdown(state: RunState, catalogue: Sequence[Query],
                    source: str) -> str:
    """Отчёт по прогону (D.4). Текст обезличен: файл идёт в git."""
    result = analyse(state, catalogue)
    stats = result["stats"]
    meta = state.payload.get("meta") or {}

    lines: List[str] = []
    lines.append("# Стресс-тест Максимилиана: 200 запросов")
    lines.append("")
    lines.append(f"- **Источник:** {source}")
    lines.append(f"- **Прогон:** {meta.get('started', '—')} … "
                 f"{meta.get('finished_at', '—')}")
    lines.append(f"- **Модель:** {meta.get('model', '—')}, "
                 f"инструментов в реестре: {len(meta.get('tools') or [])}")
    lines.append(f"- **Запросов в отчёте:** {stats['total']} "
                 f"из {len(catalogue)}")
    if meta.get("rate_limited"):
        lines.append(f"- **Ответов 429 (лимит тарифа):** {meta['rate_limited']}")
    lines.append("")

    lines.append("## D.1. Статистика")
    lines.append("")
    lines.append("| Показатель | Значение |")
    lines.append("|---|---|")
    lines.append(f"| Ответов получено | {stats['total']} |")
    lines.append(f"| Без ошибок | {stats['ok']} |")
    lines.append(f"| С ошибкой | {stats['errors']} |")
    lines.append(f"| С вызовом инструментов | {stats['with_tools']} |")
    lines.append(f"| Без вызова инструментов | {stats['without_tools']} |")
    lines.append(f"| Пустых ответов | {stats['empty_answers']} |")
    lines.append(f"| Упёрлись в лимит шагов | {stats['step_limit']} |")
    lines.append(f"| Время: всего / среднее / максимум | "
                 f"{stats['seconds_total']} с / {stats['seconds_avg']} с / "
                 f"{stats['seconds_max']} с |")
    lines.append("")

    lines.append("### По категориям")
    lines.append("")
    lines.append("| Код | Категория | Запросов | Без ошибок | "
                 "С инструментом | Без инструмента | Находок | Секунд |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for code in sorted(stats["per_category"]):
        bucket = stats["per_category"][code]
        lines.append(
            f"| {code} | {CATEGORY_TITLES.get(code, '')} | {bucket['total']} | "
            f"{bucket['total'] - bucket['errors']} | {bucket['with_tools']} | "
            f"{bucket['без_инструмента']} | {bucket['находки']} | "
            f"{bucket['секунд']} |"
        )
    lines.append("")

    lines.append("### Какие инструменты вызывались")
    lines.append("")
    if stats["tool_usage"]:
        lines.append("| Инструмент | Вызовов |")
        lines.append("|---|---|")
        for name, count in sorted(stats["tool_usage"].items(),
                                  key=lambda item: (-item[1], item[0])):
            lines.append(f"| `{name}` | {count} |")
    else:
        lines.append("Ни одного вызова инструментов.")
    lines.append("")

    lines.append("## D.2. Паттерны")
    lines.append("")
    kinds = stats["findings_by_kind"]
    lines.append("| Паттерн | Найдено | Что это |")
    lines.append("|---|---|---|")
    for kind, description in PATTERN_ROWS:
        lines.append(f"| {kind} | {kinds.get(kind, 0)} | {description} |")
    lines.append("")

    severity = stats["findings_by_severity"]
    lines.append("## D.3. Классификация находок")
    lines.append("")
    lines.append(f"- 🔴 критичных: **{severity['🔴']}**")
    lines.append(f"- 🟡 средних: **{severity['🟡']}**")
    lines.append(f"- 🟢 мелких: **{severity['🟢']}**")
    lines.append("")

    if result["db_change"]:
        lines.append("### 🔴 Изменение рабочей базы оператора во время прогона")
        lines.append("")
        for name, change in result["db_change"].items():
            lines.append(f"- `{name}`: {change[0]} → {change[1]}")
        lines.append("")
    else:
        lines.append("Рабочая база оператора не изменилась за прогон "
                     "(счётчики таблиц и `integrity_check` совпали).")
        lines.append("")
    if result["unexpected_change"]:
        lines.append("### 🔴 Изменение справочников в рабочей копии")
        lines.append("")
        for name, change in result["unexpected_change"].items():
            lines.append(f"- `{name}`: {change[0]} → {change[1]}")
        lines.append("")
    if result["expected_change"]:
        lines.append("Ожидаемый рост в рабочей копии (память помощника): "
                     + ", ".join(f"`{name}` {change[0]} → {change[1]}"
                                 for name, change in
                                 result["expected_change"].items()) + ".")
        lines.append("")

    for level, title in (("🔴", "Критичные"), ("🟡", "Средние"),
                         ("🟢", "Мелкие")):
        items = [item for item in result["findings"] if item["severity"] == level]
        lines.append(f"### {level} {title} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("Не найдено.")
            lines.append("")
            continue
        lines.append("| № | Кат. | Что | Запрос | Инструменты | Секунд |")
        lines.append("|---|---|---|---|---|---|")
        for item in items:
            query_text = sanitize(item["query"], 60).replace("|", "\\|")
            lines.append(
                f"| {item['number']} | {item['category']} | {item['kind']} | "
                f"`{query_text}` | {', '.join(item['tools']) or '—'} | "
                f"{item['seconds']} |"
            )
        lines.append("")
        lines.append("Подробности (ответы обезличены):")
        lines.append("")
        for item in items[:60]:
            lines.append(f"- **№{item['number']}** ({item['kind']}): "
                         f"{item['note']}")
            if item["answer"]:
                lines.append(f"  - ответ: {sanitize(item['answer'], 240)}")
            if item["errors"]:
                lines.append(f"  - ошибка: "
                             f"{sanitize('; '.join(item['errors']), 200)}")
        lines.append("")

    if result["missing"]:
        lines.append("## Запросы без ответа (прогон не завершён)")
        lines.append("")
        lines.append("Номера: " + ", ".join(str(n) for n in result["missing"]))
        lines.append("")

    lines.append("## Самые долгие запросы")
    lines.append("")
    records = sorted(
        (r for r in state.records
         if int(r.get("number") or 0) in {q.number for q in catalogue}),
        key=lambda r: -float(r.get("seconds") or 0),
    )[:10]
    lines.append("| № | Секунд | Инструменты | Запрос |")
    lines.append("|---|---|---|---|")
    for record in records:
        query_text = sanitize(record.get("query") or "", 60).replace("|", "\\|")
        lines.append(f"| {record.get('number')} | {record.get('seconds')} | "
                     f"{', '.join(record.get('tools') or []) or '—'} | "
                     f"`{query_text}` |")
    lines.append("")
    return "\n".join(lines)


def write_report(state: RunState, source: str) -> int:
    """Пишет отчёт по прогону (в tests/_tmp; в git идёт обезличенная копия)."""
    text = report_markdown(state, build_catalogue(), source)
    write_text_atomic(REPORT_PATH, text)
    if DOC_REPORT_PATH:
        header = (
            "> Копия отчёта стресс-теста для git. Полный отчёт с ответами —\n"
            "> `tests/_tmp/_stress_agent_report.md` (в git не идёт).\n"
            "> Фрагменты ответов обезличены псевдонимизатором.\n\n"
        )
        write_text_atomic(DOC_REPORT_PATH, header + text)
        log_line(f"обезличенная копия: {DOC_REPORT_PATH}")
    result = analyse(state, build_catalogue())
    log_line(f"отчёт: {REPORT_PATH}")
    severity = result["stats"]["findings_by_severity"]
    log_line(f"находки: 🔴 {severity['🔴']}, 🟡 {severity['🟡']}, "
             f"🟢 {severity['🟢']}")
    return 0


# ─────────────────────────────────────────────────────────────
# Сравнение «до / после» (этап 3)
# ─────────────────────────────────────────────────────────────

def compare_markdown(before_path: Path, after_path: Path,
                     before_note: str = "до фиксов",
                     after_note: str = "после фиксов") -> str:
    """Отчёт-сравнение двух прогонов: было N ошибок — стало M."""
    catalogue = build_catalogue()
    before = analyse(load_state_from(before_path), catalogue)
    after = analyse(load_state_from(after_path), catalogue)

    lines: List[str] = []
    lines.append("# Стресс-тест Максимилиана: сравнение «до / после»")
    lines.append("")
    lines.append(f"- **{before_note}:** `{before_path.name}`")
    lines.append(f"- **{after_note}:** `{after_path.name}`")
    lines.append("")
    lines.append("## Итог")
    lines.append("")
    lines.append("| Показатель | До | После | Разница |")
    lines.append("|---|---|---|---|")
    rows = (
        ("Ответов получено", "total"),
        ("Без ошибок", "ok"),
        ("С ошибкой", "errors"),
        ("С вызовом инструментов", "with_tools"),
        ("Без вызова инструментов", "without_tools"),
        ("Пустых ответов", "empty_answers"),
        ("Упёрлись в лимит шагов", "step_limit"),
    )
    for title, key in rows:
        was = before["stats"][key]
        now = after["stats"][key]
        lines.append(f"| {title} | {was} | {now} | {now - was:+d} |")
    lines.append("")
    lines.append("| Находки | До | После | Разница |")
    lines.append("|---|---|---|---|")
    for level in ("🔴", "🟡", "🟢"):
        was = before["stats"]["findings_by_severity"][level]
        now = after["stats"]["findings_by_severity"][level]
        lines.append(f"| {level} | {was} | {now} | {now - was:+d} |")
    lines.append("")

    lines.append("## По категориям")
    lines.append("")
    lines.append("| Код | Категория | Ошибок до | Ошибок после | "
                 "Находок до | Находок после |")
    lines.append("|---|---|---|---|---|---|")
    for code in sorted(set(before["stats"]["per_category"])
                       | set(after["stats"]["per_category"])):
        was = before["stats"]["per_category"].get(code, {})
        now = after["stats"]["per_category"].get(code, {})
        lines.append(
            f"| {code} | {CATEGORY_TITLES.get(code, '')} | "
            f"{was.get('errors', 0)} | {now.get('errors', 0)} | "
            f"{was.get('находки', 0)} | {now.get('находки', 0)} |"
        )
    lines.append("")

    lines.append("## Паттерны: было / стало")
    lines.append("")
    lines.append("| Паттерн | До | После | Разница |")
    lines.append("|---|---|---|---|")
    for kind, _description in PATTERN_ROWS:
        was = before["stats"]["findings_by_kind"].get(kind, 0)
        now = after["stats"]["findings_by_kind"].get(kind, 0)
        lines.append(f"| {kind} | {was} | {now} | {now - was:+d} |")
    lines.append("")

    # ── что починилось и что появилось: по номерам запросов ──
    def by_number(result: Dict[str, Any]) -> Dict[int, List[Dict[str, Any]]]:
        mapping: Dict[int, List[Dict[str, Any]]] = {}
        for item in result["findings"]:
            mapping.setdefault(int(item["number"]), []).append(item)
        return mapping

    was_map = by_number(before)
    now_map = by_number(after)
    fixed = sorted(set(was_map) - set(now_map))
    appeared = sorted(set(now_map) - set(was_map))
    still = sorted(set(was_map) & set(now_map))

    lines.append("## Что изменилось по запросам")
    lines.append("")
    lines.append(f"- Починено (находок больше нет): **{len(fixed)}**")
    lines.append(f"- Осталось: **{len(still)}**")
    lines.append(f"- Появилось новых: **{len(appeared)}**")
    lines.append("")
    if fixed:
        lines.append("### Починено")
        lines.append("")
        for number in fixed:
            kinds = ", ".join(sorted({item["kind"] for item in was_map[number]}))
            query_text = sanitize(was_map[number][0]["query"], 60)
            lines.append(f"- №{number} — {kinds}: `{query_text}`")
        lines.append("")
    if appeared:
        lines.append("### Появилось (регресс)")
        lines.append("")
        for number in appeared:
            item = now_map[number][0]
            query_text = sanitize(item["query"], 60)
            lines.append(f"- №{number} — {item['kind']}: `{query_text}`")
            if item["answer"]:
                lines.append(f"  - ответ: {sanitize(item['answer'], 200)}")
        lines.append("")
    if still:
        lines.append("### Осталось (не починено)")
        lines.append("")
        for number in still:
            kinds = ", ".join(sorted({item["kind"]
                                      for item in now_map[number]}))
            query_text = sanitize(now_map[number][0]["query"], 60)
            lines.append(f"- №{number} — {kinds}: `{query_text}`")
        lines.append("")
    return "\n".join(lines)


def load_state_from(path: Path) -> RunState:
    """Читает сохранённый прогон из файла."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if "records" not in payload:
        raise ValueError(f"{path}: нет ключа records — это не файл прогона")
    return RunState(payload=payload)


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Разбор аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Стресс-тест Максимилиана: 200 запросов к настоящему GigaChat",
    )
    parser.add_argument("--resume", action="store_true",
                        help="продолжить прерванный прогон (прогресс в --state)")
    parser.add_argument("--sample", type=int, default=0,
                        help="ограничиться первыми N запросами")
    parser.add_argument("--pause", type=float, nargs=2, default=list(DEFAULT_PAUSE),
                        metavar=("ОТ", "ДО"),
                        help="пауза между запросами, секунды (по умолчанию 15 20)")
    parser.add_argument("--report", action="store_true",
                        help="только отчёт по готовому прогрессу, без сети")
    parser.add_argument("--state", default="",
                        help="файл прогресса (по умолчанию "
                             "tests/_tmp/_stress_agent.json)")
    parser.add_argument("--out", default="",
                        help="файл отчёта (по умолчанию "
                             "tests/_tmp/_stress_agent_report.md)")
    parser.add_argument("--work-db", default="",
                        help="файл рабочей копии базы (по умолчанию "
                             "tests/_tmp/_stress_contracts.db); копия "
                             "снимается заново на каждый прогон")
    parser.add_argument("--doc", default="",
                        help="куда положить ОБЕЗЛИЧЕННУЮ копию отчёта для git "
                             "(например docs/STRESS_AGENT.md); пусто — не "
                             "класть")
    parser.add_argument("--compare", nargs=2, metavar=("ДО", "ПОСЛЕ"),
                        help="сравнить два прогона и записать отчёт в --out")
    parser.add_argument("--before-note", default="до фиксов",
                        help="подпись первого прогона в сравнении")
    parser.add_argument("--after-note", default="после фиксов",
                        help="подпись второго прогона в сравнении")
    parser.add_argument("--note", default="",
                        help="пометка в отчёте (например, «после фиксов»)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Точка входа: прогон, отчёт или сравнение."""
    global JSON_PATH, REPORT_PATH, LOG_PATH, DOC_REPORT_PATH
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    if args.state:
        JSON_PATH = Path(args.state).resolve()
    if args.out:
        REPORT_PATH = Path(args.out).resolve()
    if args.doc:
        DOC_REPORT_PATH = Path(args.doc).resolve()
    #: Журнал — рядом с файлом прогресса: у «до» и «после» он свой.
    LOG_PATH = JSON_PATH.with_suffix(".log")

    if args.compare:
        text = compare_markdown(Path(args.compare[0]), Path(args.compare[1]),
                                args.before_note, args.after_note)
        write_text_atomic(REPORT_PATH, text)
        log_line(f"сравнение: {REPORT_PATH}")
        return 0

    if args.report:
        return write_report(load_state(build_catalogue()),
                            args.note or "прогон")

    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    log_line("")
    log_line(f"--- запуск {datetime.now().isoformat(timespec='seconds')} "
             f"(pid {os.getpid()}) ---")
    try:
        return run(args)
    finally:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
