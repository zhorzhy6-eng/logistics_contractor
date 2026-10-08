"""Понятный диалог импорта: дерево сущностей, подтверждение целиком, объяснения.

Оператор видит три уровня вместо плоского списка из 57 строк:

    ВОДИТЕЛЬ: Иванов Иван Иванович, 15.03.1985
      📄 passport_01.jpg
        Серия паспорта: 60 26            [✓] [Править]
        Номер паспорта: 123456           [✓] [Править]
      📄 Не прочитано ни в одном документе
        Кем выдан паспорт: [не прочитано] [ ] [Ввести вручную]
      [✓] [Подтвердить водителя целиком]

Ничего не переносится без галочки, в базу импорт не пишет. Группировку
считает `core/import_entities.py` — диалог только показывает результат.
"""
import logging
from collections import OrderedDict
from functools import partial
from pathlib import Path
from threading import Event

from PyQt5.QtCore import Qt, QObject, QRunnable, pyqtSignal, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QLabel, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from core.document_import_service import (DocumentImportService, FieldResult, LABELS,
    SCHEMA, TITLES, canonical_value, compare_fields, normalized, same_entity, valid_value)
from core.import_entities import (STATE_CONFLICT, STATE_INVALID, STATE_UNREADABLE,
    UNREADABLE_SOURCE_LABEL, detect_conflicts, entities_from_rows, entity_by_uid,
    progress_text, summary_text)

from ui.widgets.table_helpers import (
    MODE_FIXED, install_tooltip_on_table, setup_point_table,
)

logger = logging.getLogger(__name__)

# Колонки дерева проверки.
COL_WHAT, COL_VALUE, COL_TARGET, COL_CHECK, COL_ACTION = range(5)
TREE_HEADERS = ("Что нашли в документах", "Значение", "Куда попадёт в форме (сейчас там)",
                "Подтвердить", "Действие")
# Раскладка колонок — общий помощник таблиц (ширины, режимы, подсказки).
WIDTHS_KEY = "ui/document_import/columns"
TREE_COLUMN_WIDTHS = (430, 250, 250, 90, 140)
TREE_COLUMN_MINIMUMS = {COL_WHAT: 260, COL_VALUE: 150, COL_TARGET: 150}

SOURCE_PREFIX = "📄 "
NO_SOURCE_LABEL = "📄 " + UNREADABLE_SOURCE_LABEL
FIELD_ACTION_EDIT = "Править"
FIELD_ACTION_MANUAL = "Ввести вручную"
UNREAD_FILES_HEADING = "📄 НЕ ПРОЧИТАННЫЕ ФАЙЛЫ"
FORM_EMPTY_MARK = "— пусто —"
FORM_DIFFERS_MARK = "≠ "

INSTRUCTION = (
    "Проверка документов.\n"
    "Слева — что нашли в документах. Справа — куда это попадёт в форме.\n"
    "Галочка = «да, верно, перенести». Кнопка «Подтвердить целиком» подтверждает "
    "все поля одной сущности сразу.\n"
    "Добавьте файлы или перетащите их в окно. Ничего не переносится автоматически."
)


def _files_word(count: int) -> str:
    """Склонение слова «файл» для сводки повторяющихся ошибок."""
    if count % 10 == 1 and count % 100 != 11:
        return "файл"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "файла"
    return "файлов"


class ImportSignals(QObject):
    result = pyqtSignal(str, object, str)
    text = pyqtSignal(str, object)
    done = pyqtSignal()


class ImportTask(QRunnable):
    def __init__(self, service, paths):
        super().__init__()
        self.service, self.paths = service, paths
        self.cancel = Event()
        self.signals = ImportSignals()

    def run(self):
        try:
            self.service.process(self.paths, self.cancel, self.signals.result.emit, self.signals.text.emit)
        except Exception as exc:
            logger.error("Импорт документов: внутренняя ошибка задачи (%s)", type(exc).__name__)
            self.signals.result.emit("Импорт", [], "Обработка прервана внутренней ошибкой; полученные результаты сохранены.")
        finally:
            self.signals.done.emit()


def form_snapshot(window):
    data = {s: getattr(window, s + "_tab").get_data()
            for s in ("driver", "carrier", "customer", "contract")}
    data["tractor"] = window.trailer_tab.get_tractor_data()
    data["trailer"] = window.trailer_tab.get_trailer_data()
    tab = window.vehicles_tab
    data["vehicles"] = []
    # Include partially entered and empty table rows; get_data() intentionally filters them.
    # Поля читаются по КЛЮЧАМ (get_field), а не по номерам колонок: состав
    # колонок настраивается, и «Госномер» с «Годом» могут быть скрыты —
    # значения в них при этом сохраняются (ШАГ FIX-6, часть B3).
    for row in range(tab.table.rowCount()):
        data["vehicles"].append({
            "vin": tab.get_field(row, "vin"),
            "brand_model": tab.get_field(row, "brand_model"),
            "plate_number": tab.get_field(row, "plate_number"),
            "year": tab.get_field(row, "year"),
            "color": tab.get_field(row, "color"),
            "vehicle_type": tab.get_field(row, "vehicle_type"),
        })
    return data


def apply_approved(window, changes):
    """Each change is (section, physical vehicle row or None, field, confirmed value)."""
    sections, vehicles = {}, {}
    for section, target, key, value in changes:
        if key not in SCHEMA[section] or not valid_value(key, value):
            raise ValueError("Некорректное подтверждённое поле")
        if section == "vehicles":
            vehicles.setdefault(target, {})[key] = value
        else:
            sections.setdefault(section, {})[key] = value
    for section in ("driver", "carrier", "customer", "contract"):
        if sections.get(section):
            fields = dict(sections[section])
            if section == "carrier" and "carrier_type" not in fields:
                # Existing fill_data otherwise infers a type from the name.
                fields["carrier_type"] = window.carrier_tab.carrier_type.currentText()
            getattr(window, section + "_tab").fill_data(fields)
    if sections.get("tractor") or sections.get("trailer"):
        for section in ("tractor", "trailer"):
            if "year" in sections.get(section, {}):
                getattr(window.trailer_tab, section + "_year").setRange(1886, 2100)
        window.trailer_tab.fill_data(sections.get("tractor", {}), sections.get("trailer", {}))
    tab = window.vehicles_tab
    for target, fields in vehicles.items():
        if isinstance(target, str):  # new group, separate from every existing vehicle
            tab.fill_data([fields], append=True, imported=True)
            continue
        for key, value in fields.items():
            # Значение ставится по КЛЮЧУ поля: скрытая колонка тоже
            # обновляется, поэтому подтверждённое в импорте не теряется.
            tab.set_field(target, key, value)


class DocumentImportDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Проверка документов")
        self.resize(1350, 860)
        self.setAcceptDrops(True)
        self.task = None
        self.evidence, self.rows, self.paths = [], [], []
        self.entities = []
        self.unread_files = []
        # Состояние интерфейса, которое должно переживать перерисовку дерева:
        # подтверждения — по устойчивому ключу сущности, а не по номеру строки.
        self.confirmed = {}          # (uid сущности, поле) -> bool
        self.edited = {}             # (uid сущности, поле) -> исходное значение
        self.targets = {}            # uid сущности -> (раздел, цель в форме)
        self.ocr_texts = {}
        self.unread_sources = set()
        self.reported_sources = set()
        # Текст ошибки -> источники, в которых она повторилась (для сводки).
        self.error_sources = {}
        self._items = {}             # uid сущности -> узел дерева
        self._field_items = {}       # (uid сущности, поле) -> узел дерева
        self._unreadable_items = []
        self._rendering = False
        self._closing = False
        self.snapshot = form_snapshot(window)
        self._build_ui()

    # ─────────────────────────────────────────────────────────
    # Сборка окна
    # ─────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        instruction = QLabel(INSTRUCTION)
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        self.files = QListWidget()
        self.files.setMaximumHeight(115)
        self.files.setAcceptDrops(False)
        layout.addWidget(self.files)

        bar = QHBoxLayout()
        self.add_button = QPushButton("Добавить файлы")
        self.add_button.clicked.connect(self.choose_files)
        bar.addWidget(self.add_button)
        open_button = QPushButton("Открыть выбранный оригинал")
        open_button.clicked.connect(self.open_original)
        bar.addWidget(open_button)
        text_button = QPushButton("Показать распознанный текст")
        text_button.clicked.connect(self.show_ocr_text)
        bar.addWidget(text_button)
        self.start_button = QPushButton("Распознать")
        self.start_button.clicked.connect(self.start)
        bar.addWidget(self.start_button)
        self.cancel_button = QPushButton("Отменить обработку")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        bar.addWidget(self.cancel_button)
        layout.addLayout(bar)

        cloud = self.window.settings_service.get_bool("document_cloud_enabled", False)
        if cloud and self.window.gigachat is not None:
            cloud_state = ("в GigaChat уходят изображения и сканы (Vision), а также текст, "
                           "в котором локальные правила не нашли реквизитов")
        elif cloud:
            cloud_state = "GigaChat Vision включён, но ключ недоступен — будет локальный OCR (Tesseract)"
        else:
            cloud_state = "GigaChat Vision выключен — используется локальный OCR (Tesseract)"
        self.status = QLabel("Облачная обработка: " + cloud_state)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # ── прогресс: «Обработано X из Y файлов, найдено N сущностей» ──
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(14)
        layout.addWidget(self.progress)
        self.progress_label = QLabel("Файлы ещё не обработаны.")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        # ── шапка дерева: что скрыто и что подтверждено ──
        header = QHBoxLayout()
        self.hide_unreadable_button = QPushButton("Скрыть непрочитанные")
        self.hide_unreadable_button.setCheckable(True)
        self.hide_unreadable_button.setToolTip(
            "Убирает из дерева поля, которых нет в документах. Данные не меняются.")
        self.hide_unreadable_button.toggled.connect(self._on_hide_unreadable)
        header.addWidget(self.hide_unreadable_button)
        self.uncheck_button = QPushButton("Снять галочки у выбранных")
        self.uncheck_button.setToolTip("Снимает все подтверждения — форма не меняется.")
        self.uncheck_button.clicked.connect(self.uncheck_all)
        header.addWidget(self.uncheck_button)
        expand_button = QPushButton("Развернуть всё")
        expand_button.clicked.connect(self.tree_expand_all)
        header.addWidget(expand_button)
        collapse_button = QPushButton("Свернуть всё")
        collapse_button.clicked.connect(self.tree_collapse_all)
        header.addWidget(collapse_button)
        header.addStretch(1)
        layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(TREE_HEADERS))
        self.tree.setHeaderLabels(list(TREE_HEADERS))
        # Последняя колонка («Действие») не растягивается: иначе она забирала
        # бы место у остальных, а строка уходила бы в горизонтальную прокрутку.
        self.tree.header().setStretchLastSection(False)
        # Ширины и минимумы — общий помощник таблиц (ШАГ FIX-6, часть E):
        # колонки Interactive, раскладка запоминается между запусками.
        setup_point_table(
            self.tree,
            [(column, MODE_FIXED, width)
             for column, width in enumerate(TREE_COLUMN_WIDTHS)],
            storage_key=WIDTHS_KEY,
            minimums=TREE_COLUMN_MINIMUMS,
        )
        self.tree.setWordWrap(True)
        install_tooltip_on_table(self.tree)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        manual = QHBoxLayout()
        manual.addWidget(QLabel("Если поля не прочитаны, выберите вкладку для ручной проверки:"))
        self.manual_section = QComboBox()
        for section, title in TITLES.items():
            self.manual_section.addItem(title, section)
        manual.addWidget(self.manual_section)
        self.manual_button = QPushButton("Добавить пустые поля")
        self.manual_button.clicked.connect(self.add_manual_group)
        manual.addWidget(self.manual_button)
        manual.addStretch(1)
        layout.addLayout(manual)

        self.errors = QLabel("")
        self.errors.setWordWrap(True)
        self.errors.setTextFormat(Qt.PlainText)
        layout.addWidget(self.errors)

        footer = QHBoxLayout()
        self.apply_button = QPushButton("Перенести подтверждённые поля")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        footer.addWidget(self.apply_button, 1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.reject)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    # ─────────────────────────────────────────────────────────
    # Файлы и обработка (без изменений по существу)
    # ─────────────────────────────────────────────────────────

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Документы", "", "Документы (*.png *.jpg *.jpeg *.pdf *.docx *.doc)")
        self.add_paths(paths)

    def add_paths(self, paths):
        if self.task:
            logger.info("Добавление документов пропущено: обработка уже идёт")
            return
        added = 0
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
                self.files.addItem(Path(path).name)
                added += 1
        logger.info("Документы выбраны: новых=%s, всего=%s", added, len(self.paths))

    def dragEnterEvent(self, event):
        if not self.task and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def open_original(self):
        index = self.files.currentRow()
        if 0 <= index < len(self.paths):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.paths[index]))

    def remember_text(self, source, variants):
        # UI memory only. Personal document text is not persisted or logged.
        self.ocr_texts[source] = list(variants)

    def show_ocr_text(self):
        index = self.files.currentRow()
        if not 0 <= index < len(self.paths):
            QMessageBox.information(self, "Распознанный текст", "Сначала выберите файл в списке.")
            return
        filename = Path(self.paths[index]).name
        entries = [(source, variants) for source, variants in self.ocr_texts.items()
                   if source == filename or source.startswith(filename + ", стр. ")]
        if not entries:
            QMessageBox.information(self, "Распознанный текст", "Для этого файла текст пока недоступен.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Распознанный текст — сверяйте с оригиналом")
        dialog.resize(850, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Это варианты машинного чтения. Ошибочные буквы и цифры возможны; исправляйте поля в дереве импорта."))
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText("\n\n".join(source + f" · вариант {i+1}\n" + text
            for source, variants in entries for i, text in enumerate(variants)))
        layout.addWidget(preview)
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()

    def start(self):
        if self.task or not self.paths:
            logger.info("Запуск импорта пропущен: задача уже идёт=%s, файлов=%s", bool(self.task), len(self.paths))
            return
        logger.info("Импорт документов: запуск, файлов=%s", len(self.paths))
        self.snapshot = form_snapshot(self.window)
        self.evidence = []
        self.ocr_texts = {}
        self.unread_sources = set()
        self.reported_sources = set()
        self.error_sources = {}
        self.unread_files = []
        self.confirmed = {}
        self.edited = {}
        self.targets = {}
        self.errors.setText("")
        self.load_rows([])
        settings = self.window.settings_service.as_dict()
        service = DocumentImportService(settings, self.window.gigachat)
        self.task = ImportTask(service, list(self.paths))
        self.task.signals.result.connect(self.receive)
        self.task.signals.text.connect(self.remember_text)
        self.task.signals.done.connect(self.finished)
        self.add_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.manual_button.setEnabled(False)
        self.status.setText("Обработка… Отмена сработает после текущего запроса.")
        self._refresh_progress(processed=0)
        self.window.thread_pool.start(self.task)

    def receive(self, source, evidence, error):
        logger.info("Импорт документов: получен результат, групп=%s, ошибка=%s", len(evidence), bool(error))
        self.reported_sources.add(source)
        self.status.setText("Обработан: " + source + ". Найдено полей-кандидатов: " +
                            str(sum(sum(not k.startswith('_') for k in e.values) for e in evidence)) +
                            ". Ожидаем завершения остальных документов…")
        self.evidence.extend(evidence)
        if not evidence:
            self.unread_sources.add(source)
        if error:
            # Полный список ошибок по файлам остаётся в self.errors,
            # здесь только копятся источники для сводки в статусе.
            sources = self.error_sources.setdefault(error, [])
            if source not in sources:
                sources.append(source)
            self.errors.setText(self.errors.text() + source + ": " + error + "\n")
        elif not evidence:
            self.errors.setText(self.errors.text() + source + ": поля не прочитаны; проверьте оригинал.\n")
        self._refresh_progress()

    def cancel(self):
        if self.task:
            logger.info("Импорт документов: запрошена отмена")
            self.task.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("Отмена… Ожидаем завершения текущей операции и удаления облачных файлов.")

    def reject(self):
        if self.task:
            self._closing = True
            self.cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.task:
            event.ignore()
            self.reject()
        else:
            event.accept()

    def _reported_file_count(self):
        """Сколько ФАЙЛОВ (а не страниц) уже отчитались о результате."""
        names = [Path(path).name for path in self.paths]
        done = set()
        for label in self.reported_sources:
            for name in names:
                if label == name or label.startswith(name + ", стр. "):
                    done.add(name)
                    break
        return len(done)

    def _refresh_progress(self, processed=None, entities=None, prefix=""):
        total = len(self.paths)
        done = self._reported_file_count() if processed is None else int(processed)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, max(1, total)))
        self.progress_label.setText(progress_text(done, total, entities, prefix))

    def finished(self):
        cancelled = self.task.cancel.is_set()
        logger.info("Импорт документов: задача завершена, отменено=%s, групп=%s", cancelled, len(self.evidence))
        self.task = None
        self.add_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.manual_button.setEnabled(True)
        readable = {e.source for e in self.evidence}
        self.unread_files = sorted(self.unread_sources - readable)
        self.rows = compare_fields(self.evidence)
        logger.info("Импорт документов: строк для проверки=%s", len(self.rows))
        self.render()
        prefix = "Обработка отменена." if cancelled else "Обработка завершена."
        self._refresh_progress(processed=self._reported_file_count() if cancelled else len(self.paths),
                               entities=self.entities, prefix=prefix)
        summary = self.repeated_errors_summary()
        self.status.setText(prefix + " Полученные поля доступны для проверки. "
                            "Непрочитанные значения можно ввести вручную." +
                            (" " + summary if summary else ""))
        self.apply_button.setEnabled(bool(self.entities))
        if self._closing:
            if self.errors.text():
                QMessageBox.warning(self, "Сообщения импорта", self.errors.text())
            super().reject()

    def repeated_errors_summary(self):
        """
        Сводка одинаковых ошибок для верхней строки статуса.

        Восемь файлов, упавших по одной причине, дают восемь одинаковых строк
        в self.errors — это нужно для отчёта, но загромождает экран. Здесь
        такая ошибка показывается один раз с числом остальных файлов;
        содержимое self.errors не меняется.
        """
        repeated = [(message, len(sources))
                    for message, sources in self.error_sources.items() if len(sources) > 1]
        if not repeated:
            return ""
        return "Одинаковая ошибка: " + " ".join(
            " ".join(str(message).split()) +
            f" (и ещё {count - 1} {_files_word(count - 1)} с той же ошибкой)"
            for message, count in repeated)

    # ─────────────────────────────────────────────────────────
    # Дерево сущностей
    # ─────────────────────────────────────────────────────────

    def load_rows(self, rows):
        """Загружает строки проверки и перерисовывает дерево (тесты и `finished`)."""
        self.rows = list(rows)
        self.render()

    def render(self):
        """Пересобирает дерево: сущность → источник → поля."""
        self.entities = entities_from_rows(self.rows)
        self._items, self._field_items, self._unreadable_items = {}, {}, []
        self._rendering = True
        try:
            self.tree.clear()
            for entity in self.entities:
                self._add_entity_item(entity)
            self._add_unread_files_item()
            self.tree.expandAll()
        finally:
            self._rendering = False
        self._apply_unreadable_filter()
        self._refresh_progress(entities=self.entities)
        self.apply_button.setEnabled(bool(self.entities))
        logger.info("Импорт документов: сущностей=%s, спорных полей=%s",
                    len(self.entities), len(detect_conflicts(self.entities)))

    def _add_entity_item(self, entity):
        top = QTreeWidgetItem(self.tree, [entity.display_name, "", "", "", ""])
        top.setData(COL_WHAT, Qt.UserRole, ("entity", entity.uid))
        top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
        font = top.font(COL_WHAT)
        font.setBold(True)
        top.setFont(COL_WHAT, font)
        top.setToolTip(COL_WHAT, self._entity_hint(entity))
        target = self._target_widget(entity)
        if target is not None:
            self.tree.setItemWidget(top, COL_TARGET, target)
        else:
            top.setText(COL_TARGET, TITLES.get(entity.section, ""))
        button = QPushButton(entity.button_text)
        button.setToolTip("Подтверждает все прочитанные поля этой сущности одной кнопкой.")
        button.clicked.connect(partial(self.set_entity_confirmed, entity.uid, True))
        self.tree.setItemWidget(top, COL_ACTION, button)
        self._items[entity.uid] = top

        for source, values in entity.fields_by_source().items():
            node = QTreeWidgetItem(top, [SOURCE_PREFIX + source if source else NO_SOURCE_LABEL,
                                         "", "", "", ""])
            if not source:
                node.setToolTip(COL_WHAT, "В документах этих полей нет: введите значение "
                                          "вручную, если оно нужно в форме.")
                self._unreadable_items.append(node)
            for value in values:
                self._add_field_item(node, entity, value)
        self._refresh_entity_item(entity.uid)

    def _add_field_item(self, node, entity, value):
        item = QTreeWidgetItem(node, [value.label, "", "", "", ""])
        item.setData(COL_WHAT, Qt.UserRole, ("field", entity.uid, value.field))
        item.setFlags((item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
                      & ~Qt.ItemIsDropEnabled)
        self._field_items[(entity.uid, value.field)] = item
        if value.unreadable:
            self._unreadable_items.append(item)
        button = QPushButton(FIELD_ACTION_EDIT)
        button.setToolTip("Изменить значение: правка идёт только в форму, оригинал не меняется.")
        button.clicked.connect(partial(self.edit_field, entity.uid, value.field))
        self.tree.setItemWidget(item, COL_ACTION, button)
        self._refresh_field_item(entity, value)

    def _add_unread_files_item(self):
        if not self.unread_files:
            return
        root = QTreeWidgetItem(self.tree, [UNREAD_FILES_HEADING,
                                           f"файлов: {len(self.unread_files)}", "", "", ""])
        root.setData(COL_WHAT, Qt.UserRole, ("unread_files", ""))
        font = root.font(COL_WHAT)
        font.setBold(True)
        root.setFont(COL_WHAT, font)
        root.setToolTip(COL_WHAT, "Эти файлы прочитаны, но поддерживаемые поля в них "
                                  "не найдены. Проверьте оригиналы.")
        for name in self.unread_files:
            child = QTreeWidgetItem(root, [SOURCE_PREFIX + name, "", "", "", ""])
            child.setData(COL_WHAT, Qt.UserRole, ("unread_file", name))
            child.setToolTip(COL_WHAT, "Поля не прочитаны; проверьте оригинал.")

    def _target_widget(self, entity):
        """Куда попадёт сущность: выбор есть только там, где он осмыслен."""
        if entity.section in {"carrier", "customer"}:
            combo = QComboBox()
            for section in ("carrier", "customer"):
                combo.addItem(TITLES[section], (section, None))
            combo.setCurrentIndex(0 if entity.section == "carrier" else 1)
            combo.setToolTip("Одна и та же организация может быть и перевозчиком, и заказчиком.")
        elif entity.section == "vehicles":
            combo = QComboBox()
            combo.addItem("Новое авто (из этого документа)",
                          ("vehicles", "new:" + str(entity.group)))
            for index, vehicle in enumerate(self.snapshot["vehicles"]):
                combo.addItem("Авто " + str(index + 1) + ": " +
                              (vehicle["vin"] or vehicle["plate_number"] or "без номера"),
                              ("vehicles", index))
            matches = [j for j, v in enumerate(self.snapshot["vehicles"])
                       if same_entity("vehicles", entity.values(), v)]
            if len(matches) == 1:
                combo.setCurrentIndex(matches[0] + 1)
            combo.setToolTip("Строка формы, в которую попадут поля этого автомобиля.")
        else:
            return None
        saved = self.targets.get(entity.uid)
        if saved is not None:
            self._select_target(combo, saved)
            self.targets[entity.uid] = combo.currentData()
        else:
            self.targets[entity.uid] = combo.currentData()
        combo.currentIndexChanged.connect(partial(self._on_target_changed, entity.uid, combo))
        return combo

    @staticmethod
    def _select_target(combo, target):
        """Qt's findData can return -1 for equal tuple QVariant values after a rerender."""
        for index in range(combo.count()):
            if combo.itemData(index) == target:
                combo.setCurrentIndex(index)
                return True
        return False

    def _on_target_changed(self, uid, combo, _index=None):
        """Смена цели — это другой объект формы: подтверждения группы снимаются."""
        self.targets[uid] = combo.currentData()
        self.set_entity_confirmed(uid, False)

    def _apply_unreadable_filter(self):
        for item in self._unreadable_items:
            item.setHidden(self.hide_unreadable_button.isChecked())

    def _on_hide_unreadable(self, checked):
        logger.info("Импорт документов: непрочитанные поля скрыты=%s", bool(checked))
        self._apply_unreadable_filter()

    def tree_expand_all(self):
        self.tree.expandAll()

    def tree_collapse_all(self):
        self.tree.collapseAll()

    # ─────────────────────────────────────────────────────────
    # Подтверждения и правка значений
    # ─────────────────────────────────────────────────────────

    def destination(self, entity):
        """(раздел, цель) — конкретная запись формы, куда пойдут поля сущности."""
        choice = self.targets.get(entity.uid)
        if choice:
            return choice
        if entity.section == "vehicles":
            return ("vehicles", "new:" + str(entity.group))
        return (entity.section, None)

    def entity_item(self, uid):
        """Узел дерева для сущности (тесты и программная правка)."""
        return self._items.get(uid)

    def field_item(self, uid, field):
        """Узел дерева для поля (тесты и программная правка)."""
        return self._field_items.get((uid, field))

    def is_confirmed(self, uid, field):
        return bool(self.confirmed.get((uid, field)))

    def set_field_confirmed(self, uid, field, state):
        """Подтверждение одного поля (галочка оператора)."""
        entity = entity_by_uid(self.entities, uid)
        value = entity.field_value(field) if entity else None
        if value is None:
            return
        if state and not value.confirmable:
            state = False
        self.confirmed[(uid, field)] = bool(state)
        self._refresh_field_item(entity, value)
        self._refresh_entity_item(uid)
        self._refresh_progress(entities=self.entities)

    def set_entity_confirmed(self, uid, state, _clicked=False):
        """«Подтвердить водителя целиком»: все прочитанные поля одной сущности."""
        entity = entity_by_uid(self.entities, uid)
        if entity is None:
            return
        confirmed = 0
        for value in entity.fields:
            if state and not value.confirmable:
                continue
            self.confirmed[(uid, value.field)] = bool(state)
            if state:
                confirmed += 1
            self._refresh_field_item(entity, value)
        self._refresh_entity_item(uid)
        self._refresh_progress(entities=self.entities)
        logger.info("Импорт документов: подтверждение целиком, раздел=%s, полей=%s",
                    entity.section, confirmed if state else 0)

    def uncheck_all(self):
        """Снимает все галочки: ничего не переносится, форма не меняется."""
        for entity in self.entities:
            self.set_entity_confirmed(entity.uid, False)
        logger.info("Импорт документов: галочки сняты")

    def edit_field(self, uid, field, _clicked=False):
        """Кнопка «Править» / «Ввести вручную» — правка значения в дереве."""
        item = self._field_items.get((uid, field))
        if item is None:
            return
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self.tree.editItem(item, COL_VALUE)

    def _on_item_changed(self, item, column):
        if self._rendering:
            return
        payload = item.data(COL_WHAT, Qt.UserRole)
        if not payload:
            return
        kind = payload[0]
        if kind == "field":
            uid, field = payload[1], payload[2]
            entity = entity_by_uid(self.entities, uid)
            value = entity.field_value(field) if entity else None
            if value is None:
                return
            if column == COL_VALUE:
                self._apply_manual_value(entity, value, item.text(COL_VALUE))
            elif column == COL_CHECK:
                self.set_field_confirmed(uid, field, item.checkState(COL_CHECK) == Qt.Checked)
            else:
                self._refresh_field_item(entity, value)
        elif kind == "entity" and column == COL_CHECK:
            self.set_entity_confirmed(payload[1], item.checkState(COL_CHECK) == Qt.Checked)

    def _apply_manual_value(self, entity, value, text):
        """Правка поля: значение уходит в форму, но не в документ-источник."""
        text = str(text or "").strip()
        self.edited.setdefault((entity.uid, value.field), value.value)
        value.value = text
        if not text:
            value.state, value.detail = STATE_UNREADABLE, "не прочитано"
        elif valid_value(value.field, text):
            value.state, value.detail = "ok", "введено вручную"
        else:
            value.state, value.detail = STATE_INVALID, "формат не подходит"
        self.confirmed[(entity.uid, value.field)] = False
        self._write_back_row(entity, value)
        self._refresh_field_item(entity, value)
        self._refresh_entity_item(entity.uid)
        self._refresh_progress(entities=self.entities)
        logger.info("Импорт документов: правка поля, раздел=%s, поле=%s, длина=%s",
                    entity.section, value.field, len(text))

    def _write_back_row(self, entity, value):
        """Правка должна пережить перерисовку дерева: пишем её и в строку."""
        for row in self.rows:
            if row.section == entity.section and row.group == entity.group and row.key == value.field:
                row.value = value.value
                row.state = "введено вручную" if value.value else "не прочитано"
                return

    def _refresh_field_item(self, entity, value):
        item = self._field_items.get((entity.uid, value.field))
        if item is None:
            return
        confirmable = value.confirmable
        self._rendering = True
        try:
            item.setText(COL_VALUE, self._value_text(value))
            item.setText(COL_TARGET, self._current_text(entity, value))
            item.setToolTip(COL_VALUE, self._value_hint(value))
            item.setToolTip(COL_TARGET, self._target_hint(entity, value))
            if confirmable:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                self.confirmed[(entity.uid, value.field)] = False
            checked = confirmable and self.is_confirmed(entity.uid, value.field)
            item.setCheckState(COL_CHECK, Qt.Checked if checked else Qt.Unchecked)
            button = self.tree.itemWidget(item, COL_ACTION)
            if isinstance(button, QPushButton):
                button.setText(FIELD_ACTION_EDIT if value.value else FIELD_ACTION_MANUAL)
        finally:
            self._rendering = False

    def _refresh_entity_item(self, uid):
        entity = entity_by_uid(self.entities, uid)
        item = self._items.get(uid)
        if entity is None or item is None:
            return
        confirmable = entity.confirmable_fields()
        checked = [value for value in confirmable if self.is_confirmed(uid, value.field)]
        self._rendering = True
        try:
            item.setText(COL_VALUE, self._entity_summary(entity, len(checked)))
            all_checked = bool(confirmable) and len(checked) == len(confirmable)
            item.setCheckState(COL_CHECK, Qt.Checked if all_checked else Qt.Unchecked)
        finally:
            self._rendering = False

    def _refresh_all_fields(self):
        for entity in self.entities:
            for value in entity.fields:
                self._refresh_field_item(entity, value)
            self._refresh_entity_item(entity.uid)

    def add_manual_group(self):
        """Пустая сущность для ручного ввода: подтверждения остальных не теряются."""
        section = self.manual_section.currentData()
        logger.info("Импорт документов: добавление пустой группы, раздел=%s", section)
        group = max((row.group for row in self.rows), default=-1) + 1
        index = self.files.currentRow()
        source = Path(self.paths[index]).name if 0 <= index < len(self.paths) else "Ручная проверка оригинала"
        self.rows.extend(FieldResult(group, section, key, "", source, "не прочитано")
                         for key in SCHEMA[section])
        self.render()
        self.apply_button.setEnabled(True)

    # ─────────────────────────────────────────────────────────
    # Тексты ячеек и подсказки
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _value_text(value):
        if value.state == STATE_CONFLICT:
            return "[расхождение в источниках]"
        if value.state == STATE_UNREADABLE:
            return "[не прочитано]"
        if value.state == STATE_INVALID:
            return value.value + "  (формат не подходит)"
        return value.value

    @staticmethod
    def _value_hint(value):
        lines = [f"{value.label}: {value.detail or value.state}"]
        for source, variant in value.variants:
            lines.append(f"{source}: {variant or '—'}")
        if value.note:
            lines.append("Примечание: " + value.note)
        lines.append("Нажмите «Править», чтобы изменить значение вручную.")
        return "\n".join(lines)

    @staticmethod
    def _entity_hint(entity):
        return (f"Найдено в файлах: {entity.sources_text()}.\n"
                f"Если данные верные — нажмите «{entity.button_text}».\n"
                "Если что-то не так — правьте поле и подтверждайте отдельно.")

    def _entity_summary(self, entity, confirmed):
        text = entity.counts_text()
        if confirmed:
            text += f" · подтверждено: {confirmed}"
        return text

    def _target_hint(self, entity, value):
        destination = self.destination(entity)
        current = self._current_value(self.snapshot, destination, value.field)
        section = destination[0] if destination else ""
        lines = [f"В форме («{TITLES.get(section, section)}») сейчас: {current or 'пусто'}."]
        if self.edited.get((entity.uid, value.field)) not in (None, value.value):
            lines.append("Было в документе: " + self.edited[(entity.uid, value.field)])
        return "\n".join(lines)

    def _current_text(self, entity, value):
        current = self._current_value(self.snapshot, self.destination(entity), value.field)
        if not current:
            return FORM_EMPTY_MARK
        if value.value and normalized(current) != normalized(value.value):
            return FORM_DIFFERS_MARK + current
        return current

    @staticmethod
    def _record(snapshot, destination):
        if not destination:
            return {}
        section, target = destination
        if not section:
            return {}
        data = snapshot.get(section) or {}
        if section == "vehicles":
            if isinstance(target, int) and 0 <= target < len(data):
                return data[target]
            return {}
        return data if isinstance(data, dict) else {}

    def _current_value(self, snapshot, destination, key):
        return str(self._record(snapshot, destination).get(key, "") or "")

    # ─────────────────────────────────────────────────────────
    # Перенос подтверждённого
    # ─────────────────────────────────────────────────────────

    def apply(self):
        logger.info("Импорт документов: проверка подтверждённых полей")
        confirmed = [(entity, value) for entity in self.entities for value in entity.fields
                     if self.is_confirmed(entity.uid, value.field)]
        if not confirmed:
            logger.info("Импорт документов: перенос пропущен, нет подтверждённых полей")
            return
        latest = form_snapshot(self.window)
        destinations, candidates = OrderedDict(), []
        for entity, value in confirmed:
            destination = self.destination(entity)
            if not destination:
                QMessageBox.warning(self, "Выберите цель",
                                    "Выберите целевую запись для подтверждённого поля.")
                return
            section, target = destination
            if section == "vehicles" and isinstance(target, int) and target >= len(latest["vehicles"]):
                logger.warning("Импорт документов: перенос отклонён, целевая запись удалена")
                QMessageBox.warning(self, "Запись удалена",
                                    "Целевой автомобиль удалён из формы. Выберите новую запись.")
                return
            text = str(value.value).strip()
            if value.field not in SCHEMA[section] or not valid_value(value.field, text):
                logger.warning("Импорт документов: перенос отклонён, неверное поле=%s, раздел=%s",
                               value.field, section)
                QMessageBox.warning(self, "Проверьте значение",
                                    "Пустое, неоднозначное или неверное значение: " +
                                    LABELS.get(value.field, value.field))
                return
            destinations.setdefault(destination, OrderedDict()).setdefault(entity.uid, entity)
            candidates.append((destination, entity, value))

        # 1. Поля из разных сущностей с разными опознавательными данными вместе
        #    подтверждать нельзя: в форме это разные объекты.
        for destination, members in destinations.items():
            members = list(members.values())
            if len(members) > 1 and self._mixed_entities(members):
                self._report_mixed_entities(members, destination)
                return

        # 2. Форма изменилась с момента открытия диалога — подтверждать заново.
        for destination in destinations:
            if self._record(latest, destination) != self._record(self.snapshot, destination):
                logger.warning("Импорт документов: перенос отклонён, форма изменилась")
                self.snapshot = latest
                self.uncheck_all()
                self._refresh_all_fields()
                QMessageBox.warning(self, "Форма изменилась",
                                    "Текущие значения обновлены. Подтвердите поля заново.")
                return

        # Повторная галочка на том же поле — не ошибка: одинаковые значения
        # переносятся один раз, а разные означают конфликт источников, и такое
        # поле не переносится, пока пользователь не выберет одну строку.
        entries, order = OrderedDict(), []
        for destination, entity, value in candidates:
            map_key = destination + (value.field,)
            if map_key not in entries:
                entries[map_key] = []
                order.append(map_key)
            entries[map_key].append((canonical_value(value.field, value.value), entity, value))

        changes, conflicts = [], []
        change_entities, change_pairs = {}, {}
        for map_key in order:
            section, target, key = map_key
            values = entries[map_key]
            if len({normalized(canonical) for canonical, _, _ in values}) > 1:
                conflicts.extend(value for _, _, value in values)
                logger.warning("Импорт документов: конфликт значений, поле=%s, раздел=%s",
                               key, section)
                continue
            changes.append((section, target, key, values[0][0]))
            change_entities.setdefault((section, target), OrderedDict())[values[0][1].uid] = \
                values[0][1]
            change_pairs.setdefault((section, target), set()).add(
                (key, normalized(values[0][0])))
        if not changes and not conflicts:
            logger.info("Импорт документов: перенос пропущен, нет подтверждённых полей")
            return

        for destination, members in change_entities.items():
            # Спрашиваем только о реальном смешивании: разные сущности принесли
            # разные поля. Повтор одной и той же пары «поле=значение» — не повод.
            if len(members) > 1 and len(change_pairs[destination]) > 1:
                named = "\n".join("• " + entity.display_name for entity in members.values())
                answer = QMessageBox.question(self, "Подтвердите связь документов",
                    f"Документы не удалось однозначно связать автоматически:\n{named}\n\n"
                    f"Подтверждаете, что выбранные поля относятся к одной записи "
                    f"«{TITLES[destination[0]]}»?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
        if changes:
            apply_approved(self.window, changes)
            logger.info("Импорт документов: перенесено полей=%s", len(changes))
        if conflicts:
            self._report_conflicts(conflicts, changes)
            return
        self._report_done(len(changes))

    @staticmethod
    def _mixed_entities(members):
        """Есть ли среди сущностей одной записи противоречие в опознании."""
        for index, first in enumerate(members):
            for second in members[index + 1:]:
                common = set(first.identity) & set(second.identity)
                if any(first.identity[key] != second.identity[key] for key in common):
                    return True
        return False

    def _report_mixed_entities(self, members, destination):
        """
        Поля из разных сущностей в одной записи формы.

        Оператор не должен догадываться, что случилось: сообщение называет
        обе сущности и три шага, что делать дальше.
        """
        section = destination[0] if destination else ""
        lines = []
        for entity in members:
            suffix = f" — файлы: {entity.sources_text()}" if entity.sources else ""
            lines.append("• " + entity.display_name + suffix)
        first, last = members[0], members[-1]
        steps = ["1. Нажмите «Снять галочки у выбранных»."]
        if first.uid == last.uid:
            steps.append(f"2. Подтвердите сущность целиком кнопкой «{first.button_text}».")
        else:
            steps.append(f"2. Подтвердите одну сущность целиком кнопкой «{first.button_text}».")
            steps.append(f"3. Затем — вторую: «{last.button_text}».")
        message = "\n".join([
            f"Вы выбрали поля из ДВУХ разных сущностей, а они попадают в одну запись формы "
            f"«{TITLES.get(section, section)}»:",
            "",
            *lines,
            "",
            "Одной галочкой нельзя подтвердить поля из разных сущностей — "
            "это разные объекты в форме.",
            "",
            "Что делать:",
            *steps,
        ])
        logger.warning("Импорт документов: перенос отклонён, поля из разных сущностей, "
                       "раздел=%s, сущностей=%s", section, len(members))
        self.errors.setText(self.errors.text() +
                            "Поля из разных сущностей не перенесены: " +
                            ", ".join(entity.display_name for entity in members) + "\n")
        QMessageBox.warning(self, "Разные сущности", message)

    def _report_conflicts(self, conflicts, applied):
        """Разные значения одного поля: переносим остальное, поле оставляем на выбор.

        Диалог показывается только при настоящем конфликте источников; значения
        в сообщение не попадают — только названия полей и файлы.
        """
        variants = OrderedDict()
        for value in conflicts:
            sources = variants.setdefault(value.field, [])
            for source in (value.sources or ["источник не определён"]):
                if source not in sources:
                    sources.append(source)
        labels = ", ".join(f"{LABELS.get(key, key)} (источники: {', '.join(sources)})"
                           for key, sources in variants.items())
        self.snapshot = form_snapshot(self.window)
        applied_keys = {(section, target, key) for section, target, key, _ in applied}
        for entity in self.entities:
            destination = self.destination(entity)
            for value in entity.fields:
                if destination and (destination[0], destination[1], value.field) in applied_keys:
                    self.confirmed[(entity.uid, value.field)] = False
        self._refresh_all_fields()
        tail = ("остальные подтверждённые поля уже перенесены."
                if applied else "перенос не выполнен.")
        message = ("Для одного поля выбраны разные значения из разных источников. "
                   "Такие поля не перенесены: " + labels + ".\n\n"
                   "Снимите галочку с лишней строки (или исправьте значение) "
                   "и повторите перенос — " + tail)
        self.errors.setText(self.errors.text() +
                            "Конфликт значений, поля не перенесены: " + labels + "\n")
        QMessageBox.warning(self, "Конфликт значений", message)

    def _report_done(self, transferred):
        """Итог переноса: оператор видит, что всё ушло в форму, и может закрывать."""
        self.snapshot = form_snapshot(self.window)
        self.uncheck_all()
        self._refresh_all_fields()
        self.progress_label.setText("Готово. Все данные перенесены в форму. Можно закрывать. " +
                                    summary_text(self.entities))
        self.status.setText(f"Перенесено полей: {transferred}. Форма обновлена, "
                            "диалог можно закрыть.")
        logger.info("Импорт документов: перенос завершён, полей=%s", transferred)
