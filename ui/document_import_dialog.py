"""Explicit, review-only import UI. No database writes."""
import logging
from pathlib import Path
from threading import Event

from PyQt5.QtCore import Qt, QObject, QRunnable, pyqtSignal, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QListWidget, QTableWidget, QTableWidgetItem, QComboBox, QMessageBox, QPlainTextEdit,
    QHeaderView)

from core.document_import_service import (DocumentImportService, compare_fields, SCHEMA,
    TITLES, LABELS, FieldResult, same_entity, normalized, valid_value, canonical_value)

logger = logging.getLogger(__name__)


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
    for row in range(tab.table.rowCount()):
        data["vehicles"].append({
            "vin": tab._get_cell_text(row, tab.COL_VIN),
            "brand_model": tab._get_cell_text(row, tab.COL_BRAND),
            "plate_number": tab._get_cell_text(row, tab.COL_PLATE),
            "year": tab._get_spin_value(row, tab.COL_YEAR),
            "color": tab._get_cell_text(row, tab.COL_COLOR),
            "vehicle_type": tab._get_combo_value(row, tab.COL_TYPE),
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
    columns = {"vin": tab.COL_VIN, "brand_model": tab.COL_BRAND,
               "plate_number": tab.COL_PLATE, "color": tab.COL_COLOR}
    for target, fields in vehicles.items():
        if isinstance(target, str):  # new group, separate from every existing vehicle
            tab.fill_data([fields], append=True, imported=True)
            continue
        for key, value in fields.items():
            if key in columns:
                tab.table.setItem(target, columns[key], QTableWidgetItem(value))
            elif key == "year":
                spin = tab.table.cellWidget(target, tab.COL_YEAR)
                spin.setRange(0, 2100)
                spin.setSpecialValueText("—")
                spin.setValue(int(value))
            elif key == "vehicle_type":
                tab.table.cellWidget(target, tab.COL_TYPE).setCurrentText(value)


class DocumentImportDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Загрузить документы — проверка полей")
        self.resize(1250, 800)
        self.setAcceptDrops(True)
        self.task = None
        self.evidence, self.rows, self.paths = [], [], []
        self.ocr_texts = {}
        self.unread_sources = set()
        # Текст ошибки -> источники, в которых она повторилась (для сводки).
        self.error_sources = {}
        self._closing = False
        self.snapshot = form_snapshot(window)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Добавьте файлы или перетащите их в окно. Ничего не переносится автоматически.\n"
            "Проверьте группу, целевую вкладку и каждое значение; галочка подтверждает перенос и замену текущего значения.\n"
            "Разные группы могут относиться к разным людям/ТС. Не объединяйте их без проверки оригинала."))
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
        cloud = window.settings_service.get_bool("document_cloud_enabled", False)
        if cloud and window.gigachat is not None:
            cloud_state = ("в GigaChat уходят изображения и сканы (Vision), а также текст, "
                           "в котором локальные правила не нашли реквизитов")
        elif cloud:
            cloud_state = "GigaChat Vision включён, но ключ недоступен — будет локальный OCR (Tesseract)"
        else:
            cloud_state = "GigaChat Vision выключен — используется локальный OCR (Tesseract)"
        self.status = QLabel("Облачная обработка: " + cloud_state)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Подтвердить", "Группа", "Цель", "Поле", "Найденное / правка",
                                              "Источник и варианты", "Сейчас в форме", "Состояние"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((105, 55, 135, 160, 180, 220, 130, 185)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setWordWrap(True)
        self.table.itemChanged.connect(self.value_changed)
        layout.addWidget(self.table)
        manual = QHBoxLayout()
        manual.addWidget(QLabel("Если поля не прочитаны, выберите вкладку для ручной проверки:"))
        self.manual_section = QComboBox()
        for section, title in TITLES.items():
            self.manual_section.addItem(title, section)
        manual.addWidget(self.manual_section)
        self.manual_button = QPushButton("Добавить пустые поля")
        self.manual_button.clicked.connect(self.add_manual_group)
        manual.addWidget(self.manual_button)
        layout.addLayout(manual)
        self.errors = QLabel("")
        self.errors.setWordWrap(True)
        self.errors.setTextFormat(Qt.PlainText)
        layout.addWidget(self.errors)
        self.apply_button = QPushButton("Перенести подтверждённые поля")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        layout.addWidget(self.apply_button)

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
        layout.addWidget(QLabel("Это варианты машинного чтения. Ошибочные буквы и цифры возможны; исправляйте поля в таблице импорта."))
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
        self.error_sources = {}
        self.table.setRowCount(0)
        self.errors.setText("")
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
        self.window.thread_pool.start(self.task)

    def receive(self, source, evidence, error):
        logger.info("Импорт документов: получен результат, групп=%s, ошибка=%s", len(evidence), bool(error))
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

    def finished(self):
        cancelled = self.task.cancel.is_set()
        logger.info("Импорт документов: задача завершена, отменено=%s, групп=%s", cancelled, len(self.evidence))
        self.task = None
        self.add_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.manual_button.setEnabled(True)
        self.rows = compare_fields(self.evidence)
        readable = {e.source for e in self.evidence}
        for source in sorted(self.unread_sources - readable):
            self.rows.append(FieldResult(len(self.rows), "", "", "", source, "не прочитано"))
        logger.info("Импорт документов: строк для проверки=%s", len(self.rows))
        self.render()
        summary = self.repeated_errors_summary()
        self.status.setText(("Обработка отменена. " if cancelled else "Обработка завершена. ") +
                            "Полученные поля доступны для проверки. Непрочитанные значения можно ввести вручную." +
                            (" " + summary if summary else ""))
        self.apply_button.setEnabled(bool(self.rows))
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

    def add_manual_group(self):
        # Keep every edit, checkbox and group destination when adding blank fields.
        saved = {}
        for i, row in enumerate(self.rows):
            saved[id(row)] = (self.table.item(i, 4).text(), self.table.item(i, 0).checkState(),
                              self.table.cellWidget(i, 2).currentData())
        section = self.manual_section.currentData()
        logger.info("Импорт документов: добавление пустой группы, раздел=%s", section)
        group = max((r.group for r in self.rows), default=-1) + 1
        index = self.files.currentRow()
        source = Path(self.paths[index]).name if 0 <= index < len(self.paths) else "Ручная проверка оригинала"
        self.rows.extend(FieldResult(group, section, key, "", source, "не прочитано") for key in SCHEMA[section])
        self.render()
        for i, row in enumerate(self.rows):
            if id(row) in saved:
                value, checked, target = saved[id(row)]
                combo = self.table.cellWidget(i, 2)
                combo.blockSignals(True)
                self._select_target(combo, target)
                combo.blockSignals(False)
                self.table.item(i, 4).setText(value)
                self.table.item(i, 0).setCheckState(checked)
                self.refresh_current(i)
        self.apply_button.setEnabled(True)

    @staticmethod
    def _select_target(combo, target):
        """Qt's findData can return -1 for equal tuple QVariant values after a rerender."""
        for index in range(combo.count()):
            if combo.itemData(index) == target:
                combo.setCurrentIndex(index)
                return True
        return False

    def _update_checkable(self, i):
        item = self.table.item(i, 0)
        if not self.rows[i].section:
            item.setFlags(Qt.NoItemFlags)
            return
        valid = valid_value(self.rows[i].key, self.table.item(i, 4).text())
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable if valid else Qt.ItemIsEnabled)
        if not valid:
            item.setCheckState(Qt.Unchecked)
            item.setToolTip("Введите полное значение допустимого формата, затем подтвердите поле.")
        else:
            item.setToolTip("Отметьте после проверки по оригиналу.")

    def render(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        self.targets = {}
        for i, row in enumerate(self.rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Unchecked)
            if not row.section:
                check.setFlags(Qt.NoItemFlags)
            self.table.setItem(i, 0, check)
            values = {1: str(row.group+1) if row.section else "—", 3: LABELS.get(row.key, row.key) or "Не определено", 4: row.value,
                      5: row.sources, 6: "", 7: row.state}
            for column, value in values.items():
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column != 4 or not row.section:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(i, column, item)
            self._update_checkable(i)
            combo = QComboBox()
            if not row.section:
                combo.addItem("Не определена", ("", None))
                combo.setEnabled(False)
            elif row.section in {"carrier", "customer"}:
                for section in ("carrier", "customer"):
                    combo.addItem(TITLES[section], (section, None))
                combo.setCurrentIndex(0 if row.section == "carrier" else 1)
            elif row.section == "vehicles":
                combo.addItem("Новое авто", ("vehicles", "new:"+str(row.group)))
                for index, vehicle in enumerate(self.snapshot["vehicles"]):
                    combo.addItem(f"Авто {index+1}: " + (vehicle["vin"] or vehicle["plate_number"] or "без номера"), ("vehicles", index))
                matches = [j for j, v in enumerate(self.snapshot["vehicles"]) if same_entity("vehicles", row.identity, v)]
                if len(matches) == 1:
                    combo.setCurrentIndex(matches[0]+1)
            else:
                combo.addItem(TITLES[row.section], (row.section, None))
            self.table.setCellWidget(i, 2, combo)
            combo.currentIndexChanged.connect(lambda _, group=row.group, c=combo: self.target_changed(group, c.currentData()))
        self.table.blockSignals(False)
        for i in range(len(self.rows)):
            self.refresh_current(i)
        self.table.resizeRowsToContents()
        for i in range(len(self.rows)):
            self.table.setRowHeight(i, min(90, max(36, self.table.rowHeight(i))))

    def target_changed(self, group, target):
        for i, row in enumerate(self.rows):
            if row.group != group:
                continue
            combo = self.table.cellWidget(i, 2)
            combo.blockSignals(True)
            self._select_target(combo, target)
            combo.blockSignals(False)
            self.table.item(i, 0).setCheckState(Qt.Unchecked)
            self.refresh_current(i)

    def current_value(self, snapshot, i):
        return str(self.current_record(snapshot, i).get(self.rows[i].key, "") or "")

    def current_record(self, snapshot, i):
        data = self.table.cellWidget(i, 2).currentData()
        if not data:
            return {}
        section, target = data
        if not section:
            return {}
        data = snapshot[section]
        if section == "vehicles":
            data = data[target] if isinstance(target, int) and target < len(data) else {}
        return data

    def refresh_current(self, i):
        current = self.current_value(self.snapshot, i)
        self.table.item(i, 6).setText(current)
        self.table.item(i, 6).setToolTip(current)
        value = self.table.item(i, 4).text()
        conflict = bool(current and value and normalized(current) != normalized(value))
        self.table.item(i, 7).setText("расхождение с формой" if conflict else self.rows[i].state)
        self.table.item(i, 7).setToolTip(self.table.item(i, 7).text())

    def value_changed(self, item):
        if item.column() == 4:
            self.table.item(item.row(), 0).setCheckState(Qt.Unchecked)
            self._update_checkable(item.row())
            self.refresh_current(item.row())

    def apply(self):
        logger.info("Импорт документов: проверка подтверждённых полей")
        candidates, groups = [], {}
        latest = form_snapshot(self.window)
        for i, row in enumerate(self.rows):
            if self.table.item(i, 0).checkState() != Qt.Checked:
                continue
            target_data = self.table.cellWidget(i, 2).currentData()
            if not target_data:
                QMessageBox.warning(self, "Выберите цель", "Выберите целевую запись для подтверждённого поля.")
                return
            section, target = target_data
            value = self.table.item(i, 4).text().strip()
            if section == "vehicles" and isinstance(target, int) and target >= len(latest["vehicles"]):
                logger.warning("Импорт документов: перенос отклонён, целевая запись удалена")
                QMessageBox.warning(self, "Запись удалена", "Целевой автомобиль удалён из формы. Выберите новую запись.")
                return
            if row.key not in SCHEMA[section] or not valid_value(row.key, value):
                logger.warning("Импорт документов: перенос отклонён, неверное поле=%s, раздел=%s", row.key, section)
                QMessageBox.warning(self, "Проверьте значение", "Пустое, неоднозначное или неверное значение: " + LABELS.get(row.key, row.key))
                return
            if self.current_record(latest, i) != self.current_record(self.snapshot, i):
                logger.warning("Импорт документов: перенос отклонён, форма изменилась")
                self.snapshot = latest
                for j in range(len(self.rows)):
                    self.table.item(j, 0).setCheckState(Qt.Unchecked)
                    self.refresh_current(j)
                QMessageBox.warning(self, "Форма изменилась", "Текущие значения обновлены. Подтвердите поля заново.")
                return
            destination = (section, target)
            target_groups = groups.setdefault(destination, {})
            for group, identity in target_groups.items():
                common = identity.keys() & row.identity.keys()
                if group != row.group and any(identity[k] != row.identity[k] for k in common):
                    logger.warning("Импорт документов: перенос отклонён, конфликт идентификаторов групп")
                    QMessageBox.warning(self, "Разные группы", "У выбранных документов различаются идентификаторы. Выберите одну группу для целевой записи и проверьте оригиналы.")
                    return
            target_groups[row.group] = row.identity
            candidates.append((destination, row, canonical_value(row.key, value)))
        # Повторная галочка на том же поле — не ошибка: одинаковые значения
        # переносятся один раз, а разные означают конфликт источников, и такое
        # поле не переносится, пока пользователь не выберет одну строку.
        entries, order = {}, []
        for destination, row, canonical in candidates:
            map_key = destination + (row.key,)
            if map_key not in entries:
                entries[map_key] = []
                order.append(map_key)
            entries[map_key].append((canonical, row))
        changes, conflicts = [], []
        change_groups, change_pairs = {}, {}
        for map_key in order:
            section, target, key = map_key
            values = entries[map_key]
            if len({normalized(canonical) for canonical, _ in values}) > 1:
                conflicts.extend(row for _, row in values)
                logger.warning("Импорт документов: конфликт значений, поле=%s, раздел=%s",
                               key, section)
                continue
            if len(values) > 1:
                logger.info("Импорт документов: повторное подтверждение совпало, поле=%s", key)
            changes.append((section, target, key, values[0][0]))
            change_groups.setdefault((section, target), set()).update(
                row.group for _, row in values)
            change_pairs.setdefault((section, target), set()).add(
                (key, normalized(values[0][0])))
        if not changes and not conflicts:
            logger.info("Импорт документов: перенос пропущен, нет подтверждённых полей")
            return
        for (section, target), members in change_groups.items():
            # Спрашиваем только о реальном смешивании: разные группы принесли
            # разные поля. Повтор одной и той же пары «поле=значение» — не повод.
            if len(members) > 1 and len(change_pairs[(section, target)]) > 1:
                numbers = ", ".join(str(g+1) for g in sorted(members))
                answer = QMessageBox.question(self, "Подтвердите связь документов",
                    f"Группы {numbers} не удалось однозначно связать автоматически.\n"
                    f"Подтверждаете, что выбранные поля относятся к одной записи «{TITLES[section]}»?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
        if changes:
            apply_approved(self.window, changes)
            logger.info("Импорт документов: перенесено полей=%s", len(changes))
        if conflicts:
            self._report_conflicts(conflicts, changes)
            return
        self.accept()

    def _report_conflicts(self, conflicts, applied):
        """Разные значения одного поля: переносим остальное, поле оставляем на выбор.

        Диалог показывается только при настоящем конфликте источников; значения
        в сообщение не попадают — только названия полей.
        """
        variants = {}
        for row in conflicts:
            variants.setdefault(row.key, []).append(str(row.group + 1))
        labels = ", ".join(f"{LABELS.get(key, key)} (группы {', '.join(sorted(set(numbers)))})"
                           for key, numbers in variants.items())
        self.snapshot = form_snapshot(self.window)
        applied_keys = {(section, target, key) for section, target, key, _ in applied}
        for i, row in enumerate(self.rows):
            target_data = self.table.cellWidget(i, 2).currentData()
            if target_data and (target_data[0], target_data[1], row.key) in applied_keys:
                self.table.item(i, 0).setCheckState(Qt.Unchecked)
            self.refresh_current(i)
        tail = ("остальные подтверждённые поля уже перенесены."
                if applied else "перенос не выполнен.")
        message = ("Для одного поля выбраны разные значения из разных источников. "
                   "Такие поля не перенесены: " + labels + ".\n\n"
                   "Снимите галочку с лишней строки (или исправьте значение) "
                   "и повторите перенос — " + tail)
        self.errors.setText(self.errors.text() +
                            "Конфликт значений, поля не перенесены: " + labels + "\n")
        QMessageBox.warning(self, "Конфликт значений", message)
