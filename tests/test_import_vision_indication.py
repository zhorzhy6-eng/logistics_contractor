"""Точная индикация локального пути: «выключено» и «отказало» — разные пометки.

Требование шага: в дереве импорта нельзя показывать одну пометку «OCR локально»
и для случая «оператор снял галочку облака», и для случая «GigaChat отказал».
Пометка собирается по коду причины (`CLOUD_*`), который сервис кладёт в
`Evidence.local_reason`, а подсказка (tooltip) объясняет причину словами.

Данные синтетические (AGENTS.md § 4): клиент GigaChat и Tesseract — двойники,
сеть не трогается, реальные документы не используются.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging
from threading import Event
from types import SimpleNamespace

import pytest

from core.document_import_service import (
    CLOUD_CLIENT_MISSING, CLOUD_DISABLED, CLOUD_RATE_LIMIT, CLOUD_VISION_FAILED,
    DocumentImportService, Evidence, LOCAL_OCR_FALLBACK_METHOD, LOCAL_OCR_METHOD,
    LOCAL_OCR_NOTE, local_ocr_hint, local_ocr_label,
)
from core.document_reader import DocumentPage

FIO = "Тестов Тест Тестович"
FILE = "scan_01.jpg"
COL_WHAT = 0


# ─────────────────────────────────────────────────────────────
# Двойники клиента и локального OCR
# ─────────────────────────────────────────────────────────────

def vision_ok():
    def vision(image, cancel, deep=True):
        return {"driver": {"full_name": FIO}}, ""
    return vision


def vision_fails(message):
    def vision(image, cancel, deep=True):
        raise RuntimeError(message)
    return vision


def fake_client(vision=None):
    return SimpleNamespace(recognize_image=vision or vision_ok(),
                           vision_model="GigaChat-2-Max")


#: Пять случаев: «облако выключено», «клиента нет», «401», «429», «Vision упал».
#: 401 и отсутствие клиента — один и тот же вывод для оператора: GigaChat
#: в этом запуске недоступен.
#: Поля параметра: код причины, настройки, клиент, пометка, причина словами,
#: отпечаток в логе.
CASES = [
    pytest.param(
        CLOUD_DISABLED, {"document_cloud_enabled": False}, fake_client,
        "OCR локально (облако выключено)", "Облако выключено в настройках.",
        "оператор выключил облако",
        id="cloud-disabled"),
    pytest.param(
        CLOUD_CLIENT_MISSING, {"document_cloud_enabled": True}, lambda: None,
        "OCR локально (GigaChat недоступен)", "GigaChat недоступен: нет ключа.",
        "облако включено, но GigaChat недоступен",
        id="client-missing"),
    pytest.param(
        CLOUD_CLIENT_MISSING, {"document_cloud_enabled": True},
        lambda: fake_client(vision_fails(
            "GigaChat вернул 401: неверный или просроченный access_token")),
        "OCR локально (GigaChat недоступен)", "GigaChat недоступен: нет ключа.",
        f"причина={CLOUD_CLIENT_MISSING}",
        id="vision-401"),
    pytest.param(
        CLOUD_RATE_LIMIT, {"document_cloud_enabled": True},
        lambda: fake_client(vision_fails("GigaChat: HTTP 429.")),
        "OCR локально (GigaChat вернул 429)", "GigaChat вернул 429 (лимит).",
        f"причина={CLOUD_RATE_LIMIT}",
        id="rate-limit"),
    pytest.param(
        CLOUD_VISION_FAILED, {"document_cloud_enabled": True},
        lambda: fake_client(vision_fails("GigaChat: HTTP 500.")),
        "OCR локально (Vision не сработал)", "GigaChat вернул ошибку.",
        f"причина={CLOUD_VISION_FAILED}",
        id="vision-failed"),
]

#: Подсказка начинается одинаково: чем читали локально.
HINT_PREFIX = "Tesseract, русский + английский. "


@pytest.fixture(autouse=True)
def no_real_pause(monkeypatch):
    """Пауза 429 (30 секунд) в тестах индикации не по делу: время не тратим."""
    monkeypatch.setattr("time.sleep", lambda seconds: None)


@pytest.fixture
def local_ocr(monkeypatch):
    """Локальный Tesseract: отдаёт синтетический текст, сеть не участвует."""
    import core.document_ocr as ocr_module
    calls = []

    def fake(image, cancel):
        calls.append(image)
        return "ФИО: " + FIO

    monkeypatch.setattr(ocr_module, "recognize_image", fake)
    return calls


@pytest.fixture
def pages(monkeypatch):
    """Чтение файла подменено: одна синтетическая страница-изображение."""
    import core.document_import_service as module
    monkeypatch.setattr(module, "read_document",
                        lambda *a: iter([DocumentPage(1, image=object())]))


def run_import(settings, client):
    """Прогон импорта: возвращает список `Evidence` (без исключений наружу)."""
    received = []
    DocumentImportService(settings, client).process(
        [FILE], Event(), lambda *args: received.append(args))
    return [item for _, items, _ in received for item in items]


# ─────────────────────────────────────────────────────────────
# 1. Ядро: код причины, пометка и подсказка
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason,settings,factory,label,hint_tail,log_marker", CASES)
def test_evidence_carries_reason(reason, settings, factory, label, hint_tail,
                                 log_marker, pages, local_ocr):
    """Причина отказа доходит до данных: метод, код причины и примечание."""
    evidence = run_import(settings, factory())
    assert evidence, "локальный путь не отработал"
    item = evidence[0]
    assert item.local_reason == reason
    if reason == CLOUD_DISABLED:
        # Облако выключено оператором: это не отказ, примечания нет.
        assert item.method == LOCAL_OCR_METHOD
        assert item.note == ""
    else:
        assert item.method == LOCAL_OCR_FALLBACK_METHOD
        assert item.note


@pytest.mark.parametrize("reason,settings,factory,label,hint_tail,log_marker", CASES)
def test_label_and_hint_match_reason(reason, settings, factory, label, hint_tail,
                                     log_marker):
    """Пометка и подсказка собираются по коду причины — в ядре, без Qt."""
    assert local_ocr_label(reason) == label
    assert local_ocr_hint(reason) == HINT_PREFIX + hint_tail


def test_label_without_reason_keeps_old_text():
    """Без причины пометка прежняя: «OCR локально», без придуманных скобок."""
    assert local_ocr_label("") == LOCAL_OCR_FALLBACK_METHOD
    assert local_ocr_hint("") == ""
    assert local_ocr_label("неизвестная причина") == LOCAL_OCR_FALLBACK_METHOD


@pytest.mark.parametrize("reason,settings,factory,label,hint_tail,log_marker", CASES)
def test_tree_label_uses_reason(reason, settings, factory, label, hint_tail, log_marker):
    """Подпись пути в дереве: пометка с причиной, а не одна на все случаи."""
    from ui.document_import_dialog import method_label, method_hint, source_label

    method = LOCAL_OCR_METHOD if reason == CLOUD_DISABLED else LOCAL_OCR_FALLBACK_METHOD
    assert method_label(method, reason) == label
    assert source_label(FILE, method, reason) == f"📄 {FILE} — {label}"
    assert method_hint(method, "GigaChat-2-Max", reason) == HINT_PREFIX + hint_tail


def test_tree_label_without_reason_keeps_parentheses():
    """Прежний формат строки не сломан: «📄 vu_01.jpg (OCR локально)»."""
    from ui.document_import_dialog import source_label
    assert source_label("vu_01.jpg", LOCAL_OCR_FALLBACK_METHOD) == "📄 vu_01.jpg (OCR локально)"
    assert source_label("passport.jpg", "GigaChat Vision") == "📄 passport.jpg (GigaChat Vision)"


@pytest.mark.parametrize("reason,settings,factory,label,hint_tail,log_marker", CASES)
def test_log_names_reason_without_pii(reason, settings, factory, label, hint_tail,
                                      log_marker, pages, local_ocr, caplog):
    """В логе — причина (кодом или словами), но ни значения поля, ни имени файла."""
    with caplog.at_level(logging.INFO, logger="core.document_import_service"):
        run_import(settings, factory())
    assert log_marker in caplog.text
    assert FIO not in caplog.text
    assert FILE not in caplog.text


# ─────────────────────────────────────────────────────────────
# 2. Дерево окна импорта: пометка и подсказка у строки источника
# ─────────────────────────────────────────────────────────────

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


@pytest.fixture
def dialog(window):
    from ui.document_import_dialog import DocumentImportDialog
    dialog = DocumentImportDialog(window)
    yield dialog
    dialog.deleteLater()


def all_labels(dialog):
    """Подписи всех строк дерева: сущности и их дети."""
    labels = []
    for index in range(dialog.tree.topLevelItemCount()):
        top = dialog.tree.topLevelItem(index)
        labels.append(top.text(COL_WHAT))
        labels.extend(top.child(i).text(COL_WHAT) for i in range(top.childCount()))
    return labels


def source_row(dialog, source):
    """Строка источника в дереве: (подпись, подсказка)."""
    for index in range(dialog.tree.topLevelItemCount()):
        top = dialog.tree.topLevelItem(index)
        for child_index in range(top.childCount()):
            child = top.child(child_index)
            if child.text(COL_WHAT).startswith("📄 " + source):
                return child.text(COL_WHAT), child.toolTip(COL_WHAT)
    raise AssertionError(f"в дереве нет строки источника {source}: {all_labels(dialog)}")


@pytest.mark.parametrize("reason,settings,factory,label,hint_tail,log_marker", CASES)
def test_tree_shows_reason_from_service(reason, settings, factory, label, hint_tail,
                                        log_marker, pages, local_ocr, dialog):
    """Сквозная проверка: причина от сервиса → пометка и подсказка в дереве."""
    received = []
    DocumentImportService(settings, factory()).process(
        [FILE], Event(), lambda *args: received.append(args))
    dialog.task = SimpleNamespace(cancel=Event())
    for source, items, error in received:
        dialog.receive(source, items, error)
    dialog.finished()

    source = received[0][0]
    text, hint = source_row(dialog, source)
    assert text == f"📄 {source} — {label}"
    assert hint == HINT_PREFIX + hint_tail


def test_tree_shows_two_different_notes_for_two_files(dialog):
    """Один и тот же локальный OCR читает два файла — пометки разные.

    «Облако выключено» и «клиент недоступен» больше не выглядят одинаково.
    """
    dialog.task = SimpleNamespace(cancel=Event())
    items = [
        Evidence("driver", {"full_name": FIO}, "off.jpg", LOCAL_OCR_METHOD,
                 False, "", "", CLOUD_DISABLED),
        Evidence("driver", {"full_name": FIO, "license_number": "349327"}, "err.jpg",
                 LOCAL_OCR_FALLBACK_METHOD, False, "", LOCAL_OCR_NOTE,
                 CLOUD_CLIENT_MISSING),
    ]
    for item in items:
        dialog.receive(item.source, [item], "")
    dialog.finished()

    labels = all_labels(dialog)
    assert f"📄 off.jpg — {local_ocr_label(CLOUD_DISABLED)}" in labels
    assert f"📄 err.jpg — {local_ocr_label(CLOUD_CLIENT_MISSING)}" in labels
    # Пометки действительно разные — ради этого шаг и делался.
    assert len({local_ocr_label(CLOUD_DISABLED),
                local_ocr_label(CLOUD_CLIENT_MISSING)}) == 2
