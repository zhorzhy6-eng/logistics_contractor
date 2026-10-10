"""Приоритет распознавания: GigaChat Vision первым для изображений.

Требование шага: фото, сканы и PDF без текстового слоя читает GigaChat Vision
(на «плохих» снимках у него в 4–8 раз выше recall, чем у Tesseract), локальный
OCR — только резерв. Текстовые документы идут прежним путём: локальное
извлечение, при неудаче — облако ТЕКСТОМ (дешевле Vision).

Данные синтетические (AGENTS.md § 4): реальных ПДн и реальных моделей нет —
клиент и Tesseract подменены двойниками.
"""
import logging
from threading import Event
from types import SimpleNamespace

import pytest

from core.document_import_service import (
    CLOUD_CLIENT_MISSING, CLOUD_DISABLED, CLOUD_RATE_LIMIT, CLOUD_VISION_FAILED,
    DocumentImportService, LOCAL_OCR_FAILED_NOTE, LOCAL_OCR_FALLBACK_METHOD,
    LOCAL_OCR_LIMIT_NOTE, LOCAL_OCR_METHOD, LOCAL_OCR_NOTE, LOCAL_TEXT_METHOD,
    TEXT_METHOD, VISION_METHOD,
)
from core.document_reader import DocumentPage

FIO = "Тестов Тест Тестович"
INN = "7701234567"


# ─────────────────────────────────────────────────────────────
# Двойники вместо моделей
# ─────────────────────────────────────────────────────────────

class FakeClient:
    """Клиент GigaChat: записывает вызовы, отдаёт заготовленные ответы."""

    def __init__(self, vision=None, text=None, vision_model="GigaChat-2-Max"):
        self.vision_model = vision_model
        self.vision_calls, self.text_calls = [], []
        self._vision, self._text = vision, text

    def recognize_image(self, image, cancel, deep=True):
        self.vision_calls.append((image, deep))
        return self._vision(cancel, deep) if callable(self._vision) else self._vision

    def recognize_text(self, text, **kwargs):
        self.text_calls.append(text)
        return self._text(text) if callable(self._text) else self._text


class FakeOcr:
    """Локальный Tesseract: считает вызовы, отдаёт заданный текст."""

    def __init__(self, text="", error=None):
        self.text, self.error, self.calls = text, error, []

    def __call__(self, image, cancel):
        self.calls.append(image)
        if self.error is not None:
            raise self.error
        return self.text


@pytest.fixture
def pages(monkeypatch):
    """Подменяет чтение документа: страницы задаёт сам тест."""
    import core.document_import_service as module

    def install(items):
        monkeypatch.setattr(module, "read_document", lambda *a: iter(items))
    return install


@pytest.fixture
def ocr(monkeypatch):
    """Подменяет локальный OCR, сохраняя точку импорта сервиса."""
    import core.document_ocr as ocr_module

    def install(text="", error=None):
        fake = FakeOcr(text, error)
        monkeypatch.setattr(ocr_module, "recognize_image", fake)
        return fake
    return install


def run(paths, client=None, settings=None, cancel=None):
    """Запускает импорт и возвращает тройки ``(источник, evidence, ошибка)``."""
    received = []
    DocumentImportService(settings if settings is not None else {"document_cloud_enabled": True},
                          client).process(list(paths), cancel or Event(),
                                          lambda *args: received.append(args))
    return received


def image_page(number=1):
    return DocumentPage(number, image=object())


# ─────────────────────────────────────────────────────────────
# 1. Изображение + облако → Vision, Tesseract не запускается
# ─────────────────────────────────────────────────────────────

def test_image_with_cloud_uses_vision_only(pages, ocr):
    pages([image_page()])
    local = ocr("Локальный текст, который не должен использоваться")
    client = FakeClient(vision=({"driver": {"full_name": FIO}}, ""))
    received = run(["passport_01.jpg"], client)

    assert len(client.vision_calls) == 1
    assert local.calls == []                       # локальный OCR не тронут
    assert received[0][1][0].method == VISION_METHOD
    assert received[0][1][0].values["full_name"] == FIO
    assert not received[0][2]


def test_image_vision_is_called_with_deep_chain(pages, ocr):
    """Первый запрос к Vision — всегда с глубокой цепочкой (recall важнее)."""
    pages([image_page()])
    ocr("")
    client = FakeClient(vision=({}, ""))
    run(["scan.png"], client)
    assert [deep for _, deep in client.vision_calls] == [True]


# ─────────────────────────────────────────────────────────────
# 2. Облако выключено → локальный OCR
# ─────────────────────────────────────────────────────────────

def test_image_without_cloud_uses_local_ocr(pages, ocr):
    """Галочка облака снята оператором: локальный OCR, без пометки о резерве."""
    pages([image_page()])
    local = ocr("ФИО: " + FIO)
    client = FakeClient(vision=({"driver": {"full_name": "Из облака"}}, ""))
    received = run(["scan_02.png"], client, settings={"document_cloud_enabled": False})

    assert client.vision_calls == []               # в облако не ходили
    assert len(local.calls) == 1
    assert received[0][1][0].method == LOCAL_OCR_METHOD
    assert received[0][1][0].note == ""
    # Причина локального пути доходит до дерева: «облако выключено».
    assert received[0][1][0].local_reason == CLOUD_DISABLED


def test_image_without_client_uses_local_ocr_with_note(pages, ocr):
    """Облако включено, но клиента нет (ключ забрали) — резерв с пометкой."""
    pages([image_page()])
    local = ocr("ФИО: " + FIO)
    received = run(["scan_02.png"], None)
    assert len(local.calls) == 1
    assert received[0][1][0].method == LOCAL_OCR_FALLBACK_METHOD
    assert "GigaChat недоступен" in received[0][1][0].note
    assert received[0][1][0].local_reason == CLOUD_CLIENT_MISSING


# ─────────────────────────────────────────────────────────────
# 3. Vision упал → тихий переход на Tesseract
# ─────────────────────────────────────────────────────────────

def test_vision_failure_switches_to_local_ocr(pages, ocr, caplog):
    pages([image_page()])
    local = ocr("ФИО: " + FIO)

    def vision(cancel, deep):
        raise RuntimeError("GigaChat: HTTP 503.")

    client = FakeClient(vision=vision)
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        received = run(["passport_01.jpg"], client)

    assert len(client.vision_calls) == 1
    assert len(local.calls) == 1                   # резерв сработал
    evidence = received[-1][1][0]
    assert evidence.method == LOCAL_OCR_FALLBACK_METHOD
    assert LOCAL_OCR_FAILED_NOTE in evidence.note
    assert evidence.local_reason == CLOUD_VISION_FAILED
    # Причина отказа GigaChat не потеряна: она ушла отдельным сообщением.
    assert any("503" in error for _, _, error in received)
    assert "переключение на Tesseract" in caplog.text
    assert "причина=vision_failed" in caplog.text
    # В логе нет ни значения поля, ни имени файла.
    assert FIO not in caplog.text
    assert "passport_01.jpg" not in caplog.text


def test_vision_failure_without_local_ocr_keeps_error(pages, ocr, caplog):
    """Оба пути не справились — оператор видит и причину, и отказ резерва."""
    pages([image_page()])
    ocr(error=RuntimeError("Локальный OCR недоступен: не найден Tesseract OCR"))
    client = FakeClient(vision=lambda cancel, deep: (_ for _ in ()).throw(
        RuntimeError("GigaChat: HTTP 401.")))
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        received = run(["passport_01.jpg"], client)

    errors = [error for _, _, error in received if error]
    assert any("401" in error for error in errors)
    assert any("Tesseract" in error for error in errors)
    assert all(not items for _, items, _ in received)
    assert "локальный OCR не удался" in caplog.text


# ─────────────────────────────────────────────────────────────
# 4. 429: пауза, повтор, и только потом — резерв (видимый оператору)
# ─────────────────────────────────────────────────────────────

def test_rate_limit_is_retried_after_pause(pages, ocr, monkeypatch, caplog):
    """429 → пауза 30 секунд → повтор Vision, и только он даёт результат."""
    pages([image_page()])
    local = ocr("ФИО: " + FIO)
    # Спим не по-настоящему: проверяем и запрошенную паузу, и сам факт ожидания.
    monkeypatch.setattr(DocumentImportService, "RATE_LIMIT_PAUSE_SECONDS", 30.0)
    pauses = []
    monkeypatch.setattr("time.sleep", lambda seconds: pauses.append(seconds))

    def vision(cancel, deep):
        if len(client.vision_calls) == 1:
            raise RuntimeError("GigaChat: HTTP 429.")
        return {"driver": {"full_name": FIO}}, ""

    client = FakeClient(vision=vision)
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        received = run(["passport_01.jpg"], client)

    # Пауза идёт короткими шагами (отмена должна срабатывать мгновенно),
    # но суммарно — ровно 30 секунд.
    assert sum(pauses) == pytest.approx(30.0)
    assert len(pauses) == 300
    assert len(client.vision_calls) == 2           # и ровно один повтор
    assert local.calls == []                       # до резерва дело не дошло
    assert received[-1][1][0].method == VISION_METHOD
    assert "повтор через 30 сек" in caplog.text
    assert "429" in caplog.text


def test_rate_limit_exhausted_falls_back_with_visible_note(pages, ocr, monkeypatch, caplog):
    """Повтор тоже 429 → локальный OCR, но оператор видит ограничение."""
    pages([image_page()])
    local = ocr("ФИО: " + FIO)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def vision(cancel, deep):
        raise RuntimeError("GigaChat: HTTP 429.")

    client = FakeClient(vision=vision)
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        received = run(["passport_01.jpg"], client)

    assert len(client.vision_calls) == 2           # повтор был
    assert len(local.calls) == 1                   # и только потом резерв
    evidence = received[-1][1][0]
    assert evidence.method == LOCAL_OCR_FALLBACK_METHOD
    assert evidence.note == LOCAL_OCR_LIMIT_NOTE
    assert evidence.local_reason == CLOUD_RATE_LIMIT
    assert "429" in evidence.note
    assert "429" in caplog.text


def test_rate_limit_retry_once_per_import(pages, ocr, monkeypatch):
    """Лимит запросов — один повтор на импорт, а не на каждый файл."""
    pages([image_page()])
    local = ocr("")
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def vision(cancel, deep):
        raise RuntimeError("GigaChat: HTTP 429.")

    client = FakeClient(vision=vision)
    run(["a.png", "b.png"], client)

    assert len(client.vision_calls) == 3           # 2 файла + 1 повтор
    assert len(local.calls) == 2                   # оба файла прочитаны локально


# ─────────────────────────────────────────────────────────────
# 5. Текстовые документы: локально, Vision не вызывается
# ─────────────────────────────────────────────────────────────

def test_text_pdf_is_read_locally(pages, ocr):
    """PDF с текстовым слоем: локальный разбор, ни Vision, ни Tesseract."""
    pages([DocumentPage(1, text="Перевозчик\nФИО: " + FIO + "\nИНН: " + INN)])
    local = ocr("")
    client = FakeClient(vision=({}, ""), text={})
    received = run(["contract.pdf"], client)

    assert client.vision_calls == []
    assert client.text_calls == []
    assert local.calls == []
    assert received[0][1][0].method == LOCAL_TEXT_METHOD
    assert received[0][1][0].values["full_name"] == FIO


def test_text_without_fields_goes_to_text_model_not_vision(pages, ocr):
    """Текст есть, полей нет: облако получает ТЕКСТ (дешевле), не картинку."""
    pages([DocumentPage(1, text="Свободное описание без подписей и меток реквизитов")])
    client = FakeClient(vision=({"driver": {"full_name": FIO}}, ""),
                        text=lambda text: {"carrier": {"inn": INN}})
    received = run(["letter.pdf"], client)

    assert client.text_calls                       # текстовая модель вызвана
    # Vision вызовется только последней попыткой — уже по рендеру страницы.
    assert received[0][1][0].method == TEXT_METHOD
    assert received[0][1][0].values["inn"] == INN


def test_docx_is_read_locally(pages, ocr):
    """DOCX — текстовый документ: локальное извлечение, облако не участвует."""
    pages([DocumentPage(1, text="Водитель\nФИО: " + FIO)])
    client = FakeClient(vision=({}, ""), text={})
    received = run(["anketa.docx"], client)

    assert client.vision_calls == [] and client.text_calls == []
    assert received[0][1][0].method == LOCAL_TEXT_METHOD


# ─────────────────────────────────────────────────────────────
# 6. Архивы и многостраничные PDF: путь выбирается ПО СТРАНИЦЕ
# ─────────────────────────────────────────────────────────────

def test_archive_pages_choose_path_per_page(pages, ocr):
    """В архиве и текст, и картинка — каждая страница идёт своим путём."""
    pages([
        DocumentPage(1, text="Перевозчик\nИНН: " + INN, label="docs.zip → reestr.docx"),
        DocumentPage(2, image=object(), label="docs.zip → scan.jpg"),
    ])
    local = ocr("")
    client = FakeClient(vision=({"driver": {"full_name": FIO}}, ""))
    received = run(["docs.zip"], client)

    methods = {source: items[0].method for source, items, _ in received if items}
    assert methods == {"docs.zip → reestr.docx": LOCAL_TEXT_METHOD,
                       "docs.zip → scan.jpg": VISION_METHOD}
    assert local.calls == []


def test_pdf_text_page_keeps_text_first_path(pages, ocr):
    """Текстовая страница PDF с ленивым рендером: текст → текст-модель → Vision."""
    page = DocumentPage(1, text="Описание без подписей и меток реквизитов",
                        image_loader=lambda: object())
    pages([page])
    client = FakeClient(vision=({"driver": {"full_name": FIO}}, ""), text={})
    received = run(["scan.pdf"], client)

    assert client.text_calls                       # сначала текстовая модель
    assert [deep for _, deep in client.vision_calls] == [False]
    assert received[-1][1][0].method == VISION_METHOD


# ─────────────────────────────────────────────────────────────
# 7. Лог: метод совпадает с путём, ПДн нет
# ─────────────────────────────────────────────────────────────

def test_log_reports_method_and_page_without_pii(pages, ocr, caplog):
    pages([image_page(3)])
    ocr("")
    client = FakeClient(vision=({"driver": {"full_name": FIO}}, ""))
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        run(["hidden_name.jpg"], client)

    assert f"метод={VISION_METHOD}" in caplog.text
    assert "страница=3" in caplog.text
    assert FIO not in caplog.text
    assert INN not in caplog.text
    assert "hidden_name.jpg" not in caplog.text


def test_log_reports_local_fallback(pages, ocr, caplog):
    pages([image_page()])
    ocr("ФИО: " + FIO)
    client = FakeClient(vision=lambda cancel, deep: (_ for _ in ()).throw(
        RuntimeError("GigaChat: HTTP 500.")))
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        run(["scan.png"], client)

    assert f"метод={LOCAL_OCR_FALLBACK_METHOD}" in caplog.text
    assert "причина=vision_failed" in caplog.text
    assert FIO not in caplog.text
