"""Тесты группировки полей импорта по сущностям (core/import_entities.py).

Данные синтетические: реальных ПДн в тестах нет (AGENTS.md § 4).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.document_import_service import SCHEMA, Evidence, compare_fields
from core.import_entities import (
    EMPTY_SOURCE, STATE_CONFLICT, STATE_OK, STATE_UNREADABLE,
    count_by_kind, detect_conflicts, entities_from_rows, entity_by_uid, field_from_row,
    group_fields, parse_sources, progress_text, summary_text,
)

# ── синтетические данные ──
FIO = "Иванов Иван Иванович"
BIRTH = "15.03.1985"
PASSPORT_SERIES = "60 26"
PASSPORT_NUMBER = "123456"
VIN = "BX517602ABCDEFGHJ"
INN = "7701234567"


def evidence(section, values, source, method="GigaChat Vision", **kwargs):
    return Evidence(section, values, source, method, **kwargs)


# ─────────────────────────────────────────────────────────────
# Группировка
# ─────────────────────────────────────────────────────────────

def test_group_empty_list():
    assert group_fields([]) == []


def test_group_driver_from_passport():
    """Одно evidence с ФИО и паспортом → одна сущность «driver»."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_series": PASSPORT_SERIES,
                            "passport_number": PASSPORT_NUMBER}, "passport_01.jpg"),
    ])
    assert len(entities) == 1
    driver = entities[0]
    assert driver.kind == "driver"
    assert driver.section == "driver"
    assert driver.heading == "ВОДИТЕЛЬ"
    assert driver.title == FIO
    assert driver.sources == ["passport_01.jpg"]
    assert driver.field_value("passport_number").value == PASSPORT_NUMBER
    assert driver.field_value("passport_number").source == "passport_01.jpg"
    assert driver.field_value("passport_number").method == "GigaChat Vision"


def test_group_driver_from_passport_and_vu():
    """Два evidence с одним ФИО → одна сущность, два источника."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_series": PASSPORT_SERIES,
                            "passport_number": PASSPORT_NUMBER}, "passport_01.jpg"),
        evidence("driver", {"full_name": FIO, "license_number": "349327",
                            "license_categories": "B, C"}, "vu_01.jpg", method="OCR"),
    ])
    assert len(entities) == 1
    driver = entities[0]
    assert sorted(driver.sources) == ["passport_01.jpg", "vu_01.jpg"]
    assert driver.field_value("passport_number").value == PASSPORT_NUMBER
    assert driver.field_value("license_number").value == "349327"
    # Оба источника видны в подписи, поле принадлежит своему источнику.
    assert driver.field_value("license_number").source == "vu_01.jpg"


def test_two_drivers_stay_separate():
    """Разные ФИО — разные сущности, поля не смешиваются."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": "Петров Пётр Петрович",
                            "passport_number": "654321"}, "passport_02.jpg"),
    ])
    assert len(entities) == 2
    titles = sorted(entity.title for entity in entities)
    assert titles == [FIO, "Петров Пётр Петрович"]
    for entity in entities:
        # Чужого номера паспорта в сущности нет.
        assert entity.field_value("passport_number").value in {PASSPORT_NUMBER, "654321"}


def test_organization_by_inn():
    """Два evidence с одним ИНН → одна сущность организации."""
    entities = group_fields([
        evidence("carrier", {"full_name": "ООО «Ромашка»", "inn": INN}, "egrul.pdf"),
        evidence("carrier", {"inn": INN, "bik": "044525225"}, "bank.pdf", method="Текст"),
    ])
    assert len(entities) == 1
    organization = entities[0]
    assert organization.kind == "organization"
    assert organization.heading == "ОРГАНИЗАЦИЯ (перевозчик)"
    assert INN in organization.title
    assert sorted(organization.sources) == ["bank.pdf", "egrul.pdf"]


def test_vehicle_by_vin():
    """Машина опознаётся по VIN, поля не путаются между разными VIN."""
    entities = group_fields([
        evidence("vehicles", {"vin": VIN, "color": "красный"}, "polupricep.docx"),
        evidence("vehicles", {"vin": "ABCDEFGHJ12345678", "color": "синий"}, "other.docx"),
    ])
    assert len(entities) == 2
    colors = {entity.field_value("vin").value: entity.field_value("color").value
              for entity in entities}
    assert colors == {VIN: "красный", "ABCDEFGHJ12345678": "синий"}
    assert all(entity.kind == "vehicle" for entity in entities)
    assert entities[0].title == f"VIN {VIN}"


def test_entities_are_ordered_people_first():
    """Порядок блоков в дереве: водитель → организация → машина."""
    entities = group_fields([
        evidence("vehicles", {"vin": VIN}, "polupricep.docx"),
        evidence("carrier", {"inn": INN}, "egrul.pdf"),
        evidence("driver", {"full_name": FIO}, "passport_01.jpg"),
    ])
    assert [entity.kind for entity in entities] == ["driver", "organization", "vehicle"]


# ─────────────────────────────────────────────────────────────
# Конфликты
# ─────────────────────────────────────────────────────────────

def test_conflict_passport_number():
    """Один ФИО, разные номера паспорта → конфликт, значение не выбирается само."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": FIO, "passport_number": "654321"},
                 "passport_02.jpg", method="OCR"),
    ])
    assert len(entities) == 1
    field_value = entities[0].field_value("passport_number")
    assert field_value.state == STATE_CONFLICT
    assert field_value.value == ""          # выбирать за оператора нельзя
    assert not field_value.confirmable
    conflicts = detect_conflicts(entities)
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "passport_number"
    assert sorted(conflicts[0]["sources"]) == ["passport_01.jpg", "passport_02.jpg"]
    assert {value for _, value in conflicts[0]["variants"]} == {PASSPORT_NUMBER, "654321"}


def test_conflict_of_different_fields_is_not_a_conflict():
    """Разные значения РАЗНЫХ сущностей конфликтом не считаются."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "phone": "+7 900 000-00-00"},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": "Петров Пётр Петрович",
                            "phone": "+7 900 111-11-11"}, "passport_02.jpg"),
    ])
    assert detect_conflicts(entities) == []
    assert all(entity.field_value("phone").state == STATE_OK for entity in entities)


def test_conflicts_do_not_leak_personal_values_into_logs_keys():
    """Словарь конфликта называет поля, а не документы целиком."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": FIO, "passport_number": "654321"},
                 "passport_02.jpg"),
    ])
    conflict = detect_conflicts(entities)[0]
    assert set(conflict) == {"entity", "section", "field", "label", "sources", "variants"}


# ─────────────────────────────────────────────────────────────
# Непрочитанные поля (задел интерфейса: поле не исчезает)
# ─────────────────────────────────────────────────────────────

def test_unreadable_fields_have_state_unreadable():
    """Непрочитанное поле остаётся в сущности, а не пропадает из списка."""
    entities = group_fields([
        evidence("driver", {"full_name": FIO}, "passport_01.jpg"),
    ])
    driver = entities[0]
    assert len(driver.fields) == len(SCHEMA["driver"])
    unreadable = driver.unreadable_fields()
    assert len(unreadable) == len(SCHEMA["driver"]) - 1
    series = driver.field_value("passport_series")
    assert series.state == STATE_UNREADABLE
    assert series.value == ""
    assert series.sources == []
    assert not series.confirmable
    # Непрочитанные поля идут последним источником в дереве.
    assert EMPTY_SOURCE in driver.fields_by_source()


def test_confidence_is_reserved_for_future_model():
    """Уверенность уже в структуре, но пока всегда 1.0 (GigaChat её не отдаёт)."""
    entities = group_fields([evidence("driver", {"full_name": FIO}, "passport_01.jpg")])
    assert all(value.confidence == 1.0 for value in entities[0].fields)


def test_unknown_state_of_row_becomes_unreadable():
    """Пустое значение — всегда «не прочитано», каким бы ни было состояние строки."""
    from core.document_import_service import FieldResult
    value = field_from_row(FieldResult(0, "driver", "passport_number", "", "a.jpg",
                                       "требует проверки"))
    assert value.state == STATE_UNREADABLE


def test_invalid_value_is_not_confirmable():
    """Значение есть, но формату не отвечает — подтвердить нельзя."""
    from core.document_import_service import FieldResult
    value = field_from_row(FieldResult(0, "driver", "passport_number", "60 26 123456",
                                       "a.jpg · OCR: 60 26 123456", "требует проверки"))
    assert value.state == "invalid"
    assert not value.confirmable


# ─────────────────────────────────────────────────────────────
# Разбор строки источников и раскладка полей по файлам
# ─────────────────────────────────────────────────────────────

def test_parse_sources_reads_variants_and_skips_notes():
    rows = compare_fields([
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg", method="OCR", note="проверьте разряд"),
    ])
    value = field_from_row(next(row for row in rows if row.key == "passport_number"))
    assert parse_sources(rows[0].sources)[0][0] == "passport_01.jpg"
    assert value.variants == [("passport_01.jpg", PASSPORT_NUMBER)]
    assert value.note == "проверьте разряд"


def test_fields_by_source_puts_each_field_once():
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": FIO, "license_number": "349327"}, "vu_01.jpg"),
    ])
    by_source = entities[0].fields_by_source()
    placed = [value.field for values in by_source.values() for value in values]
    assert len(placed) == len(set(placed))          # ни одного поля дважды
    assert set(placed) == {value.field for value in entities[0].fields}
    assert "license_number" in [value.field for value in by_source["vu_01.jpg"]]


def test_entities_from_rows_is_the_same_grouping_as_group_fields():
    """Диалог и конвейер собирают сущности из одного правила группировки."""
    items = [
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": FIO, "license_number": "349327"}, "vu_01.jpg"),
        evidence("vehicles", {"vin": VIN}, "polupricep.docx"),
    ]
    direct = group_fields(items)
    from_rows = entities_from_rows(compare_fields(items))
    assert [entity.uid for entity in direct] == [entity.uid for entity in from_rows]
    assert [entity.display_name for entity in direct] == \
        [entity.display_name for entity in from_rows]


# ─────────────────────────────────────────────────────────────
# Подписи и сводка
# ─────────────────────────────────────────────────────────────

def test_entity_labels_are_operator_friendly():
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "birth_date": BIRTH}, "passport_01.jpg"),
        evidence("trailer", {"vin": VIN}, "polupricep.docx"),
        evidence("customer", {"full_name": "ООО «Заказчик»", "inn": INN}, "dogovor.docx"),
    ])
    driver, trailer, customer = entities[0], entities[2], entities[1]
    assert driver.title == f"{FIO}, {BIRTH}"
    assert driver.display_name == f"ВОДИТЕЛЬ: {FIO}, {BIRTH}"
    assert driver.button_text == "Подтвердить водителя целиком"
    assert trailer.heading == "ПОЛУПРИЦЕП"
    assert trailer.button_text == "Подтвердить полуприцеп целиком"
    assert customer.heading == "ОРГАНИЗАЦИЯ (заказчик)"


def test_counts_and_summary_text():
    entities = group_fields([
        evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg"),
        evidence("driver", {"full_name": FIO, "passport_number": "654321"},
                 "passport_02.jpg"),
        evidence("vehicles", {"vin": VIN}, "polupricep.docx"),
        evidence("carrier", {"inn": INN}, "egrul.pdf"),
    ])
    assert count_by_kind(entities) == {"driver": 1, "vehicle": 1, "organization": 1}
    text = summary_text(entities)
    assert "водителей — 1" in text
    assert "машин — 1" in text
    assert "организаций — 1" in text
    assert "Спорных полей — 1" in text


def test_progress_text_without_entities():
    assert progress_text(3, 12) == "Обработано 3 из 12 файлов."


def test_progress_text_with_prefix_and_entities():
    entities = group_fields([evidence("driver", {"full_name": FIO}, "passport_01.jpg")])
    text = progress_text(12, 12, entities, prefix="Обработка завершена.")
    assert text.startswith("Обработка завершена. Обработано 12 из 12 файлов. Найдено:")
    assert "водителей — 1" in text


def test_entity_by_uid_finds_entity():
    entities = group_fields([evidence("driver", {"full_name": FIO}, "passport_01.jpg")])
    assert entity_by_uid(entities, entities[0].uid) is entities[0]
    assert entity_by_uid(entities, "нет такой") is None


@pytest.mark.parametrize("section,heading", [
    ("driver", "ВОДИТЕЛЬ"),
    ("carrier", "ОРГАНИЗАЦИЯ (перевозчик)"),
    ("customer", "ОРГАНИЗАЦИЯ (заказчик)"),
    ("vehicles", "АВТОМОБИЛЬ"),
    ("tractor", "ТЯГАЧ"),
    ("trailer", "ПОЛУПРИЦЕП"),
    ("contract", "ДОГОВОР"),
])
def test_every_schema_section_has_a_heading(section, heading):
    entities = group_fields([evidence(section, {"full_name": FIO}, "a.jpg")])
    assert entities[0].heading == heading
    assert entities[0].uid.startswith(section + "#")
