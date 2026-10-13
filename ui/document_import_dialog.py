"""Проверка документов: распознал → видно, что нашли → одна кнопка → перенос.

Как выглядит окно после переделки (13.10.2026):

    Перетащите файлы или нажмите «Добавить», затем «Распознать».        [?]
    [+ Добавить файлы]                                [ Распознать ]
    ┌───────────────────┬──────────────┬────────────────────────────┐
    │ Файл              │ Статус       │ Найдено                    │
    │ Договор.pdf       │ ✅ готово    │ 12 полей                   │
    │ Скан.jpg          │ ❌ ошибка    │ Vision 429                 │
    └───────────────────┴──────────────┴────────────────────────────┘
    ┌───────────────────────────────────────────────────────────────┐
    │ ВОДИТЕЛЬ: Иванов Иван Иванович                                │
    │   📄 passport_01.jpg (GigaChat Vision)                        │
    │     Серия паспорта │ 60 26          │ Перевозчик → ИНН        │
    └───────────────────────────────────────────────────────────────┘
    Распознано: 12 полей из 15. Не найдено: место рождения, ВУ.
                                  [ Закрыть ] [ Перенести в форму ]

Что изменилось по существу
--------------------------
* **Галочек подтверждения нет.** Переносится ВСЁ, что распознано и прошло
  проверку формата: оператор и так видит найденное в дереве. Один шаг
  («Перенести в форму») вместо «отметить → перенести подтверждённое».
* **Файлы — таблица со статусами** («ожидает», «распознаётся…», «готово»,
  «ошибка»), строки подсвечены цветом. Пока файлов нет, вместо таблицы
  показана подписанная зона перетаскивания.
* **Кнопки разделены:** слева второстепенная «+ Добавить файлы», справа
  главная «Распознать»; «Открыть оригинал» и «Показать распознанный текст»
  переехали в контекстное меню строки, «Отменить обработку» видна только
  во время распознавания.
* **Поля без колонок «Подтвердить» и «Действие»**: «Что нашли», «Значение»,
  «Куда попадёт в форме». Непрочитанное поле видно серым («— не найдено —»),
  но перенос не блокирует.
* Полный текст объяснений — в кнопке «?» в правом верхнем углу.

Группировку (водитель / организация / машина) считает `core/import_entities.py`,
диалог только показывает результат. В базу импорт не пишет: поля уходят
в форму вызовом `apply_approved`.
"""
import logging
from collections import OrderedDict
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QSize, Qt, QObject, QRunnable, pyqtSignal, QUrl
from PyQt5.QtGui import QBrush, QColor, QDesktopServices
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from core.document_import_service import (DocumentImportService, FieldResult, LABELS,
    LOCAL_OCR_FALLBACK_METHOD, LOCAL_OCR_METHOD, LOCAL_OCR_METHODS, LOCAL_TEXT_METHOD,
    SCHEMA, TEXT_METHOD, TITLES, VISION_METHOD, canonical_value, compare_fields,
    local_ocr_hint, local_ocr_label, normalized, same_entity, valid_value)
from core.import_entities import (STATE_CONFLICT, STATE_INVALID, STATE_UNREADABLE,
    UNREADABLE_SOURCE_LABEL, detect_conflicts, entities_from_rows, entity_by_uid,
    progress_text, source_methods, summary_text)

from ui import theme
from ui.icons import action_icon
from ui.widgets.table_helpers import (
    MODE_FIXED, MODE_STRETCH, install_tooltip_on_table, setup_point_table,
)

logger = logging.getLogger(__name__)

# Колонки дерева проверки: что нашли, значение и куда оно попадёт.
# Колонок «Подтвердить» и «Действие» здесь нет: подтверждений в окне не
# осталось, а правка значения идёт двойным кликом по строке.
COL_WHAT, COL_VALUE, COL_TARGET = range(3)

#: Свойства виджетов дерева: какому (сущность, поле) они служат. Слоты берут
#: их у отправителя сигнала, поэтому в connect нет ни lambda, ни partial
#: (цикл ссылок Python ↔ Qt роняет процесс при выходе, грабли 2B.7).
ENTITY_UID_PROPERTY = "import_entity_uid"
FIELD_PROPERTY = "import_field"
TREE_HEADERS = ("Что нашли в документах", "Значение", "Куда попадёт в форме (сейчас там)")
# Раскладка колонок — общий помощник таблиц (ширины, режимы, подсказки).
WIDTHS_KEY = "ui/document_import/columns"
TREE_COLUMN_WIDTHS = (520, 280, 300)
TREE_COLUMN_MINIMUMS = {COL_WHAT: 280, COL_VALUE: 180, COL_TARGET: 180}

# Колонки таблицы файлов: имя, состояние обработки и что нашлось.
FILE_COL_NAME, FILE_COL_STATUS, FILE_COL_FOUND = range(3)
FILE_HEADERS = ("Файл", "Статус", "Найдено")
FILE_WIDTHS_KEY = "ui/document_import/files_columns"
FILE_COLUMN_WIDTHS = (460, 180, 260)
FILE_COLUMN_MINIMUMS = {FILE_COL_NAME: 220, FILE_COL_STATUS: 140, FILE_COL_FOUND: 160}

#: Состояния файла в таблице. Тексты короткие: строка таблицы — не отчёт,
#: подробности (текст ошибки) стоят в колонке «Найдено» и в подсказке.
FILE_WAITING = "ожидает"
FILE_RUNNING = "⏳ распознаётся…"
FILE_DONE = "✅ готово"
FILE_ERROR = "❌ ошибка"
FILE_EMPTY = "⚠ поля не найдены"

SOURCE_PREFIX = "📄 "
NO_SOURCE_LABEL = "📄 " + UNREADABLE_SOURCE_LABEL
UNREAD_FILES_HEADING = "📄 НЕ ПРОЧИТАННЫЕ ФАЙЛЫ"
FORM_EMPTY_MARK = "— пусто —"
FORM_DIFFERS_MARK = "≠ "
#: Поле есть в схеме, но не найдено ни в одном документе: серым, но перенос
#: оно не блокирует.
FIELD_NOT_FOUND_MARK = "— не найдено —"
#: Строку убрали из переноса правым кликом: значение остаётся видимым.
FIELD_REMOVED_MARK = "— удалено из переноса —"

#: Строка состояния, пока файлы не выбраны.
FILES_NOT_CHOSEN_TEXT = "Файлы не выбраны: добавьте документы и нажмите «Распознать»."

#: Одна строка над кнопками. Остальное объяснение — в «?».
HEADER_HINT = "Перетащите файлы или нажмите «Добавить», затем «Распознать»."
HELP_TITLE = "Проверка документов — как это работает"
HELP_TEXT = (
    "1. Добавьте файлы: «+ Добавить файлы» или перетащите их в окно.\n"
    "2. Нажмите «Распознать» — документы прочитает облако (GigaChat) или\n"
    "   локальный OCR. Ход работы виден в таблице файлов: «ожидает»,\n"
    "   «распознаётся…», «готово», «ошибка».\n"
    "3. Проверьте, что нашлось: слева — поле и значение, справа — куда оно\n"
    "   попадёт в форме и что там сейчас.\n"
    "4. Нажмите «Перенести в форму». Подтверждать поля не нужно: переносится\n"
    "   всё, что распознано.\n"
    "\n"
    "Чего в документах нет — видно серым «— не найдено —»: такие поля можно\n"
    "заполнить в форме вручную, перенос они не блокируют.\n"
    "Поле в дереве правится двойным кликом. Правый клик по строке — «Открыть\n"
    "оригинал», «Показать распознанный текст», «Скопировать значение»,\n"
    "«Удалить строку» (строка не пойдёт в форму).\n"
    "\n"
    "Рядом с именем файла указано, чем он прочитан: (GigaChat Vision) —\n"
    "облако; OCR локально (облако выключено / GigaChat недоступен / GigaChat\n"
    "вернул 429) — распознавание на этом компьютере.\n"
    "В базу импорт ничего не пишет: поля попадают только в форму договора."
)

# ── подписи зоны перетаскивания (пока файлов нет) ──
DROP_TITLE = "Перетащите файлы сюда"
DROP_SUBTITLE = "или нажмите «+ Добавить файлы»"
DROP_FORMATS = "PDF, DOCX, JPG, PNG"
DROP_PAGE = 0        # страница стека: зона перетаскивания
FILES_PAGE = 1       # страница стека: таблица файлов

# ── признак пути распознавания у источника ──
# Оператор должен видеть, чем прочитан документ: облаком или локально.
# Подписи короткие, потому что стоят в строке дерева рядом с именем файла.
# Локальный путь к тому же называет ПРИЧИНУ: «облако выключено» и «облако
# отказало» — разные вещи, и одинаковой пометкой их показывать нельзя
# (текст причины собирает core/document_import_service.local_ocr_label).
METHOD_LABELS = {
    VISION_METHOD: "GigaChat Vision",
    TEXT_METHOD: "GigaChat Текст",
    LOCAL_TEXT_METHOD: "текст документа",
    LOCAL_OCR_METHOD: "OCR локально",
    LOCAL_OCR_FALLBACK_METHOD: "OCR локально",
}
#: Чего стоит каждый путь — для подсказки (tooltip) строки источника.
METHOD_HINTS = {
    VISION_METHOD: "Фото и скан читает облачная Vision-модель {model}.",
    TEXT_METHOD: "Текст документа уходит в облачную текстовую модель {model}.",
    LOCAL_TEXT_METHOD: "Текстовый слой документа прочитан на этом компьютере, без облака.",
    LOCAL_OCR_METHOD: "Локальный OCR: Tesseract, русский + английский. Файл никуда не отправлялся.",
    LOCAL_OCR_FALLBACK_METHOD: "Локальный OCR: Tesseract, русский + английский.",
}


def method_label(method: str, reason: str = "") -> str:
    """Короткая подпись пути распознавания (пусто — подписи нет).

    Для локального OCR с известной причиной подпись несёт саму причину:
    «OCR локально (облако выключено)», «… (GigaChat вернул 429)» и так далее.
    """
    if reason and method in LOCAL_OCR_METHODS:
        return local_ocr_label(reason)
    return METHOD_LABELS.get(method, str(method or ""))


def source_label(source: str, method: str, reason: str = "") -> str:
    """Строка источника в дереве: «📄 passport_01.jpg (GigaChat Vision)».

    Пометка с причиной несёт свои скобки — «OCR локально (облако выключено)»,
    поэтому второй пары вокруг неё не ставится: «📄 scan.png — OCR локально
    (облако выключено)».
    """
    label = method_label(method, reason)
    if not label:
        return SOURCE_PREFIX + source
    if "(" in label:
        return f"{SOURCE_PREFIX}{source} — {label}"
    return f"{SOURCE_PREFIX}{source} ({label})"


def method_hint(method: str, model: str = "", reason: str = "") -> str:
    """Подсказка к строке источника: чем и как прочитан документ."""
    if not method:
        return ""
    if reason and method in LOCAL_OCR_METHODS:
        return local_ocr_hint(reason)
    if method in METHOD_HINTS:
        return METHOD_HINTS[method].format(model=model or VISION_METHOD)
    return "Путь распознавания: " + str(method)


def source_hint(method: str, model: str = "", note: str = "", reason: str = "") -> str:
    """Подсказка строки источника: путь распознавания и, если был, резерв.

    Резерв называется прямо: оператор должен знать, что облако не сработало
    и данные прочитаны локально, а не молча получить другой результат.
    Когда причина отказа известна, примечание не дублируется: развёрнутая
    причина уже сказана словами («GigaChat недоступен: нет ключа»).
    """
    parts = [method_hint(method, model, reason)]
    if note and not reason:
        parts.append(str(note))
    return " ".join(part for part in parts if part)

# ── оформление строк ──
#: Спорное поле (источники дают разные значения) — светло-красный фон:
#: видно издалека, но текст остаётся читаемым.
CONFLICT_BRUSH = QBrush(QColor(255, 214, 214))
#: Непрочитанное значение — серым курсивом.
UNREADABLE_BRUSH = QBrush(QColor(130, 130, 130))
#: Убранная из переноса строка — серым.
REMOVED_BRUSH = QBrush(QColor(150, 150, 150))
#: Подсказка «проверить» под сущностью.
NOTE_BRUSH = QBrush(QColor(255, 244, 204))
NO_BRUSH = QBrush()

#: Цвет строки таблицы файлов: зелёный — готово, жёлтый — распознаётся,
#: красный — ошибка, серый — ожидает.
FILE_STATUS_BRUSHES = {
    FILE_DONE: QBrush(QColor(226, 245, 227)),
    FILE_RUNNING: QBrush(QColor(255, 248, 214)),
    FILE_ERROR: QBrush(QColor(255, 214, 214)),
    FILE_EMPTY: NOTE_BRUSH,
    FILE_WAITING: QBrush(QColor(238, 238, 238)),
}


def _drop_zone_qss() -> str:
    """Рамка зоны перетаскивания в цветах активной темы."""
    p = theme.palette()
    return ("QFrame#dropZone { background: %s; border: 2px dashed %s;"
            " border-radius: 10px; }" % (p["surface_alt"], p["border_input"]))


def _files_word(count: int) -> str:
    """Склонение слова «файл» для сводки повторяющихся ошибок."""
    if count % 10 == 1 and count % 100 != 11:
        return "файл"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "файла"
    return "файлов"


def _fields_word(count: int) -> str:
    """Склонение слова «поле» для строки состояния («23 поля», «12 полей»)."""
    if count % 10 == 1 and count % 100 != 11:
        return "поле"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "поля"
    return "полей"


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
    """Each change is (section, physical vehicle row or None, field, распознанное значение)."""
    sections, vehicles = {}, {}
    for section, target, key, value in changes:
        if key not in SCHEMA[section] or not valid_value(key, value):
            raise ValueError("Некорректное распознанное поле")
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
            # обновляется, поэтому распознанное в импорте не теряется.
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
        # правки — по устойчивому ключу сущности, а не по номеру строки.
        self.edited = {}             # (uid сущности, поле) -> исходное значение
        self.removed = set()         # (uid сущности, поле) -> строка убрана из переноса
        self.targets = {}            # uid сущности -> (раздел, цель в форме)
        self.ocr_texts = {}
        self.unread_sources = set()
        self.reported_sources = set()
        # Источники, прочитанные локально (облако выключено или не сработало):
        # в дереве у них должна быть видна ПРИЧИНА, а не только слово «локально».
        self.local_fallback_sources = set()
        self.fallback_notes = {}     # источник -> примечание сервиса о резерве
        self.local_reasons = {}      # источник -> код причины (CLOUD_*)
        # Текст ошибки -> источники, в которых она повторилась (для сводки).
        self.error_sources = {}
        # Таблица файлов: путь -> строка, имя файла -> состояние обработки.
        self.file_rows = {}          # имя файла -> номер строки таблицы
        self.file_status = {}        # имя файла -> состояние (FILE_*)
        self.file_fields = {}        # имя файла -> сколько полей нашлось
        self.file_notes = {}         # имя файла -> текст ошибки или примечания
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

        # ── шапка: ОДНА строка и «?» с полным текстом ──
        header = QHBoxLayout()
        self.header_hint = QLabel(HEADER_HINT)
        self.header_hint.setWordWrap(True)
        header.addWidget(self.header_hint, 1)
        self.help_button = theme.secondary_button("?", "Справка: как работает импорт документов")
        self.help_button.setFixedSize(32, 28)
        self.help_button.setToolTip(self._help_text())
        self.help_button.setAccessibleName("Справка по окну импорта")
        header.addWidget(self.help_button, 0, Qt.AlignTop)
        layout.addLayout(header)

        # ── кнопки: слева второстепенная, справа главная ──
        bar = QHBoxLayout()
        self.add_button = theme.secondary_button(
            "+ Добавить файлы", "Выбрать документы на диске (PDF, DOCX, JPG, PNG)")
        self.add_button.clicked.connect(self.choose_files)
        bar.addWidget(self.add_button)
        bar.addStretch(1)
        self.start_button = theme.primary_button(
            "Распознать", "Прочитать документы и показать найденные поля")
        self.start_button.setIcon(action_icon("recognize.svg"))
        self.start_button.setIconSize(QSize(18, 18))
        self.start_button.clicked.connect(self.start)
        bar.addWidget(self.start_button)
        # «Отменить обработку» видна только во время распознавания — вместо
        # «Распознать», а не рядом с ней.
        self.cancel_button = theme.secondary_button(
            "Отменить обработку", "Остановить распознавание: уже прочитанное останется")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setVisible(False)
        bar.addWidget(self.cancel_button)
        layout.addLayout(bar)

        # ── прогресс: виден только во время распознавания ──
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(14)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setObjectName("pageHint")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)

        self.status = QLabel(self._cloud_state_text())
        self.status.setWordWrap(True)
        self.status.setObjectName("pageHint")
        layout.addWidget(self.status)

        # ── файлы: подписанная зона перетаскивания ИЛИ таблица статусов ──
        self.drop_zone = self._build_drop_zone()
        self.files_table = self._build_files_table()
        self.files_stack = QStackedWidget()
        self.files_stack.addWidget(self.drop_zone)
        self.files_stack.addWidget(self.files_table)
        self.files_stack.setCurrentIndex(DROP_PAGE)
        layout.addWidget(self.files_stack)

        # ── дерево: что нашли, значение и куда попадёт ──
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(TREE_HEADERS))
        self.tree.setHeaderLabels(list(TREE_HEADERS))
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
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        self.errors = QLabel("")
        self.errors.setWordWrap(True)
        self.errors.setTextFormat(Qt.PlainText)
        self.errors.setVisible(False)
        layout.addWidget(self.errors)

        # ── нижняя панель: что распознано и ОДНА главная кнопка ──
        footer = QHBoxLayout()
        self.summary = QLabel(FILES_NOT_CHOSEN_TEXT)
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        self.close_button = theme.secondary_button("Закрыть", "Закрыть окно, ничего не перенося")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        self.apply_button = theme.primary_button(
            "Перенести в форму", "Перенести в форму все распознанные поля")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)

    def _build_drop_zone(self):
        """Подписанная зона перетаскивания: видна, пока файлов нет."""
        zone = QFrame()
        zone.setObjectName("dropZone")
        zone.setStyleSheet(_drop_zone_qss())
        zone.setMinimumHeight(140)
        box = QVBoxLayout(zone)
        box.addStretch(1)
        title = QLabel(DROP_TITLE)
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignCenter)
        box.addWidget(title)
        subtitle = QLabel(DROP_SUBTITLE)
        subtitle.setObjectName("pageHint")
        subtitle.setAlignment(Qt.AlignCenter)
        box.addWidget(subtitle)
        box.addSpacing(6)
        formats = QLabel(DROP_FORMATS)
        formats.setObjectName("pageHint")
        formats.setAlignment(Qt.AlignCenter)
        box.addWidget(formats)
        box.addStretch(1)
        return zone

    def _build_files_table(self):
        """Таблица файлов со статусами: видна, когда файлы добавлены."""
        table = QTableWidget(0, len(FILE_HEADERS))
        table.setHorizontalHeaderLabels(list(FILE_HEADERS))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setMaximumHeight(160)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_files_menu)
        setup_point_table(
            table,
            [(FILE_COL_NAME, MODE_STRETCH, 0),
             (FILE_COL_STATUS, MODE_FIXED, FILE_COLUMN_WIDTHS[FILE_COL_STATUS]),
             (FILE_COL_FOUND, MODE_FIXED, FILE_COLUMN_WIDTHS[FILE_COL_FOUND])],
            storage_key=FILE_WIDTHS_KEY,
            minimums=FILE_COLUMN_MINIMUMS,
        )
        return table

    def _cloud_state_text(self):
        """Короткая строка о том, кто читает документы в этом запуске."""
        cloud = self.window.settings_service.get_bool("document_cloud_enabled", False)
        if cloud and self.window.gigachat is not None:
            return ("Облачная обработка: включена — фото и сканы читает GigaChat Vision, "
                    "текст уходит в облачную модель.")
        if cloud:
            return "Облачная обработка: ключ недоступен — будет локальный OCR (Tesseract)."
        return "Облачная обработка: выключена — локальный OCR (Tesseract), файлы никуда не отправляются."

    def _help_text(self):
        """Полное объяснение для кнопки «?» (в окне осталась одна строка)."""
        return HELP_TITLE + "\n\n" + HELP_TEXT + "\n\n" + self._cloud_state_text()

    # ─────────────────────────────────────────────────────────
    # Файлы: выбор, перетаскивание, таблица статусов
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
            name = Path(path).name
            if path in self.paths or name in self.file_rows:
                continue
            self.paths.append(path)
            self._add_file_row(name)
            added += 1
        logger.info("Документы выбраны: новых=%s, всего=%s", added, len(self.paths))
        if added:
            self._switch_files_view()
            self._refresh_summary()
            self.status.setText("Файлов выбрано: " + str(len(self.paths)) + ". Нажмите «Распознать».")

    def _add_file_row(self, name):
        """Строка таблицы файлов: имя, «ожидает», пустое «Найдено»."""
        row = self.files_table.rowCount()
        self.files_table.insertRow(row)
        self.file_rows[name] = row
        self.file_status[name] = FILE_WAITING
        self.file_fields[name] = 0
        self.file_notes[name] = ""
        for column, text in enumerate((name, FILE_WAITING, "")):
            item = QTableWidgetItem(text)
            item.setToolTip(name if column == FILE_COL_NAME else text)
            self.files_table.setItem(row, column, item)
        self._paint_file_row(name)

    def _paint_file_row(self, name):
        """Красит строку файла по его состоянию."""
        row = self.file_rows.get(name)
        if row is None:
            return
        brush = FILE_STATUS_BRUSHES.get(self.file_status.get(name, FILE_WAITING), NO_BRUSH)
        for column in range(self.files_table.columnCount()):
            item = self.files_table.item(row, column)
            if item is not None:
                item.setBackground(brush)

    def _set_file_status(self, name, status, found="", note=""):
        """Состояние файла в таблице: «ожидает» → «распознаётся…» → «готово»/«ошибка»."""
        if name not in self.file_rows:
            self._add_file_row(name)
        self.file_status[name] = status
        if found:
            self.file_fields[name] = found
        if note:
            self.file_notes[name] = note
        row = self.file_rows[name]
        status_item = self.files_table.item(row, FILE_COL_STATUS)
        found_item = self.files_table.item(row, FILE_COL_FOUND)
        if status_item is not None:
            status_item.setText(status)
            status_item.setToolTip(status + ("\n" + self.file_notes[name] if self.file_notes[name] else ""))
        if found_item is not None:
            found_item.setText(self.file_found_text(name))
            found_item.setToolTip(self.file_notes[name] or self.file_found_text(name))
        if status == FILE_ERROR and self.file_notes[name]:
            self._append_error(name + ": " + self.file_notes[name])
        self._paint_file_row(name)

    def file_found_text(self, name):
        """Колонка «Найдено»: сколько полей нашлось или чем файл не прочитан."""
        if self.file_status.get(name) == FILE_ERROR:
            return self.file_notes.get(name, "")
        if self.file_status.get(name) == FILE_EMPTY:
            return "поля не найдены"
        count = self.file_fields.get(name, 0)
        if not count:
            return ""
        return f"{count} полей"

    def _switch_files_view(self):
        """Пока файлов нет — зона перетаскивания, когда есть — таблица файлов."""
        self.files_stack.setCurrentIndex(FILES_PAGE if self.file_rows else DROP_PAGE)

    def _row_file_name(self, row=None):
        """Имя файла по номеру строки таблицы (по умолчанию — текущей)."""
        if row is None:
            row = self.files_table.currentRow()
        for name, index in self.file_rows.items():
            if index == row:
                return name
        return ""

    def dragEnterEvent(self, event):
        if not self.task and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def path_for_source(self, source):
        """Полный путь к файлу по имени источника из дерева (пусто — не найден)."""
        name = str(source or "").split(", стр. ")[0].strip()
        if not name:
            return ""
        for path in self.paths:
            if Path(path).name == name:
                return path
        return ""

    def open_original(self, source=None):
        """Открывает оригинал файла системным просмотрщиком."""
        if source is None:
            source = self._row_file_name()
        path = self.path_for_source(source)
        if path:
            logger.info("Импорт документов: открыт оригинал файла")
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, "Оригинал", "Файл не найден среди добавленных.")

    def remember_text(self, source, variants):
        # UI memory only. Personal document text is not persisted or logged.
        self.ocr_texts[source] = list(variants)

    def show_ocr_text(self, source=None):
        """Показывает распознанный текст файла (из памяти окна)."""
        if not source:
            source = self._row_file_name()
        if not source:
            QMessageBox.information(self, "Распознанный текст", "Сначала выберите файл в таблице.")
            return
        filename = str(source).split(", стр. ")[0].strip()
        entries = [(item_source, variants) for item_source, variants in self.ocr_texts.items()
                   if item_source == filename or item_source.startswith(filename + ", стр. ")]
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

    # ─────────────────────────────────────────────────────────
    # Контекстные меню (правый клик по строке)
    # ─────────────────────────────────────────────────────────

    def _build_files_menu(self, row=None):
        """Меню строки таблицы файлов (тесты вызывают его напрямую)."""
        name = self._row_file_name(row)
        menu = QMenu(self.files_table)
        if not name:
            return menu
        menu.addAction("Открыть оригинал").setData(("open", name))
        menu.addAction("Показать распознанный текст").setData(("text", name))
        if not self.task:
            menu.addSeparator()
            menu.addAction("Убрать из списка").setData(("forget", name))
        for action in menu.actions():
            action.triggered.connect(self._on_menu_action)
        return menu

    def _build_tree_menu(self, item):
        """Меню строки дерева полей (тесты вызывают его напрямую)."""
        menu = QMenu(self.tree)
        payload = item.data(COL_WHAT, Qt.UserRole) if item is not None else None
        kind = payload[0] if payload else ""
        source = self._payload_source(payload)
        if source:
            menu.addAction("Открыть оригинал").setData(("open", source))
            menu.addAction("Показать распознанный текст").setData(("text", source))
        if kind == "field":
            uid, field = payload[1], payload[2]
            if self._field_value(uid, field) is not None:
                menu.addAction("Скопировать значение").setData(("copy", uid, field))
            if (uid, field) in self.removed:
                menu.addAction("Вернуть строку в перенос").setData(("restore", uid, field))
            else:
                menu.addAction("Удалить строку").setData(("drop", uid, field))
        elif kind == "entity":
            uid = payload[1]
            entity = entity_by_uid(self.entities, uid)
            menu.addAction("Скопировать название").setData(("copy_name", uid))
            if entity is not None and any((uid, value.field) not in self.removed
                                          for value in entity.fields):
                menu.addAction("Удалить строки сущности").setData(("drop_entity", uid))
            else:
                menu.addAction("Вернуть строки сущности").setData(("restore_entity", uid))
        submenu = menu.addMenu("Добавить пустые поля вручную")
        for section, title in TITLES.items():
            submenu.addAction(title).setData(("manual", section))
        for action in menu.actions():
            action.triggered.connect(self._on_menu_action)
            if action.menu() is not None:
                for sub_action in action.menu().actions():
                    sub_action.triggered.connect(self._on_menu_action)
        return menu

    def _payload_source(self, payload):
        """Источник файла у строки дерева (пусто — строке нечего открывать)."""
        if not payload:
            return ""
        if payload[0] in ("source", "field", "variant") and len(payload) > 3:
            return str(payload[3] or "")
        if payload[0] == "unread_file":
            return str(payload[1] or "")
        return ""

    def _show_files_menu(self, position):
        menu = self._build_files_menu(self.files_table.rowAt(position.y()))
        if menu.isEmpty():
            return
        menu.exec_(self.files_table.viewport().mapToGlobal(position))

    def _show_tree_menu(self, position):
        menu = self._build_tree_menu(self.tree.itemAt(position))
        if menu.isEmpty():
            return
        menu.exec_(self.tree.viewport().mapToGlobal(position))

    def _on_menu_action(self, _checked: bool = False):
        """Слот пунктов контекстного меню: команда лежит в data() отправителя."""
        action = self.sender()
        data = action.data() if action is not None else None
        if not data:
            return
        command = data[0]
        if command in ("open", "text"):
            source = data[1]
            if command == "open":
                self.open_original(source)
            else:
                self.show_ocr_text(source)
        elif command == "copy":
            self.copy_field_value(data[1], data[2])
        elif command == "copy_name":
            entity = entity_by_uid(self.entities, data[1])
            if entity is not None:
                QApplication.clipboard().setText(entity.display_name)
        elif command == "drop":
            self.set_field_removed(data[1], data[2], True)
        elif command == "restore":
            self.set_field_removed(data[1], data[2], False)
        elif command == "drop_entity":
            self.set_entity_removed(data[1], True)
        elif command == "restore_entity":
            self.set_entity_removed(data[1], False)
        elif command == "manual":
            self.add_manual_group(data[1])
        elif command == "forget":
            self.forget_file(data[1])

    def copy_field_value(self, uid, field):
        """«Скопировать значение»: значение поля уходит в буфер обмена."""
        value = self._field_value(uid, field)
        if value is None:
            return
        QApplication.clipboard().setText(str(value.value or ""))
        logger.info("Импорт документов: значение поля скопировано, раздел=%s, поле=%s",
                    value.section, field)

    def forget_file(self, name):
        """Убирает файл из списка до следующего распознавания."""
        if self.task:
            return
        path = self.path_for_source(name)
        if path in self.paths:
            self.paths.remove(path)
        row = self.file_rows.pop(name, None)
        self.file_status.pop(name, None)
        self.file_fields.pop(name, None)
        self.file_notes.pop(name, None)
        if row is not None:
            self.files_table.removeRow(row)
            for other, index in list(self.file_rows.items()):
                if index > row:
                    self.file_rows[other] = index - 1
        logger.info("Импорт документов: файл убран из списка, осталось=%s", len(self.paths))
        self._switch_files_view()
        self._refresh_summary()

    # ─────────────────────────────────────────────────────────
    # Обработка
    # ─────────────────────────────────────────────────────────

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
        self.local_fallback_sources = set()
        self.fallback_notes = {}
        self.local_reasons = {}
        self.error_sources = {}
        self.unread_files = []
        self.edited = {}
        self.removed = set()
        self.targets = {}
        self._set_errors("")
        self.load_rows([])
        for name in self.file_rows:
            self.file_fields[name] = 0
            self.file_notes[name] = ""
            self._set_file_status(name, FILE_WAITING)
        # Первый файл помечаем «распознаётся…»: сервис читает файлы по порядку.
        if self.paths:
            self._set_file_status(Path(self.paths[0]).name, FILE_RUNNING)
        settings = self.window.settings_service.as_dict()
        service = DocumentImportService(settings, self.window.gigachat)
        self.task = ImportTask(service, list(self.paths))
        self.task.signals.result.connect(self.receive)
        self.task.signals.text.connect(self.remember_text)
        self.task.signals.done.connect(self.finished)
        self._busy(True)
        self.status.setText("Обработка… Отмена сработает после текущего запроса.")
        self._refresh_progress(processed=0)
        self.window.thread_pool.start(self.task)

    def _busy(self, running):
        """Во время распознавания главная кнопка уступает место отмене."""
        self.add_button.setEnabled(not running)
        self.start_button.setVisible(not running)
        self.start_button.setEnabled(not running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self.progress.setVisible(running)
        # Подпись прогресса остаётся после работы: в ней итоговая сводка.
        self.progress_label.setVisible(running or bool(self.progress_label.text().strip()))
        if running:
            self.apply_button.setEnabled(False)
        else:
            self._refresh_summary()

    def receive(self, source, evidence, error):
        logger.info("Импорт документов: получен результат, групп=%s, ошибка=%s", len(evidence), bool(error))
        self.reported_sources.add(source)
        self.evidence.extend(evidence)
        # Резервный путь виден и в дереве, и в логе — но без значений полей.
        for item in evidence:
            if item.method in LOCAL_OCR_METHODS or item.local_reason:
                self.local_fallback_sources.add(source)
                self._remember_fallback(source, item.note, item.local_reason)
                logger.info("Импорт документов: изображение прочитано локально, причина=%s",
                            item.local_reason or "не указана")
                break
        found = sum(sum(not k.startswith('_') for k in e.values) for e in evidence)
        name = str(source).split(", стр. ")[0].strip()
        if error:
            # Полный список ошибок по файлам остаётся в self.errors,
            # здесь только копится состояние строки для сводки в статусе.
            sources = self.error_sources.setdefault(error, [])
            if source not in sources:
                sources.append(source)
            self._set_file_status(name, FILE_ERROR, note=error)
        elif not evidence:
            self.unread_sources.add(source)
            self._set_file_status(name, FILE_EMPTY)
            self._append_error(source + ": поля не прочитаны; проверьте оригинал.")
        else:
            previous = self.file_fields.get(name, 0)
            self._set_file_status(name, FILE_DONE, found=previous + found)
        self._mark_next_running(name)
        self.status.setText("Обработан: " + source + ". Найдено полей-кандидатов: " +
                            str(found) + ". Ожидаем завершения остальных документов…")
        self._refresh_summary()
        self._refresh_progress()

    def _mark_next_running(self, name):
        """Следующий необработанный файл — «распознаётся…» (сервис идёт по порядку)."""
        names = [Path(path).name for path in self.paths]
        if name in names:
            index = names.index(name)
            for following in names[index + 1:]:
                if self.file_status.get(following) == FILE_WAITING:
                    self._set_file_status(following, FILE_RUNNING)
                    return

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
        """Полоса и подпись прогресса: во время работы — «Распознано N из M»."""
        total = len(self.paths)
        done = self._reported_file_count() if processed is None else int(processed)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, max(1, total)))
        if prefix or entities is not None:
            self.progress_label.setText(progress_text(done, total, entities, prefix))
        else:
            self.progress_label.setText(f"Распознано {done} из {total} файлов…")

    def finished(self):
        cancelled = self.task.cancel.is_set()
        logger.info("Импорт документов: задача завершена, отменено=%s, групп=%s", cancelled, len(self.evidence))
        self.task = None
        readable = {e.source for e in self.evidence}
        self.unread_files = sorted(self.unread_sources - readable)
        self.rows = compare_fields(self.evidence)
        logger.info("Импорт документов: строк для проверки=%s", len(self.rows))
        self.render()
        prefix = "Обработка отменена." if cancelled else "Обработка завершена."
        self._refresh_progress(processed=self._reported_file_count() if cancelled else len(self.paths),
                               entities=self.entities, prefix=prefix)
        summary = self.repeated_errors_summary()
        self.status.setText(prefix + " Распознанные поля ниже — проверьте и переносите в форму." +
                            (" " + summary if summary else ""))
        self._busy(False)
        self._refresh_summary()
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
    # Сводка и состояние нижней панели
    # ─────────────────────────────────────────────────────────

    def _append_error(self, line):
        """Копит сообщения об ошибках: строка видна, только когда есть что сказать."""
        text = self.errors.text()
        self.errors.setText((text + line + "\n") if text else (line + "\n"))
        self.errors.setVisible(True)

    def _set_errors(self, text):
        self.errors.setText(text)
        self.errors.setVisible(bool(text.strip()))

    def field_counts(self):
        """(всего полей, распознано) — без строк, убранных из переноса."""
        total = found = 0
        for entity in self.entities:
            for value in entity.fields:
                if (entity.uid, value.field) in self.removed:
                    continue
                total += 1
                if value.readable:
                    found += 1
        return total, found

    def transferable_count(self):
        """Сколько полей реально уйдёт в форму: распознано, формат верный, есть в форме."""
        count = 0
        for entity in self.entities:
            for value in entity.fields:
                if (entity.uid, value.field) in self.removed:
                    continue
                if self.is_transferable(entity, value):
                    count += 1
        return count

    @staticmethod
    def is_transferable(entity, value):
        """Поле уйдёт в форму: распознано, формат верный и поле есть в разделе формы."""
        return value.confirmable and value.field in SCHEMA.get(entity.section, ())

    def missing_labels(self, limit=3):
        """Подписи ненайденных полей для строки состояния (без ПДн)."""
        labels = []
        for entity in self.entities:
            for value in entity.fields:
                if (entity.uid, value.field) in self.removed:
                    continue
                if value.unreadable and value.label not in labels:
                    labels.append(value.label)
        return labels[:limit], max(0, len(labels) - limit)

    def _error_file_count(self):
        return sum(1 for status in self.file_status.values() if status == FILE_ERROR)

    def _summary_text(self):
        """Строка слева внизу: что распознано и что осталось незаполненным."""
        parts = []
        errors = self._error_file_count()
        if errors:
            parts.append(f"Ошибки: {errors} {_files_word(errors)}. Подробности — в таблице.")
        if not self.paths and not self.entities:
            return FILES_NOT_CHOSEN_TEXT
        if not self.entities:
            if not self.reported_sources:
                parts.append(f"Файлов выбрано: {len(self.paths)}. Нажмите «Распознать».")
            else:
                parts.append("Распознанных полей нет: проверьте документы "
                             "или добавьте другие файлы.")
            return " ".join(parts)
        total, found = self.field_counts()
        if found and found == total:
            parts.append(f"Распознано: {total} из {total}. Всё готово.")
        else:
            missing, rest = self.missing_labels()
            tail = ""
            if missing:
                tail = " Не найдено: " + ", ".join(missing) + (" и ещё " + str(rest) if rest else "") + "."
            parts.append(f"Распознано: {found} {_fields_word(found)} из {total}." + tail)
        skipped = found - self.transferable_count()
        if skipped:
            parts.append(f"Не переносится: {skipped} (нет в форме, формат или расхождение).")
        return " ".join(parts)

    def _refresh_summary(self):
        """Обновляет строку состояния и доступность главной кнопки."""
        self.summary.setText(self._summary_text())
        self.apply_button.setEnabled(self.task is None and self.transferable_count() > 0)

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
        self._refresh_progress(entities=self.entities)
        self._refresh_summary()
        logger.info("Импорт документов: сущностей=%s, спорных полей=%s, переносимых=%s",
                    len(self.entities), len(detect_conflicts(self.entities)),
                    self.transferable_count())

    def _add_entity_item(self, entity):
        top = QTreeWidgetItem(self.tree, [entity.display_name, "", ""])
        top.setData(COL_WHAT, Qt.UserRole, ("entity", entity.uid))
        font = top.font(COL_WHAT)
        font.setBold(True)
        top.setFont(COL_WHAT, font)
        top.setToolTip(COL_WHAT, self._entity_hint(entity))
        target = self._target_widget(entity)
        if target is not None:
            self.tree.setItemWidget(top, COL_TARGET, target)
        else:
            top.setText(COL_TARGET, TITLES.get(entity.section, ""))
        self._items[entity.uid] = top

        # Подсказки «проверить» — сразу под сущностью, до источников: это то,
        # на что оператору нужно посмотреть в первую очередь.
        for note in entity.notes:
            note_item = QTreeWidgetItem(top, ["⚠ " + note, "", ""])
            note_item.setData(COL_WHAT, Qt.UserRole, ("note", entity.uid))
            note_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            note_item.setBackground(COL_WHAT, NOTE_BRUSH)
            note_item.setToolTip(COL_WHAT, "Документы связаны по совпавшим полям. "
                                           "Проверьте отмеченное по оригиналу.")

        for source, values in entity.fields_by_source().items():
            methods = source_methods(values)
            method = methods.get(source, "")
            reason = self._local_reason(source) if source in self.local_fallback_sources else ""
            node = QTreeWidgetItem(top, [source_label(source, method, reason) if source else NO_SOURCE_LABEL,
                                         "", ""])
            node.setData(COL_WHAT, Qt.UserRole, ("source", entity.uid, "", source))
            if not source:
                node.setToolTip(COL_WHAT, "В документах этих полей нет: введите значение "
                                          "в поле формы вручную, если оно нужно.")
                self._unreadable_items.append(node)
            else:
                fallback = self._fallback_note(source) if source in self.local_fallback_sources else ""
                node.setToolTip(COL_WHAT, source_hint(method, self.vision_model, fallback, reason))
            for value in values:
                self._add_field_item(node, entity, value, source, methods)
        self._refresh_entity_item(entity.uid)

    def _add_field_item(self, node, entity, value, source="", methods=None):
        item = QTreeWidgetItem(node, [value.label, "", ""])
        item.setData(COL_WHAT, Qt.UserRole, ("field", entity.uid, value.field, source))
        item.setFlags((item.flags() | Qt.ItemIsEditable) & ~Qt.ItemIsDropEnabled)
        self._field_items[(entity.uid, value.field)] = item
        if value.unreadable:
            self._unreadable_items.append(item)
        self._add_conflict_variants(item, entity, value, methods)
        self._refresh_field_item(entity, value)

    def _add_conflict_variants(self, item, entity, value, methods=None):
        """
        Спорное поле: оба значения остаются в дереве, каждое со своим файлом.

        Оператор видит, что именно напечатано в каждом документе, и решает
        сам — правкой поля или выбором одного из значений. Рядом с файлом —
        путь распознавания: у одного поля значения могут прийти из облака
        и из локального OCR.
        """
        if not value.conflicted:
            return
        # Метод нужен КОНКРЕТНОГО источника этого поля: у спорного поля
        # значения могли прийти и из облака, и из локального OCR.
        methods = dict(methods or {})
        methods.update(value.source_methods)
        for source, variant in value.variants:
            if not variant:
                continue
            reason = self._local_reason(source) if source in self.local_fallback_sources else ""
            child = QTreeWidgetItem(item, [source_label(source, methods.get(source, ""), reason),
                                           variant, ""])
            child.setData(COL_WHAT, Qt.UserRole, ("variant", entity.uid, value.field, source))
            child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            child.setBackground(COL_WHAT, CONFLICT_BRUSH)
            child.setBackground(COL_VALUE, CONFLICT_BRUSH)
            fallback = self._fallback_note(source) if source in self.local_fallback_sources else ""
            child.setToolTip(COL_WHAT, source_hint(methods.get(source, ""), self.vision_model,
                                                   fallback, reason)
                             or "Значение из этого файла. Исправить поле можно "
                                "двойным кликом по строке выше.")

    def _add_unread_files_item(self):
        if not self.unread_files:
            return
        root = QTreeWidgetItem(self.tree, [UNREAD_FILES_HEADING,
                                           f"файлов: {len(self.unread_files)}", ""])
        root.setData(COL_WHAT, Qt.UserRole, ("unread_files", ""))
        font = root.font(COL_WHAT)
        font.setBold(True)
        root.setFont(COL_WHAT, font)
        root.setToolTip(COL_WHAT, "Эти файлы прочитаны, но поддерживаемые поля в них "
                                  "не найдены. Проверьте оригиналы.")
        for name in self.unread_files:
            child = QTreeWidgetItem(root, [SOURCE_PREFIX + name, "", ""])
            child.setData(COL_WHAT, Qt.UserRole, ("unread_file", name))
            child.setToolTip(COL_WHAT, "Поля не прочитаны; проверьте оригинал.")

    @property
    def vision_model(self):
        """Имя Vision-модели для подсказки: что именно читало документ."""
        client = getattr(self.window, "gigachat", None)
        model = getattr(client, "vision_model", "")
        if model:
            return model
        try:
            return self.window.settings_service.get_str("gigachat_model", "") or VISION_METHOD
        except Exception:
            return VISION_METHOD

    def _remember_fallback(self, source, note, reason=""):
        """Причина локального пути — по источнику: «429», «нет ключа» и
        «облако выключено» звучат по-разному и ведут к разным пометкам."""
        if note:
            self.fallback_notes[source] = str(note)
        if reason:
            self.local_reasons[source] = str(reason)

    def _fallback_note(self, source):
        """Примечание о резерве: переданное сервисом или общее."""
        return self.fallback_notes.get(source) or "GigaChat недоступен, распознано локально"

    def _local_reason(self, source):
        """Код причины локального пути у источника (пусто — причина неизвестна)."""
        return self.local_reasons.get(source, "")

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
        combo.setProperty(ENTITY_UID_PROPERTY, entity.uid)
        combo.currentIndexChanged.connect(self._on_target_changed_pressed)
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
        """Смена цели: поля этой сущности уйдут в другую запись формы."""
        self.targets[uid] = combo.currentData()

    # ── Слоты виджетов дерева ──
    # Виджет помнит своё (сущность, поле) свойством, а слот берёт его у
    # отправителя сигнала: partial и lambda в connect держат сильную ссылку
    # на диалог, а связь живёт в C++ объекте виджета — цикл Python ↔ Qt,
    # который роняет процесс при выходе (грабли 2B.7).
    def _sender_property(self, name: str):
        """Свойство виджета-отправителя сигнала (None, если его нет)."""
        widget = self.sender()
        return widget.property(name) if widget is not None else None

    def _on_target_changed_pressed(self, _index: int = 0):
        uid = self._sender_property(ENTITY_UID_PROPERTY)
        if not isinstance(uid, str):
            logger.warning("Импорт документов: у списка целей нет сущности — пропуск")
            return
        self._on_target_changed(uid, self.sender())

    def tree_expand_all(self):
        self.tree.expandAll()

    def tree_collapse_all(self):
        self.tree.collapseAll()

    # ─────────────────────────────────────────────────────────
    # Правка значений и строки, убранные из переноса
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

    def _field_value(self, uid, field):
        entity = entity_by_uid(self.entities, uid)
        return entity.field_value(field) if entity else None

    def is_removed(self, uid, field):
        """Строка убрана из переноса правым кликом? (данные при этом целы)"""
        return (uid, field) in self.removed

    def set_field_removed(self, uid, field, state):
        """«Удалить строку» / «Вернуть строку»: поле не пойдёт в форму."""
        value = self._field_value(uid, field)
        if value is None:
            return
        if state:
            self.removed.add((uid, field))
        else:
            self.removed.discard((uid, field))
        entity = entity_by_uid(self.entities, uid)
        self._refresh_field_item(entity, value)
        self._refresh_summary()
        logger.info("Импорт документов: строка убрана из переноса=%s, раздел=%s, поле=%s",
                    bool(state), value.section, field)

    def set_entity_removed(self, uid, state):
        """Убирает из переноса все строки сущности (или возвращает их)."""
        entity = entity_by_uid(self.entities, uid)
        if entity is None:
            return
        for value in entity.fields:
            if state:
                self.removed.add((uid, value.field))
            else:
                self.removed.discard((uid, value.field))
            self._refresh_field_item(entity, value)
        self._refresh_summary()
        logger.info("Импорт документов: строки сущности убраны из переноса=%s, раздел=%s, полей=%s",
                    bool(state), entity.section, len(entity.fields))

    def edit_field(self, uid, field, _clicked=False):
        """Двойной клик по строке — правка значения прямо в дереве."""
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
            else:
                self._refresh_field_item(entity, value)

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
        self._write_back_row(entity, value)
        self._refresh_field_item(entity, value)
        self._refresh_entity_item(entity.uid)
        self._refresh_summary()
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
        self._rendering = True
        try:
            item.setText(COL_VALUE, self._value_text(value, entity.uid))
            item.setText(COL_TARGET, self._current_text(entity, value))
            item.setToolTip(COL_VALUE, self._value_hint(value))
            item.setToolTip(COL_TARGET, self._target_hint(entity, value))
            self._apply_field_style(item, value, entity.uid)
        finally:
            self._rendering = False

    def _apply_field_style(self, item, value, uid=""):
        """Оформление поля: спорное — на красном фоне, непрочитанное — серым курсивом."""
        removed = (uid, value.field) in self.removed
        font = item.font(COL_VALUE)
        font.setItalic(value.unreadable or removed)
        font.setStrikeOut(removed)
        item.setFont(COL_VALUE, font)
        if value.unreadable or removed:
            item.setForeground(COL_VALUE, REMOVED_BRUSH if removed else UNREADABLE_BRUSH)
        else:
            item.setForeground(COL_VALUE, NO_BRUSH)
        background = CONFLICT_BRUSH if value.conflicted else NO_BRUSH
        item.setBackground(COL_WHAT, background)
        item.setBackground(COL_VALUE, background)

    def _refresh_entity_item(self, uid):
        entity = entity_by_uid(self.entities, uid)
        item = self._items.get(uid)
        if entity is None or item is None:
            return
        self._rendering = True
        try:
            item.setText(COL_VALUE, self._entity_summary(entity))
        finally:
            self._rendering = False

    def _refresh_all_fields(self):
        for entity in self.entities:
            for value in entity.fields:
                self._refresh_field_item(entity, value)
            self._refresh_entity_item(entity.uid)

    def add_manual_group(self, section=None):
        """Пустая сущность для ручного ввода — из контекстного меню окна."""
        if section is None:
            section = next(iter(TITLES))
        if section not in SCHEMA:
            logger.warning("Импорт документов: неизвестный раздел ручного ввода")
            return
        logger.info("Импорт документов: добавление пустой группы, раздел=%s", section)
        group = max((row.group for row in self.rows), default=-1) + 1
        name = self._row_file_name()
        source = name or "Ручная проверка оригинала"
        self.rows.extend(FieldResult(group, section, key, "", source, "не прочитано")
                         for key in SCHEMA[section])
        self.render()

    # ─────────────────────────────────────────────────────────
    # Тексты ячеек и подсказки
    # ─────────────────────────────────────────────────────────

    def _value_text(self, value, uid=""):
        if (uid, value.field) in self.removed:
            return FIELD_REMOVED_MARK
        if value.state == STATE_CONFLICT:
            return "[расхождение в источниках]"
        if value.state == STATE_UNREADABLE:
            return FIELD_NOT_FOUND_MARK
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
        lines.append("Двойной клик по значению — исправить. Правый клик — открыть "
                     "оригинал или убрать строку из переноса.")
        return "\n".join(lines)

    @staticmethod
    def _entity_hint(entity):
        lines = [f"Найдено в файлах: {entity.sources_text()}."]
        if entity.needs_review:
            lines.append("Опознать по документам не удалось — проверьте поля вручную.")
        lines.extend("⚠ " + note for note in entity.notes)
        lines.append("Значение поля исправляется двойным кликом по строке.")
        return "\n".join(lines)

    def _entity_summary(self, entity):
        text = entity.counts_text()
        if entity.needs_review:
            text += " · требует ручной проверки"
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

    def _form_changed(self, latest, destination, value):
        """Поле правили в форме после открытия окна? Тогда его не перезаписываем."""
        now = self._current_value(latest, destination, value.field)
        was = self._current_value(self.snapshot, destination, value.field)
        return normalized(now) != normalized(was)

    # ─────────────────────────────────────────────────────────
    # Перенос в форму
    # ─────────────────────────────────────────────────────────

    def apply(self):
        """Переносит в форму ВСЕ распознанные поля: подтверждать нечего."""
        logger.info("Импорт документов: перенос распознанных полей")
        latest = form_snapshot(self.window)
        destinations, candidates = OrderedDict(), []
        conflicts, invalid, stale, unmapped = [], [], [], []
        for entity in self.entities:
            destination = self.destination(entity)
            if not destination:
                QMessageBox.warning(self, "Выберите цель",
                                    "Выберите целевую запись для распознанных полей.")
                return
            section, target = destination
            if section == "vehicles" and isinstance(target, int) and target >= len(latest["vehicles"]):
                logger.warning("Импорт документов: перенос отклонён, целевая запись удалена")
                QMessageBox.warning(self, "Запись удалена",
                                    "Целевой автомобиль удалён из формы. Выберите новую запись.")
                return
            for value in entity.fields:
                if (entity.uid, value.field) in self.removed:
                    continue
                if value.conflicted:
                    conflicts.append(value)
                    continue
                text = str(value.value or "").strip()
                if not text:
                    continue
                if value.field not in SCHEMA.get(section, ()):
                    # Такое поле распознано, но в этом разделе формы его нет:
                    # оператор видит его в дереве, в форму оно не уходит.
                    unmapped.append(value)
                    continue
                if not valid_value(value.field, text):
                    invalid.append(value)
                    continue
                if self._form_changed(latest, destination, value):
                    stale.append(value)
                    continue
                destinations.setdefault(destination, OrderedDict())[entity.uid] = entity
                candidates.append((destination, entity, value))

        # 1. Поля из разных сущностей с разными опознавательными данными вместе
        #    переносить нельзя: в форме это разные объекты.
        for destination, members in destinations.items():
            members = list(members.values())
            if len(members) > 1 and self._mixed_entities(members):
                self._report_mixed_entities(members, destination)
                return

        # Повтор одного и того же поля из разных файлов — не ошибка: одинаковые
        # значения переносятся один раз, а разные означают конфликт источников,
        # и такое поле не переносится, пока оператор не выберет одну строку.
        entries, order = OrderedDict(), []
        for destination, entity, value in candidates:
            map_key = destination + (value.field,)
            if map_key not in entries:
                entries[map_key] = []
                order.append(map_key)
            entries[map_key].append((canonical_value(value.field, value.value), entity, value))

        changes = []
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
        if not changes and not conflicts and not invalid and not stale and not unmapped:
            logger.info("Импорт документов: перенос пропущен, переносимых полей нет")
            self.snapshot = latest
            self._refresh_summary()
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
        self.snapshot = form_snapshot(self.window)
        if conflicts or invalid or stale or unmapped:
            self._report_skipped(conflicts, invalid, stale, unmapped, len(changes))
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
        обе сущности и говорит, что делать дальше.
        """
        section = destination[0] if destination else ""
        lines = []
        for entity in members:
            suffix = f" — файлы: {entity.sources_text()}" if entity.sources else ""
            lines.append("• " + entity.display_name + suffix)
        message = "\n".join([
            f"В документах {len(members)} разных записей, а в форме одна запись "
            f"«{TITLES.get(section, section)}»:",
            "",
            *lines,
            "",
            "Перенести их одной кнопкой нельзя: в форме это одно место.",
            "",
            "Что делать:",
            "• если это разные люди или машины — уберите лишние строки правым кликом "
            "(«Удалить строки сущности») и повторите перенос;",
            "• если это одна и та же запись — исправьте опознавательные поля "
            "двойным кликом по строке;",
            "• для машин и организаций проверьте колонку «Куда попадёт в форме».",
        ])
        logger.warning("Импорт документов: перенос отклонён, поля из разных сущностей, "
                       "раздел=%s, сущностей=%s", section, len(members))
        self._append_error("Поля из разных сущностей не перенесены: " +
                           ", ".join(entity.display_name for entity in members))
        QMessageBox.warning(self, "Разные сущности", message)

    def _report_skipped(self, conflicts, invalid, stale, unmapped, applied):
        """Что не ушло в форму и почему: конфликт, формат, правка в форме, нет поля.

        Значения в сообщение не попадают — только названия полей (логи без ПДн).
        Убранные из переноса строки сюда не входят: это осознанный выбор.
        """
        lines = []
        if conflicts:
            sources = OrderedDict()
            for value in conflicts:
                files = sources.setdefault(value.field, [])
                for source in (value.sources or ["источник не определён"]):
                    if source not in files:
                        files.append(source)
            lines.append("• разные значения в источниках: " + ", ".join(
                LABELS.get(key, key) for key in sources))
        if invalid:
            lines.append("• формат не подходит: " + ", ".join(
                sorted({LABELS.get(value.field, value.field) for value in invalid})))
        if unmapped:
            lines.append("• такого поля нет в разделе формы: " + ", ".join(
                sorted({LABELS.get(value.field, value.field) for value in unmapped})))
        if stale:
            lines.append("• вы уже правили эти поля в форме после открытия окна: " + ", ".join(
                sorted({LABELS.get(value.field, value.field) for value in stale})))
        tail = ("Остальные поля уже перенесены." if applied
                else "В форму ничего не перенесено.")
        message = ("Не всё ушло в форму:\n\n" + "\n".join(lines) + "\n\n"
                   "Исправьте значение двойным кликом по строке (или уберите строку "
                   "правым кликом) и нажмите «Перенести в форму» ещё раз. " + tail)
        self._append_error("Не перенесено: " + "; ".join(lines))
        self._refresh_all_fields()
        self._refresh_summary()
        QMessageBox.warning(self, "Проверьте поля", message)

    def _report_done(self, transferred):
        """Итог переноса: оператор видит, что всё ушло в форму, и может закрывать."""
        self._refresh_all_fields()
        self._refresh_progress(entities=self.entities)
        self.summary.setText(f"Готово: перенесено полей — {transferred}. "
                             "Форма обновлена, окно можно закрывать.")
        self.status.setText(f"Перенесено полей: {transferred}. " + summary_text(self.entities))
        self.apply_button.setEnabled(self.transferable_count() > 0)
        logger.info("Импорт документов: перенос завершён, полей=%s", transferred)
