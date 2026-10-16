#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Обратимое обезличивание персональных данных перед отправкой в GigaChat.

Зачем модуль: во внешнюю модель уходит текст документов (паспорт, ВУ, ПТС,
заявки), то есть ФИО, серии и номера документов, ИНН, СНИЛС, телефоны,
адреса, VIN и госномера. Здесь эти значения заменяются на плейсхолдеры вида
``<<PERSON_1>>``; модель работает с токенами, а ответ восстанавливается
локально — оригиналы не покидают машину.

Гарантии модуля:

  * **Только память.** Маппинг — обычный dict, живущий ровно столько,
    сколько живёт одна операция распознавания. На диск он не пишется,
    в логи не попадает и наружу (в UI, в ответ модели) не передаётся.
  * **Никаких ПДн в логах.** В DEBUG пишутся только типы найденных сущностей
    и их количество (``PERSON=2, PHONE=1``), в WARNING — только имя токена,
    который не удалось восстановить. Значения не логируются никогда.
  * **Без обязательных внешних зависимостей.** Ядро детекторов построено
    на стандартной библиотеке (``re``, ``logging``, ``threading``):
    регулярные выражения, контрольные суммы (ИНН, СНИЛС) и контекстные
    проверки. Natasha подключается опционально (см. ниже).
  * **Обратимость.** ``restore(anonymize(text)[0], anonymize(text)[1]) == text``
    для любого текста: совпадения не пересекаются, а токен хранит точную
    исходную подстроку.

Слой Natasha включается опционально. Если библиотека не установлена
или модель не загружена — маскирование работает на регулярках.
Natasha ловит иностранные имена, фамилии без контекста и цельные
адреса, которые регулярка разбивает на куски. Приоритет при
пересечении: регулярное совпадение выигрывает у Natasha.

Устойчивость к «испорченным» токенам: модель может вернуть ``<<PERSON 1>>``,
``<<person-1>>`` или ``PERSON_1`` вместо ``<<PERSON_1>>`` — при
восстановлении имя токена нормализуется (регистр, пробелы, дефисы,
подчёркивания), поэтому такие варианты распознаются.

**Паспорт — два токена.** Серия и номер паспорта («58 38 312160») закрываются
РАЗНЫМИ плейсхолдерами: ``<<PASSPORT_SERIES_1>>`` и ``<<PASSPORT_NUMBER_1>>``.
С одним токеном модель не видела границы «серия/номер» и в трети случаев
теряла паспорт целиком. Разделитель между серией и номером в тексте
сохраняется (он остаётся между токенами), поэтому ответ модели с двумя
полями восстанавливается обратно в исходную строку. Если разделителя нет
(«5838312160»), делить нечем — выходит один токен ``<<PASSPORT_1>>``.

Если токен всё равно не найден в маппинге, значение не выдумывается:
остаток плейсхолдера вычищается (``drop_unknown=True``), а в лог уходит
WARNING с именем токена — без оригинала.

Ограничение (осознанное): модуль обезличивает только текст. Изображения,
которые уходят в GigaChat Vision, не маскируются.
"""

import logging
import os
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.validation.checksums import inn_checksum_ok, snils_checksum_ok

logger = logging.getLogger("core.pseudonymizer")

#: Типы сущностей, для которых строятся плейсхолдеры.
#: Порядок в кортеже = порядок описания в логах и в отчётах.
TOKEN_TYPES = (
    "EMAIL",
    "SNILS",
    "VIN",
    "PASSPORT",
    "PASSPORT_SERIES",
    "PASSPORT_NUMBER",
    "LICENSE",
    "GOSNOMER",
    "INN",
    "PHONE",
    "PERSON",
    "ADDRESS",
)

#: Приоритет при пересечении совпадений: больше — важнее.
#: Специализированные форматы (СНИЛС, VIN, паспорт) перебивают общие
#: (телефон, ФИО, адрес), чтобы «хвост» одного не съел другой.
#: Natasha-типы стоят НИЖЕ своих регулярных собратьев: если оба нашли одно
#: и то же место, выигрывает регулярка (она точнее по формату). Natasha
#: нужна там, где регулярка молчит вовсе.
_PRIORITY = {
    "EMAIL": 100,
    "SNILS": 90,
    "VIN": 85,
    "PASSPORT": 80,
    "PASSPORT_SERIES": 80,
    "PASSPORT_NUMBER": 80,
    "LICENSE": 75,
    "GOSNOMER": 70,
    "INN": 65,
    "PHONE": 55,
    "PERSON": 45,
    "PERSON_NATASHA": 44,
    "ADDRESS": 30,
    "ADDRESS_NATASHA": 29,
}

# ─────────────────────────────────────────────────────────────
# Опциональная зависимость: Natasha
# ─────────────────────────────────────────────────────────────

#: None = не проверяли, True/False = проверено.
_NATASHA_AVAILABLE: Optional[bool] = None

#: Модульный кэш моделей Natasha: создаются один раз, живут до конца
#: процесса. Повторная инициализация — это секунды и десятки мегабайт.
_NATASHA: Dict[str, Any] = {}

#: Инициализация идёт из нескольких потоков (распознавание в QThreadPool),
#: поэтому модели строятся под замком: иначе два потока загрузят их дважды.
_NATASHA_LOCK = threading.Lock()


def _natasha_ready() -> bool:
    """Проверяет наличие Natasha лениво, один раз за процесс."""
    global _NATASHA_AVAILABLE
    if _NATASHA_AVAILABLE is None:
        try:
            from natasha import (  # noqa: F401
                Doc, Segmenter, MorphVocab,
                NewsEmbedding, NewsNERTagger,
                NamesExtractor, AddrExtractor,
            )
            _NATASHA_AVAILABLE = True
        except Exception as exc:
            # Ловим не только ImportError: сломанная установка (например,
            # отсутствующий pkg_resources при setuptools>=81) не должна
            # ронять распознавание — слой просто выключается.
            _NATASHA_AVAILABLE = False
            logger.info(
                "Natasha не установлена (%s) — маскирование работает "
                "на регулярках (pip install natasha)",
                type(exc).__name__,
            )
    return _NATASHA_AVAILABLE


def _natasha_pipeline() -> Optional[Dict[str, Any]]:
    """Ленивая инициализация Natasha (один раз на процесс)."""
    if "ready" in _NATASHA:
        return _NATASHA if _NATASHA["ready"] else None

    if not _natasha_ready():
        _NATASHA["ready"] = False
        return None

    with _NATASHA_LOCK:
        # Пока ждали замок, модели мог построить другой поток.
        if "ready" in _NATASHA:
            return _NATASHA if _NATASHA["ready"] else None

        try:
            from natasha import (
                Segmenter, MorphVocab,
                NewsEmbedding, NewsNERTagger,
                NamesExtractor, AddrExtractor,
            )
            morph_vocab = MorphVocab()
            embedding = NewsEmbedding()
            _NATASHA.update({
                "ready": True,
                "segmenter": Segmenter(),
                "morph_vocab": morph_vocab,
                "ner_tagger": NewsNERTagger(embedding),
                # Оба извлекателя требуют морфологию: в natasha 1.6
                # MorphVocab — обязательный позиционный аргумент.
                "names_extractor": NamesExtractor(morph_vocab),
                "addr_extractor": AddrExtractor(morph_vocab),
            })
            logger.info("Natasha инициализирована (NER + AddressExtractor)")
        except Exception as exc:
            _NATASHA["ready"] = False
            logger.warning(
                "Natasha недоступна (%s): маскирование на регулярках",
                type(exc).__name__,
            )
            return None

    return _NATASHA

# ─────────────────────────────────────────────────────────────
# Токены
# ─────────────────────────────────────────────────────────────

#: Токен в «правильном» виде: <<PERSON_1>>.
_TOKEN_RE = re.compile(r"<<([^<>]{1,60})>>")

#: Токен, у которого модель потеряла угловые скобки: PERSON_1, PERSON 1.
#: Подчёркивание входит в имя токена: у паспорта оно двойное
#: (``PASSPORT_SERIES_1``), и без этого такой токен не восстанавливался бы.
#: Верхняя граница длины — 20 символов: более длинных имён у модуля нет.
_BARE_TOKEN_RE = re.compile(r"(?<![\w<])([A-Z][A-Z_]{1,19})[_ ](\d{1,3})(?![\w>])")

#: Символы, которые модель может вставить внутрь имени токена.
_TOKEN_JUNK_RE = re.compile(r"[\s\-–—_.«»\"'`]+")


def token_name(token: str) -> str:
    """
    Нормализует имя токена для сопоставления с маппингом.

    ``<<PERSON_1>>`` → ``PERSON_1``, ``<<PERSON 1>>`` → ``PERSON_1``,
    ``<< person-1 >>`` → ``PERSON_1``.
    """
    text = str(token or "").strip().strip("<>").strip()
    text = text.upper()
    text = _TOKEN_JUNK_RE.sub("_", text)
    return text.strip("_")


def _mapping_index(mapping: Dict[str, str]) -> Dict[str, str]:
    """
    Индекс «нормализованное имя токена → оригинал» для быстрого поиска.

    Кроме точных имён в индекс попадают «шаблонные» варианты, и только когда
    семья токенов ОДНОЗНАЧНА (в тексте ровно один токен этого вида):
    ``<<PERSON>>`` и ``<<PERSON_N>>`` для единственного ``<<PERSON_1>>``.
    Так модель иногда возвращает имя токена: в промпте оно показано образцом
    вида ``<<PASSPORT_SERIES_N>>``, и модель копирует букву вместо цифры из
    текста. Если токенов одного вида несколько, подставлять нечего —
    неоднозначное имя не восстанавливается (значения не выдумываются).

    Отдельный случай — паспорт, закрытый ОДНИМ общим токеном (слитная запись
    «5838312160»): серии и номера в маппинге нет вовсе, но модель, зная про
    два токена, просит ``<<PASSPORT_SERIES_1>>`` или
    ``<<PASSPORT_NUMBER_1>>``. Оба таких имени указывают на тот же общий
    токен: пара «серия/номер» раскладывается постобработкой
    (`core.gigachat_client._normalize_document_ids`) уже после
    восстановления, поэтому значение не теряется и не выдумывается.
    """
    index: Dict[str, str] = {}
    families: Dict[str, List[str]] = {}
    for token, value in (mapping or {}).items():
        name = token_name(token)
        if not name or name in index:
            continue
        index[name] = "" if value is None else str(value)
        family = name.rsplit("_", 1)[0] if "_" in name else name
        families.setdefault(family, []).append(name)
    for family, names in families.items():
        if len(names) != 1:
            continue
        value = index[names[0]]
        index.setdefault(family, value)
        index.setdefault(family + "_N", value)
    # Части паспорта → общий токен паспорта (только если частей нет вовсе,
    # а общий токен ровно один: иначе непонятно, о каком паспорте речь).
    for part_family, whole_family in (("PASSPORT_SERIES", "PASSPORT"),
                                      ("PASSPORT_NUMBER", "PASSPORT")):
        if any(name.startswith(part_family + "_") for name in index):
            continue
        whole = families.get(whole_family) or []
        if len(whole) != 1:
            continue
        value = index[whole[0]]
        suffix = whole[0].rsplit("_", 1)[1]
        for alias in (f"{part_family}_{suffix}", f"{part_family}_N", part_family):
            index.setdefault(alias, value)
    return index


# ─────────────────────────────────────────────────────────────
# Общие помощники детекторов
# ─────────────────────────────────────────────────────────────

#: Окно контекста вокруг совпадения (символов слева и справа).
CONTEXT_WINDOW = 80


def _context(text: str, start: int, end: int, window: int = CONTEXT_WINDOW) -> str:
    """Текст вокруг совпадения в нижнем регистре — для контекстных проверок."""
    left = text[max(0, start - window):start]
    right = text[end:end + window]
    return (left + " " + right).casefold()


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


#: Контрольные суммы: расчёт живёт в `core.validation.checksums` — тем же
#: кодом пользуется валидатор договора. Здесь остались ПРЕЖНИЕ ИМЕНА,
#: на которые смотрят детекторы этого модуля и его тесты: это те же
#: функции (алиасы, а не обёртки), второй копии алгоритма в проекте нет.
_inn_checksum_ok = inn_checksum_ok
_snils_checksum_ok = snils_checksum_ok


# ─────────────────────────────────────────────────────────────
# Регулярные выражения детекторов
# ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z])"
)

#: СНИЛС: XXX-XXX-XXX YY (проверяется контрольной суммой).
_SNILS_RE = re.compile(r"(?<!\d)(\d{3})[- ](\d{3})[- ](\d{3})[ ]?(\d{2})(?!\d)")

#: VIN: 17 символов, без I/O/Q; обязательно хотя бы одна буква.
_VIN_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-HJ-NPR-Za-hj-npr-z0-9]{17})(?![A-Za-z0-9])"
)

#: Серия+номер документа: 4 цифры + 6 цифр. Между группами серии OCR
#: может поставить пробел или дефис («60 26 123456», «60-26 123456»,
#: «6026 123456»), а между серией и номером — №, N или тире.
_DOC_NUMBER_RE = re.compile(r"(?<!\d)(\d{2})[\s\-–—]?(\d{2})[\s\-–—№Nn]{0,3}(\d{6})(?!\d)")

#: Слитный 10-значный номер документа — принимается только по контексту.
_DOC_SOLID_RE = re.compile(r"(?<!\d)(\d{10})(?!\d)")

#: Ключевые слова, по которым различаются паспорт и водительское
#: удостоверение (структура «4 цифры + 6 цифр» у них одинаковая).
_PASSPORT_KEYWORDS = ("паспорт", "серия", "подразделен")
_LICENSE_KEYWORDS = ("водительск", "удостоверен", "категори", "ву", "в/у")
#: Слова справа от номера: «выдан» — паспорт, «действительно до» — ВУ.
_PASSPORT_RIGHT_KEYWORDS = ("выдан", "выдач", "паспорт")
_LICENSE_RIGHT_KEYWORDS = ("категори", "действительн", "стаж", "в/у")


def _last_keyword_pos(text: str, keywords) -> int:
    """Позиция самого правого ключевого слова в тексте (или -1)."""
    return max((text.rfind(word) for word in keywords), default=-1)


def _document_kind(text: str, start: int, end: int) -> str:
    """
    Определяет, к какому документу относится номер «4 цифры + 6 цифр».

    Смотрим 60 символов слева: побеждает то ключевое слово, которое стоит
    ближе к номеру (иначе «водительское удостоверение» из следующей строки
    перебивало бы паспорт, стоящий в той же строке).
    """
    left = text[max(0, start - 60):start].casefold()
    passport_pos = _last_keyword_pos(left, _PASSPORT_KEYWORDS)
    license_pos = _last_keyword_pos(left, _LICENSE_KEYWORDS)
    if passport_pos >= 0 or license_pos >= 0:
        if passport_pos < 0:
            return "LICENSE"
        if license_pos < 0:
            return "PASSPORT"
        return "LICENSE" if license_pos > passport_pos else "PASSPORT"

    right = text[end:end + 30].casefold()
    if _last_keyword_pos(right, _LICENSE_RIGHT_KEYWORDS) >= 0:
        return "LICENSE"
    if _last_keyword_pos(right, _PASSPORT_RIGHT_KEYWORDS) >= 0:
        return "PASSPORT"
    return ""

#: Госномер: А123ВС77 (авто) и АВ123477 (прицеп, мото).
_PLATE_CAR_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}"
    r"(?![А-Яа-яЁёA-Za-z0-9])"
)
_PLATE_TRAILER_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{4}\s?\d{2,3}"
    r"(?![А-Яа-яЁёA-Za-z0-9])"
)
#: Смешанный формат (71ABF18): принимается только рядом со словом-маркером,
#: иначе слишком легко спутать с реквизитами. Набор букв здесь шире
#: российского: у прицепов и техники встречаются любые латинские буквы.
_PLATE_MIXED_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"\d{2}\s?[A-Za-zА-Яа-яЁё]{2,3}\s?\d{2,4}"
    r"(?![А-Яа-яЁёA-Za-z0-9])"
)
#: Контекст, при котором смешанный формат считается госномером.
_PLATE_CONTEXT_RE = re.compile(
    r"гос|номер|прицеп|полуприцеп|тягач|автомобил|авто|машин|тс\b", re.IGNORECASE
)

#: Телефон: +7/8 + код + номер, либо городской в скобках.
_PHONE_MOBILE_RE = re.compile(
    r"(?<!\d)(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
_PHONE_CITY_RE = re.compile(
    r"(?<!\d)\(\d{3,5}\)[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)

#: ИНН: 10 или 12 цифр подряд (проверяется контрольной суммой).
_INN_10_RE = re.compile(r"(?<!\d)(\d{10})(?!\d)")
_INN_12_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")

#: Разделитель ВНУТРИ значения: пробел или табуляция, но НЕ перевод строки.
#: С `\s` маска ФИО и адреса заходила на следующую строку и съедала подпись
#: следующего поля: «Место рождения:г. Мурманск, ул. Центральная\nТелефон:+7»
#: превращалось в «Место рождения:<<ADDRESS_1>> <<PHONE_1>>» — метка
#: «Телефон:» до модели не доходила, и поле «Место рождения» терялось.
_HSPACE = r"[^\S\n\r]"
_HSPACE_PLUS = _HSPACE + "+"
_HSPACE_STAR = _HSPACE + "*"
_HSPACE_OPT = _HSPACE + "?"

#: ФИО: два-три слова с заглавной кириллицей, либо «Фамилия И.О.».
#: Сканирование идёт через lookahead: обычный finditer пропускает
#: перекрывающиеся кандидаты, и в «Водитель Сергеев Сергей» фамилия
#: терялась бы, потому что первым совпадением идёт «Водитель Сергеев».
#: Дефис и апостроф — ЧАСТЬ слова, а не разделитель: «Иванов-Петров» и
#: «О'Брайен» обязаны быть одним словом, иначе маска стартует с середины
#: фамилии, а начало уходит в облако открытым текстом. Первая ветвь —
#: слова с дефисом или апострофом («О'Брайен»: заглавная, апостроф,
#: заглавная — строчных букв перед апострофом может не быть вовсе),
#: вторая — обычное слово; обе требуют минимум двух букв, поэтому
#: одиночные инициалы словами ФИО не становятся.
_PERSON_WORD = r"(?:[А-ЯЁ][а-яё]*(?:['’\-][А-ЯЁ][а-яё]+)+|[А-ЯЁ][а-яё]+)"
#: Одно слово-ФИО целиком (для одиночной фамилии после команды).
_PERSON_WORD_RE = re.compile(_PERSON_WORD)
#: Одиночное слово-ФИО с проверкой границ — якорь для поиска перебором.
#: Группа 1 обязательна: наружу нужны ГРАНИЦЫ слова, а вокруг него стоят
#: проверки нулевой ширины (тем же приёмом сделаны образцы на два и три слова).
_PERSON_SINGLE_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + r"(?![А-Яа-яЁё])))"
)
_PERSON_THREE_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + _HSPACE_PLUS + _PERSON_WORD
    + _HSPACE_PLUS + _PERSON_WORD + r"(?![А-Яа-яЁё])))"
)
_PERSON_TWO_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + _HSPACE_PLUS + _PERSON_WORD
    + r"(?![А-Яа-яЁё])))"
)
_PERSON_INITIALS_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + _HSPACE_PLUS + r"[А-ЯЁ]\."
    + _HSPACE_OPT + r"[А-ЯЁ]\.))"
)

#: Отчество — сильный признак ФИО.
_PATRONYMIC_RE = re.compile(r"(?:ович|евич|ьевич|овна|евна|ична|инична)$", re.IGNORECASE)
#: Контекст, при котором допустимо ФИО без отчества.
_PERSON_CONTEXT_RE = re.compile(
    r"фио|ф\.\s?и\.\s?о|водител|директор|предпринимател|гражданин|паспорт"
    r"|выдан|подпис|заказчик|перевозчик|доверенн|руководител",
    re.IGNORECASE,
)
#: Слова, которые не могут быть частью ФИО (организации, регионы, реквизиты).
_PERSON_STOPWORDS = frozenset({
    "ооо", "оао", "зао", "пао", "ао", "ип", "муп", "гуп", "нко", "банк",
    "сбербанк", "россия", "российская", "федерация", "республика", "область",
    "обл", "край", "округ", "город", "улица", "проспект", "шоссе", "переулок",
    "район", "общество", "ограниченной", "ответственностью", "индивидуальный",
    "предприниматель", "генеральный", "директор", "водитель", "заказчик",
    "перевозчик", "договор", "заявка", "перевозка", "груз", "января",
    "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря", "телефон", "адрес",
})

#: Первое слово ФИО не может быть словом-КОМАНДОЙ: «Удали Иванов» — это
#: команда и фамилия, а не человек по фамилии «Удали».
#:
#: Откуда взялось. Регулярка ФИО берёт два заглавных слова подряд, а рядом
#: стоящий маркер («ФИО:», «Водитель», «Директор») делает такое совпадение
#: допустимым. В документе, куда попала команда оператора, маска закрывала
#: ДВА слова одним токеном: «ФИО: Удали Иванов» → «ФИО: <<PERSON_1>>», и
#: настоящая фамилия уезжала в облако под чужим именем. Живой случай из
#: задачи: «Удали» → <<PERSON_1>>.
#:
#: Список строится ЛЕНИВО и берётся из общей таблицы служебных слов
#: (`core/agent/words.py`) — она уже одна на весь проект (см. докстроку того
#: модуля). Плюс слова, которые командой не являются, но фамилией быть не
#: могут: разделы программы и реквизиты. Иначе «Сумма Договора», «Дата
#: Номера» и «Машина Клиента» закрывались бы как люди.
#:
#: Почему файлом, а не импортом. Обычное `from core.agent.words import …`
#: сначала выполняет `core/agent/__init__.py`, а тот тянет весь пакет агента
#: (`agent.py` → `llm.py` → сюда же) — из ядра обезличивания это даёт
#: «partially initialized module». Подмодуль ``words`` не зависит ни от чего,
#: кроме ``typing``, поэтому здесь он загружается ФАЙЛОМ: `sys.modules` при
#: этом заполняется, и обычный импорт агента позже берёт уже готовый модуль.
#: Если файла нет — работаем на собственном стоп-листе, маска не ломается.
#:
#: Обратной зависимости нет: ядро обезличивания знает про таблицу
#: служебных слов, таблица про ядро — нет.
_WORDS_MODULE = "core.agent.words"


def load_words_module():
    """Модуль общей таблицы служебных слов (или None, если недоступен)."""
    module = sys.modules.get(_WORDS_MODULE)
    if module is not None:
        return module

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "agent", "words.py")
    if not os.path.exists(path):
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(_WORDS_MODULE, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_WORDS_MODULE] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # noqa: BLE001 — маска обязана работать и без агента
        sys.modules.pop(_WORDS_MODULE, None)
        logger.warning("Таблица служебных слов не загружена (%s)", e)
        return None


def _service_words() -> frozenset:
    """Служебные слова проекта: команды, вопросы, роли (из общей таблицы)."""
    module = load_words_module()
    if module is None:
        return frozenset()

    try:
        words = (
            tuple(module.COMMAND_WORDS) + tuple(module.QUESTION_WORDS)
            + tuple(module.ASK_WORDS) + tuple(module.ROLE_WORDS)
        )
    except AttributeError as e:  # noqa: BLE001 — чужой модуль может измениться
        logger.warning("Таблица служебных слов без ожидаемых списков: %s", e)
        return frozenset()

    return frozenset(word.casefold() for word in words)


#: Разделы программы и реквизиты: фамилией быть не могут, а заглавными
#: словами в документе стоят («Договор № 5», «Сумма 80000»).
#: Падежные формы перечислены явно: сверка идёт по ПЕРВОМУ слову целиком,
#: а не по основе — основы команд ловят настоящие фамилии («Показеев»,
#: «Заполянский», «Удальцов»), и такая ошибка дороже пропущенной команды.
_SERVICE_NOUNS = frozenset({
    # Реквизиты и суммы
    "сумма", "суммы", "сумму", "сумме", "суммой", "суммах",
    "цена", "цены", "цену", "цене", "ценой",
    "дата", "даты", "дату", "дате", "датой",
    "номер", "номера", "номеру", "номером", "номере", "номеров",
    "адрес", "адреса", "адресу", "адресом",
    "статус", "статуса", "статусу", "тариф", "тарифа", "тарифы",
    "ндс", "предоплата", "оплата", "оплаты", "срок", "сроки", "срока",
    "период", "периода", "итог", "итоги", "итого", "отчёт", "отчет",
    "справка", "справки", "акт", "акта", "акты",
    # Груз и маршрут
    "груз", "груза", "грузу", "грузом", "грузы", "грузов", "грузам",
    "маршрут", "маршрута", "маршруту", "маршруты", "маршрутов",
    # Разделы программы (падежные формы — по ним маска тоже ошибалась)
    "аренда", "аренды", "аренду", "аренде", "арендой",
    "клиент", "клиента", "клиенту", "клиенты", "клиентов",
    "машина", "машины", "машину", "машине", "машиной", "машин",
    "водитель", "водителя", "водителю", "водители", "водителей",
    "договор", "договора", "договору", "договоры", "договоров",
    "заявка", "заявки", "заявку", "заявке", "заявок",
    "перевозчик", "перевозчика", "перевозчики", "перевозчиков",
    "заказчик", "заказчика", "заказчики", "заказчиков",
})

#: СТОП-ЛИСТ МАСКИРОВАНИЯ: слово, с которого НЕ начинается ФИО.
#: Проверяется в `_find_person` перед принятием кандидата (см. `_is_service_word`).
#: Вычисляется при первом обращении: список общий с агентом (`_service_words`),
#: а пакет агента к моменту импорта ядра может быть ещё не загружен.
_PERSON_STOP_CACHE: Optional[frozenset] = None


def person_stop_words() -> frozenset:
    """
    Стоп-лист маскирования ФИО: служебные слова + разделы и реквизиты.

    Вычисляется один раз за процесс. Тесты могут сбросить кэш
    (`reset_person_stop_words`), если подменили таблицу служебных слов.
    """
    global _PERSON_STOP_CACHE
    if _PERSON_STOP_CACHE is None:
        _PERSON_STOP_CACHE = _service_words() | _SERVICE_NOUNS
    return _PERSON_STOP_CACHE


def reset_person_stop_words() -> None:
    """Забывает вычисленный стоп-лист (нужно тестам и пробам)."""
    global _PERSON_STOP_CACHE
    _PERSON_STOP_CACHE = None

#: Адрес: улица и дом (маркеры + значение). Город НЕ трогаем — он не
#: идентифицирует человека и нужен модели для маршрута и поля «city».
#: Разделители внутри значения — `_HSPACE*`, а не `\s`: иначе необязательный
#: «хвост» улицы и номер дома заходили на следующую строку и съедали метку
#: следующего поля (см. `_HSPACE`).
_ADDRESS_STREET_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z])"
    r"(?:ул|улица|проспект|просп|пр-т|пр-кт|переулок|пер|проезд|шоссе|ш"
    r"|набережная|наб|площадь|пл|бульвар|б-р|тупик|туп|аллея|микрорайон|мкр)"
    r"\.?" + _HSPACE_PLUS + r"[А-ЯЁ0-9][^\s,;]{1,25}"
    r"(?:" + _HSPACE_PLUS + r"[А-ЯЁ0-9][^\s,;]{1,25})?",
    re.IGNORECASE,
)
_ADDRESS_HOUSE_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"(?:д|дом|влад|владение)\.?" + _HSPACE_STAR + r"№?" + _HSPACE_STAR
    + r"\d{1,4}" + _HSPACE_STAR + r"[А-Яа-яЁё]?"
    r"(?:" + _HSPACE_STAR + r"(?:корп|корпус|стр|строение|лит|литера)\.?"
    + _HSPACE_STAR + r"\d{1,3}[А-Яа-яЁё]?)?",
    re.IGNORECASE,
)
_ADDRESS_FLAT_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"(?:кв|квартира|офис|оф|пом|помещение|комн|комната)\.?"
    + _HSPACE_STAR + r"№?" + _HSPACE_STAR + r"\d{1,4}",
    re.IGNORECASE,
)


class _Candidate:
    """Найденное значение ПДн: границы в тексте, тип и подстрока."""

    __slots__ = ("start", "end", "kind", "value")

    def __init__(self, start: int, end: int, kind: str, value: str):
        self.start = start
        self.end = end
        self.kind = kind
        self.value = value

    @property
    def length(self) -> int:
        return self.end - self.start


def _add(candidates: List[_Candidate], match, kind: str) -> None:
    """Добавляет совпадение, обрезая окружающие пробелы."""
    value = match.group(0)
    trimmed = value.strip()
    if not trimmed:
        return
    offset = len(value) - len(value.lstrip())
    start = match.start() + offset
    candidates.append(_Candidate(start, start + len(trimmed), kind, trimmed))


def _add_span(candidates: List[_Candidate], text: str, start: int, end: int,
              kind: str) -> None:
    """
    Добавляет совпадение по ЯВНЫМ границам внутри одного match.

    Нужно там, где одно совпадение регулярки обязано стать ДВУМЯ токенами
    (серия и номер паспорта): группы регулярки дают точные границы частей.
    """
    value = text[start:end]
    trimmed = value.strip()
    if not trimmed:
        return
    offset = len(value) - len(value.lstrip())
    candidates.append(_Candidate(
        start + offset, start + offset + len(trimmed), kind, trimmed))


# ─────────────────────────────────────────────────────────────
# Детекторы
# ─────────────────────────────────────────────────────────────

def _find_email(text: str, out: List[_Candidate]) -> None:
    for match in _EMAIL_RE.finditer(text):
        _add(out, match, "EMAIL")


def _find_snils(text: str, out: List[_Candidate]) -> None:
    for match in _SNILS_RE.finditer(text):
        digits = _digits(match.group(0))
        if _snils_checksum_ok(digits):
            _add(out, match, "SNILS")


def _find_vin(text: str, out: List[_Candidate]) -> None:
    for match in _VIN_RE.finditer(text):
        value = match.group(1)
        # VIN обязательно содержит буквы: 17-значные числа — не VIN.
        if any(ch.isalpha() for ch in value):
            _add(out, match, "VIN")


def _find_documents(text: str, out: List[_Candidate]) -> None:
    """
    Паспорт и водительское удостоверение: серия 4 цифры + номер 6 цифр.

    У ПАСПОРТА серия и номер закрываются ДВУМЯ токенами (``PASSPORT_SERIES``
    и ``PASSPORT_NUMBER``), если в тексте между ними есть разделитель
    (пробел, дефис, «№»): тогда граница «серия/номер» видна модели, и она
    может вернуть оба поля по отдельности. Слитный номер без разделителя
    (``5838312160``) разделить нечем — остаётся ОДИН токен ``PASSPORT``,
    а пару «серия/номер» восстанавливает ядро разбора
    (``core.gigachat_client._normalize_document_ids``) уже после
    восстановления оригинала.

    ВУ (``LICENSE``) остаётся одним токеном: его пара и так разбирается
    постобработкой, а полей у него в разборе меньше.
    """
    for match in _DOC_NUMBER_RE.finditer(text):
        kind = _document_kind(text, match.start(), match.end())
        if not kind:
            continue
        # start(3) == end(2) — между серией и номером разделителя НЕТ.
        if kind == "PASSPORT" and match.start(3) > match.end(2):
            _add_span(out, text, match.start(1), match.end(2), "PASSPORT_SERIES")
            _add_span(out, text, match.start(3), match.end(3), "PASSPORT_NUMBER")
            continue
        _add(out, match, kind)

    # Слитный номер (1822926830) отличается от ИНН только контекстом.
    for match in _DOC_SOLID_RE.finditer(text):
        kind = _document_kind(text, match.start(), match.end())
        if kind:
            _add(out, match, kind)


def _find_plates(text: str, out: List[_Candidate]) -> None:
    for pattern in (_PLATE_CAR_RE, _PLATE_TRAILER_RE):
        for match in pattern.finditer(text):
            _add(out, match, "GOSNOMER")
    # Смешанный формат — только при явном слове-маркере рядом.
    for match in _PLATE_MIXED_RE.finditer(text):
        context = _context(text, match.start(), match.end(), 40)
        if _PLATE_CONTEXT_RE.search(context):
            _add(out, match, "GOSNOMER")


def _find_inn(text: str, out: List[_Candidate]) -> None:
    for pattern in (_INN_12_RE, _INN_10_RE):
        for match in pattern.finditer(text):
            if _inn_checksum_ok(match.group(1)):
                _add(out, match, "INN")


def _find_phones(text: str, out: List[_Candidate]) -> None:
    for pattern in (_PHONE_MOBILE_RE, _PHONE_CITY_RE):
        for match in pattern.finditer(text):
            _add(out, match, "PHONE")


def _first_token(value: str):
    """
    Первое слово совпадения: (слово для сверки, длина в тексте).

    Длина нужна, чтобы продолжить разбор РОВНО за этим словом: у ведущих
    пробелов длина другая, и позиция «после слова» по ней не считается.
    """
    head = re.split(r"[\s\-]+", value.strip(), maxsplit=1)[0]
    return head.strip(".,;:!?()\"'«»").casefold(), len(head)


def _is_service_word(value: str) -> bool:
    """
    Начинается ли совпадение со служебного слова (см. `person_stop_words`).

    Проверяется ПЕРВОЕ слово, а не любое: «Иванов Иван» — человек, даже
    если второе слово случайно совпало с ролью. Обратное («Сумма Договора»)
    человеком не является, потому что первое слово — реквизит.
    """
    if not value:
        return False
    return _first_token(value)[0] in person_stop_words()


def _person_at(text: str, start: int,
               limit: Optional[int] = None) -> Optional[_Candidate]:
    """
    Ближайший кандидат в ФИО, начинающийся в ``[start, limit)``.

    Нужен после отказа: если совпадение начиналось со служебного слова
    («Удали Иванов»), разбор продолжается ЗА этим словом — иначе настоящая
    фамилия осталась бы ненайденной. ``limit`` не даёт заглянуть за пределы
    исходного совпадения: хвост одного кандидата не должен захватывать
    начало следующего.

    Почему ``finditer``, а не ``pattern.match(text, start)``: образцы ФИО
    начинаются с нулевой проверки ``(?=…)``, и у неё тоже есть начало.
    ``match`` требует совпадения РОВНО в ``start`` и на «Иванова» (одно
    слово там, где образец ждёт два) не находит ничего, тогда как
    ``finditer`` доходит до слова и возвращает его.
    """
    for pattern in (_PERSON_THREE_RE, _PERSON_TWO_RE, _PERSON_INITIALS_RE):
        for match in pattern.finditer(text, start):
            if limit is not None and match.start(1) >= limit:
                break
            value = match.group(1)
            if not value:
                continue
            return _Candidate(match.start(1), match.end(1), "PERSON", value)
    return None


def _single_word_at(text: str, start: int,
                    limit: Optional[int] = None) -> Optional[_Candidate]:
    """
    Одно слово, похожее на фамилию, начиная с ``start``.

    Отдельный поиск нужен потому, что общий образец ФИО ждёт ДВА слова:
    на «Иванова» он не срабатывает вовсе (второго слова нет), а на «Удали
    Иванова» отдаёт оба слова вместе. Вызывается ТОЛЬКО из разбора после
    служебного слова — там, где контекст уже задан командой («Удали
    Иванова» — оператор говорит про человека).
    """
    for match in _PERSON_SINGLE_RE.finditer(text, start):
        if limit is not None and match.start(1) >= limit:
            break
        value = match.group(1)
        if not value:
            continue
        return _Candidate(match.start(1), match.end(1), "PERSON", value)
    return None


def _tail_after_service_word(text: str, start: int, end: int) -> Optional[_Candidate]:
    """
    ФИО, целиком лежащее в ХВОСТЕ совпадения, начавшегося со служебного слова.

    «Удали Иванова» — регулярка ФИО взяла два слова, но первое из них не имя,
    а команда. Хвост после неё разбирается заново, в пределах исходного
    совпадения:

      * два-три слова — общим правилом (``_person_at``);
      * ОДНО слово — отдельным поиском (``_single_word_at``). Контекст здесь
        и есть команда: «Удали Иванова» оператор пишет про человека, а не про
        слово «Иванова». «Создай заявку» под правило не попадает — «заявку»
        со строчной буквы именем не является (``_PERSON_WORD`` требует
        заглавной), а «Удали Договор» отсекает стоп-лист.

    Возвращает кандидата или None; границы не выходят за ``[start, end)``,
    поэтому хвост одного совпадения не захватывает начало следующего.
    """
    word, length = _first_token(text[start:end])
    resume = start + length
    if resume >= end:
        return None

    for candidate in (_person_at(text, resume, limit=end),
                      _single_word_at(text, resume, limit=end)):
        if candidate is None:
            continue
        if candidate.value.casefold() in _PERSON_STOPWORDS:
            continue
        if candidate.value.casefold() in person_stop_words():
            continue
        return candidate
    return None


def _accept_tail(tail: Optional[_Candidate]) -> bool:
    """
    Принимать ли ФИО, найденное В ХВОСТЕ совпадения со служебным словом.

    Общее правило `allowed` здесь не подходит: одиночная фамилия без
    отчества и без маркера ФИО («Удали Иванова») им отвергается — а она и
    есть то, что нужно закрыть. Контекст в этом случае задаёт сама команда:
    оператор пишет её про человека.

    Что остаётся проверкой: слово похоже на имя (заглавная кириллица) и не
    из стоп-листов (организации, регионы, реквизиты, служебные слова).
    """
    if tail is None or not tail.value:
        return False
    word = tail.value.strip()
    if not _PERSON_WORD_RE.fullmatch(word):
        return False
    folded = word.casefold()
    if folded in _PERSON_STOPWORDS or folded in person_stop_words():
        return False
    parts = re.split(r"[\s\-]+", word)
    if any(part.casefold() in _PERSON_STOPWORDS for part in parts if part):
        return False
    return True


def _find_person(text: str, out: List[_Candidate]) -> None:
    def allowed(value: str, context: str) -> bool:
        """
        ФИО принимается, если есть отчество или рядом слово-маркер
        (ФИО, водитель, директор, ...). Организации и регионы отсекаются
        стоп-словами, иначе «Республика Башкортостан» стало бы человеком.

        Служебные слова (команды, разделы программы, реквизиты) отсекаются
        отдельно — см. `person_stop_words`: «Удали Иванов» это команда и
        фамилия, а не человек с фамилией «Удали».
        """
        words = re.split(r"[\s\-]+", value)
        if any(word.casefold() in _PERSON_STOPWORDS for word in words if word):
            return False
        if _is_service_word(value):
            return False
        if words and _PATRONYMIC_RE.search(words[-1]):
            return True
        return bool(_PERSON_CONTEXT_RE.search(context))

    for pattern in (_PERSON_THREE_RE, _PERSON_TWO_RE, _PERSON_INITIALS_RE):
        for match in pattern.finditer(text):
            value = match.group(1)
            start, end = match.start(1), match.end(1)
            if allowed(value, _context(text, start, end)):
                out.append(_Candidate(start, end, "PERSON", value))
                continue

            # Отказали из-за служебного слова — разбираем ХВОСТ совпадения:
            # за командой может стоять настоящее ФИО («ФИО: Удали Иванов» →
            # маскируется «Иванов»).
            if not _is_service_word(value):
                continue
            tail = _tail_after_service_word(text, start, end)
            if _accept_tail(tail):
                out.append(tail)


def _find_address(text: str, out: List[_Candidate]) -> None:
    for pattern in (_ADDRESS_STREET_RE, _ADDRESS_HOUSE_RE, _ADDRESS_FLAT_RE):
        for match in pattern.finditer(text):
            _add(out, match, "ADDRESS")


#: Что допустимо между двумя частями одного адреса. Между «г. Москва» и
#: «ул. Тверская» стоит «, » — это тот же адрес. Если между частями
#: оказалось слово, склеивать нельзя: это уже другой фрагмент текста.
_ADDR_GAP_RE = re.compile(r"^[\s,;.\-–—/]{0,4}$")

#: Части, которые сами по себе делают группу адресом: улица и её аналоги.
#: Города, области, сёла и индексы — это ГЕОГРАФИЯ: модуль их не маскирует
#: (они не идентифицируют человека и нужны модели для маршрута и поля city),
#: но в составе адреса с улицей они закрываются вместе с ним.
#: Правило заодно отсекает ложные срабатывания AddrExtractor: «с Остапом
#: Бендером» он считает селом (маркер «с» — сокращение от «село»), а «180300»
#: в «Стоимость 180300 руб.» — индексом. Улицы в таких фрагментах нет.
_ADDR_ANCHOR_TYPES = frozenset({
    "улица", "проспект", "шоссе", "проезд", "переулок",
    "набережная", "площадь", "бульвар",
})


def _merge_addr_parts(text: str, matches) -> List[Tuple[int, int]]:
    """
    Склеивает части адреса Natasha в цельные адреса.

    ``AddrExtractor`` отдаёт адрес ПО ЧАСТЯМ (``г. Москва``, ``ул. Тверская``,
    ``д. 5``), а нужен один span на весь адрес: иначе на месте адреса
    получилось бы три токена вместо одного. Части склеиваются, только если
    между ними нет ничего, кроме разделителей, — иначе это разные адреса.

    Отбрасываются две вещи:

      * части без типа (``type is None``) — так Natasha помечает любое
        слово с заглавной буквы, и «Иванов Иван Иванович, г. Москва»
        превратилось бы в один «адрес» вместе с ФИО;
      * группы без «якоря» (``_ADDR_ANCHOR_TYPES``) — без улицы это
        география или прямое ложное срабатывание.
    """
    merged: List[List[Any]] = []
    current: Optional[List[Any]] = None
    for match in matches:
        part_type = getattr(getattr(match, "fact", None), "type", None)
        if part_type is None:
            continue
        start, end = match.start, match.stop
        anchor = part_type in _ADDR_ANCHOR_TYPES
        if current is None:
            current = [start, end, anchor]
            continue
        if _ADDR_GAP_RE.match(text[current[1]:start]):
            current[1] = end
            current[2] = current[2] or anchor
            continue
        merged.append(current)
        current = [start, end, anchor]
    if current is not None:
        merged.append(current)
    return [(item[0], item[1]) for item in merged if item[2]]


def _widen_addr_over_regular(start: int, end: int,
                             regular: List[_Candidate]) -> Tuple[int, int]:
    """
    Расширяет span Natasha до регулярных находок ВНУТРИ него.

    «Бештаугорское шоссе 17»: Natasha закрывает только название улицы,
    а номер дома ловит регулярка («шоссе 17»). Без расширения номер ушёл бы
    в модель открытым текстом. Совпадение с тем же началом, что и у Natasha,
    не трогаем: там по приоритету выигрывает регулярка.
    """
    changed = True
    while changed:
        changed = False
        for item in regular:
            if start < item.start < end and item.end > end:
                end = item.end
                changed = True
    return start, end


def _find_person_natasha(text: str, out: List[_Candidate]) -> None:
    """
    ФИО через Natasha NER.

    Ловит то, что регулярка пропускает: иностранные имена без отчества,
    фамилии без контекстных слов. Приоритет ниже регулярного PERSON:
    если оба нашли одно и то же — выиграет регулярка (она точнее
    по формату).

    Из разметки берутся только PER: LOC («Россия», «Москва») и ORG
    («ООО «Ромашка»») не маскируются.
    """
    nlp = _natasha_pipeline()
    if nlp is None:
        return

    try:
        from natasha import Doc, PER
    except ImportError:
        return

    try:
        doc = Doc(text)
        doc.segment(nlp["segmenter"])
        doc.tag_ner(nlp["ner_tagger"])
    except Exception as exc:
        logger.debug("Natasha NER не сработал (%s)", type(exc).__name__)
        return

    for span in doc.spans:
        if span.type != PER:
            continue
        value = (span.text or "").strip()
        if not value:
            continue
        # NER иногда принимает за фамилию слово-метку («Водитель»,
        # «Директор»), название организации или КОМАНДУ оператора
        # («Удали Иванова»). Отсекаем их тем же списком стоп-слов, что и
        # регулярный детектор ФИО.
        words = re.split(r"[\s\-]+", value)
        if any(word.casefold() in _PERSON_STOPWORDS for word in words if word):
            continue
        if _is_service_word(value):
            # Span начинается со служебного слова: если внутри него есть
            # настоящая фамилия («Удали Иванова»), маскируем ТОЛЬКО её —
            # тот же разбор, что и в регулярном слое.
            tail = _tail_after_service_word(text, span.start, span.stop)
            if _accept_tail(tail):
                out.append(tail)
            continue
        out.append(_Candidate(span.start, span.stop, "PERSON_NATASHA", value))


def _find_address_natasha(text: str, out: List[_Candidate]) -> None:
    """
    Адрес через Natasha AddressExtractor.

    Даёт ЦЕЛЬНЫЙ span («г. Москва, ул. Тверская, д. 5»), а не отдельные
    «ул. Тверская» и «д. 5». Это то, что нужно: один токен <<ADDRESS_N>>
    на весь адрес.
    """
    nlp = _natasha_pipeline()
    if nlp is None:
        return

    extractor = nlp.get("addr_extractor")
    if extractor is None:
        return

    try:
        matches = list(extractor(text))
    except Exception as exc:
        logger.debug(
            "Natasha AddressExtractor не сработал (%s)",
            type(exc).__name__,
        )
        return

    # Регулярные находки нужны, чтобы Natasha не отменила ни одну из них:
    # номер дома, который регулярка поймала, обязан остаться закрытым.
    regular: List[_Candidate] = []
    _find_address(text, regular)

    for start, end in _merge_addr_parts(text, matches):
        start, end = _widen_addr_over_regular(start, end, regular)
        raw = text[start:end]
        value = raw.strip()
        if not value:
            continue
        # Как и в _add: окружающие пробелы в токен не попадают.
        offset = len(raw) - len(raw.lstrip())
        out.append(_Candidate(
            start + offset, start + offset + len(value),
            "ADDRESS_NATASHA", value,
        ))


_DETECTORS = (
    _find_email,
    _find_snils,
    _find_vin,
    _find_documents,
    _find_plates,
    _find_inn,
    _find_phones,
    _find_person,
    _find_address,
    _find_person_natasha,
    _find_address_natasha,
)


def _detect(text: str) -> List[_Candidate]:
    """Все совпадения в тексте, без пересечений, в порядке появления."""
    found: List[_Candidate] = []
    for detector in _DETECTORS:
        detector(text, found)

    # При равном начале выигрывает более приоритетный тип, затем более
    # длинное совпадение; остальные отбрасываются как пересекающиеся.
    found.sort(key=lambda item: (item.start, -_PRIORITY.get(item.kind, 0), -item.length))

    result: List[_Candidate] = []
    last_end = -1
    for item in found:
        if item.start < last_end:
            continue
        result.append(item)
        last_end = item.end
    return result


# ─────────────────────────────────────────────────────────────
# Псевдонимизатор
# ─────────────────────────────────────────────────────────────

class Pseudonymizer:
    """
    Обратимое обезличивание текста и ответа модели.

    Экземпляр не хранит состояния: маппинг возвращается вызывающей стороне
    и живёт только в её локальной переменной. Один экземпляр можно
    безопасно использовать из нескольких потоков.

    :param mask_addresses: маскировать ли улицу, дом и квартиру. По
        умолчанию True — так работает распознавание документов. Диалог с
        Максимилианом ставит False: адрес — это данные, с которыми он
        РАБОТАЕТ (стандартизация через DaData, подстановка в форму), а в
        маске инструмент возвращал бы модели ``<<ADDRESS_1>>`` вместо
        адреса — и ответ становился неразборчивым. Города и регионы не
        маскируются и по умолчанию: они не идентифицируют человека.
    """

    def __init__(self, mask_addresses: bool = True):
        self.mask_addresses = bool(mask_addresses)

    def anonymize(self, text: str) -> Tuple[str, Dict[str, str]]:
        """
        Заменяет персональные данные на плейсхолдеры ``<<TYPE_N>>``.

        :param text: исходный текст (может быть пустым)
        :return: пара ``(safe_text, mapping)``, где mapping — словарь
                 «токен → оригинал». Mapping нельзя логировать и сохранять
                 на диск: он существует только на время одной операции.
        """
        original = "" if text is None else str(text)
        if not original:
            return "", {}

        candidates = _detect(original)
        if not self.mask_addresses:
            # Адрес оставляем открытым: с ним работает агент. Остальные
            # сущности (ФИО, паспорта, телефоны) маскируются как обычно.
            candidates = [item for item in candidates
                          if item.kind.replace("_NATASHA", "") != "ADDRESS"]
        if not candidates:
            logger.debug("Обезличивание: сущностей не найдено")
            return original, {}

        counters: Dict[str, int] = {}
        mapping: Dict[str, str] = {}
        # Дедупликация строго по точному значению: тогда восстановление
        # возвращает исходное написание в каждом месте текста.
        tokens_by_value: Dict[Tuple[str, str], str] = {}

        parts: List[str] = []
        cursor = 0
        for item in candidates:
            # Natasha-типы нужны только для приоритета: в маппинг и в токены
            # они попадают как PERSON и ADDRESS (иначе в describe() и в логах
            # появились бы отдельные категории, которых нет в TOKEN_TYPES).
            kind = item.kind.replace("_NATASHA", "")
            key = (kind, item.value)
            token = tokens_by_value.get(key)
            if token is None:
                counters[kind] = counters.get(kind, 0) + 1
                token = f"<<{kind}_{counters[kind]}>>"
                tokens_by_value[key] = token
                mapping[token] = item.value
            parts.append(original[cursor:item.start])
            parts.append(token)
            cursor = item.end
        parts.append(original[cursor:])

        # В лог — только типы и количество (значения не попадают никогда).
        logger.debug(
            "Обезличивание выполнено: %s | всего=%d",
            ", ".join(f"{kind}={count}" for kind, count in sorted(counters.items())),
            len(mapping),
        )
        return "".join(parts), mapping

    def restore(self, text: str, mapping: Dict[str, str],
                drop_unknown: bool = True) -> str:
        """
        Восстанавливает оригиналы по маппингу.

        Токены, которые модель испортила (пробелы, дефисы, регистр,
        потерянные угловые скобки), распознаются по нормализованному имени.

        :param text: ответ модели (или любой текст с токенами)
        :param mapping: словарь из :meth:`anonymize`
        :param drop_unknown: вычищать остатки неизвестных плейсхолдеров
                             (иначе они остаются в тексте как есть)
        :return: текст с оригиналами
        """
        if not text or not mapping:
            return "" if text is None else str(text)

        index = _mapping_index(mapping)
        if not index:
            return str(text)

        unknown: List[str] = []

        def replace_angle(match) -> str:
            name = token_name(match.group(1))
            if name in index:
                return index[name]
            unknown.append(name)
            return "" if drop_unknown else match.group(0)

        result = _TOKEN_RE.sub(replace_angle, str(text))

        def replace_bare(match) -> str:
            name = f"{match.group(1)}_{match.group(2)}"
            if name in index:
                return index[name]
            if drop_unknown and match.group(1) in TOKEN_TYPES:
                unknown.append(name)
                return ""
            return match.group(0)

        result = _BARE_TOKEN_RE.sub(replace_bare, result)

        if unknown:
            # В лог — только имена токенов, без значений.
            logger.warning(
                "Токены не найдены в маппинге и не восстановлены: %s",
                ", ".join(sorted(set(unknown))),
            )
        return result

    def restore_json(self, data: Any, mapping: Dict[str, str],
                     drop_unknown: bool = True) -> Any:
        """
        Восстанавливает значения во всей структуре ответа модели.

        Работает с dict/list/str рекурсивно и не изменяет входные объекты.
        """
        if not mapping:
            return data
        if isinstance(data, dict):
            return {
                self.restore(key, mapping, drop_unknown):
                    self.restore_json(value, mapping, drop_unknown)
                for key, value in data.items()
            }
        if isinstance(data, (list, tuple)):
            return [self.restore_json(item, mapping, drop_unknown) for item in data]
        if isinstance(data, str):
            return self.restore(data, mapping, drop_unknown)
        return data

    @staticmethod
    def describe(mapping: Dict[str, str]) -> str:
        """
        Безопасное описание маппинга для логов и аудита: типы и количество.

        Значения не раскрываются — только ``ADDRESS=2, PERSON=1``.
        """
        counters: Dict[str, int] = {}
        for token in (mapping or {}):
            name = token_name(token)
            kind = name.rsplit("_", 1)[0] if "_" in name else name
            counters[kind] = counters.get(kind, 0) + 1
        return ", ".join(f"{kind}={count}" for kind, count in sorted(counters.items()))
