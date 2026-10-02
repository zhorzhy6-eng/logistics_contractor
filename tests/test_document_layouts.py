"""Synthetic layouts and readings only; no user images or personal data."""
from threading import Event
from types import SimpleNamespace

from core.document_import_service import extract_local_fields, DocumentImportService, compare_fields
from core.import_cancel import ImportCancelled
import pytest


def test_salary_bank_inn_is_not_carrier_inn():
    data = extract_local_fields("""ИП Примеров Тест Тестович
Реквизиты:
Получатель: Примеров Тест Тестович
Номер счёта Получателя: 40817000000000000001
Банк Получателя: Банк Пример
ИНН Банка Получателя: 1234567890
БИК Банка Получателя: 123456789
К/С Банка Получателя: 30101000000000000001""")["carrier"][0]
    assert data["bank_account"] == "40817000000000000001"
    assert data["correspondent_account"] == "30101000000000000001"
    assert data["bik"] == "123456789"
    assert "inn" not in data
    assert data["full_name"].startswith("ИП Примеров")


def test_owner_reverse_does_not_become_driver():
    data = extract_local_fields("""СОБСТВЕННИК (владелец)
ПРИМЕРОВА ТЕСТА ТЕСТОВНА
Код подразделения ГИБДД: 123456
Дата выдачи: 01.02.2024""")
    assert "driver" not in data


def test_owner_becomes_carrier_candidate():
    data = extract_local_fields("""СОБСТВЕННИК (владелец)
ПРИМЕРОВА ТЕСТА ТЕСТОВНА
Республика, край, область Ростовская область
Нас. пункт г. Ростов-на-Дону
Улица ул. Тестовая
Дом 1
Особые отметки ПОЛУПРИЦЕП АВТОВОЗ""")
    assert "driver" not in data
    assert data["carrier"][0]["full_name"] == "ПРИМЕРОВА ТЕСТА ТЕСТОВНА"
    assert "Ростовская область" in data["carrier"][0]["legal_address"]


def test_tractor_category_caption_is_not_trailer_type():
    data = extract_local_fields("""СВИДЕТЕЛЬСТВО О РЕГИСТРАЦИИ ТС
Государственный регистрационный номер
А123АА77
Марка EXAMPLE 400
Тип ТС Грузовой тягач седельный
Категория ТС (ABCD, прицеп) C/N3
Год выпуска ТС 2021
Цвет Белый""")
    assert "tractor" in data and "trailer" not in data
    assert data["tractor"][0]["plate_number"] == "А123АА77"
    assert data["tractor"][0]["year"] == "2021"


def test_passport_multiline_labels():
    data = extract_local_fields("""Паспорт выдан МВД ПО ТЕСТОВОЙ ОБЛАСТИ
01.02.2020 123-456
Код подразделения
Фамилия
ПРИМЕРОВ
Имя
ТЕСТ
Отчество
ТЕСТОВИЧ
МУЖ. Дата рождения 03.04.1990
Место рождения
ГОРОД ПРИМЕР""")["driver"][0]
    assert data["full_name"] == "ПРИМЕРОВ ТЕСТ ТЕСТОВИЧ"
    assert data["birth_date"] == "03.04.1990"
    assert data["passport_issue_date"] == "01.02.2020"
    assert "passport_number" not in data


def test_passport_faded_labels_keep_observed_name_without_completing_it():
    data = extract_local_fields("""МВД ПО ТЕСТОВОЙ ОБЛАСТИ
01.02.2020 123-456
ПРИМЕРОВ
ТЕСТ
ТЕСТОВИЧ
МУЖ. 03.04.1990""")["driver"][0]
    assert data["full_name"] == "ПРИМЕРОВ ТЕСТ ТЕСТОВИЧ"
    assert data["birth_date"] == "03.04.1990"


def test_license_numbered_fields_do_not_swap_dates():
    data = extract_local_fields("""ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ
1. ПРИМЕРОВ
2. ТЕСТ ТЕСТОВИЧ
3. 03.04.1990
4a) 01.02.2020 4b) 01.02.2030
5. 12 34 567890""")["driver"][0]
    assert data["full_name"] == "ПРИМЕРОВ ТЕСТ ТЕСТОВИЧ"
    assert data["license_series"] == "12 34"
    assert data["license_number"] == "567890"
    assert data["license_issue_date"] == "01.02.2020"
    assert data["license_expiry_date"] == "01.02.2030"


def test_stamp_date_is_not_birth_date_and_not_assigned_to_named_person():
    data = extract_local_fields("""МЕСТО ЖИТЕЛЬСТВА
ЗАРЕГИСТРИРОВАН 01.02.2020
РЕСП. ТЕСТОВАЯ
УЛ. ПРИМЕРНАЯ""")["driver"][0]
    assert data["registration_address"]
    assert "birth_date" not in data and "full_name" not in data


def test_alternative_readings_of_one_document_are_disputed():
    a = extract_local_fields("ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ\n5. 12 34 567890")
    b = extract_local_fields("ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ\n5. 12 34 567891")
    evidence = DocumentImportService._evidence(a, "synthetic", "OCR") + DocumentImportService._evidence(b, "synthetic", "OCR")
    rows = {r.key: r for r in compare_fields(evidence)}
    assert rows["license_number"].state == "расхождение"
    assert rows["license_number"].value == ""
    assert rows["license_series"].value == "12 34"
    # Different unidentified documents do not merge by empty identities.
    evidence[1].source = "another synthetic"
    assert len({r.group for r in compare_fields(evidence)}) == 2


def test_local_ocr_cancel_before_work():
    from PIL import Image
    from core.document_ocr import recognize_image
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        recognize_image(Image.new("RGB", (10, 10)), cancel)


def test_local_ocr_returns_raw_text(monkeypatch):
    from PIL import Image
    import core.document_ocr as module
    monkeypatch.setattr(module, "_run_tesseract", lambda image: "ФИО: Тестов Тест")
    result = module.recognize_image(Image.new("RGB", (30, 30)), Event())
    assert result == "ФИО: Тестов Тест"


def test_local_ocr_missing_engine_is_reported(monkeypatch):
    from PIL import Image
    import pytesseract
    import core.document_ocr as module
    def unavailable(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()
    monkeypatch.setattr(pytesseract, "image_to_string", unavailable)
    with pytest.raises(RuntimeError, match="Tesseract"):
        module.recognize_image(Image.new("RGB", (30, 30)), Event())


def test_service_diagnostics_never_log_recognized_values(monkeypatch, caplog):
    import core.document_import_service as module
    from core.document_reader import DocumentPage
    monkeypatch.setattr(module, "read_document", lambda *a: iter([DocumentPage(1, image=object())]))
    client = SimpleNamespace(recognize_image=lambda *a: ({"driver": {"full_name": "Личное Значение"}}, ""))
    with caplog.at_level("INFO", logger=module.__name__):
        DocumentImportService({"document_cloud_enabled": True}, client).process(
            ["private-filename.png"], Event(), lambda *a: None)
    assert "Импорт документов" in caplog.text
    assert "Личное" not in caplog.text and "private-filename" not in caplog.text
