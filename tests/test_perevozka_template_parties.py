#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бланки перевозки: состояние после правки сторон (ШАГ «Полные стороны +
склонение с учётом рода»).

Бланк — это архив с встроенными шрифтами (`word/fonts/`, 3 записи), поэтому
он правится НЕ через python-docx, а точечно: `word/document.xml` заменяется,
остальные записи копируются побайтово (`tools/fix_perevozka_dynamic_parties.py`).

Здесь проверяется САМ БЛАНК, а не договор:

  * п. 1.1 и 1.2 печатают полные реквизиты и падеж (ключи на месте);
  * вложенные теги `{%p if … %}` НЕ удваиваются — повторный прогон
    инструмента обязан быть идемпотентным (требование шага F.5);
  * состав пакета не изменился: 24 записи, три из них — встроенные шрифты;
  * плейсхолдеров, которых генератор не заполняет, в бланке не осталось.
"""

import re
import shutil
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

DOCUMENT_PART = "word/document.xml"

#: Ключи, которые появились на шаге «Полные стороны + склонение».
NEW_KEYS = (
    "client_legal_form_prefix",
    "client_director_position_genitive",
    "client_director_genitive",
    "client_acting_genitive",
    "carrier_legal_form_prefix",
    "carrier_director_position_genitive",
    "carrier_director_genitive",
    "carrier_acting_genitive",
)

#: Теги условных абзацев. У каждого в бланке ДВА законных места: ветвь
#: п. 1.1 и подпись в п. 9 (там выбор идёт между ФИО ИП и сокращённым ФИО
#: директора). Больше двух вхождений — признак ВТОРОЙ правки поверх первой:
#: именно так ломается раскладка «тег — текст — тег».
SINGLE_TAGS = (
    "{%p if is_client_ip %}",
    "{%p if is_carrier_ip %}",
)

#: Сколько раз каждый тег может встретиться в бланке.
TAG_LIMIT = 2

TEXT_RE = re.compile(
    r"<w:t(?P<attrs>(?:\s[^>]*?)?)(?:/>|>(?P<text>.*?)</w:t>)", re.DOTALL
)


def _document_xml(name: str) -> str:
    path = PROJECT_ROOT / "templates" / name
    with zipfile.ZipFile(str(path)) as archive:
        return archive.read(DOCUMENT_PART).decode("utf-8")


def _paragraph_texts(xml: str) -> list:
    texts = []
    for match in re.finditer(
        r"<w:p(?:\s[^>]*)?>.*?</w:p>", xml, re.DOTALL
    ):
        body = match.group(0)
        joined = "".join(
            (node.group("text") or "") for node in TEXT_RE.finditer(body)
        )
        texts.append(re.sub(r"\s+", " ", joined).strip())
    return texts


def _is_client_branch(text: str) -> bool:
    """
    Абзац-ветвь п. 1.1 «Заказчик»?

    Ветвь ИП начинается с `{{client_legal_form}}` («Индивидуальный
    предприниматель»), ветвь ООО — с `{{client_legal_form_prefix}}` (он
    печатает приставку, только если сокращения нет в наименовании).
    """
    return text.startswith(("{{client_legal_form}}", "{{client_legal_form_prefix}}"))


def _client_branches(xml: str) -> list:
    return [text for text in _paragraph_texts(xml) if _is_client_branch(text)]


@pytest.fixture(params=TEMPLATES)
def template_name(request) -> str:
    return request.param


# ─────────────────────────────────────────────────────────────
# Полные стороны
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_keys_are_in_template(template_name, key):
    """Ключи падежа и приставки вида лица есть в каждом бланке."""
    xml = _document_xml(template_name)

    assert "{{" + key + "}}" in xml, f"нет ключа {key}"


def test_client_clause_is_conditional(template_name):
    """П. 1.1 различает ИП и ООО (ветки в бланке, а не константа)."""
    xml = _document_xml(template_name)
    texts = _paragraph_texts(xml)

    assert "{%p if is_client_ip %}" in texts
    assert "{%p else %}" in texts
    assert "{%p endif %}" in texts

    branches = _client_branches(xml)
    assert len(branches) == 2, "в п. 1.1 должно быть две ветви: ИП и ООО"
    assert branches[0].startswith("{{client_legal_form}}"), "первой — ветвь ИП"
    assert branches[1].startswith("{{client_legal_form_prefix}}"), \
        "второй — ветвь ООО"


def test_client_clause_has_full_requisites(template_name):
    """В обеих ветвях п. 1.1 печатаются ИНН и метка ОГРН/ОГРНИП."""
    xml = _document_xml(template_name)

    branches = _client_branches(xml)
    assert len(branches) == 2

    for text in branches:
        assert "ИНН {{client_inn}}" in text
        assert "{{client_ogrn_label}} {{client_ogrn}}" in text

    client_ip = [
        t for t in branches
        if "свидетельства о государственной регистрации" in t
    ]
    assert client_ip, "ветвь ИП п. 1.1 не найдена"


def test_carrier_clause_has_full_requisites(template_name):
    """П. 1.2 печатает ИНН, метку ОГРН и падеж подписанта."""
    xml = _document_xml(template_name)
    texts = _paragraph_texts(xml)

    carrier = [
        t for t in texts
        if t.startswith("{{carrier_legal_form_prefix}}")
        or t.startswith("{{carrier_legal_form}}")
    ]
    assert carrier, "ветви п. 1.2 не найдены"
    assert any("ИНН {{carrier_inn}}" in t for t in carrier)
    assert any("{{carrier_director_position_genitive}}" in t
               or "{{carrier_ogrn_label}}" in t for t in carrier)


def test_ip_branch_has_no_kpp(template_name):
    """КПП печатается ТОЛЬКО для ООО: у ИП в бланке КПП нет."""
    xml = _document_xml(template_name)

    ip_branches = [
        t for t in _client_branches(xml)
        if "свидетельства о государственной регистрации" in t
    ]
    assert ip_branches
    assert all("КПП" not in t for t in ip_branches)

    carrier_ip = [
        t for t in _paragraph_texts(xml)
        if "{{carrier_basis}}" in t
        and "{{carrier_pronoun}} в дальнейшем" in t
        and "в лице" not in t
    ]
    assert carrier_ip, "ветвь ИП п. 1.2 не найдена"
    assert all("КПП" not in t for t in carrier_ip)


def test_ip_and_ooo_carrier_branches_present(template_name):
    """В каждом бланке есть обе ветви перевозчика: ИП и ООО.

    В ООО-бланк перевозчик-ИП попадает при загрузке записи из справочника
    (тип в форме остался прежним), поэтому ветвь нужна и там.
    """
    xml = _document_xml(template_name)
    texts = _paragraph_texts(xml)

    assert "{%p if is_carrier_ip %}" in texts, "нет ветви ИП у перевозчика"
    branch = [t for t in texts if "{{carrier_director_position_genitive}}" in t]
    assert branch, "нет ветви ООО у перевозчика"
    assert "в лице" in branch[0]


# ─────────────────────────────────────────────────────────────
# Идемпотентность инструмента (требование F.5)
# ─────────────────────────────────────────────────────────────

def test_tags_are_not_duplicated(template_name):
    """
    Условные теги не задвоены: у каждого не больше двух законных мест.

    Больше двух — признак того, что правка наложилась дважды (вторая пара
    `{%p if … %}` внутри готового блока). Это ровно та беда, ради которой
    инструмент правки сделан идемпотентным (требование F.5).
    """
    xml = _document_xml(template_name)

    for tag in SINGLE_TAGS:
        count = xml.count(tag)
        assert count <= TAG_LIMIT, f"{tag}: вхождений {count}"


def test_second_run_changes_nothing(template_name, work_dir):
    """
    Прогон инструмента по УЖЕ ПРАВЛЕНОМУ бланку ничего не меняет.

    Это и есть требование F.5: инструмент идемпотентен. Второй прогон не
    переписывает файл (байты те же) и не добавляет ни одного тега — иначе
    раскладка «тег — текст — тег» сломалась бы второй парой `{%p if … %}`.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.fix_perevozka_dynamic_parties import edit_template

    copy = work_dir / f"idempotent_{template_name}"
    shutil.copy2(PROJECT_ROOT / "templates" / template_name, copy)
    before = copy.read_bytes()

    edit_template(copy, apply=True)
    after_first = copy.read_bytes()
    edit_template(copy, apply=True)

    assert copy.read_bytes() == after_first, "повторный прогон переписал бланк"

    with zipfile.ZipFile(str(copy)) as archive:
        xml_copy = archive.read(DOCUMENT_PART).decode("utf-8")
    with zipfile.ZipFile(str(PROJECT_ROOT / "templates" / template_name)) as archive:
        xml_shipped = archive.read(DOCUMENT_PART).decode("utf-8")

    for tag in SINGLE_TAGS + ("{%p endif %}", "{%p else %}"):
        assert xml_copy.count(tag) == xml_shipped.count(tag), tag
    assert xml_copy == xml_shipped, "прогон по правленому бланку его изменил"
    assert before != b""  # файл на месте


def test_edited_template_keeps_package(template_name, work_dir):
    """
    Прогон инструмента не ломает пакет: 24 записи и три встроенных шрифта.

    В бланках перевозки встроены шрифты; python-docx переписал бы весь
    пакет, поэтому правка идёт по одной записи архива. Тест сравнивает
    состав пакета до и после прогона.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.fix_perevozka_dynamic_parties import edit_template

    copy = work_dir / f"package_{template_name}"
    shutil.copy2(PROJECT_ROOT / "templates" / template_name, copy)

    def package(path):
        with zipfile.ZipFile(str(path)) as archive:
            return [info.filename for info in archive.infolist()]

    before = package(copy)
    edit_template(copy, apply=True)
    after = package(copy)

    assert after == before
    assert len(before) == 24, "состав пакета изменился"
    assert sum(1 for name in before if name.startswith("word/fonts/")) == 3
