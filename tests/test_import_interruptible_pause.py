"""Прерываемая пауза при 429: кнопка «Отменить» не ждёт 30 секунд.

Требование шага: пауза перед повтором Vision после ответа 429 (30 секунд)
должна прерываться отменой импорта. Раньше здесь стоял `time.sleep(pause)`
одним куском, и всё это время окно импорта не реагировало на «Отменить».

Данные синтетические (AGENTS.md § 4): клиент GigaChat и Tesseract подменены
двойниками, сеть не трогается, реальные документы не используются.
"""
import logging
import threading
import time
from threading import Event
from types import SimpleNamespace

import pytest

from core.document_import_service import (
    CLOUD_RATE_LIMIT, DocumentImportService, LOCAL_OCR_FALLBACK_METHOD,
)
from core.document_reader import DocumentPage
from core.import_cancel import (
    CANCEL_POLL_SECONDS, ImportCancelled, check_cancel, interruptible_sleep,
)

FIO = "Тестов Тест Тестович"


class FakeClock:
    """Подмена `time.sleep`: считает шаги и «двигает» время без ожидания.

    Шаг ожидания равен 0,1 секунды, поэтому десять вызовов — это ровно
    одна секунда виртуального времени.
    """

    def __init__(self, cancel=None, cancel_after=None):
        self.cancel = cancel
        self.cancel_after = cancel_after
        self.steps = []

    def __call__(self, seconds):
        self.steps.append(seconds)
        if self.cancel is not None and self.cancel_after is not None \
                and len(self.steps) >= self.cancel_after:
            self.cancel.set()

    @property
    def seconds(self):
        """Сколько виртуального времени «прошло»."""
        return sum(self.steps)


@pytest.fixture
def page(monkeypatch):
    """Синтетическая страница-изображение вместо чтения файла."""
    import core.document_import_service as module
    monkeypatch.setattr(module, "read_document",
                        lambda *a: iter([DocumentPage(1, image=object())]))


@pytest.fixture
def local_ocr(monkeypatch):
    """Локальный Tesseract: считает вызовы, отдаёт заданный текст."""
    import core.document_ocr as ocr_module
    calls = []

    def fake(image, cancel):
        calls.append(image)
        return "ФИО: " + FIO

    monkeypatch.setattr(ocr_module, "recognize_image", fake)
    return calls


@pytest.fixture
def clock(monkeypatch):
    """Виртуальные часы: `time.sleep` не спит по-настоящему."""
    def install(cancel=None, cancel_after=None):
        fake = FakeClock(cancel, cancel_after)
        monkeypatch.setattr(time, "sleep", fake)
        return fake
    return install


# ─────────────────────────────────────────────────────────────
# 1. Само ожидание: шаги, сумма, реакция на отмену
# ─────────────────────────────────────────────────────────────

def test_long_pause_is_split_into_short_steps(clock):
    """30 секунд ждём шагами по 0,1 — сумма та же, но отмена видна сразу."""
    fake = clock()
    interruptible_sleep(30.0, Event())
    assert fake.seconds == pytest.approx(30.0, abs=1e-6)
    assert len(fake.steps) == 300
    # Ни одного длинного куска: каждый шаг — примерно 0,1 секунды.
    assert max(fake.steps) <= CANCEL_POLL_SECONDS + 1e-9
    assert min(fake.steps) >= CANCEL_POLL_SECONDS - 1e-3


def test_pause_stops_on_cancel_without_waiting(clock):
    """Отменённый импорт не ждёт вовсе: ImportCancelled на первой проверке."""
    cancel = Event()
    cancel.set()
    fake = clock()
    with pytest.raises(ImportCancelled):
        interruptible_sleep(30.0, cancel)
    assert fake.steps == []


def test_pause_is_interrupted_after_one_second(clock):
    """Отмена через секунду ожидания: пауза обрывается, ImportCancelled наружу."""
    cancel = Event()
    fake = clock(cancel, cancel_after=10)
    with pytest.raises(ImportCancelled):
        interruptible_sleep(30.0, cancel)
    assert cancel.is_set()
    assert fake.seconds == pytest.approx(1.0)
    assert len(fake.steps) == 10


def test_pause_without_cancel_waits_in_one_call(clock):
    """Отмены нет — ждём как раньше, одним куском и ровно столько, сколько просили."""
    fake = clock()
    interruptible_sleep(30.0, None)
    assert fake.steps == [30.0]


def test_zero_pause_only_checks_cancel(clock):
    fake = clock()
    interruptible_sleep(0, Event())
    assert fake.steps == []


def test_check_cancel_without_event_does_not_fail():
    """Признак отмены необязателен: проверка без него ничего не ломает."""
    check_cancel(None)


# ─────────────────────────────────────────────────────────────
# 2. 429 в сервисе импорта: пауза прерывается, повтора и резерва нет
# ─────────────────────────────────────────────────────────────

def run_image(settings=None, client=None, cancel=None):
    """Прогон импорта одной картинки; возвращает то, что получил оператор."""
    received = []
    DocumentImportService(settings if settings is not None else
                          {"document_cloud_enabled": True},
                          client).process(["passport_01.jpg"], cancel or Event(),
                                          lambda *args: received.append(args))
    return received


def rate_limited_client():
    """Клиент-двойник: Vision всегда отвечает 429."""
    calls = []

    def vision(image, cancel, deep=True):
        calls.append(deep)
        raise RuntimeError("GigaChat: HTTP 429.")

    return SimpleNamespace(recognize_image=vision, vision_model="GigaChat-2-Max"), calls


def test_rate_limit_pause_is_interrupted_by_cancel(page, local_ocr, clock, caplog):
    """Отмена во время паузы 429: повтора нет, локальный OCR не запускается."""
    cancel = Event()
    client, vision_calls = rate_limited_client()
    fake = clock(cancel, cancel_after=10)          # отмена через 1 секунду паузы

    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        received = run_image(client=client, cancel=cancel)

    assert cancel.is_set()
    assert vision_calls == [True]                  # повтора Vision не было
    assert local_ocr == []                         # и локальный OCR не понадобился
    assert fake.seconds == pytest.approx(1.0)      # ждали секунду, а не 30
    assert "пауза 429 прервана пользователем" in caplog.text
    # Оператор видит причину отказа Vision, но не ждёт окончания импорта.
    assert any("429" in error for _, _, error in received)


def test_rate_limit_pause_finishes_when_not_cancelled(page, local_ocr, clock, caplog):
    """Без отмены пауза доходит до конца: 30 секунд и ровно один повтор."""
    client, vision_calls = rate_limited_client()
    fake = clock()

    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        received = run_image(client=client)

    assert fake.seconds == pytest.approx(30.0)
    assert vision_calls == [True, False]           # повтор без глубокой цепочки
    assert len(local_ocr) == 1                     # и только потом резерв
    assert "пауза 429 прервана пользователем" not in caplog.text
    evidence = [item for _, items, _ in received for item in items]
    assert evidence[-1].method == LOCAL_OCR_FALLBACK_METHOD
    assert evidence[-1].local_reason == CLOUD_RATE_LIMIT


def test_cancelled_pause_stops_the_whole_import(page, local_ocr, clock, caplog):
    """Отмена во время паузы обрывает импорт целиком: следующий файл не читается."""
    cancel = Event()
    client, vision_calls = rate_limited_client()
    clock(cancel, cancel_after=5)
    received = []

    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        DocumentImportService({"document_cloud_enabled": True}, client).process(
            ["passport_01.jpg", "passport_02.jpg"], cancel,
            lambda *args: received.append(args))

    assert cancel.is_set()
    assert len(vision_calls) == 1                  # второй файл не обрабатывался
    assert local_ocr == []                         # и резерв не запускался
    assert "пауза 429 прервана пользователем" in caplog.text
    assert len(received) == 1                      # результат только по первому файлу


def test_pause_is_interrupted_in_wall_clock(page, local_ocr, caplog):
    """Живая проверка: отмена через секунду обрывает паузу, не дожидаясь 30."""
    cancel = Event()
    client, vision_calls = rate_limited_client()
    timer = threading.Timer(1.0, cancel.set)
    timer.start()
    started = time.perf_counter()
    try:
        with caplog.at_level(logging.INFO, logger="core.document_import_service"):
            run_image(client=client, cancel=cancel)
    finally:
        timer.cancel()
    elapsed = time.perf_counter() - started

    assert "пауза 429 прервана пользователем" in caplog.text
    assert vision_calls == [True]
    assert elapsed < 10.0, f"пауза не прервалась: {elapsed:.1f} с"


# ─────────────────────────────────────────────────────────────
# 3. Паузы самого клиента GigaChat (повторы при 429/5xx/таймауте)
# ─────────────────────────────────────────────────────────────

def client_with_status(status_code, monkeypatch):
    """Клиент GigaChat с подменённым HTTP: ключ и токен — двойники.

    `requests.post` подменяется через monkeypatch: живой модуль `requests`
    тестами не правится.
    """
    import core.gigachat_client as module
    client = module.GigaChatClient(auth_key="synthetic")
    monkeypatch.setattr(client, "_get_token", lambda force=False: "synthetic-token")
    monkeypatch.setattr(module.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=status_code,
                                                        text="{}", json=lambda: {}))
    return client


def test_client_backoff_is_interruptible(monkeypatch, clock):
    """Пауза повтора внутри клиента тоже прерывается отменой."""
    cancel = Event()
    clock(cancel, cancel_after=1)
    client = client_with_status(429, monkeypatch)
    with pytest.raises(ImportCancelled):
        client.recognize_text("Водитель: " + FIO, cancel=cancel)


def test_client_backoff_without_cancel_waits_fully(monkeypatch, clock):
    """Без отмены клиент ждёт свои задержки как раньше: 1с → 2с → 4с."""
    fake = clock()
    client = client_with_status(429, monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        client.recognize_text("Водитель: " + FIO)
    assert fake.steps == [1.0, 2.0, 4.0]
    assert "429" in str(exc.value)
