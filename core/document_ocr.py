"""Локальный OCR (Tesseract) — офлайн-fallback, когда GigaChat Vision недоступен.

Распознанный текст отдаётся как есть: символы не исправляются и не додумываются,
а разбор полей выполняет core.document_fields по подписанным строкам.
"""

from PIL import ImageOps

from core.import_cancel import check_cancel

# Локальная обработка ограничивает размер, чтобы не выедать память на сканах.
MAX_PIXELS = 40_000_000
MAX_SIDE = 4400

#: Сколько букв и цифр считается «текст есть». Меньше — снимок не прочитан, и
#: стоит попробовать повернуть кадр (см. recognize_image).
MIN_TEXT_ALNUM = 20


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


def _alnum(text: str) -> int:
    """Сколько в тексте букв и цифр — мера «текст вообще есть»."""
    return sum(1 for char in (text or "") if char.isalnum())


def recognize_image(image, cancel):
    """Распознаёт одно изображение локально и возвращает строку текста.

    Если прямой проход не дал текста, пробуются повороты на 90°, 270° и 180°.
    Так снимается самый частый вид брака у оператора: документ сфотографирован
    боком (вертикальный кадр телефона, а бланк — горизонтальный). Раньше такой
    снимок молча давал пустой текст, и файл попадал в «не прочитано», хотя
    данные в нём есть. Повороты пробуются ТОЛЬКО когда прямой проход пуст:
    на нормальном скане лишних распознаваний не будет.

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
    if _alnum(text) >= MIN_TEXT_ALNUM:
        return text

    best, best_score = text, _alnum(text)
    for angle in (90, 270, 180):
        check_cancel(cancel)
        candidate = _run_tesseract(prepared.rotate(angle, expand=True))
        score = _alnum(candidate)
        if score > best_score:
            best, best_score = candidate, score
    check_cancel(cancel)
    return best
