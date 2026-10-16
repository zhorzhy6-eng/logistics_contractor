"""Проверка документов: дерево полей, таблица файлов, перенос без галочек.

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

COL_WHAT, COL_VALUE, COL_TARGET = range(3)


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


def menu_titles(menu):
    return [action.text() for action in menu.actions()]


def label_texts(widget):
    """Подписи внутри виджета (для зоны перетаскивания)."""
    from PyQt5.QtWidgets import QLabel
    return [label.text() for label in widget.findChildren(QLabel)]


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
# Шапка окна: одна строка и «?» с полным текстом
# ─────────────────────────────────────────────────────────────

def test_dialog_explains_itself_in_one_line(dialog):
    """В шапке — ОДНА строка, остальное объяснение спрятано в «?»."""
    from PyQt5.QtWidgets import QLabel
    assert dialog.windowTitle() == "Проверка документов"
    assert dialog.header_hint.text() == ("Перетащите файлы или нажмите «Добавить», "
                                         "затем «Распознать».")
    labels = [widget.text() for widget in dialog.findChildren(QLabel)]
    assert not any("Галочка" in text for text in labels)
    assert not any("Слева — что нашли в документах" in text for text in labels)

    help_text = dialog.help_button.toolTip()
    assert dialog.help_button.text() == "?"
    assert "Проверка документов" in help_text
    assert "Перенести в форму" in help_text
    assert "Галочек" not in help_text


def test_header_has_no_checkbox_controls(dialog):
    """Кнопок-переключателей из прежней версии в окне не осталось."""
    for name in ("hide_unreadable_button", "uncheck_button", "manual_button",
                 "manual_section"):
        assert not hasattr(dialog, name), name
    texts = [button.text() for button in dialog.findChildren(type(dialog.add_button))]
    assert "Скрыть непрочитанные" not in texts
    assert "Снять галочки у выбранных" not in texts
    assert "Развернуть всё" not in texts
    assert "Свернуть всё" not in texts
    assert "Добавить пустые поля" not in texts


def test_main_buttons_are_highlighted(dialog):
    """«Распознать» и «Перенести в форму» — главные кнопки темы."""
    assert dialog.start_button.objectName() == "primary"
    assert dialog.apply_button.objectName() == "primary"
    assert dialog.add_button.objectName() == "secondary"
    assert dialog.cancel_button.objectName() == "secondary"
    # «Отменить обработку» видна только во время распознавания.
    assert dialog.cancel_button.isHidden()
    assert not dialog.start_button.isHidden()


# ─────────────────────────────────────────────────────────────
# Файлы: зона перетаскивания и таблица статусов
# ─────────────────────────────────────────────────────────────

def test_drop_zone_is_shown_while_there_are_no_files(dialog):
    """Пока файлов нет — подписанная зона перетаскивания вместо пустоты."""
    from ui.document_import_dialog import (DROP_FORMATS, DROP_PAGE, DROP_SUBTITLE,
                                           DROP_TITLE, FILES_PAGE)
    assert dialog.files_stack.currentIndex() == DROP_PAGE
    assert dialog.files_stack.currentWidget() is dialog.drop_zone
    texts = label_texts(dialog.drop_zone)
    assert DROP_TITLE in texts
    assert DROP_SUBTITLE in texts
    assert DROP_FORMATS in texts
    assert dialog.files_stack.currentIndex() != FILES_PAGE


def test_files_table_replaces_drop_zone_when_files_added(dialog):
    from ui.document_import_dialog import FILE_WAITING, FILES_PAGE
    dialog.add_paths(["passport_01.jpg", "vu_01.jpg"])
    assert dialog.files_stack.currentIndex() == FILES_PAGE
    assert dialog.files_stack.currentWidget() is dialog.files_table
    assert dialog.files_table.rowCount() == 2
    assert dialog.files_table.item(0, 0).text() == "passport_01.jpg"
    assert dialog.files_table.item(0, 1).text() == FILE_WAITING
    # Один и тот же файл дважды не добавляется.
    dialog.add_paths(["passport_01.jpg"])
    assert dialog.files_table.rowCount() == 2


def test_file_statuses_change_from_waiting_to_done(dialog):
    """Статусы файла: «ожидает» → «распознаётся…» → «готово» / «ошибка»."""
    from ui.document_import_dialog import (FILE_DONE, FILE_ERROR, FILE_RUNNING,
                                           FILE_WAITING)
    dialog.add_paths(["passport_01.jpg", "vu_01.jpg"])
    assert dialog.file_status["passport_01.jpg"] == FILE_WAITING
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("passport_01.jpg", driver_evidence()[:2], "")
    assert dialog.file_status["passport_01.jpg"] == FILE_DONE
    assert dialog.files_table.item(0, 1).text() == FILE_DONE
    # Следующий файл сервис читает сразу после первого.
    assert dialog.file_status["vu_01.jpg"] == FILE_RUNNING
    dialog.receive("vu_01.jpg", [], "Vision 429")
    assert dialog.file_status["vu_01.jpg"] == FILE_ERROR
    assert dialog.files_table.item(1, 2).text() == "Vision 429"
    dialog.finished()


def test_file_rows_are_colored_by_status(dialog):
    """Цвет строки: зелёный — готово, красный — ошибка."""
    from PyQt5.QtCore import Qt
    from ui.document_import_dialog import FILE_ERROR
    dialog.add_paths(["passport_01.jpg", "vu_01.jpg"])
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("passport_01.jpg", driver_evidence()[:1], "")
    dialog.receive("vu_01.jpg", [], "Vision 429")
    done = dialog.files_table.item(0, 1).background().color()
    error = dialog.files_table.item(1, 1).background().color()
    assert dialog.files_table.item(0, 1).background().style() != Qt.NoBrush
    assert done.green() > done.red()
    assert error.red() > error.green()
    assert dialog.file_status["vu_01.jpg"] == FILE_ERROR


def test_progress_bar_is_visible_only_while_running(dialog):
    """Прогресс-бар показывается на время распознавания и прячется после."""
    dialog.add_paths(["passport_01.jpg"])
    assert dialog.progress.isHidden()
    dialog._busy(True)
    dialog._refresh_progress(processed=0)
    assert not dialog.progress.isHidden()
    assert dialog.progress_label.text() == "Распознано 0 из 1 файлов…"
    dialog._busy(False)
    assert dialog.progress.isHidden()


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


def test_tree_has_no_confirm_or_action_columns(dialog):
    """Колонок «Подтвердить» и «Действие» в дереве нет."""
    from ui.document_import_dialog import TREE_HEADERS
    headers = [dialog.tree.headerItem().text(i) for i in range(dialog.tree.columnCount())]
    assert headers == list(TREE_HEADERS)
    assert len(headers) == 3
    assert "Подтвердить" not in headers
    assert "Действие" not in headers


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
    assert "двойным кликом" in hint


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


# ─────────────────────────────────────────────────────────────
# Перенос: без галочек, все распознанные поля
# ─────────────────────────────────────────────────────────────

def test_transfer_takes_every_recognized_field_without_checkboxes(dialog, window, monkeypatch):
    """Сигнал результата содержит ВСЕ распознанные поля — без галочек."""
    import ui.document_import_dialog as module
    captured = []
    monkeypatch.setattr(module, "apply_approved", lambda w, changes: captured.extend(changes))
    load(dialog, driver_evidence())
    dialog.apply()
    keys = {(section, key) for section, _target, key, _value in captured}
    assert ("driver", "full_name") in keys
    assert ("driver", "passport_number") in keys
    assert ("driver", "passport_series") in keys
    assert ("driver", "license_number") in keys
    assert ("driver", "license_categories") in keys
    assert ("trailer", "color") in keys
    assert ("carrier", "inn") in keys
    # Ничего не подтверждали руками — и всё равно перенеслось всё, что есть в форме.
    recognized = {(entity.section, value.field)
                  for entity in dialog.entities for value in entity.fields
                  if dialog.is_transferable(entity, value)}
    assert keys == recognized


def test_all_fields_go_to_form_on_apply(dialog, window):
    """Одна кнопка: форма получает все распознанные поля."""
    load(dialog, driver_evidence())
    dialog.apply()
    data = window.driver_tab.get_data()
    assert data["full_name"] == FIO
    assert data["passport_number"] == PASSPORT_NUMBER
    assert data["passport_series"] == PASSPORT_SERIES
    assert data["license_number"] == "349327"
    assert window.carrier_tab.get_data()["inn"] == INN


def test_apply_button_is_enabled_only_with_recognized_fields(dialog):
    """«Перенести в форму» активна, когда есть что переносить."""
    assert not dialog.apply_button.isEnabled()
    dialog.add_paths(["synthetic.png"])
    load(dialog, [])
    assert not dialog.apply_button.isEnabled()
    load(dialog, driver_evidence())
    assert dialog.apply_button.isEnabled()


def test_apply_button_disabled_when_every_field_removed(dialog, window):
    """Все строки убраны из переноса — переносить нечего."""
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    assert dialog.apply_button.isEnabled()
    dialog.set_entity_removed(driver.uid, True)
    assert not dialog.apply_button.isEnabled()
    before = window.driver_tab.get_data()
    dialog.apply()
    assert before == window.driver_tab.get_data()
    dialog.set_entity_removed(driver.uid, False)
    assert dialog.apply_button.isEnabled()


def test_done_message_after_transfer(dialog, window):
    """После переноса оператор видит, что всё ушло в форму, и может закрывать."""
    load(dialog, driver_evidence())
    dialog.apply()
    assert dialog.summary.text().startswith("Готово: перенесено полей —")
    assert "окно можно закрывать" in dialog.summary.text()


def test_summary_counts_found_and_missing(dialog):
    """Строка состояния: сколько найдено и чего в документах нет."""
    from ui.document_import_dialog import FILES_NOT_CHOSEN_TEXT
    assert dialog.summary.text() == FILES_NOT_CHOSEN_TEXT
    dialog.add_paths(["passport_01.jpg"])
    assert dialog.summary.text() == "Файлов выбрано: 1. Нажмите «Распознать»."
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    text = dialog.summary.text()
    assert "Распознано: 1 поле из" in text
    assert "Не найдено:" in text


def test_summary_reports_errors(dialog):
    """Ошибки файлов видны в строке состояния, перенос они не блокируют."""
    dialog.add_paths(["bad.png"])
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("bad.png", [], "Vision 429")
    dialog.finished()
    assert "Ошибки: 1 файл." in dialog.summary.text()
    assert "Подробности — в таблице." in dialog.summary.text()


# ─────────────────────────────────────────────────────────────
# Контекстное меню строки
# ─────────────────────────────────────────────────────────────

def test_tree_context_menu_has_original_and_text(dialog):
    """Правый клик по строке поля: оригинал, текст, копирование, удаление."""
    load(dialog, driver_evidence())
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "full_name")
    titles = menu_titles(dialog._build_tree_menu(item))
    assert "Открыть оригинал" in titles
    assert "Показать распознанный текст" in titles
    assert "Скопировать значение" in titles
    assert "Удалить строку" in titles
    # «Добавить пустые поля» уехало из главного экрана в это же меню.
    assert "Добавить пустые поля вручную" in titles


def test_tree_context_menu_on_source_row(dialog):
    """Правый клик по строке источника: оригинал и распознанный текст."""
    load(dialog, driver_evidence())
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    labels = [driver.child(i).text(COL_WHAT) for i in range(driver.childCount())]
    source = driver.child(next(i for i, text in enumerate(labels) if text.startswith("📄 passport")))
    titles = menu_titles(dialog._build_tree_menu(source))
    assert "Открыть оригинал" in titles
    assert "Показать распознанный текст" in titles
    assert "Удалить строку" not in titles


def test_files_context_menu_has_actions(dialog):
    """Правый клик по строке таблицы файлов."""
    dialog.add_paths(["passport_01.jpg"])
    titles = menu_titles(dialog._build_files_menu(0))
    assert "Открыть оригинал" in titles
    assert "Показать распознанный текст" in titles
    assert "Убрать из списка" in titles


def test_context_menu_copy_puts_value_into_clipboard(dialog):
    """«Скопировать значение» кладёт значение поля в буфер обмена."""
    load(dialog, driver_evidence())
    driver = entity_of(dialog, "driver")
    dialog.copy_field_value(driver.uid, "full_name")
    from PyQt5.QtWidgets import QApplication
    assert QApplication.clipboard().text() == FIO


def test_context_menu_removes_and_returns_row(dialog):
    """«Удалить строку» убирает поле из переноса, «Вернуть» — возвращает."""
    from ui.document_import_dialog import FIELD_REMOVED_MARK
    load(dialog, driver_evidence())
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "passport_number")
    assert item.text(COL_VALUE) == PASSPORT_NUMBER
    dialog.set_field_removed(driver.uid, "passport_number", True)
    assert dialog.is_removed(driver.uid, "passport_number")
    assert item.text(COL_VALUE) == FIELD_REMOVED_MARK
    assert "Вернуть строку в перенос" in menu_titles(dialog._build_tree_menu(item))
    dialog.set_field_removed(driver.uid, "passport_number", False)
    assert not dialog.is_removed(driver.uid, "passport_number")
    assert item.text(COL_VALUE) == PASSPORT_NUMBER


def test_original_path_is_found_by_source_name(dialog):
    """Подсказка «Открыть оригинал» находит файл по имени источника."""
    path = "C:\\документы\\passport_01.jpg"
    dialog.add_paths([path])
    assert dialog.path_for_source("passport_01.jpg") == path
    assert dialog.path_for_source("passport_01.jpg, стр. 2") == path
    assert dialog.path_for_source("нет_такого.jpg") == ""


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
    dialog.apply()
    assert [title for title, _ in asked] == ["Разные сущности"]
    message = asked[0][1]
    assert FIO in message
    assert "Петров Пётр Петрович" in message
    assert "Что делать:" in message
    assert "Удалить строки сущности" in message
    # Форма не тронута: два разных человека в одну запись не переносятся.
    assert window.driver_tab.get_data()["full_name"] in ("", None) or \
        window.driver_tab.get_data()["full_name"] != FIO


def test_same_entity_fields_are_not_reported_as_different(dialog, window, monkeypatch):
    """Паспорт и ВУ одного водителя — одна сущность, ошибки быть не должно."""
    from PyQt5.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: asked.append(args[1:3]))
    load(dialog, driver_evidence())
    dialog.apply()
    assert asked == []
    assert window.driver_tab.get_data()["full_name"] == FIO


# ─────────────────────────────────────────────────────────────
# Непрочитанные поля
# ─────────────────────────────────────────────────────────────

def test_unreadable_fields_are_visible_as_not_found(dialog):
    """Ненайденное поле видно серым «— не найдено —» и перенос не блокирует."""
    from ui.document_import_dialog import FIELD_NOT_FOUND_MARK
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = dialog.entity_item(entity_of(dialog, "driver").uid)
    labels = all_node_texts(driver)
    assert "📄 Не прочитано ни в одном документе" in labels
    assert "Кем выдан паспорт" in labels

    item = dialog.field_item(entity_of(dialog, "driver").uid, "passport_issuer")
    assert item.text(COL_VALUE) == FIELD_NOT_FOUND_MARK
    assert not item.isHidden()


def test_unreadable_field_value_is_gray_italic(dialog):
    from PyQt5.QtCore import Qt
    from ui.document_import_dialog import FIELD_NOT_FOUND_MARK
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "passport_number")
    assert item.text(COL_VALUE) == FIELD_NOT_FOUND_MARK
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


def test_double_click_opens_inline_editor(dialog):
    """Правка значения — двойной клик по строке поля."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QAbstractItemView
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    item = dialog.field_item(driver.uid, "passport_number")
    assert item.flags() & Qt.ItemIsEditable
    dialog.edit_field(driver.uid, "passport_number")
    assert dialog.tree.state() == QAbstractItemView.EditingState


def test_typed_value_replaces_unreadable_and_transfers(dialog, window):
    load(dialog, [Evidence("driver", {"full_name": FIO}, "passport_01.jpg", "Текст")])
    driver = entity_of(dialog, "driver")
    dialog.field_item(driver.uid, "passport_number").setText(COL_VALUE, PASSPORT_NUMBER)
    assert driver.field_value("passport_number").value == PASSPORT_NUMBER
    dialog.apply()
    assert window.driver_tab.get_data()["passport_number"] == PASSPORT_NUMBER


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
    # Подсказка не прячется: фильтра непрочитанных в окне больше нет.
    assert not item.child(children.index(notes[0])).isHidden()


# ─────────────────────────────────────────────────────────────
# Прогресс и сводка
# ─────────────────────────────────────────────────────────────

def test_progress_reports_files_and_entities(dialog, monkeypatch):
    dialog.paths = ["passport_01.jpg", "vu_01.jpg"]
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("passport_01.jpg", driver_evidence()[:2], "")
    assert dialog.progress_label.text() == "Распознано 1 из 2 файлов…"
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
    # Значения-варианты не правятся как поле.
    assert not item.child(0).flags() & Qt.ItemIsEditable


def test_unread_files_are_shown_separately(dialog):
    dialog.task = SimpleNamespace(cancel=Event())
    dialog.receive("synthetic.png, стр. 1", [], "")
    dialog.finished()
    assert dialog.entities == []
    assert any("НЕ ПРОЧИТАННЫЕ ФАЙЛЫ" in text for text in top_level_texts(dialog))


def test_manual_group_is_added_from_context_menu(dialog):
    """«Добавить пустые поля вручную» живёт в контекстном меню, не на экране."""
    from core.document_import_service import SCHEMA
    dialog.load_rows(compare_fields([]))
    assert dialog.entities == []
    dialog.add_manual_group("driver")
    assert [entity.section for entity in dialog.entities] == ["driver"]
    assert len(dialog.entities[0].fields) == len(SCHEMA["driver"])
    # Пустые поля переносить нечего: кнопка остаётся неактивной.
    assert not dialog.apply_button.isEnabled()


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
    dialog.apply()
    assert window.vehicles_tab.get_field(0, "color") == "красный"


def test_new_vehicle_becomes_its_own_row(dialog, window):
    from ui.document_import_dialog import COL_TARGET as COL_TARGET_INDEX
    load(dialog, [Evidence("vehicles", {"vin": VIN, "color": "красный"},
                           "polupricep.docx", "Текст")])
    entity = entity_of(dialog, "vehicles")
    combo = dialog.tree.itemWidget(dialog.entity_item(entity.uid), COL_TARGET_INDEX)
    assert combo.currentData() == ("vehicles", "new:0")
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
