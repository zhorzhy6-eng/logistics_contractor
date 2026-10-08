# -*- coding: utf-8 -*-
"""
Пути проекта для пакета контрактов (Шаг 1 рефакторинга).

Единая точка вычисления корня проекта: старый код в
core/contract_generator.py считал его двумя dirname() от своего __file__,
что ломается при переезде файла глубже (core/contracts/...). Здесь корень
вычисляется один раз от местоположения этого модуля.

Здесь же — папка рейса: <Фамилия_И.О.>_<маршрут>_<ДД.ММ.ГГГГ>. Раньше все
готовые документы падали в output/ вперемешку, и найти нужный рейс было
нельзя. Имя папки считается из ОДНИХ И ТЕХ ЖЕ полей данных (водитель,
маршрут, дата договора), поэтому документы одного рейса — договор
Экспедиторства и зеркалённые в Формику / Логистикс заявки — оказываются
в одной папке безо всякой связи между окнами: связь идёт через ключ данных.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.dates import parse_date

#: Корень проекта: parents[2] = core/contracts/paths.py → core → корень.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Папка шаблонов DOCX (общая для всех типов, пока без подпапок).
TEMPLATES_DIR = PROJECT_ROOT / "templates"

#: Папка готовых договоров (как DEFAULT_OUTPUT_DIRNAME = "output").
OUTPUT_DIR = PROJECT_ROOT / "output"

#: Сколько символов маршрута попадает в имя папки. Длинный маршрут
#: («Москва - Санкт-Петербург - …») в имени папки нечитаем, а полный текст
#: маршрута есть в самом договоре.
ROUTE_LIMIT = 40

#: Что остаётся в имени папки: буквы (в т.ч. кириллица), цифры, точка,
#: дефис и подчёркивание. \w покрывает буквы и цифры Unicode.
_UNSAFE_CHARS = re.compile(r"[^\w.\-]", re.UNICODE)

#: Повторные подчёркивания в имени папки схлопываются: «Москва - Тверь»
#: после замены стрелки дало бы «Москва___Тверь».
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")

#: Стрелки и тире между пунктами маршрута: в имени папки они становятся
#: обычным дефисом — «Чехов → Санкт-Петербург» → «Чехов-Санкт-Петербург».
_ROUTE_ARROWS = re.compile(r"\s*[→—–]\s*")

#: Пробелы вокруг дефиса схлопываются: «Мурманск - Пятигорск» и
#: «Мурманск-Пятигорск» — один и тот же рейс, и папка у них должна быть одна,
#: а не «Мурманск_-_Пятигорск» (пробел превратился бы в подчёркивание).
_ROUTE_SPACED_DASH = re.compile(r"\s*-\s*")


def _short_driver(full_name: str) -> str:
    """
    «Иванов Иван Иванович» → «Иванов_И.И.».

    Фамилия + инициалы: полное ФИО в имени папки занимает половину строки и
    не помогает найти рейс. Два слова («Петров Пётр») дают одну инициалу,
    одно слово («Петров») — только фамилию, пустое имя — «Без_водителя»
    (папка нужна и без водителя).
    """
    parts = str(full_name or "").split()
    if len(parts) >= 3:
        return f"{parts[0]}_{parts[1][0]}.{parts[2][0]}."
    if len(parts) == 2:
        return f"{parts[0]}_{parts[1][0]}."
    if len(parts) == 1:
        return parts[0]
    return "Без_водителя"


def _short_route(route: str) -> str:
    """
    Маршрут для имени папки: стрелки → дефис, обрезка до 40 символов,
    всё недопустимое в имени папки — подчёркивание.

    Пробелы вокруг дефиса убираются: «Мурманск - Пятигорск» — тот же рейс,
    что «Мурманск-Пятигорск», и папка у них должна быть одна. Иначе пробел
    стал бы подчёркиванием и имя папки разошлось бы у двух документов рейса.

    Санитайзинг нужен именно здесь, а не только на сборке имени целиком:
    иначе «Москва/Тверь» превратилось бы в «Москва_Тверь» уже после обрезки
    и порвало бы границу 40 символов.
    """
    text = _ROUTE_ARROWS.sub("-", str(route or "").strip())
    text = _ROUTE_SPACED_DASH.sub("-", text)
    if len(text) > ROUTE_LIMIT:
        text = text[:ROUTE_LIMIT].rstrip("-_ ")
    text = _UNSAFE_CHARS.sub("_", text)
    text = _REPEATED_UNDERSCORES.sub("_", text)
    return text.strip("_")


def _date_part(date_str: Any) -> str:
    """
    Дата документа для имени папки: «2026-10-08» → «08.10.2026».

    Разбор — общим парсером проекта (core/dates.py): он понимает и ISO, и
    «08.10.2026», и «08/10/2026». Для Хавалов это обязательно: вкладка
    заявки отдаёт дату как «ДД.ММ.ГГГГ», а имя папки нужно одно и то же
    у всех типов.

    Дата не разобралась или её нет — сегодняшняя: папку рейса всё равно
    нужно создать, а сегодняшний день — единственная разумная догадка.
    """
    parsed = parse_date(date_str, warn=False)
    if parsed is not None:
        return parsed.strftime("%d.%m.%Y")
    return datetime.now().strftime("%d.%m.%Y")


def _block_of(contract_data: Any, name: str) -> Dict[str, Any]:
    """
    Блок данных (``driver`` / ``contract``) из любого входа.

    Вход бывает двух видов, и оба должны работать одинаково:

      * ``ContractData`` — так данные приходят от сборщиков окон и из
        base_generator.generate() (он коерцит вход ПЕРЕД генерацией);
      * обычный dict — так их передают старый код и часть тестов.

    У ContractData блоки лежат атрибутами-словарями (``.driver``,
    ``.contract``), у dict — ключами. Если ни того, ни другого нет, блок
    считается пустым: имени папки из ничего не собрать, но и падать нельзя.
    """
    try:
        block = getattr(contract_data, name, None)
    except Exception:  # noqa: BLE001 — битое свойство не повод падать
        block = None

    if block is None and isinstance(contract_data, dict):
        block = contract_data.get(name)

    return block if isinstance(block, dict) else {}


def contract_folder_name(contract_data: Any) -> str:
    """
    Имя папки рейса: <Фамилия_И.О.>_<маршрут>_<ДД.ММ.ГГГГ>.

    Примеры:
      «Иванов Иван Иванович», «Чехов — Санкт-Петербург», 2026-10-08
        → «Иванов_И.И._Чехов-Санкт-Петербург_08.10.2026»
      «Петров Пётр», «Москва-Тверь», 2026-10-08
        → «Петров_П._Москва-Тверь_08.10.2026»
      «», «Москва-Тверь», 2026-10-08
        → «Без_водителя_Москва-Тверь_08.10.2026»
      «Иванов Иван Иванович», «», 2026-10-08
        → «Иванов_И.И._08.10.2026»

    Источники:
      * ``driver.full_name`` — фамилия + инициалы;
      * ``contract.route`` — маршрут рейса;
      * ``contract.date`` — дата договора (ISO, YYYY-MM-DD).

    Санитайзинг: только буквы, цифры, точка, дефис, подчёркивание.
    Стрелки → «-», пробелы вокруг дефиса убираются. Маршрут обрезается
    до 40 символов.

    Принимает и ``ContractData``, и совместимый dict. Мусор на входе (None,
    строка, чужие ключи) сборку не роняет: чего нет — то пусто, а папка всё
    равно получает имя.
    """
    driver = _block_of(contract_data, "driver")
    contract = _block_of(contract_data, "contract")

    parts = [_short_driver(driver.get("full_name"))]

    route = _short_route(contract.get("route"))
    if route:
        parts.append(route)

    parts.append(_date_part(contract.get("date")))

    name = _REPEATED_UNDERSCORES.sub("_", "_".join(parts))
    return _UNSAFE_CHARS.sub("_", name).strip("_")


def folder_key_from_form(contract_data: Any) -> Dict[str, Any]:
    """
    Ключ папки рейса из ПЛОСКОЙ формы заявки.

    Понимает два вида входа:

      * ``{"driver": {...}, "contract": {...}}`` — форма ContractData (в том
        числе сам объект ContractData); возвращается как есть;
      * ``{"zayavka": {...}}`` — схема промпта Хавалов, где поля плоские
        и лежат в блоке ``zayavka`` (``driver_last_name``,
        ``loading_city`` / ``unloading_city``, ``date``).

    Нужна затем, что у Хавалов выход — .xlsx и ContractData не собирается
    (см. ui/windows/havaly/data.py), поэтому общая функция имени папки
    получает от генератора знакомую ей форму. Ключи не выдумываются:
    водитель складывается из тех полей, что заполнены, маршрут — из городов
    погрузки и доставки, дата берётся как есть.
    """
    source: Dict[str, Any] = contract_data if isinstance(contract_data, dict) else {}
    if _block_of(contract_data, "driver") or _block_of(contract_data, "contract"):
        return source

    zayavka = source.get("zayavka") if isinstance(source.get("zayavka"), dict) else {}
    if not zayavka:
        return {}

    def field(key: str) -> str:
        """Значение плоского поля заявки строкой (None → пусто)."""
        value = zayavka.get(key)
        return "" if value is None else str(value).strip()

    key: Dict[str, Any] = {}

    full_name = " ".join(
        part for part in (
            field("driver_last_name"),
            field("driver_first_name"),
            field("driver_middle_name"),
        ) if part
    )
    if full_name:
        key["driver"] = {"full_name": full_name}

    #: Каждый пункт маршрута — своим полем: город, а если его нет — пункт.
    #: Соединять их пробелом, а потом менять пробелы на дефисы нельзя:
    #: «Санкт Петербург» превратился бы в «Санкт-Петербург» не там, где надо.
    loading = field("loading_city") or field("loading_point")
    unloading = field("unloading_city") or field("unloading_point")
    route = "-".join(part for part in (loading, unloading) if part)

    contract: Dict[str, Any] = {}
    if route:
        contract["route"] = route
    if field("date"):
        contract["date"] = field("date")
    if contract:
        key["contract"] = contract

    return key


def resolve_contract_folder(output_dir: Any, contract_data: Any) -> Path:
    """
    Path папки рейса (создаёт, если нужно).

    Если папка с таким именем существует — используется она. Суффикс «_2»
    НЕ добавляется: два документа одного рейса (договор Экспедиторства и
    зеркалённая заявка) должны лежать рядом, а не в разных папках.

    Особый случай: папкой вывода указана САМА папка рейса (так бывает, когда
    пользователь выбирает папку договора и правит документ «на месте»).
    Тогда вкладывать её в себя не нужно — иначе получилось бы
    «Иванов_И.И._…/Иванов_И.И._…», и документы рейса разъехались бы по
    двум папкам с одинаковым именем.

    Имя пустое (не должно случиться, но вход бывает любым) — запасное
    «Без_имени_<сегодня>»: папка нужна всегда, иначе файл некуда положить.
    """
    folder_name = contract_folder_name(contract_data)
    if not folder_name:
        folder_name = "Без_имени_" + datetime.now().strftime("%d.%m.%Y")

    folder = Path(output_dir)
    if folder.name != folder_name:
        folder = folder / folder_name

    folder.mkdir(parents=True, exist_ok=True)
    return folder


def contract_output_path(
    output_dir: Any,
    filename: str,
    contract_data: Any,
) -> str:
    """
    Полный путь готового документа: <output_dir>/<папка рейса>/<имя файла>.

    Единая точка сборки пути для всех генераторов: DOCX-типы зовут её из
    base_generator.generate(), Хавалы — из ZayavkaExcelGenerator. Иначе
    «где именно лежит договор» разошлось бы между типами, а по этому пути
    UI открывает папку («Открыть папку» в диалоге создания).

    :param output_dir: корневая папка вывода (обычно output/ проекта).
    :param filename: имя файла БЕЗ папки (его считает генератор типа).
    :param contract_data: данные договора или плоская форма заявки.
    :return: путь файла строкой — генераторы возвращают именно строку.
    """
    folder = resolve_contract_folder(output_dir, contract_data)
    return str(folder / filename)
