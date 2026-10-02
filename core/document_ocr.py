"""Локальный OCR (Tesseract) — офлайн-fallback, когда GigaChat Vision недоступен.

Распознанный текст отдаётся как есть: символы не исправляются и не додумываются,
а разбор полей выполняет core.document_fields по подписанным строкам.
"""

from PIL import ImageOps

from core.import_cancel import check_cancel

# Локальная обработка ограничивает размер, чтобы не выедать память на сканах.
MAX_PIXELS = 40_000_000
MAX_SIDE = 4400


def _run_tesseract(image) -> str:
    """Запускает Tesseract (русский + английский) без постобработки текста."""
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "Локальный OCR недоступен: установите pytesseract "
            "(pip install pytesseract) и Tesseract OCR."
        ) from exc

    try:
        return pytesseract.image_to_string(image, lang="rus+eng")
    except pytesseract.TesseractNotFoundError:
        raise RuntimeError(
            "Локальный OCR недоступен: не найден Tesseract OCR (tesseract.exe). "
            "Установите его и добавьте в PATH."
        ) from None
    except pytesseract.TesseractError:
        raise RuntimeError(
            "Локальный OCR не смог обработать изображение (Tesseract вернул ошибку)."
        ) from None
    except OSError:
        raise RuntimeError(
            "Локальный OCR недоступен: Tesseract не запускается."
        ) from None


def recognize_image(image, cancel):
    """Распознаёт одно изображение локально и возвращает строку текста.

    :param image: PIL.Image — кадр страницы после чтения документа
    :param cancel: threading.Event — кооперативная отмена
    :return: распознанный текст (str); при отмене бросает ImportCancelled,
             при недоступном Tesseract — RuntimeError с понятным сообщением
    """
    check_cancel(cancel)
    prepared = ImageOps.exif_transpose(image).convert("RGB")
    if prepared.width * prepared.height > MAX_PIXELS:
        prepared.thumbnail((MAX_SIDE, MAX_SIDE))
    check_cancel(cancel)
    text = _run_tesseract(prepared)
    check_cancel(cancel)
    return text
