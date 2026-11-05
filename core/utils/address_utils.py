#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утилиты разбора адресов: извлечение города и отделение наименования салона.

Модуль вынесен из db/database.py (Шаг 1 рефакторинга), чтобы:
  - доменная логика не жила в слое БД;
  - эту же функцию мог использовать core/contract_data.py для поля city;
  - не появлялось второй копии парсера адресов.

Поведение extract_city/clean_address_from_index полностью совпадает с прежними
db.database._extract_city и db.database._clean_address_from_index.

Правило split_salon_name() — одно на всё приложение: справочник салонов
(импорт из Excel) и любой другой импорт адресов.
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("core.address_utils")


# Слова, которые НЕ являются городом
NOT_CITY = {
    "г", "город", "ул", "улица", "пр", "проспект", "пр-т", "пр-кт",
    "пер", "переулок", "проезд", "ш", "шоссе", "наб", "набережная",
    "пл", "площадь", "б-р", "бульвар", "туп", "тупик", "аллея",
    "д", "дом", "к", "корп", "корпус", "стр", "строение", "офис",
    "пом", "помещение", "кв", "квартира", "влад", "владение",
    "обл", "область", "край", "район", "р-н", "респ", "республика",
    "пос", "поселок", "п", "село", "с", "деревня", "д", "тер",
    "мкр", "микрорайон", "аг", "аул", "х", "хутор", "рп", "г.о",
    "муниципальный", "сельское", "поселение", "округ", "автодорога",
    "тракт", "проезд", "зона", "промзона",
}


def clean_address_from_index(address: str) -> str:
    """
    Убирает ведущий индекс (5-6 цифр) в начале строки.
    Поддерживает: '183052,', '142111.', '305000 ', '42034 '.
    """
    s = str(address).strip()

    # Ведущие цифры (4-6) + возможный разделитель (,. -) + пробелы
    s = re.sub(r'^\s*\d{4,6}\s*[,.\-]?\s*', '', s)

    # Убираем ведущую пунктуацию
    s = re.sub(r'^[\s,;.\-–—]+', '', s)

    return s.strip()


# ─────────────────────────────────────────────────────────────
# Наименование салона в хвосте адреса
# ─────────────────────────────────────────────────────────────

#: Организационно-правовые формы, по которым узнаётся наименование
#: юридического лица в конце адреса. Длинные формы стоят раньше коротких:
#: иначе «ПАО» распозналось бы как «АО».
ORG_FORMS = (
    "ФГУП", "ГУП", "МУП", "ПАО", "НАО", "ОАО", "ЗАО", "ООО", "ТОО", "АО", "ИП",
)

_ORG_FORM_PATTERN = "|".join(ORG_FORMS)
_ORG_FORM_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z])(" + _ORG_FORM_PATTERN + r")(?![А-Яа-яЁёA-Za-z])",
    re.IGNORECASE,
)
_QUOTES_RE = re.compile(r"[«»\"'„“”`]")
_LEADING_PUNCT_RE = re.compile(r"^[\s,;.\-–—]+")
_TRAILING_PUNCT_RE = re.compile(r"[\s,;.\-–—]+$")

#: Слова, с которых начинается адресный элемент, а не наименование салона.
#: Нужны, когда имя приклеено к адресу без запятой:
#: «строение 11 АВТОМАГ (Нижневартовск) ООО».
_ADDRESS_MARKERS = (
    "д.", "д ", "дом", "стр.", "строение", "соор.", "сооружение",
    "влад.", "владение", "корп.", "корпус", "литера", "зд.", "здание",
    "уч.", "участок", "км", "пр-т", "проспект", "ул.", "улица",
    "ш.", "шоссе", "пер.", "переулок", "наб.", "набережная",
    "пл.", "площадь", "проезд", "тер.", "территория", "мкр.",
    "микрорайон", "г.", "город", "п.", "пос.", "с.", "село", "№",
)


def normalize_salon_text(text: str) -> str:
    """
    Нормализует строку для сравнения адреса и наименования.

    Регистр, «ё», кавычки и лишние пробелы не должны мешать понять, что
    «ООО "СК-МОТОРС ПРЕМИУМ"» и «ООО «СК-МОТОРС ПРЕМИУМ»» — одно и то же.
    """
    value = str(text or "").casefold().replace("ё", "е")
    value = _QUOTES_RE.sub("", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,;.-–—")


def _is_usable_address(text: str) -> bool:
    """Адрес после обрезки не пуст и содержит хоть одну букву или цифру."""
    value = text.strip(" ,;.-–—")
    return len(value) >= 3 and bool(re.search(r"[А-Яа-яЁёA-Za-z0-9]", value))


def _clean_address(text: str) -> str:
    """Убирает лишние разделители и пробелы по краям адреса."""
    value = _LEADING_PUNCT_RE.sub("", str(text or ""))
    value = _TRAILING_PUNCT_RE.sub("", value)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _clean_salon_name(text: str) -> str:
    """Наименование без лишних пробелов и разделителей по краям."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value.strip(" ,;.-–—")


def _cut_known_name(address: str, salon_name: str) -> Optional[str]:
    """
    Отрезает от адреса хвост, ТОЧНО совпадающий с колонкой «Наименование».

    Сравнение идёт по нормализованным строкам (без кавычек и регистра), а
    режется исходный текст — поэтому в адресе не остаётся запятой-сироты.
    """
    norm_name = normalize_salon_text(salon_name)
    if not norm_name or not normalize_salon_text(address).endswith(norm_name):
        return None

    parts = [part.strip() for part in address.split(",") if part.strip()]
    # Последние сегменты целиком образуют наименование.
    for cut in range(len(parts) - 1, 0, -1):
        tail = ", ".join(parts[cut:]).strip()
        if normalize_salon_text(tail) == norm_name:
            rest = _clean_address(", ".join(parts[:cut]))
            if _is_usable_address(rest):
                return rest

    # Обрезку «по длине имени» здесь НЕ делаем: если в колонке лежит
    # короткое имя («Ромашка»), а в адресе — «ООО «Ромашка»», такая обрезка
    # оставила бы в адресе «ООО «». Такие случаи разбирает _cut_org_tail —
    # он отрезает наименование целиком.
    return None


def _starts_with_address_marker(text: str) -> bool:
    """Сегмент начинается с адресного элемента («строение 11 …»), а не с имени."""
    low = str(text or "").strip().lower()
    return any(low.startswith(marker) for marker in _ADDRESS_MARKERS)


def _split_mixed_tail(segment: str) -> Optional[Tuple[str, str]]:
    """
    Делит сегмент, в котором адрес и наименование слиплись.

    Примеры:
      «строение 11 АВТОМАГ (Нижневартовск) ООО» → («строение 11», «АВТОМАГ …»)
      «д. 5 ООО «Ромашка»»                     → («д. 5», «ООО «Ромашка»»)
    """
    # 1) Наименование стоит ПЕРЕД организационно-правовой формой:
    #    «строение 11 АВТОМАГ (Нижневартовск) ООО».
    #    Начало имени — заглавная буква без флага IGNORECASE, иначе совпало бы
    #    и служебное «строение».
    match = re.search(
        r"(?P<name>[А-ЯЁA-Z][^,;]*?)\s+((?i:" + _ORG_FORM_PATTERN + r"))\s*$",
        segment,
    )
    if match:
        name = _clean_salon_name(
            match.group("name") + " " + match.group(2)
        )
        prefix = segment[: match.start()].strip()
        if name and _is_usable_address(prefix):
            return _clean_address(prefix), name

    # 2) Форма стоит в начале названия: «д. 5 ООО «Ромашка»».
    form = _ORG_FORM_RE.search(segment)
    if form:
        name = _clean_salon_name(segment[form.start():])
        prefix = segment[: form.start()].strip()
        # Если после формы ничего нет, это не название, а обрывок адреса.
        if (len(normalize_salon_text(name))
                > len(normalize_salon_text(form.group(0))) + 1
                and _is_usable_address(prefix)):
            return _clean_address(prefix), name

    return None


def _cut_org_tail(address: str) -> Optional[Tuple[str, str]]:
    """
    Отрезает последний сегмент адреса, если это наименование организации.

    Работает только при наличии запятой: без неё нельзя отличить имя от
    улицы, поэтому такой случай оставляем без изменений (кроме точного
    совпадения с колонкой «Наименование» — см. _cut_known_name).
    """
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) < 2:
        return None

    last = parts[-1]
    if not _ORG_FORM_RE.search(last):
        return None

    if _starts_with_address_marker(last):
        mixed = _split_mixed_tail(last)
        if mixed is None:
            return None
        prefix, name = mixed
        rest = _clean_address(", ".join(parts[:-1] + [prefix]))
        if not _is_usable_address(rest):
            return None
        return rest, name

    rest = _clean_address(", ".join(parts[:-1]))
    if not _is_usable_address(rest):
        return None
    return rest, _clean_salon_name(last)


def split_salon_name(address: str, salon_name: str = "") -> Tuple[str, str]:
    """
    Отделяет наименование салона от адреса.

    :param address: адрес из файла (может содержать наименование в хвосте).
    :param salon_name: значение колонки «Наименование салона» (может быть пустым).
    :return: (чистый адрес, наименование салона).

    Правила:
      1. если адрес заканчивается на значение колонки — отрезаем его;
      2. иначе, если последний сегмент после запятой содержит организационно-
         правовую форму (ООО, АО, ПАО, …), считаем его наименованием;
      3. если наименование в колонке уже есть, оно важнее найденного в адресе;
      4. операция идемпотентна: повторный вызов ничего не меняет.

    Пустой адрес не возвращается: если после обрезки ничего не осталось,
    отдаётся исходная строка (лучше сохранить запись, чем потерять её).
    """
    # Чистые строки НЕ нормализуем: в справочнике встречаются двойные
    # пробелы и переносы строк внутри адреса, и трогать их без причины
    # нельзя. Нормализация нужна только для сравнения — она внутри правил.
    address_text = str(address or "").strip()
    name_text = str(salon_name or "").strip()
    if not address_text:
        return "", name_text

    cut = _cut_known_name(address_text, name_text)
    if cut is not None:
        return cut, name_text

    tail = _cut_org_tail(address_text)
    if tail is not None:
        clean_address, extracted_name = tail
        return clean_address, name_text or extracted_name

    return address_text, name_text


def extract_city(address: str) -> str:
    """
    Извлекает название города из адреса.
    Возвращает строку для сортировки (в нижнем регистре).

    Примеры:
      '183052, г.Мурманск, пр.Кольский, д.53' → 'мурманск'
      '109316, г. Москва, Волгоградский пр-т'  → 'москва'
      'ул. Крылатая, 12/1, Краснодар, ...'     → 'краснодар'
      '450027, Республика Башкортостан, г.Уфа, ...' → 'уфа'
    """
    if not address:
        return ""

    s = clean_address_from_index(address)

    # ── 1. Ищем "г. Город" или "город Город" ──
    # Поддерживаем: "г.Москва", "г. Москва", "г Москва", "город Москва"
    match = re.search(
        r'(?:^|[\s,;])(?:г\.?|город)\s*([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)',
        s
    )
    if match:
        city = match.group(1).strip()
        if city.lower() not in NOT_CITY:
            return city.lower()

    # ── 2. Ищем "г.о. Город" или "городской округ Город" ──
    match = re.search(
        r'(?:г\.о\.?|городской\s+округ)\s*([А-ЯЁ][а-яё\-]+)',
        s
    )
    if match:
        city = match.group(1).strip()
        if city.lower() not in NOT_CITY:
            return city.lower()

    # ── 3. Ищем слово перед запятой, которое не «ул», «обл», «респ» и т.п. ──
    # Например: "ул. Крылатая, 12/1, Краснодар, Краснодарский край"
    parts = re.split(r'[,\n;]', s)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Убираем "г. ", "ул. ", "пр. " и т.п.
        part_clean = re.sub(r'^(?:г\.?|ул\.?|пр\.?|пр\-т\.?|ш\.?|пер\.?|наб\.?)\s*', '', part, flags=re.IGNORECASE)
        part_clean = part_clean.strip()

        # Если это одно слово с большой буквы и не служебное — это город
        words = part_clean.split()
        if not words:
            continue

        first_word = words[0]
        if (first_word and first_word[0].isupper()
                and first_word.lower() not in NOT_CITY
                and len(first_word) > 2):
            # Дополнительно: если в части нет «ул», «д», «кв» — вероятно, это город
            if not re.search(r'\b(?:ул|д|кв|стр|корп|офис|пом)\b', part.lower()):
                return first_word.lower()

    # ── 4. Fallback: первое слово с большой буквы ──
    words = re.findall(r'[А-ЯЁ][а-яё\-]+', s)
    for w in words:
        if w.lower() not in NOT_CITY and len(w) > 2:
            return w.lower()

    # ── 5. Совсем fallback: первые 20 символов строки ──
    return s[:20].lower()


def city_for_document(address: str, default: str = "Москва") -> str:
    """
    Город для подстановки в договор: «Мурманск», «Санкт-Петербург».

    Отличие от extract_city() — регистр: в шаблоне договора стоит
    «г. {{city}}», поэтому нужен город с заглавной буквы и без префикса «г.».
    """
    city = extract_city(address)
    if not city:
        return default
    return city.title()
