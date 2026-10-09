#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Синтетические шаблоны документов для стресс-теста распознавания (ЧАСТЬ 2B).

    python tools/make_doc_templates.py            # превью в tests/_tmp/templates_preview
    python tools/make_doc_templates.py --out DIR

Пять шаблонов — паспорт РФ, водительское удостоверение, банковские реквизиты,
ПТС/СТС, ИНН/ОГРН. Каждый шаблон описывает ТОЛЬКО РАСПОЛОЖЕНИЕ ПОЛЕЙ и
оформление: пропорции бланка, порядок блоков, набор полей, кегли и начертания.
Значения подставляет вызывающий код — синтетический генератор
(`tools/make_synthetic_docs.py`).

ОТКУДА ВЗЯТА СТРУКТУРА
-----------------------
Из локального разбора папки оператора (`tools/doc_structure.py`,
`tests/_tmp/doc_structure.md`): пропорции бланков, набор полей и порядок
блоков. Ни один реальный документ в шаблон не копировался: здесь нет ни
одного байта из сканов, только геометрия и имена полей.

ЗАЧЕМ ШАБЛОНЫ, А НЕ ГОТОВЫЕ КАРТИНКИ
------------------------------------
Распознавание проверяется на 1000 сценариев с разными данными и разным
качеством рендера (поворот, размытие, шум, блики). Хранить 1000 картинок
в репозитории нельзя — они большие; поэтому в git (при необходимости) идут
шаблоны, а картинки и ground truth собираются прогоном за секунды.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "tests" / "_tmp" / "templates_preview"

#: Точка на дюйм для «печатного» рендера. 300 dpi — предел, на котором
#: Tesseract показывает себя лучше всего; выше растёт только вес картинки.
DPI = 300

#: Сколько пикселей в миллиметре при выбранном DPI.
PX_PER_MM = DPI / 25.4

#: Физические размеры бланков в миллиметрах.
SIZES_MM = {
    # Паспорт РФ: 125×88 мм (внутренний разворот, одна страница).
    "passport": (125.0, 88.0),
    # Водительское удостоверение: 85,6×54 мм (ISO/IEC 7810 ID-1).
    "license": (85.6, 54.0),
    # СТС: 100×75 мм; ПТС: 210×148 мм (A5).
    "sts": (100.0, 75.0),
    "pts": (210.0, 148.0),
    # Реквизиты и ИНН/ОГРН — выписки на A4.
    "bank": (210.0, 297.0),
    "inn": (210.0, 297.0),
}

#: Кандидаты шрифтов: сначала «как в бланке» (Times New Roman для паспорта,
#: Arial для остального), затем свободные замены, затем что найдётся.
#:
#: Почему у цифровых полей («mono») в кандидатах стоит Arial, а не Consolas:
#: Consolas на замере читает 20-значный счёт как «3010181049…» — одна цифра
#: теряется, и реквизит становится неверным. Arial на том же замере читает
#: все цифровые поля без ошибок, поэтому «моноширинность» здесь уступлена
#: читаемости: в бланках реквизитов шрифт и так пропорциональный.
FONT_CANDIDATES = {
    "serif": ("times.ttf", "Times New Roman.ttf", "DejaVuSerif.ttf",
              "LiberationSerif-Regular.ttf", "georgia.ttf"),
    "serif_bold": ("timesbd.ttf", "Times New Roman Bold.ttf",
                   "DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"),
    "sans": ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
             "LiberationSans-Regular.ttf", "segoeui.ttf"),
    "sans_bold": ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
                  "LiberationSans-Bold.ttf", "segoeuib.ttf"),
    "mono": ("arial.ttf", "Arial.ttf", "DejaVuSansMono.ttf",
             "consola.ttf", "cour.ttf"),
}

_FONT_CACHE: Dict[Tuple[str, int], Any] = {}


def _font_dirs() -> List[Path]:
    """Папки, где лежат шрифты Windows и рядом лежащие свободные наборы."""
    dirs = []
    windir = Path("C:/Windows/Fonts")
    if windir.is_dir():
        dirs.append(windir)
    local = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    if local.is_dir():
        dirs.append(local)
    # Шрифты Pillow: DejaVu идут в комплекте с библиотекой.
    try:
        import PIL

        pil_fonts = Path(PIL.__file__).resolve().parent / "fonts"
        if pil_fonts.is_dir():
            dirs.append(pil_fonts)
    except Exception:                                       # noqa: BLE001
        pass
    return dirs


def load_font(role: str, size: int):
    """
    Шрифт по роли и кеглю. Всегда возвращает рабочий шрифт.

    Без подходящего файла берётся встроенный шрифт Pillow: шаблон останется
    работоспособным, но огрубеет. Молча подменять шрифт нельзя — об этом
    пишется предупреждение при сборке превью.
    """
    key = (role, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    from PIL import ImageFont

    for directory in _font_dirs():
        for name in FONT_CANDIDATES.get(role, ()):
            path = directory / name
            if path.is_file():
                try:
                    font = ImageFont.truetype(str(path), size)
                    _FONT_CACHE[key] = font
                    return font
                except OSError:
                    continue
    font = ImageFont.load_default(size)
    _FONT_CACHE[key] = font
    return font


def font_report() -> Dict[str, str]:
    """Какой файл шрифта выбран для каждой роли (для отчёта и тестов)."""
    report: Dict[str, str] = {}
    for role in FONT_CANDIDATES:
        chosen = "встроенный Pillow"
        for directory in _font_dirs():
            for name in FONT_CANDIDATES[role]:
                if (directory / name).is_file():
                    chosen = str(directory / name)
                    break
            if chosen != "встроенный Pillow":
                break
        report[role] = chosen
    return report


def mm(value: float) -> int:
    """Миллиметры → пиксели при выбранном DPI."""
    return int(round(value * PX_PER_MM))


@dataclass
class Field:
    """
    Поле бланка: где стоит, как подписано, каким шрифтом.

    `key` — имя поля в терминах core.document_import_service.SCHEMA, чтобы
    ground truth сценария и разбор документа говорили на одном языке.
    """

    key: str
    label: str
    x_mm: float
    y_mm: float
    role: str = "sans"
    size_pt: int = 10
    label_size_pt: int = 7
    uppercase: bool = False
    align: str = "left"          # left | center | right
    width_mm: Optional[float] = None
    #: Заполняется ли поле значением (False — только подпись, как «Пол» с
    #: готовыми вариантами «М/Ж»).
    filled: bool = True


@dataclass
class Template:
    """Описание бланка: размер, заголовки, поля."""

    name: str
    kind: str
    size_mm: Tuple[float, float]
    titles: List[Tuple[str, float, float, str, int]] = field(default_factory=list)
    fields: List[Field] = field(default_factory=list)
    #: Цвет бланка: паспорт и ВУ печатаются на цветном фоне, выписки — белые.
    background: Tuple[int, int, int] = (255, 255, 255)
    #: Цвет «типографской краски» подписей полей.
    label_color: Tuple[int, int, int] = (90, 90, 90)
    #: Цвет значений.
    value_color: Tuple[int, int, int] = (20, 20, 20)

    @property
    def size_px(self) -> Tuple[int, int]:
        return mm(self.size_mm[0]), mm(self.size_mm[1])

    def field_by_key(self, key: str) -> Optional[Field]:
        for item in self.fields:
            if item.key == key:
                return item
        return None

    def keys(self) -> List[str]:
        return [f.key for f in self.fields if f.filled]


# ─────────────────────────────────────────────────────────────
# Бланки
# ─────────────────────────────────────────────────────────────

def make_passport_template() -> Template:
    """
    Паспорт РФ, страница с данными (внутренний, 125×88 мм).

    Порядок блоков — как в бланке: серия и номер сверху, затем ФИО тремя
    отдельными строками (именно так их и разбирает `_passport`), пол, дата и
    место рождения, ниже — «Паспорт выдан», дата выдачи и код подразделения.
    """
    fields = [
        Field("passport_series", "", 8, 12, "mono", 12, 8),
        Field("passport_number", "", 45, 12, "mono", 12, 8),
        Field("surname", "Фамилия", 30, 28, "serif", 12, 9, uppercase=True),
        Field("given_name", "Имя", 30, 37, "serif", 12, 9),
        Field("patronymic", "Отчество", 30, 46, "serif", 12, 9),
        Field("sex", "Пол", 8, 56, "serif", 11, 9),
        Field("birth_date", "Дата рождения", 30, 56, "serif", 11, 9),
        Field("birth_place", "Место рождения", 8, 66, "serif", 10, 8),
        Field("passport_issuer", "Паспорт выдан", 8, 74, "serif", 10, 8),
        Field("passport_issue_date", "Дата выдачи", 8, 82, "serif", 10, 8),
        Field("passport_code", "Код подразделения", 70, 82, "mono", 10, 8),
    ]
    titles = [
        ("РОССИЙСКАЯ ФЕДЕРАЦИЯ", 0.5, 3.0, "center", 9),
        ("ПАСПОРТ", 0.5, 20.0, "center", 14),
    ]
    return Template(
        name="passport",
        kind="passport",
        size_mm=SIZES_MM["passport"],
        titles=titles,
        fields=fields,
        background=(252, 248, 240),
    )


def make_license_template() -> Template:
    """
    Водительское удостоверение (85,6×54 мм).

    Бланк пронумерован: разбор `_license` ищет строки «1 Фамилия»,
    «2 Имя Отчество», «3 Дата рождения», «4а Дата выдачи», «4б Действительно
    до», «5 Серия и номер». Номера пунктов — часть бланка, без них разбор
    не работает.
    """
    fields = [
        # Имя и отчество — КАЖДОЕ своей строкой пункта 2. В бланке ВУ они
        # стоят рядом на одной строке, но при OCR широкого зазора Tesseract
        # подписывает обе половины («2. Дмитрий 2. Денисович»), и разбор
        # теряет ФИО вместе с серией и номером (в ВУ они идут после имени).
        # Вертикальная раскладка — то же содержимое бланка без этого эффекта.
        Field("surname", "1.", 4, 10, "sans", 10, 8),
        Field("given_name", "2.", 4, 16, "sans", 10, 8),
        Field("patronymic", "2.", 4, 22, "sans", 10, 8),
        Field("birth_date", "3.", 4, 28, "sans", 10, 8),
        Field("license_issue_date", "4а", 4, 34, "sans", 9, 8),
        Field("license_expiry_date", "4б", 42, 34, "sans", 9, 8),
        Field("license_series", "5.", 4, 40, "mono", 10, 8),
        Field("license_number", "5.", 32, 40, "mono", 10, 8),
        Field("license_categories", "9.", 4, 47, "sans", 10, 8),
    ]
    titles = [
        # Заголовок один и отдельной строкой: при двух заголовках OCR склеивает
        # их в одну строку, а разбор блока ВУ ищет метку В НАЧАЛЕ строки — и
        # блок обрывался на первой же строке (серия, номер и категории
        # терялись). В настоящем бланке надпись «RUSSIAN FEDERATION» стоит
        # мелким шрифтом над заголовком и в разборе не участвует.
        ("ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ", 0.5, 3.0, "center", 9),
    ]
    return Template(
        name="license",
        kind="license",
        size_mm=SIZES_MM["license"],
        titles=titles,
        fields=fields,
        background=(250, 250, 245),
    )


def make_bank_template() -> Template:
    """
    Банковские реквизиты (A4).

    Разбор `_bank` ищет подписи «Банк получателя», «БИК», «номер счёта
    получателя», «К/с». Полное наименование получателя — отдельной строкой:
    только явное «ИП Фамилия Имя Отчество» считается перевозчиком.
    """
    fields = [
        Field("full_name", "Получатель", 15, 30, "sans", 12, 9),
        Field("inn", "ИНН", 15, 45, "mono", 11, 9),
        Field("kpp", "КПП", 60, 45, "mono", 11, 9),
        Field("bank_name", "Банк получателя", 15, 65, "sans", 12, 9),
        Field("bik", "БИК", 15, 80, "mono", 11, 9),
        Field("correspondent_account", "К/с", 60, 80, "mono", 11, 9),
        Field("bank_account", "номер счёта получателя", 15, 95, "mono", 11, 9),
        Field("legal_address", "Адрес", 15, 115, "sans", 10, 9),
        Field("phone", "Телефон", 15, 130, "sans", 10, 9),
    ]
    titles = [
        ("РЕКВИЗИТЫ", 0.5, 12.0, "center", 12),
    ]
    return Template(
        name="bank",
        kind="bank_requisites",
        size_mm=SIZES_MM["bank"],
        titles=titles,
        fields=fields,
    )


def make_sts_template() -> Template:
    """
    СТС — свидетельство о регистрации ТС (100×75 мм).

    Разбор `_vehicle` ищет «Регистрационный номер», «Год выпуска», «Марка,
    модель», «VIN», блок «Собственник (владелец)»; тип ТС выбирается по
    словам «тягач» / «полуприцеп».
    """
    fields = [
        Field("plate_number", "Регистрационный номер", 6, 12, "mono", 11, 8),
        Field("vin", "VIN", 6, 24, "mono", 10, 8),
        Field("brand_model", "Марка, модель", 6, 36, "sans", 10, 8),
        Field("year", "Год выпуска", 6, 46, "mono", 10, 8),
        Field("color", "Цвет", 45, 46, "sans", 10, 8),
        Field("owner_full_name", "Собственник (владелец)", 6, 60, "sans", 10, 8),
        Field("owner_address", "", 6, 68, "sans", 9, 8),
    ]
    titles = [
        ("СВИДЕТЕЛЬСТВО О РЕГИСТРАЦИИ ТС", 0.5, 2.0, "center", 8),
    ]
    return Template(
        name="sts",
        kind="sts",
        size_mm=SIZES_MM["sts"],
        titles=titles,
        fields=fields,
        background=(250, 252, 255),
    )


def make_pts_template() -> Template:
    """
    ПТС — паспорт транспортного средства (A5, 210×148 мм).

    Отличия от СТС: отдельный блок «Тип ТС» (тягач / полуприцеп) и блок
    «Особые отметки». Разбор относит такой документ к карточке ТС.
    """
    fields = [
        Field("plate_number", "Регистрационный номер", 10, 20, "mono", 12, 8),
        Field("vin", "VIN", 10, 36, "mono", 11, 8),
        Field("brand_model", "Марка, модель ТС", 10, 52, "sans", 11, 8),
        Field("vehicle_type", "Тип ТС", 10, 66, "sans", 11, 8),
        Field("year", "Год выпуска ТС", 10, 80, "mono", 11, 8),
        Field("color", "Цвет", 90, 80, "sans", 11, 8),
        Field("owner_full_name", "Собственник (владелец)", 10, 100, "sans", 11, 8),
        Field("owner_address", "", 10, 112, "sans", 10, 8),
        Field("notes", "Особые отметки", 10, 132, "sans", 10, 8),
    ]
    titles = [
        ("ПАСПОРТ ТРАНСПОРТНОГО СРЕДСТВА", 0.5, 6.0, "center", 12),
    ]
    return Template(
        name="pts",
        kind="pts",
        size_mm=SIZES_MM["pts"],
        titles=titles,
        fields=fields,
        background=(252, 252, 246),
    )


def make_inn_template() -> Template:
    """
    ИНН / ОГРН — выписка или уведомление (A4).

    Разбор `extract_document_fields` относит такой документ к реквизитам
    организации, а `_registration_certificate` / `_egrul_record` ищут
    ИНН/КПП одной строкой и ОГРН отдельной.
    """
    fields = [
        Field("full_name", "Полное наименование", 15, 30, "sans", 12, 9),
        # ИНН и КПП стоят на одной строке с меткой — так их печатает
        # свидетельство о постановке на учёт. Зазор между значениями широкий
        # намеренно: при зазоре 15 мм Tesseract склеивает ИНН и КПП в одно
        # 18-значное число и теряет цифру, а разбор отказывается угадывать
        # границу (и правильно делает — угаданный ИНН хуже отсутствующего).
        Field("inn", "ИНН/КПП", 15, 50, "sans", 12, 9),
        Field("kpp", "", 62, 50, "sans", 12, 9),
        Field("ogrn", "ОГРН", 15, 68, "sans", 12, 9),
        Field("legal_address", "Адрес (место нахождения)", 15, 88, "sans", 10, 9),
        Field("director_name", "Ф.И.О.", 15, 108, "sans", 11, 9),
        Field("director_position", "Исполнительный орган", 15, 122, "sans", 11, 9),
    ]
    titles = [
        ("СВИДЕТЕЛЬСТВО О ПОСТАНОВКЕ НА УЧЕТ", 0.5, 10.0, "center", 12),
        ("ИНН / КПП", 0.5, 21.0, "center", 9),
    ]
    return Template(
        name="inn",
        kind="inn_ogrn",
        size_mm=SIZES_MM["inn"],
        titles=titles,
        fields=fields,
    )


def make_application_template() -> Template:
    """
    Заявка — свободная запись водителя, тягача и полуприцепа (A4).

    Так операторы присылают данные в мессенджере: подписанные строки без
    бланка. Разбор относит такой текст к «карточке водителя»
    (`_driver_card`) и ищет блоки «Водитель», «Паспорт РФ», «Водительское
    удостоверение», «Тягач», «Полуприцеп», «Телефон».
    """
    fields = [
        Field("full_name", "Водитель:", 20, 30, "sans", 12, 10),
        Field("birth_date", "дата рождения:", 20, 42, "sans", 11, 10),
        Field("birth_place", "место рождения:", 20, 54, "sans", 11, 10),
        Field("passport_block", "Паспорт РФ:", 20, 70, "sans", 11, 10),
        Field("passport_series", "", 55, 70, "sans", 11, 10),
        Field("passport_number", "", 72, 70, "sans", 11, 10),
        Field("passport_issue_date", "", 92, 70, "sans", 11, 10),
        Field("passport_code", "код подразделения:", 20, 82, "sans", 11, 10),
        Field("passport_issuer", "Кем выдан:", 20, 94, "sans", 11, 10),
        Field("registration_address", "Адрес регистрации:", 20, 106, "sans", 11, 10),
        Field("license_block", "Водительское удостоверение:", 20, 122, "sans", 11, 10),
        Field("license_series", "", 105, 122, "sans", 11, 10),
        Field("license_number", "", 122, 122, "sans", 11, 10),
        Field("license_issue_date", "выдано", 20, 134, "sans", 11, 10),
        Field("license_expiry_date", "срок до", 70, 134, "sans", 11, 10),
        Field("license_categories", "категории", 20, 146, "sans", 11, 10),
        Field("phone", "Телефон:", 20, 162, "sans", 11, 10),
        Field("tractor_brand", "Тягач:", 20, 178, "sans", 11, 10),
        Field("tractor_plate", "госномер", 70, 178, "sans", 11, 10),
        Field("tractor_color", "цвет", 130, 178, "sans", 11, 10),
        Field("trailer_brand", "Полуприцеп:", 20, 192, "sans", 11, 10),
        Field("trailer_plate", "госномер", 85, 192, "sans", 11, 10),
    ]
    titles = [
        ("ЗАЯВКА НА ПЕРЕВОЗКУ", 0.5, 12.0, "center", 14),
    ]
    return Template(
        name="application",
        kind="application",
        size_mm=SIZES_MM["bank"],
        titles=titles,
        fields=fields,
    )


#: Реестр шаблонов: имя → фабрика.
TEMPLATES = {
    "passport": make_passport_template,
    "license": make_license_template,
    "bank": make_bank_template,
    "sts": make_sts_template,
    "pts": make_pts_template,
    "inn": make_inn_template,
    "application": make_application_template,
}


def all_templates() -> Dict[str, Template]:
    """Все шаблоны, собранные заново."""
    return {name: factory() for name, factory in TEMPLATES.items()}


# ─────────────────────────────────────────────────────────────
# Рендер
# ─────────────────────────────────────────────────────────────

def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def render_template(template: Template, values: Dict[str, str],
                    draw_labels: bool = True) -> Image.Image:
    """
    Рисует бланк с подставленными значениями.

    :param values: {ключ поля: значение}. Пустое значение и отсутствие ключа
        одинаковы: поле остаётся незаполненным, подпись — на месте. Именно
        так выглядит скан с нечитаемым полем.
    :param draw_labels: рисовать ли подписи полей (у паспорта подписи
        бледные, у выписки — обычные; выключение нужно для проверки
        устойчивости разбора к пропавшим подписям).
    """
    image = Image.new("RGB", template.size_px, template.background)
    draw = ImageDraw.Draw(image)

    for text, x_ratio, y_mm, align, size_pt in template.titles:
        font = load_font("sans_bold", max(8, int(size_pt * DPI / 72)))
        width, _ = _text_size(draw, text, font)
        if align == "center":
            x = (image.width - width) // 2
        elif align == "right":
            x = image.width - width - mm(4)
        else:
            x = mm(4)
        draw.text((x, mm(y_mm)), text, font=font, fill=template.value_color)

    for item in template.fields:
        value = str(values.get(item.key, "") or "")
        x = mm(item.x_mm)
        y = mm(item.y_mm)

        if draw_labels and item.label:
            label_font = load_font("sans", max(6, int(item.label_size_pt * DPI / 72)))
            draw.text((x, y), item.label, font=label_font,
                      fill=template.label_color)
            label_width, label_height = _text_size(draw, item.label, label_font)
            value_x = x + label_width + mm(1.5)
        else:
            label_height = 0
            value_x = x

        if not value:
            continue

        shown = value.upper() if item.uppercase else value
        font = load_font(item.role, max(7, int(item.size_pt * DPI / 72)))
        width, _ = _text_size(draw, shown, font)
        if item.align == "center" and item.width_mm:
            value_x = x + (mm(item.width_mm) - width) // 2
        elif item.align == "right" and item.width_mm:
            value_x = x + mm(item.width_mm) - width

        draw.text((value_x, y - label_height * 0.2 if draw_labels else y),
                  shown, font=font, fill=template.value_color)

    return image


def preview(out_dir: Path) -> List[Path]:
    """
    Рисует по одному образцу каждого шаблона и сохраняет превью.

    Значения — вымышленные и намеренно короткие: превью нужно, чтобы
    ГЛАЗАМИ проверить расположение блоков, а не чтобы изобразить документ.
    """
    samples = {
        "passport": {
            "passport_series": "45 12", "passport_number": "345678",
            "surname": "ТЕСТОВ", "given_name": "Тест", "patronymic": "Тестович",
            "sex": "МУЖ.", "birth_date": "15.03.1985",
            "birth_place": "г. Тестоград",
            "passport_issuer": "Отделом УФМС России по г. Тестограду",
            "passport_issue_date": "20.06.2015", "passport_code": "160-001",
        },
        "license": {
            "surname": "ТЕСТОВ", "given_name": "Тест", "patronymic": "Тестович",
            "birth_date": "15.03.1985", "license_series": "16 34",
            "license_number": "567890", "license_issue_date": "10.10.2020",
            "license_expiry_date": "10.10.2030", "license_categories": "B, C, CE",
        },
        "bank": {
            "full_name": "ИП Тестов Тест Тестович",
            "inn": "770123456789", "kpp": "770101001",
            "bank_name": "ПАО «Тест-Банк»", "bik": "044525225",
            "correspondent_account": "30101810400000000225",
            "bank_account": "40702810000000000001",
            "legal_address": "г. Тестоград, ул. Тестовая, д. 1",
            "phone": "+7 (900) 123-45-67",
        },
        "sts": {
            "plate_number": "А123ВС77", "vin": "EC3TEUMB0T0002608",
            "brand_model": "JETOUR T2", "year": "2024", "color": "Белый",
            "owner_full_name": "Тестов Тест Тестович",
            "owner_address": "г. Тестоград, ул. Тестовая, д. 1",
        },
        "pts": {
            "plate_number": "А123ВС77", "vin": "EC3TEUMB0T0002608",
            "brand_model": "JETOUR T2", "vehicle_type": "тягач",
            "year": "2024", "color": "Белый",
            "owner_full_name": "ООО «Тестовая Компания»",
            "owner_address": "г. Тестоград, ул. Тестовая, д. 1",
            "notes": "без отметок",
        },
        "inn": {
            "full_name": "ООО «Тестовая Компания»",
            "inn": "7701234567", "kpp": "770101001",
            "ogrn": "1027700132195",
            "legal_address": "г. Тестоград, ул. Тестовая, д. 1",
            "director_name": "Тестов Тест Тестович",
            "director_position": "Генеральный директор",
        },
        "application": {
            "full_name": "Тестов Тест Тестович",
            "birth_date": "15.03.1985",
            "birth_place": "г. Тестоград, ул. Тестовая",
            "passport_block": "45 12", "passport_series": "345678",
            "passport_number": "выдан 20.06.2015",
            "passport_issue_date": "",
            "passport_code": "160-001",
            "passport_issuer": "Отделом УФМС России по г. Тестограду",
            "registration_address": "г. Тестоград, ул. Тестовая, д. 1",
            "license_block": "16 34", "license_series": "567890",
            "license_number": "",
            "license_issue_date": "10.10.2020",
            "license_expiry_date": "10.10.2030",
            "license_categories": "B, C, CE",
            "phone": "+7 (900) 123-45-67",
            "tractor_brand": "VOLVO FH", "tractor_plate": "А123ВС77",
            "tractor_color": "синий",
            "trailer_brand": "KRONE SD", "trailer_plate": "ЕК456789",
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, template in all_templates().items():
        image = render_template(template, samples[name])
        path = out_dir / f"{name}.png"
        image.save(path)
        written.append(path)
    return written


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="Синтетические шаблоны документов")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    written = preview(Path(args.out))
    print("Шаблоны:")
    for path in written:
        with Image.open(path) as image:
            print(f"  {path.name}: {image.width}×{image.height}")
    print("Шрифты:")
    for role, path in font_report().items():
        print(f"  {role}: {path}")
    print(f"Папка: {Path(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
