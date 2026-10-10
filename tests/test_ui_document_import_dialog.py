"""Понятный диалог импорта: дерево сущностей, подтверждение целиком, объяснения.

Данные синтетические (AGENTS.md § 4): реальных ПДн в тестах нет.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from threading import Event
from types import SimpleNamespace

import pytest

from core.document_import_service import Evidence, compare_fields
from core.document_import_service import LOCAL_OCR_FALLBACK_METHOD, LOCAL_OCR_LIMIT_NOTE

# ── синтетические данные ──
FIO = "Иванов Иван Иванович"
BIRTH = "15.03.1985"
PASSPORT_SERIES = "60 26"
PASSPORT_NUMBER = "123456"
VIN = "BX517602ABCDEFGHJ"
INN = "7701234567"

COL_WHAT, COL_VALUE, COL_TARGET, COL_CHECK, COL_ACTION = range(5)


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


def load(dialog, evidence):
    """Загружает синтетические документы так же, как это делает `finished`."""
    dialog.load_rows(compare_fields(evidence))
    return dialog.entities


def entity_of(dialog, section, index=0):
    return [entity for entity in dialog.entities if entity.section == section][index]


def top_level_texts(dialog):
    return [dialog.tree.topLevelItem(i).text(COL_WHAT)
            for i in range(dialog.tree.topLevelItemCount())]


def all_node_texts(item):
    texts = [item.text(COL_WHAT)]
    for index in range(item.childCount()):
        texts.extend(all_node_texts(item.child(index)))
    return texts


def driver_evidence():
    return [
        Evidence("driver", {"full_name": FIO, "birth_date": BIRTH,
                            "passport_series": PASSPORT_SERIES,
                            "passport_number": PASSPORT_NUMBER}, "passport_01.jpg",
                 "GigaChat Vision"),
        Evidence("driver", {"full_name": FIO, "license_number": "349327",
                            "license_categories": "B, C"}, "vu_01.jpg", "OCR"),
        Evidence("trailer", {"vin": VIN, "color": "красный"}, "polupricep.docx", "Текст"),
        Evidence("carrier", {"full_name": "ООО «Ромашка»", "inn": INN}, "egrul.pdf", "Текст"),
    ]


# ─────────────────────────────────────────────────────────────
# Дерево: сущность → источник → поля
# ─────────────────────────────────────────────────────────────

def test_dialog_has_entity_grouping(dialog):
    """В дереве есть узлы сущностей: ВОДИТЕЛЬ, ПОЛУПРИЦЕП, ОРГАНИЗАЦИЯ."""
    load(dialog, driver_evidence())
    texts = top_level_texts(dialog)
    assert len(texts) == 3
    assert any(text.startswith("ВОДИТЕЛЬ") for text in texts)
    assert any(text.startswith("ПОЛУПРИЦЕП") for text in texts)
    assert any(text.startswith("ОРГАНИЗАЦИЯ") for text in texts)
    assert f"ВОДИТЕЛЬ: {FIO}, {BIRTH}" in texts


def test_tree_shows_source_under_entity_and_fields_under_source(dialog):
    """Три уровня: сущность → 📄 источник → поля. У источника — путь распознавания."""
    load(dialog, driver_evidence())
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    sources = [driver.child(i).text(COL_WHAT) for i in range(driver.childCount())]
    assert "📄 passport_01.jpg (GigaChat Vision)" in sources
    assert "📄 vu_01.jpg (OCR локально)" in sources
    passport = driver.child(sources.index("📄 passport_01.jpg (GigaChat Vision)"))
    fields = [passport.child(i).text(COL_WHAT) for i in range(passport.childCount())]
    assert "Серия паспорта" in fields
    assert "Номер паспорта" in fields


def test_entity_node_lists_documents_in_tooltip(dialog):
    """Подсказка сущности объясняет, откуда данные и что нажимать."""
    load(dialog, driver_evidence())
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    hint = driver.toolTip(COL_WHAT)
    assert "Найдено в файлах: passport_01.jpg, vu_01.jpg" in hint
    assert "Подтвердить водителя целиком" in hint


def test_source_tooltip_names_the_recognition_path(dialog):
    """Подсказка источника: чем прочитан документ, без значений полей."""
    load(dialog, driver_evidence())
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    sources = {driver.child(i).text(COL_WHAT): driver.child(i).toolTip(COL_WHAT)
               for i in range(driver.childCount())}
    vision_hint = sources["📄 passport_01.jpg (GigaChat Vision)"]
    assert "Vision" in vision_hint and "модель" in vision_hint
    ocr_hint = sources["📄 vu_01.jpg (OCR локально)"]
    assert "Tesseract" in ocr_hint and "русский" in ocr_hint
    # Значений полей в подсказках нет — только путь распознавания.
    assert FIO not in vision_hint and PASSPORT_NUMBER not in vision_hint


def test_local_fallback_is_visible_in_tree(dialog):
    """Резерв после отказа GigaChat: и в строке источника, и в подсказке."""
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("scan_01.png", [Evidence("driver", {"full_name": FIO}, "scan_01.png",
                                            LOCAL_OCR_FALLBACK_METHOD, False, "", LOCAL_OCR_LIMIT_NOTE)], "")
    dialog.finished()
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    labels = [driver.child(i).text(COL_WHAT) for i in range(driver.childCount())]
    assert "📄 scan_01.png (OCR локально)" in labels
    hint = driver.child(labels.index("📄 scan_01.png (OCR локально)")).toolTip(COL_WHAT)
    assert "429" in hint and "распознано локально" in hint


def test_dialog_explains_itself(dialog):
    """Заголовок окна и инструкция — словами оператора, без жаргона."""
    from PyQt5.QtWidgets import QLabel
    assert dialog.windowTitle() == "Проверка документов"
    texts = [widget.text() for widget in dialog.findChildren(QLabel)]
    instruction = next(text for text in texts if "Галочка" in text)
    assert "Слева — что нашли в документах" in instruction
    assert "Подтвердить целиком" in instruction


def test_header_has_hide_unreadable_toggle(dialog):
    assert dialog.hide_unreadable_button.text() == "Скрыть непрочитанные"
    assert dialog.hide_unreadable_button.isCheckable()
    assert dialog.uncheck_button.text() == "Снять галочки у выбранных"


# ─────────────────────────────────────────────────────────────
# Подтверждение сущности целиком
# ─────────────────────────────────────────────────────────────

def test_confirm_entity_transfers_all_fields_at_once(dialog, window):
    """Одна кнопка на сущность: подтверждает все прочитанные поля сразу."""
    load(dialog, driver_evidence())
    driver = entity_of(dialog, "driver")
    button = dialog.tree.itemWidget(dialog.entity_item(driver.uid), COL_ACTION)
    assert button.text() == "Подтвердить водителя целиком"
    button.click()
    confirmed = [value.field for value in driver.fields if dialog.is_confirmed(driver.uid, value.field)]
    assert "full_name" in confirmed and "passport_number" in confirmed
    dialog.apply()
    data = window.driver_tab.get_data()
    assert data["full_name"] == FIO
    assert data["passport_number"] == PASSPORT_NUMBER
    assert data["passport_series"] == PASSPORT_SERIES
    assert data["license_number"] == "349327"


def test_entity_checkbox_reflects_confirmed_fields(dialog):
    from PyQt5.QtCore import Qt
    load(dialog, driver_evidence())
    driver = entity_of(dialog, "driver")
    item = dialog.entity_item(driver.uid)
    assert item.checkState(COL_CHECK) == Qt.Unchecked
    dialog.set_entity_confirmed(driver.uid, True)
    assert item.checkState(COL_CHECK) == Qt.Checked
    dialog.uncheck_all()
    assert item.checkState(COL_CHECK) == Qt.Unchecked


def test_uncheck_button_clears_every_confirmation(dialog):
    load(dialog, driver_evidence())
    for entity in dialog.entities:
        dialog.set_entity_confirmed(entity.uid, True)
    assert any(dialog.is_confirmed(entity.uid, value.field)
               for entity in dialog.entities for value in entity.fields)
    dialog.uncheck_button.click()
    assert not any(dialog.is_confirmed(entity.uid, value.field)
                   for entity in dialog.entities for value in entity.fields)


def test_done_message_after_transfer(dialog, window):
    """После переноса оператор видит, что всё ушло в форму, и может закрывать."""
    load(dialog, driver_evidence())
    dialog.set_entity_confirmed(entity_of(dialog, "driver").uid, True)
    dialog.apply()
    assert dialog.progress_label.text().startswith("Готово. Все данные перенесены в форму.")
    assert "Можно закрывать" in dialog.progress_label.text()


# ─────────────────────────────────────────────────────────────
# Ошибка «разные сущности» объясняет, что делать
# ─────────────────────────────────────────────────────────────

def test_error_message_lists_both_entities(dialog, window, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: asked.append(args[1:3]))
    load(dialog, [
        Evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg", "Текст"),
        Evidence("driver", {"full_name": "Петров Пётр Петрович", "phone": "+7 900 111-11-11"},
                 "passport_02.jpg", "Текст"),
    ])
    for entity in dialog.entities:
        dialog.set_entity_confirmed(entity.uid, True)
    dialog.apply()
    assert [title for title, _ in asked] == ["Разные сущности"]
    message = asked[0][1]
    assert FIO in message
    assert "Петров Пётр Петрович" in message
    assert "Что делать:" in message
    assert "Снять галочки у выбранных" in message
    assert "Подтвердить водителя целиком" in message
    # Форма не тронута, подтверждения сняты, поля ждут решения оператора.
    assert window.driver_tab.get_data()["full_name"] in ("", None) or \
        window.driver_tab.get_data()["full_name"] != FIO


def test_same_entity_fields_are_not_reported_as_different(dialog, window, monkeypatch):
    """Паспорт и ВУ одного водителя — одна сущность, ошибки быть не должно."""
    from PyQt5.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: asked.append(args[1:3]))
    load(dialog, driver_evidence())
    dialog.set_entity_confirmed(entity_of(dialog, "driver").uid, True)
    dialog.apply()
    assert asked == []
    assert window.driver_tab.get_data()["full_name"] == FIO


# ─────────────────────────────────────────────────────────────
# Непрочитанные поля
# ─────────────────────────────────────────────────────────────

def test_unreadable_fields_are_visible_and_hidden_by_toggle(dialog):
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    labels = all_node_texts(driver)
    assert "📄 Не прочитано ни в одном документе" in labels
    assert "Кем выдан паспорт" in labels

    hidden = [dialog.field_item(entity_of(dialog, "driver").uid, "passport_issuer")]
    assert not hidden[0].isHidden()
    dialog.hide_unreadable_button.setChecked(True)
    assert hidden[0].isHidden()
    dialog.hide_unreadable_button.setChecked(False)
    assert not hidden[0].isHidden()


def test_unreadable_field_shows_placeholder_and_cannot_be_confirmed(dialog):
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "passport_number")
    assert item.text(COL_VALUE) == "[не прочитано]"
    dialog.set_field_confirmed(driver.uid, "passport_number", True)
    assert not dialog.is_confirmed(driver.uid, "passport_number")


def test_every_field_has_an_edit_button(dialog):
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    labels = set()
    for value in driver.fields:
        button = dialog.tree.itemWidget(dialog.field_item(driver.uid, value.field), COL_ACTION)
        labels.add(button.text())
    assert labels == {"Править", "Ввести вручную"}


def test_edit_button_opens_inline_editor(dialog):
    from PyQt5.QtWidgets import QAbstractItemView
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "passport_number")
    dialog.tree.setCurrentItem(item)
    dialog.tree.itemWidget(item, COL_ACTION).click()
    assert dialog.tree.state() == QAbstractItemView.EditingState


def test_typed_value_replaces_unreadable_and_transfers(dialog, window):
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    dialog.field_item(driver.uid, "passport_number").setText(COL_VALUE, PASSPORT_NUMBER)
    assert driver.field_value("passport_number").value == PASSPORT_NUMBER
    dialog.set_field_confirmed(driver.uid, "passport_number", True)
    dialog.apply()
    assert window.driver_tab.get_data()["passport_number"] == PASSPORT_NUMBER


# ─────────────────────────────────────────────────────────────
# Прогресс и сводка
# ─────────────────────────────────────────────────────────────

def test_progress_reports_files_and_entities(dialog, monkeypatch):
    dialog.paths = ["passport_01.jpg", "vu_01.jpg"]
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("passport_01.jpg", driver_evidence()[:2], "")
    assert dialog.progress_label.text() == "Обработано 1 из 2 файлов."
    dialog.receive("vu_01.jpg", [], "")
    dialog.finished()
    text = dialog.progress_label.text()
    assert text.startswith("Обработка завершена. Обработано 2 из 2 файлов.")
    assert "водителей — 1" in text
    assert "Спорных полей — 0" in text
    assert dialog.progress.value() == 2


def test_progress_counts_disputed_fields(dialog):
    load(dialog, [
        Evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg", "Текст"),
        Evidence("driver", {"full_name": FIO, "passport_number": "654321"},
                 "passport_02.jpg", "OCR"),
    ])
    text = dialog.progress_label.text()
    assert "Спорных полей — 1" in text
    item = dialog.field_item(entity_of(dialog, "driver").uid, "passport_number")
    assert item.text(COL_VALUE) == "[расхождение в источниках]"
    assert PASSPORT_NUMBER in item.toolTip(COL_VALUE)
    assert "654321" in item.toolTip(COL_VALUE)


def test_conflict_field_keeps_both_values_in_tree(dialog):
    """Спорное поле: оба значения остаются в дереве, каждое со своим файлом."""
    from PyQt5.QtCore import Qt
    load(dialog, [
        Evidence("driver", {"full_name": FIO, "passport_number": PASSPORT_NUMBER},
                 "passport_01.jpg", "Текст"),
        Evidence("driver", {"full_name": FIO, "passport_number": "654321"},
                 "passport_02.jpg", "OCR"),
    ])
    item = dialog.field_item(entity_of(dialog, "driver").uid, "passport_number")
    variants = [(item.child(i).text(COL_WHAT), item.child(i).text(COL_VALUE))
                for i in range(item.childCount())]
    assert sorted(variants) == [("📄 passport_01.jpg (текст документа)", PASSPORT_NUMBER),
                                ("📄 passport_02.jpg (OCR локально)", "654321")]
    # Спорное поле — на красном фоне, у значений тоже.
    assert item.background(COL_VALUE).style() != Qt.NoBrush
    assert item.child(0).background(COL_VALUE).style() != Qt.NoBrush
    # Значения-варианты нельзя подтвердить или править как поле.
    assert not item.child(0).flags() & Qt.ItemIsUserCheckable


def test_unreadable_field_value_is_gray_italic(dialog):
    from PyQt5.QtCore import Qt
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "passport_number")
    assert item.text(COL_VALUE) == "[не прочитано]"
    assert item.font(COL_VALUE).italic()
    color = item.foreground(COL_VALUE).color()
    assert item.foreground(COL_VALUE).style() != Qt.NoBrush
    assert color.red() == color.green() == color.blue()      # серый
    # Читаемое поле серым не становится.
    readable = dialog.field_item(driver.uid, "full_name")
    assert not readable.font(COL_VALUE).italic()
    assert readable.foreground(COL_VALUE).style() == Qt.NoBrush


def test_entity_without_name_is_marked_in_tree(dialog):
    """Сущность без опознания видна оператору, а не молчит."""
    load(dialog, [Evidence("driver", {"license_number": "349327"}, "vu_01.jpg", "OCR")])
    driver = entity_of(dialog, "driver")
    assert driver.needs_review
    assert driver.key == "unknown_1"
    item = dialog.entity_item(driver.uid)
    assert "опознать не удалось" in item.text(COL_WHAT)
    assert "требует ручной проверки" in item.text(COL_VALUE)
    assert "Опознать по документам не удалось" in item.toolTip(COL_WHAT)
    assert "Опознать не удалось — 1" in dialog.progress_label.text()


def test_review_note_is_shown_under_entity(dialog):
    """Паспорт и ВУ связаны по ФИО — подсказка про дату рождения видна в дереве."""
    load(dialog, [
        Evidence("driver", {"full_name": FIO, "birth_date": BIRTH,
                            "passport_number": PASSPORT_NUMBER}, "passport_01.jpg", "Текст"),
        Evidence("driver", {"full_name": FIO, "license_number": "349327"},
                 "vu_01.jpg", "OCR"),
    ])
    driver = entity_of(dialog, "driver")
    item = dialog.entity_item(driver.uid)
    children = [item.child(i).text(COL_WHAT) for i in range(item.childCount())]
    notes = [text for text in children if text.startswith("⚠")]
    assert len(notes) == 1
    assert notes[0].startswith("⚠ проверить:")
    assert "Дата рождения" in notes[0]
    assert "vu_01.jpg" in notes[0]
    assert "проверить" in item.toolTip(COL_WHAT)
    # Подсказка не прячется вместе с непрочитанными полями.
    note_index = children.index(notes[0])
    dialog.hide_unreadable_button.setChecked(True)
    assert not item.child(note_index).isHidden()


def test_unread_files_are_shown_separately(dialog):
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("synthetic.png, стр. 1", [], "")
    dialog.finished()
    assert dialog.entities == []
    assert any("НЕ ПРОЧИТАННЫЕ ФАЙЛЫ" in text for text in top_level_texts(dialog))


# ─────────────────────────────────────────────────────────────
# Цель в форме
# ─────────────────────────────────────────────────────────────

def test_vehicle_entity_can_target_an_existing_row(dialog, window):
    from ui.document_import_dialog import COL_TARGET as COL_TARGET_INDEX, form_snapshot
    window.vehicles_tab.fill_data([{"vin": VIN, "brand_model": "Синтетик"}])
    # Слепок формы диалог снимает при открытии: в тесте форма заполнена позже.
    dialog.snapshot = form_snapshot(window)
    load(dialog, [Evidence("vehicles", {"vin": VIN, "color": "красный"},
                           "polupricep.docx", "Текст")])
    entity = entity_of(dialog, "vehicles")
    assert dialog.destination(entity) == ("vehicles", 0)
    combo = dialog.tree.itemWidget(dialog.entity_item(entity.uid), COL_TARGET_INDEX)
    assert combo.currentData() == ("vehicles", 0)
    dialog.set_entity_confirmed(entity.uid, True)
    dialog.apply()
    assert window.vehicles_tab.get_field(0, "color") == "красный"


def test_new_vehicle_becomes_its_own_row(dialog, window):
    from ui.document_import_dialog import COL_TARGET as COL_TARGET_INDEX
    load(dialog, [Evidence("vehicles", {"vin": VIN, "color": "красный"},
                           "polupricep.docx", "Текст")])
    entity = entity_of(dialog, "vehicles")
    combo = dialog.tree.itemWidget(dialog.entity_item(entity.uid), COL_TARGET_INDEX)
    assert combo.currentData() == ("vehicles", "new:0")
    dialog.set_entity_confirmed(entity.uid, True)
    dialog.apply()
    assert window.vehicles_tab.table.rowCount() == 1
    assert window.vehicles_tab.get_field(0, "vin") == VIN


def test_hover_over_import_tree_does_not_crash(dialog):
    """
    Наведение мыши на дерево импорта не роняет приложение.

    Срочный фикс 09.10.2026: общий помощник подсказок звал `item.toolTip()`
    без номера колонки, а у `QTreeWidgetItem` метод её требует. Исключение
    в слоте Qt для PyQt фатально (`qFatal` → `abort`, `0xC0000409` в
    `Qt5Core.dll`), поэтому наведение курсора на это дерево убивало
    приложение. Проверяем путь сигналом `itemEntered`, как его шлёт Qt.
    """
    load(dialog, driver_evidence())
    tree = dialog.tree
    top = tree.topLevelItem(0)
    nodes = [top] + [top.child(i) for i in range(top.childCount())]

    for node in nodes:
        for column in range(tree.columnCount()):
            tree.itemEntered.emit(node, column)

    assert top.toolTip(0), "подсказка узла проставлена"
