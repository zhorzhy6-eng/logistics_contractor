# -*- coding: utf-8 -*-
"""
Распознавание документов: чтение файлов, OCR, извлечение полей.

Слой над ядром: использует core.data, core.utils, infrastructure.api
(GigaChat), но не Qt и не UI. Может вызываться из UI через сервис
DocumentImportService.

Импорты через реэкспорт:
    from services.recognition.reader import read_document
Или через старый путь (работает через алиасы sys.modules):
    from core.document_reader import read_document
"""
