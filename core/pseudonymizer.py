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
  * **Без внешних зависимостей.** Только стандартная библиотека (``re``,
    ``logging``) — детекторы построены на регулярных выражениях,
    контрольных суммах (ИНН, СНИЛС) и контекстных проверках.
  * **Обратимость.** ``restore(anonymize(text)[0], anonymize(text)[1]) == text``
    для любого текста: совпадения не пересекаются, а токен хранит точную
    исходную подстроку.

Устойчивость к «испорченным» токенам: модель может вернуть ``<<PERSON 1>>``,
``<<person-1>>`` или ``PERSON_1`` вместо ``<<PERSON_1>>`` — при
восстановлении имя токена нормализуется (регистр, пробелы, дефисы,
подчёркивания), поэтому такие варианты распознаются.

Если токен всё равно не найден в маппинге, значение не выдумывается:
остаток плейсхолдера вычищается (``drop_unknown=True``), а в лог уходит
WARNING с именем токена — без оригинала.

Ограничение (осознанное): модуль обезличивает только текст. Изображения,
которые уходят в GigaChat Vision, не маскируются.
"""

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("core.pseudonymizer")

#: Типы сущностей, для которых строятся плейсхолдеры.
#: Порядок в кортеже = порядок описания в логах и в отчётах.
TOKEN_TYPES = (
    "EMAIL",
    "SNILS",
    "VIN",
    "PASSPORT",
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
_PRIORITY = {
    "EMAIL": 100,
    "SNILS": 90,
    "VIN": 85,
    "PASSPORT": 80,
    "LICENSE": 75,
    "GOSNOMER": 70,
    "INN": 65,
    "PHONE": 55,
    "PERSON": 45,
    "ADDRESS": 30,
}

# ─────────────────────────────────────────────────────────────
# Токены
# ─────────────────────────────────────────────────────────────

#: Токен в «правильном» виде: <<PERSON_1>>.
_TOKEN_RE = re.compile(r"<<([^<>]{1,60})>>")

#: Токен, у которого модель потеряла угловые скобки: PERSON_1, PERSON 1.
_BARE_TOKEN_RE = re.compile(r"(?<![\w<])([A-Z]{2,12})[_ ](\d{1,3})(?![\w>])")

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
    """Индекс «нормализованное имя токена → оригинал» для быстрого поиска."""
    index: Dict[str, str] = {}
    for token, value in (mapping or {}).items():
        name = token_name(token)
        if name and name not in index:
            index[name] = "" if value is None else str(value)
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


def _inn_checksum_ok(digits: str) -> bool:
    """Контрольная сумма ИНН (алгоритм ФНС) — 10 или 12 цифр."""
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        check = sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10
        return check == int(digits[9])
    if len(digits) == 12:
        weights_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        weights_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        check_11 = sum(int(d) * w for d, w in zip(digits, weights_11)) % 11 % 10
        check_12 = sum(int(d) * w for d, w in zip(digits, weights_12)) % 11 % 10
        return check_11 == int(digits[10]) and check_12 == int(digits[11])
    return False


def _snils_checksum_ok(digits: str) -> bool:
    """Контрольная сумма СНИЛС — 11 цифр."""
    if len(digits) != 11:
        return False
    base = digits[:9]
    total = sum(int(d) * (9 - i) for i, d in enumerate(base))
    check = total % 101
    if check == 100:
        check = 0
    return check == int(digits[9:])


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

#: ФИО: два-три слова с заглавной кириллицей, либо «Фамилия И.О.».
#: Сканирование идёт через lookahead: обычный finditer пропускает
#: перекрывающиеся кандидаты, и в «Водитель Сергеев Сергей» фамилия
#: терялась бы, потому что первым совпадением идёт «Водитель Сергеев».
_PERSON_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_PERSON_THREE_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + r"\s+" + _PERSON_WORD + r"\s+"
    + _PERSON_WORD + r"(?![А-Яа-яЁё])))"
)
_PERSON_TWO_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + r"\s+" + _PERSON_WORD
    + r"(?![А-Яа-яЁё])))"
)
_PERSON_INITIALS_RE = re.compile(
    r"(?=((?<![А-Яа-яЁё])" + _PERSON_WORD + r"\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.))"
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

#: Адрес: улица и дом (маркеры + значение). Город НЕ трогаем — он не
#: идентифицирует человека и нужен модели для маршрута и поля «city».
_ADDRESS_STREET_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z])"
    r"(?:ул|улица|проспект|просп|пр-т|пр-кт|переулок|пер|проезд|шоссе|ш"
    r"|набережная|наб|площадь|пл|бульвар|б-р|тупик|туп|аллея|микрорайон|мкр)"
    r"\.?\s+[А-ЯЁ0-9][^\s,;]{1,25}(?:\s+[А-ЯЁ0-9][^\s,;]{1,25})?",
    re.IGNORECASE,
)
_ADDRESS_HOUSE_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"(?:д|дом|влад|владение)\.?\s*№?\s*\d{1,4}\s*[А-Яа-яЁё]?"
    r"(?:\s*(?:корп|корпус|стр|строение|лит|литера)\.?\s*\d{1,3}[А-Яа-яЁё]?)?",
    re.IGNORECASE,
)
_ADDRESS_FLAT_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z0-9])"
    r"(?:кв|квартира|офис|оф|пом|помещение|комн|комната)\.?\s*№?\s*\d{1,4}",
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
    """Паспорт и водительское удостоверение: серия 4 цифры + номер 6 цифр."""
    for match in _DOC_NUMBER_RE.finditer(text):
        kind = _document_kind(text, match.start(), match.end())
        if kind:
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


def _find_person(text: str, out: List[_Candidate]) -> None:
    def allowed(value: str, context: str) -> bool:
        """
        ФИО принимается, если есть отчество или рядом слово-маркер
        (ФИО, водитель, директор, ...). Организации и регионы отсекаются
        стоп-словами, иначе «Республика Башкортостан» стало бы человеком.
        """
        words = re.split(r"[\s\-]+", value)
        if any(word.casefold() in _PERSON_STOPWORDS for word in words if word):
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


def _find_address(text: str, out: List[_Candidate]) -> None:
    for pattern in (_ADDRESS_STREET_RE, _ADDRESS_HOUSE_RE, _ADDRESS_FLAT_RE):
        for match in pattern.finditer(text):
            _add(out, match, "ADDRESS")


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
    """

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
            key = (item.kind, item.value)
            token = tokens_by_value.get(key)
            if token is None:
                counters[item.kind] = counters.get(item.kind, 0) + 1
                token = f"<<{item.kind}_{counters[item.kind]}>>"
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
