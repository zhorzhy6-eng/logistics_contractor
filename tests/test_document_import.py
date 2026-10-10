"""Only synthetic documents; never add private sample files to fixtures."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from threading import Event
from types import SimpleNamespace
import pytest
from core.document_import_service import (Evidence, compare_fields, extract_local_fields,
    DocumentImportService, SCHEMA, valid_value)
from core.document_reader import read_document, DocumentPage
from core.import_cancel import ImportCancelled


def test_docx_paragraph_table_header(work_file):
    from docx import Document
    path = work_file("sample.docx")
    doc = Document()
    doc.add_paragraph("Перевозчик")
    row = doc.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text, row.cells[1].text = "ИНН", "7701234567"
    doc.sections[0].header.paragraphs[0].text = "Synthetic header"
    doc.save(path)
    pages = list(read_document(path, Event()))
    assert "Synthetic header" in pages[0].text
    assert extract_local_fields(pages[0].text)["carrier"][0]["inn"] == "7701234567"


def test_pdf_text_and_scan(work_file):
    import pypdfium2 as pdfium
    # Minimal synthetic PDF, no external PDF-generation dependency.
    path = work_file("sample.pdf")
    stream = b"BT /F1 12 Tf 30 100 Td (Synthetic document with a readable text layer for local import) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] >>"]
    payload, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(payload)
    payload += b"xref\n0 7\n0000000000 65535 f \n"
    payload += b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    payload += f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    path.write_bytes(payload)
    pages = list(read_document(path, Event()))
    assert len(pages) == 2 and "Synthetic" in pages[0].text
    assert pages[0].image is None and pages[1].image is not None


def test_doc_requires_converter(work_file, monkeypatch):
    import core.document_reader as reader
    monkeypatch.setattr(reader.shutil, "which", lambda _: None)
    monkeypatch.setattr(reader, "_convert_doc_with_word", lambda *a: None)
    path = work_file("sample.doc")
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="LibreOffice"):
        list(read_document(path, Event()))


def test_doc_word_conversion_when_libreoffice_missing(work_file, monkeypatch):
    import core.document_reader as reader
    from docx import Document
    monkeypatch.setattr(reader.shutil, "which", lambda _: None)

    def fake_word(source, folder):
        converted = work_file("converted.docx")
        document = Document()
        document.add_paragraph("ФИО: Тестов Тест")
        document.save(converted)
        return converted

    monkeypatch.setattr(reader, "_convert_doc_with_word", fake_word)
    path = work_file("legacy.doc")
    path.write_bytes(b"synthetic")
    pages = list(read_document(path, Event()))
    assert "Тестов Тест" in pages[0].text


def test_rtf_text_extraction(work_file):
    path = work_file("synthetic.rtf")
    # \'hh = cp1251 byte, \uN = unicode escape with one fallback character.
    path.write_bytes(
        b"{\\rtf1\\ansi\\ansicpg1251\\uc1 "
        b"\\'d4\\'e8\\'ee: \\'d8\\'e2\\'e0\\'ed\\'ee\\'e2 \\u1058\\u1077\\u1089\\u1090\\par"
        b"\\'c8\\'cd\\'cd: 7701234567}")
    pages = list(read_document(path, Event()))
    text = pages[0].text
    assert "Фио" in text or "ФИО" in text.upper()
    assert "7701234567" in text


def test_zip_archive_is_extracted(work_file):
    import zipfile
    from docx import Document
    inner = work_file("inner.docx")
    document = Document()
    document.add_paragraph("ФИО: Тестов Тест")
    document.save(inner)
    archive = work_file("bundle.zip")
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(inner, "nested/inner.docx")
        bundle.writestr("nested/readme.txt", "не документ")
    pages = list(read_document(archive, Event()))
    assert len(pages) == 1
    assert "Тестов Тест" in pages[0].text
    assert "bundle.zip" in pages[0].label


def test_zip_rejects_too_many_files(work_file, monkeypatch):
    import zipfile
    import core.document_reader as reader
    monkeypatch.setattr(reader, "ARCHIVE_MAX_FILES", 1)
    archive = work_file("many.zip")
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("a.docx", b"x")
        bundle.writestr("b.docx", b"y")
    with pytest.raises(RuntimeError, match="50 файлов"):
        list(read_document(archive, Event()))


def _synthetic_pdf(text):
    stream = ("BT /F1 12 Tf 30 100 Td (" + text + ") Tj ET").encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    payload, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(payload)
    payload += b"xref\n0 6\n0000000000 65535 f \n"
    payload += b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    payload += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    return payload


def test_broken_text_layer_is_rendered(work_file):
    path = work_file("lookalike.pdf")
    text = ("AOTOBOP APEHAbI TPAHCnOPTHOTO CPEACTBA " * 4).strip()
    path.write_bytes(_synthetic_pdf(text))
    pages = list(read_document(path, Event()))
    assert pages[0].text
    assert pages[0].image is not None


def test_text_page_can_render_lazily(work_file):
    path = work_file("readable.pdf")
    path.write_bytes(_synthetic_pdf(
        "Synthetic document with a readable text layer for local import"))
    page = list(read_document(path, Event()))[0]
    assert page.image is None
    assert page.load_image() is not None


def test_parser_multiple_vehicles_no_defaults():
    data = extract_local_fields("Автомобиль\nVIN: WAUZZZ8K9DA000001\nЦвет: Белый\nАвтомобиль\nVIN: WAUZZZ8K9DA000002")
    assert len(data["vehicles"]) == 2
    assert "year" not in data["vehicles"][0]
    assert "contract" not in data


def test_counterparty_card_fields():
    text = (
        "Карта контрагента\n"
        "1.1 Полное наименование Общество с ограниченной\n"
        "ответственностью «ТЕСТ ЛОГИСТИК»\n"
        "1.2 Сокращенное наименование ООО «ТЛ»\n"
        "2. Адрес\n"
        "2.1 Юридический адрес 443084, Самарская область,\n"
        "улица Тестовая, дом 1\n"
        "2.2 Фактический и почтовый адрес 443084, Самарская область, улица Тестовая, дом 1\n"
        "2.3 E-mail test@inbox.ru\n"
        "3. Контактные телефоны 8(987)0000000\n"
        "4. Сведения о регистрации\n"
        "4.3 ОГРН 1246300021956\n"
        "4.4 ИНН/КПП 6319264760/631901001\n"
        "5. Банковские реквизиты\n"
        "5.1 Наименование банка АО «ТЕСТ-БАНК»\n"
        "5.2 Расчетный счет № 40702810229390004346\n"
        "5.3 Корр/счет 30101810200000000824\n"
        "5.4 БИК 042202824\n"
        "6. Руководство\n"
        "6.1 Исполнительный орган\nорганизации\nДиректор\n"
        "6.2 Ф.И.О. Тестов Тест Тестович\n"
    )
    fields = extract_local_fields(text)["carrier"][0]
    assert "ТЕСТ ЛОГИСТИК" in fields["full_name"]
    assert fields["short_name"] == "ООО «ТЛ»"
    assert fields["inn"] == "6319264760"
    assert fields["kpp"] == "631901001"
    assert fields["ogrn"] == "1246300021956"
    assert fields["bank_account"] == "40702810229390004346"
    assert fields["correspondent_account"] == "30101810200000000824"
    assert fields["bik"] == "042202824"
    assert fields["director_position"] == "Директор"
    assert fields["director_name"] == "Тестов Тест Тестович"


def test_registration_certificate_spaced_digits():
    text = (
        "Федеральная налоговая служба\nСВИДЕТЕЛЬСТВО\n"
        "О ПОСТАНОВКЕ НА УЧЕТ РОССИЙСКОЙ ОРГАНИЗАЦИИ\n"
        "В НАЛОГОВОМ ОРГАНЕ ПО МЕСТУ ЕЕ НАХОЖДЕНИЯ\n"
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ \"АВАТЭК\"\n"
        "ИНН/КПП\n6 6 2 3 1 3 5 7 6 9 6 6 7 8 0 1 0 0 1\n"
        "ОГРН\n1 2 0 6 6 0 0 0 5 8 5 6 5\n"
    )
    fields = extract_local_fields(text)["carrier"][0]
    assert fields["full_name"] == 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "АВАТЭК"'
    assert fields["inn"] == "6623135769"
    assert fields["kpp"] == "667801001"
    assert fields["ogrn"] == "1206600058565"


def test_driver_card_fields():
    text = (
        "Автовоз открытого типа: марка тягача: Вольво FH-TRUCK 4X2 г/н О391ОУ702\n"
        "Марка прицепа: LOHR S2M52X, г/н ВХ517602\n"
        "Водитель: Тестов Тест Тестович\n"
        "Паспорт: 80 10   081540\n"
        "Выдан: Отделом УФМС России по Республике Тест\n"
        "Дата выдачи: 03.06.2010 г.\n"
        "Код подразделения: 020-006\n"
        "Дата рождения: 30.11.1989 г.\n"
        "Прописка: РБ, г. Уфа, ул. Тестовая, д. 19, кв. 16\n"
        "Место рождения: Респ. Башкортостан\n"
        "Водительское удостоверение: 02 32   786184 от 30.08.2017 г.\n"
        "Тел: +7 917-349-03-83\n"
    )
    data = extract_local_fields(text)
    driver = data["driver"][0]
    assert driver["full_name"] == "Тестов Тест Тестович"
    assert driver["passport_series"] == "80 10"
    assert driver["passport_number"] == "081540"
    assert driver["passport_code"] == "020-006"
    assert driver["birth_date"] == "30.11.1989"
    assert driver["license_series"] == "02 32"
    assert driver["license_number"] == "786184"
    assert driver["phone"] == "+7 917-349-03-83"


def test_egrul_record_fields():
    text = (
        "Форма № Р50007\nЛист записи\n"
        "Единого государственного реестра юридических лиц\n"
        "основной государственный регистрационный номер (ОГРН)\n"
        "1 1 9 5 0 2 7 0 0 3 5 6 6\n"
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ \"ТАУРУС\"\n"
        "полное наименование юридического лица\n"
    )
    fields = extract_local_fields(text)["carrier"][0]
    assert fields["full_name"] == 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ТАУРУС"'
    assert fields["ogrn"] == "1195027003566"


def test_do_not_compare_different_vins_even_same_plate():
    rows = compare_fields([
        Evidence("vehicles", {"vin": "WAUZZZ8K9DA000001", "plate_number": "A123AA"}, "a", "OCR"),
        Evidence("vehicles", {"vin": "WAUZZZ8K9DA000002", "plate_number": "A123AA"}, "b", "GigaChat")])
    assert len({r.group for r in rows}) == 2
    assert all(r.state != "расхождение" for r in rows)


def test_match_and_conflict_by_field():
    rows = compare_fields([
        Evidence("driver", {"full_name": "Тестов Тест", "passport_number": "123456"}, "a", "OCR", True),
        Evidence("driver", {"full_name": "Тестов Тест", "passport_number": "123457"}, "b", "GigaChat")])
    fields = {r.key: r for r in rows}
    assert fields["full_name"].state.startswith("совпало")
    assert fields["passport_number"].state == "расхождение"
    assert fields["passport_number"].value == ""
    assert "123456" in fields["passport_number"].sources
    assert fields["birth_date"].state == "не прочитано"


def test_no_identity_no_merge():
    rows = compare_fields([Evidence("vehicles", {"color": "Белый"}, "a", "OCR"),
                           Evidence("vehicles", {"color": "Белый"}, "b", "GigaChat")])
    assert len({r.group for r in rows}) == 2


@pytest.mark.parametrize("key,value", [("vin", "WAUZZZ8K9DA00000?"), ("inn", "770I234567"),
    ("passport_number", "12345?"), ("bank_account", "1"*19), ("birth_date", "31.02.2020"),
    ("year", "0"), ("passport_number", "1234567")])
def test_no_guessing(key, value):
    assert not valid_value(key, value)


def test_cancel_before_read():
    cancel = Event()
    cancel.set()
    called = []
    DocumentImportService().process(["nonexistent"], cancel, lambda *args: called.append(args))
    assert called == []


def test_file_failure_preserves_other_results(monkeypatch):
    import core.document_import_service as module
    def read(path, *args):
        if path == "bad":
            raise RuntimeError("Повреждённый файл")
        yield DocumentPage(1, "Водитель\nФИО: Тестов Тест")
    monkeypatch.setattr(module, "read_document", read)
    received = []
    DocumentImportService().process(["bad", "good"], Event(), lambda *args: received.append(args))
    assert received[0][2]
    assert received[1][1][0].values["full_name"] == "Тестов Тест"


def test_cloud_vision_replaces_local_ocr(monkeypatch):
    import core.document_import_service as module
    import core.document_ocr as ocr_module
    monkeypatch.setattr(module, "read_document", lambda *a: iter([DocumentPage(1, image=object())]))
    calls = []
    def local(*args):
        calls.append("local")
        return "ФИО: Тестов Тест"
    monkeypatch.setattr(ocr_module, "recognize_image", local)
    client = SimpleNamespace(recognize_image=lambda *a: ({"driver": {"full_name": "Тестов Тест"}}, ""))
    result = []
    DocumentImportService({"document_cloud_enabled": True}, client).process(["a"], Event(), lambda *a: result.append(a))
    assert len(result) == 1 and not result[0][2]
    assert result[0][1][0].method == "GigaChat Vision"
    assert calls == []  # облако включено — локальный OCR не запускается
    DocumentImportService({}, client).process(["a"], Event(), lambda *a: result.append(a))
    assert calls == ["local"]  # облако выключено — работает офлайн-fallback
    assert result[1][1][0].method == "OCR"
    assert not result[1][2]


def test_cloud_enabled_without_client_uses_local_ocr(monkeypatch, caplog):
    import core.document_import_service as module
    import core.document_ocr as ocr_module
    monkeypatch.setattr(module, "read_document", lambda *a: iter([DocumentPage(1, image=object())]))
    monkeypatch.setattr(ocr_module, "recognize_image", lambda *a: "ФИО: Тестов Тест")
    received = []
    with caplog.at_level("INFO", logger=module.__name__):
        DocumentImportService({"document_cloud_enabled": True}, None).process(
            ["a"], Event(), lambda *a: received.append(a))
    assert received[0][1][0].method == "OCR локально"
    assert "облако включено, но GigaChat недоступен, используется локальный OCR" in caplog.text
    # Сообщение о выборе метода выводится один раз на запуск, а не на каждую страницу.
    assert caplog.text.count("облако включено, но GigaChat недоступен") == 1

def test_vision_cleanup_warning_and_preview(monkeypatch):
    import core.document_import_service as module
    monkeypatch.setattr(module, "read_document", lambda *a: iter([DocumentPage(1, image=object())]))
    client = SimpleNamespace(recognize_image=lambda *a: (
        {"driver": {"full_name": "Тестов Тест"}},
        "Не удалось удалить загруженный файл из GigaChat."))
    received, texts = [], []
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["a"], Event(), lambda *a: received.append(a), lambda *a: texts.append(a))
    assert received[0][1][0].values["full_name"] == "Тестов Тест"
    assert "Не удалось удалить" in received[0][2]
    assert "Тестов Тест" in texts[0][1][0]  # распознанный JSON доступен для предпросмотра


@pytest.mark.parametrize("mode", ["success", "error", "cancel", "cleanup_error"])
def test_attachment_cleanup(monkeypatch, mode):
    import core.gigachat_client as module
    from PIL import Image
    client = module.GigaChatClient(auth_key="synthetic")
    monkeypatch.setattr(client, "_get_token", lambda: "token")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    cancel, calls = Event(), []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/files"):
            if mode == "cancel":
                cancel.set()
            data, code = {"id": "synthetic-id"}, 200
        elif url.endswith("/delete"):
            data, code = {}, 500 if mode == "cleanup_error" else 200
        else:
            data = {"choices": [{"message": {"content": '{"driver": {"full_name": "Test"}}'}}]}
            code = 500 if mode == "error" else 200
            assert kwargs["json"]["messages"][1]["attachments"] == ["synthetic-id"]
        return SimpleNamespace(status_code=code, json=lambda: data)
    monkeypatch.setattr(module.requests, "post", post)
    if mode == "cancel":
        with pytest.raises(ImportCancelled):
            client.recognize_image(Image.new("RGB", (30, 30)), cancel)
    elif mode == "error":
        with pytest.raises(RuntimeError):
            client.recognize_image(Image.new("RGB", (30, 30)), cancel)
    else:
        result, warning = client.recognize_image(Image.new("RGB", (30, 30)), cancel)
        assert result["driver"]["full_name"] == "Test"
        assert bool(warning) == (mode == "cleanup_error")
    assert calls[-1][0].endswith("/files/synthetic-id/delete")


def test_vision_json_repairs_and_redacted_diagnostics(caplog):
    from core.gigachat_client import _clean_and_parse_json
    assert _clean_and_parse_json('Текст {"a":null,} ещё текст') == {"a": ""}
    assert _clean_and_parse_json('{"a":"null,",}') == {"a": "null,"}
    assert _clean_and_parse_json('{"a":None}') == {"a": ""}
    with pytest.raises(RuntimeError):
        _clean_and_parse_json('Иванов 123456 {"a": ?????')
    assert "Иванов" not in caplog.text
    assert "123456" not in caplog.text


def test_vision_plain_text_fallback_after_two_invalid_json(monkeypatch):
    from PIL import Image
    import core.gigachat_client as module
    client = module.GigaChatClient(auth_key="synthetic")
    monkeypatch.setattr(client, "_get_token", lambda: "token")
    prompts = []
    def post(url, **kwargs):
        if url.endswith("/files"):
            data = {"id": "synthetic-id"}
        elif url.endswith("/delete"):
            data = {}
        else:
            prompts.append(kwargs["json"]["messages"][0]["content"])
            content = "not json" if len(prompts) < 3 else "ИНН: 7701234567"
            data = {"choices": [{"message": {"content": content}}]}
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(module.requests, "post", post)
    result, warning = client.recognize_image(Image.new("RGB", (30, 30)), Event())
    assert result == {"_raw_text": "ИНН: 7701234567"}
    assert warning == ""
    assert len(prompts) == 3
    assert "Без JSON" in prompts[-1]


def test_vision_empty_json_uses_transcription_and_text_model(monkeypatch):
    from PIL import Image
    import core.gigachat_client as module
    client = module.GigaChatClient(auth_key="synthetic")
    monkeypatch.setattr(client, "_get_token", lambda: "token")
    calls = {"structured": 0, "transcribe": 0, "text": 0}

    def post(url, **kwargs):
        if url.endswith("/files"):
            return SimpleNamespace(status_code=200, text="{}",
                                   json=lambda: {"id": f"id-{len(calls)}"})
        if url.endswith("/delete"):
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {})
        payload = kwargs["json"]
        attachments = payload["messages"][1].get("attachments")
        system_prompt = payload["messages"][0]["content"]
        if attachments and "Без JSON" in system_prompt:
            calls["transcribe"] += 1
            content = ("Паспорт гражданина выдан отделом УФМС, серия и номер "
                       "видны на изображении полностью")
        elif attachments:
            calls["structured"] += 1
            # Вне схемы тоже бывает «непустой» мусор: он не должен считаться данными.
            content = ('{"driver": {"full_name": ""}, "carrier": {"inn": ""},'
                       ' "document_type": "паспорт"}')
        else:
            calls["text"] += 1
            content = '{"carrier": {"inn": "7701234567"}}'
        return SimpleNamespace(status_code=200, text=content,
                               json=lambda: {"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(module.requests, "post", post)
    result, warning = client.recognize_image(Image.new("RGB", (30, 30)), Event())
    assert result["carrier"]["inn"] == "7701234567"
    assert calls == {"structured": 1, "transcribe": 1, "text": 1}
    assert "текстовое извлечение" in warning


def test_vision_empty_result_with_unreadable_scan_enhances(monkeypatch):
    from PIL import Image
    import core.gigachat_client as module
    client = module.GigaChatClient(auth_key="synthetic")
    monkeypatch.setattr(client, "_get_token", lambda: "token")
    calls = {"structured": 0, "transcribe": 0}

    def post(url, **kwargs):
        if url.endswith("/files"):
            return SimpleNamespace(status_code=200, text="{}",
                                   json=lambda: {"id": f"id-{len(calls)}"})
        if url.endswith("/delete"):
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {})
        payload = kwargs["json"]
        attachments = payload["messages"][1].get("attachments")
        system_prompt = payload["messages"][0]["content"]
        if attachments and "Без JSON" in system_prompt:
            calls["transcribe"] += 1
            content = "?? ??"  # скан не читается без усиления
        else:
            calls["structured"] += 1
            content = ('{"driver": {"full_name": ""}}' if calls["structured"] == 1
                       else '{"driver": {"full_name": "Тестов Тест"}}')
        return SimpleNamespace(status_code=200, text=content,
                               json=lambda: {"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(module.requests, "post", post)
    result, warning = client.recognize_image(Image.new("RGB", (30, 30)), Event())
    assert result["driver"]["full_name"] == "Тестов Тест"
    assert calls == {"structured": 2, "transcribe": 1}
    assert "контраст" in warning


def test_text_page_uses_cloud_text_fallback(monkeypatch):
    import core.document_import_service as module
    monkeypatch.setattr(module, "read_document", lambda *a: iter([
        DocumentPage(1, "Некое описание без подписей и без меток реквизитов")]))
    calls = []

    def recognize_text(text, **kwargs):
        calls.append(text)
        return {"carrier": {"inn": "7701234567"}}

    client = SimpleNamespace(recognize_text=recognize_text)
    received = []
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["synthetic.pdf"], Event(), lambda *args: received.append(args))
    assert calls
    assert len(received) == 1
    assert received[0][1][0].values["inn"] == "7701234567"
    assert received[0][1][0].method == "GigaChat Текст"


def test_text_page_falls_back_to_rendered_vision(monkeypatch):
    import core.document_import_service as module
    from PIL import Image
    page = DocumentPage(1, "Некое описание без подписей и без меток реквизитов",
                        image_loader=lambda: Image.new("RGB", (10, 10)))
    monkeypatch.setattr(module, "read_document", lambda *a: iter([page]))
    client = SimpleNamespace(
        recognize_text=lambda text, **kwargs: {"driver": {"full_name": ""}},
        recognize_image=lambda image, cancel, deep=True: ({"driver": {"full_name": "Тестов Тест"}}, ""))
    received = []
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["synthetic.pdf"], Event(), lambda *args: received.append(args))
    assert received[-1][1][0].values["full_name"] == "Тестов Тест"
    assert received[-1][1][0].method == "GigaChat Vision"


def vision_deep_probe(records):
    """Клиент-двойник: глубокий флаг каждой страницы попадает в записи."""
    def vision(image, cancel, deep=True):
        records.append(deep)
        return {}, ""
    return vision


def test_deep_fallback_is_budgeted_per_file(monkeypatch):
    """Бюджет резервных попыток действует на ТЕКСТОВЫЕ страницы, не на фото.

    Фото и сканы идут в Vision основным путём, и глубокая цепочка у них
    всегда включена: иначе «плохие» снимки с третьей страницы файла уходили бы
    на слабый локальный OCR.
    """
    import core.document_import_service as module
    import core.document_ocr as ocr_module
    from PIL import Image
    monkeypatch.setattr(ocr_module, "recognize_image", lambda *a: "")
    calls = []
    texts = []

    def recognize_text(text, **kwargs):
        texts.append(text)
        return {}

    client = SimpleNamespace(recognize_text=recognize_text,
                             recognize_image=vision_deep_probe(calls))
    monkeypatch.setattr(module, "read_document", lambda *a: iter([
        DocumentPage(number, text="Описание без подписей и меток реквизитов",
                     image_loader=lambda: Image.new("RGB", (12, 12)))
        for number in range(1, 5)]))
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["synthetic.pdf"], Event(), lambda *args: None)
    # Первые две страницы тратят бюджет: текстовая модель и повтор по картинке.
    # Дальше бюджет исчерпан — в облако не уходит ни одного запроса.
    assert calls == [False, False]
    assert len(texts) == 2


def test_image_pages_always_use_deep_vision(monkeypatch):
    """Бюджет не сокращает глубокую цепочку Vision: проверяем на текстовых страницах.

    У изображения резервных попыток нет вовсе — глубокий флаг всегда True.
    """
    import core.document_import_service as module
    from PIL import Image
    calls = []
    client = SimpleNamespace(recognize_image=vision_deep_probe(calls))
    monkeypatch.setattr(module, "read_document", lambda *a: iter([
        DocumentPage(number, image=Image.new("RGB", (12, 12))) for number in range(1, 4)]))
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["synthetic.pdf"], Event(), lambda *args: None)
    assert calls == [True, True, True]


def test_deep_fallback_stops_after_first_data(monkeypatch):
    import core.document_import_service as module
    from PIL import Image
    calls = []

    def vision(image, cancel, deep=True):
        calls.append(deep)
        if len(calls) == 1:
            return {"driver": {"full_name": "Тестов Тест"}}, ""
        return {}, ""

    client = SimpleNamespace(recognize_text=lambda text, **kwargs: {},
                             recognize_image=vision)
    monkeypatch.setattr(module, "read_document", lambda *a: iter([
        DocumentPage(number, text="Описание без подписей и меток реквизитов",
                     image_loader=lambda: Image.new("RGB", (12, 12)))
        for number in range(1, 3)]))
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["synthetic.pdf"], Event(), lambda *args: None)
    # Первая страница дала данные — дальше глубоких повторов нет вовсе.
    assert calls == [False]


def test_import_service_extracts_vision_plain_text(monkeypatch):
    import core.document_import_service as module
    monkeypatch.setattr(module, "read_document", lambda *a: iter([DocumentPage(1, image=object())]))
    client = SimpleNamespace(recognize_image=lambda *a: ({"_raw_text": "ИНН: 7701234567"}, ""))
    received = []
    DocumentImportService({"document_cloud_enabled": True}, client).process(
        ["synthetic.png"], Event(), lambda *args: received.append(args))
    assert received[0][1][0].values["inn"] == "7701234567"
    assert "fallback" in received[0][2]


def test_token_expiry_milliseconds_are_normalized(monkeypatch):
    """GigaChat отдаёт expires_at в миллисекундах — клиент приводит его к секундам."""
    import time as time_module
    import core.gigachat_client as module
    client = module.GigaChatClient(auth_key="synthetic")
    future_ms = int((time_module.time() + 1800) * 1000)
    response = SimpleNamespace(status_code=200,
                               json=lambda: {"access_token": "synthetic-token",
                                             "expires_at": future_ms},
                               text="")
    monkeypatch.setattr(module.requests, "post", lambda *a, **k: response)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    assert client._get_token() == "synthetic-token"
    assert client._token_expires < 10_000_000_000
    assert abs(client._token_expires - future_ms / 1000) < 0.001


@pytest.fixture
def window(monkeypatch):
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_init_gigachat_client", lambda *a, **k: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window = MainWindow()
    yield window
    window.close()


def test_review_unchecked_and_manual_values_preserved(window):
    from ui.document_import_dialog import DocumentImportDialog
    window.driver_tab.phone.setText("manual phone")
    window.driver_tab.full_name.setText("manual name")
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([Evidence("driver", {"full_name": "Test", "phone": "other"}, "a", "OCR")]))
    driver = dialog.entities[0]
    # Ничего не подтверждено заранее: сначала оператор проверяет по оригиналу.
    assert not any(dialog.is_confirmed(driver.uid, value.field) for value in driver.fields)
    dialog.apply()
    assert window.driver_tab.full_name.text() == "manual name"
    dialog.set_field_confirmed(driver.uid, "full_name", True)
    dialog.apply()
    assert window.driver_tab.full_name.text() == "Test"
    assert window.driver_tab.phone.text() == "manual phone"


def test_stale_form_requires_reconfirmation(window):
    from ui.document_import_dialog import DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([Evidence("driver", {"full_name": "Test"}, "a", "OCR")]))
    driver = dialog.entities[0]
    dialog.set_field_confirmed(driver.uid, "full_name", True)
    window.driver_tab.full_name.setText("new manual entry")
    dialog.apply()
    assert window.driver_tab.full_name.text() == "new manual entry"
    assert not dialog.is_confirmed(driver.uid, "full_name")


def test_new_vehicle_no_defaults_no_existing_row_loss(window):
    from ui.document_import_dialog import apply_approved
    tab = window.vehicles_tab
    tab.fill_data([{"vin": "WAUZZZ8K9DA000001", "year": 2020}])
    tab.table.insertRow(1)
    apply_approved(window, [("vehicles", "new:1", "vin", "WAUZZZ8K9DA000002")])
    assert tab.table.rowCount() == 3
    assert tab._get_cell_text(0, tab.COL_VIN) == "WAUZZZ8K9DA000001"
    assert tab._get_spin_value(2, tab.COL_YEAR) == 0
    assert tab._get_combo_value(2, tab.COL_TYPE) == ""


def test_carrier_does_not_infer_unconfirmed_type(window):
    from ui.document_import_dialog import apply_approved
    tab = window.carrier_tab
    tab.carrier_type.setCurrentText("ИП без НДС")
    apply_approved(window, [("carrier", None, "full_name", "ООО Тест")])
    assert tab.carrier_type.currentText() == "ИП без НДС"


def test_cancel_during_ocr_discards_late_fields(monkeypatch):
    import core.document_import_service as module
    import core.document_ocr as ocr_module
    cancel = Event()
    monkeypatch.setattr(module, "read_document", lambda *a: iter([DocumentPage(1, image=object())]))
    def ocr(*args):
        cancel.set()
        return "ФИО: Поздний результат"
    monkeypatch.setattr(ocr_module, "recognize_image", ocr)
    received = []
    DocumentImportService().process(["a"], cancel, lambda *a: received.append(a))
    assert not received


def test_vision_error_does_not_stop_batch(monkeypatch):
    import core.document_import_service as module
    import core.document_ocr as ocr_module
    from PIL import Image
    monkeypatch.setattr(module, "read_document", lambda *a: iter([
        DocumentPage(1, image=Image.new("RGB", (20, 20)))]))
    monkeypatch.setattr(ocr_module, "recognize_image", lambda *a: "ФИО: Локально Прочитан")
    calls = []
    def vision(image, cancel, deep=True):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("GigaChat: HTTP 401.")
        return {"driver": {"full_name": "Тестов Тест"}}, ""
    client = SimpleNamespace(recognize_image=vision)
    received = []
    service = DocumentImportService({"document_cloud_enabled": True}, client)
    service.process(["bad.png", "good.png"], Event(), lambda *a: received.append(a))
    assert len(calls) == 2
    # Первый файл: причина отказа GigaChat, затем — результат локального OCR.
    assert "401" in received[0][2] and received[0][1] == []
    assert received[1][1][0].method == "OCR локально"
    assert "GigaChat недоступен" in received[1][1][0].note
    # Второй файл прочитан облаком обычным путём.
    assert received[2][1][0].method == "GigaChat Vision"
    assert received[2][1][0].values["full_name"] == "Тестов Тест"


def test_unread_file_is_visible_and_cannot_be_applied(window):
    from ui.document_import_dialog import DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("synthetic.png, стр. 1", [], "")
    dialog.finished()
    # Непрочитанный файл виден отдельным блоком, но подтверждать в нём нечего.
    headings = [dialog.tree.topLevelItem(i).text(0)
                for i in range(dialog.tree.topLevelItemCount())]
    assert any("НЕ ПРОЧИТАННЫЕ ФАЙЛЫ" in text for text in headings)
    assert dialog.entities == []
    before = window.driver_tab.get_data()
    dialog.apply()
    assert before == window.driver_tab.get_data()
    dialog.manual_section.setCurrentIndex(dialog.manual_section.findData("driver"))
    dialog.add_manual_group()
    assert [entity.section for entity in dialog.entities] == ["driver"]
    assert len(dialog.entities[0].fields) == len(SCHEMA["driver"])


def test_different_people_cannot_fill_one_driver(window):
    from ui.document_import_dialog import DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([
        Evidence("driver", {"full_name": "First Person"}, "a", "Текст"),
        Evidence("driver", {"full_name": "Second Person", "phone": "123"}, "b", "Текст")]))
    assert len(dialog.entities) == 2
    for entity in dialog.entities:
        dialog.set_entity_confirmed(entity.uid, True)
    before = window.driver_tab.get_data()
    dialog.apply()
    assert before == window.driver_tab.get_data()


def test_repeated_confirmation_of_same_value_is_not_conflict(window, monkeypatch):
    from ui.document_import_dialog import DocumentImportDialog
    from PyQt5.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: asked.append(args[1:3]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([
        Evidence("carrier", {"director_name": "Тестов Тест"}, "a", "GigaChat Vision"),
        Evidence("carrier", {"director_name": "Тестов Тест"}, "b", "Текст")]))
    assert len(dialog.entities) == 2
    checked = 0
    for entity in dialog.entities:
        dialog.set_entity_confirmed(entity.uid, True)
        checked += 1 if dialog.is_confirmed(entity.uid, "director_name") else 0
    assert checked == 2  # одно и то же поле подтверждено дважды, значение совпадает
    dialog.apply()
    assert asked == []  # повторная галочка — не конфликт
    assert window.carrier_tab.director_name.text() == "Тестов Тест"


def test_field_conflict_does_not_block_other_fields(window, monkeypatch):
    from ui.document_import_dialog import DocumentImportDialog
    from PyQt5.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: asked.append(args[1:3]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([
        Evidence("carrier", {"director_name": "Тестов Тест"}, "a", "GigaChat Vision"),
        Evidence("carrier", {"director_name": "Другой Человек", "phone": "+7 900 000-00-00"},
                 "b", "Текст")]))
    for entity in dialog.entities:
        dialog.set_entity_confirmed(entity.uid, True)
    dialog.apply()
    # Конфликтное поле не перенесено, остальные поля — перенесены, диалог открыт.
    assert [title for title, _ in asked] == ["Конфликт значений"]
    assert "Руководитель" in asked[0][1]
    assert window.carrier_tab.director_name.text() == ""
    assert window.carrier_tab.phone.text() == "+7 900 000-00-00"
    assert dialog.result() == 0


def test_edit_resets_confirmation(window):
    from ui.document_import_dialog import COL_VALUE, DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([Evidence("driver", {"full_name": "Test"}, "a", "OCR")]))
    driver = dialog.entities[0]
    dialog.set_field_confirmed(driver.uid, "full_name", True)
    dialog.field_item(driver.uid, "full_name").setText(COL_VALUE, "Edited")
    assert not dialog.is_confirmed(driver.uid, "full_name")
    assert driver.field_value("full_name").value == "Edited"


def test_settings_import_is_opt_in(work_file):
    from core.settings_service import SettingsService
    from ui.settings_dialog import SettingsDialog
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    settings = SettingsService(str(work_file("settings.json")))
    assert not settings.get_bool("document_cloud_enabled")
    dialog = SettingsDialog(settings.as_dict())
    assert not dialog.document_cloud.isChecked()
    dialog.document_cloud.setChecked(True)
    assert dialog.get_settings()["document_cloud_enabled"] is True


@pytest.mark.parametrize("confirm", [False, True])
def test_unidentified_pages_require_explicit_link_confirmation(window, monkeypatch, confirm):
    from ui.document_import_dialog import DocumentImportDialog
    from PyQt5.QtWidgets import QMessageBox
    asked = []
    def question(*args):
        asked.append(True)
        return QMessageBox.Yes if confirm else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", question)
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([
        Evidence("driver", {"full_name": "Примеров Тест Тестович"}, "passport", "OCR"),
        Evidence("driver", {"license_number": "123456"}, "license", "OCR")]))
    for entity in dialog.entities:
        dialog.set_entity_confirmed(entity.uid, True)
    before = window.driver_tab.get_data()
    dialog.apply()
    assert asked
    if confirm:
        assert window.driver_tab.full_name.text() == "Примеров Тест Тестович"
        assert window.driver_tab.license_number.text() == "123456"
    else:
        assert window.driver_tab.get_data() == before


def test_manual_group_button_keeps_target_and_does_not_crash(window):
    from ui.document_import_dialog import DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([Evidence("driver", {"full_name": "Тестовый Человек",
                                                        "birth_date": "01.02.1990"}, "synthetic", "OCR")]))
    driver = dialog.entities[0]
    dialog.set_field_confirmed(driver.uid, "full_name", True)
    for _ in range(3):
        dialog.manual_button.click()
        assert dialog.destination(driver) == ("driver", None)
        assert dialog.is_confirmed(driver.uid, "full_name")
    assert len(dialog.entities) == 4          # водитель и три пустые группы


def test_unread_or_invalid_date_cannot_be_confirmed_until_corrected(window):
    from ui.document_import_dialog import COL_VALUE, DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.load_rows(compare_fields([Evidence("driver", {"full_name": "Тестовый Человек"},
                                              "synthetic", "OCR")]))
    driver = dialog.entities[0]
    assert not driver.field_value("birth_date").confirmable
    dialog.set_field_confirmed(driver.uid, "birth_date", True)
    assert not dialog.is_confirmed(driver.uid, "birth_date")

    item = dialog.field_item(driver.uid, "birth_date")
    item.setText(COL_VALUE, "01.02.19?0")
    assert not driver.field_value("birth_date").confirmable
    dialog.set_field_confirmed(driver.uid, "birth_date", True)
    assert not dialog.is_confirmed(driver.uid, "birth_date")

    item.setText(COL_VALUE, "01.02.1990 г.")
    assert driver.field_value("birth_date").confirmable
    dialog.set_field_confirmed(driver.uid, "birth_date", True)
    dialog.apply()
    assert window.driver_tab.get_data()["birth_date"] == "1990-02-01"


def test_ocr_preview_is_only_in_memory(window):
    from ui.document_import_dialog import DocumentImportDialog
    dialog = DocumentImportDialog(window)
    dialog.remember_text("synthetic.png, стр. 1", ["Synthetic OCR variant"])
    assert dialog.ocr_texts["synthetic.png, стр. 1"] == ["Synthetic OCR variant"]
    dialog.start_button.setEnabled(False)
