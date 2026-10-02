"""Bounded, local document extraction; one page at a time."""
from dataclasses import dataclass
from pathlib import Path
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from core.import_cancel import check_cancel

logger = logging.getLogger(__name__)

SUPPORTED = {".png", ".jpg", ".jpeg", ".pdf", ".docx", ".doc", ".rtf", ".zip", ".rar"}
ARCHIVE_SUFFIXES = {".zip", ".rar"}
ARCHIVE_MAX_FILES = 50
ARCHIVE_MAX_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024

# Characters that never appear in clean Russian document text: box-drawing
# glyphs, stray brackets and similar OCR noise. Punctuation of normal prose is
# not counted, so the check does not fire on ordinary documents.
_JUNK_PATTERN = re.compile(r"[^\w\s.,:;!?()\"'«»№\-–—/\\*%+=#]")
_CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


def text_layer_is_suspicious(text):
    """Detect unusable OCR/text layers (look-alike Latin, junk glyphs).

    A PDF text layer may exist yet be worthless: some scanners embed text
    where Cyrillic letters are replaced by visually identical Latin ones
    ("AOTOBOP" instead of "ДОГОВОР"), others leave box-drawing noise. Such a
    page must be rendered and sent to OCR/Vision instead of trusting the
    layer. Ordinary Russian text and short captions never match this check.
    """
    cyrillic = len(_CYRILLIC_PATTERN.findall(text))
    latin = len(_LATIN_PATTERN.findall(text))
    letters = cyrillic + latin
    if letters < 40:
        return False
    # Latin look-alikes dominate while Cyrillic still occurs somewhere.
    if cyrillic and latin >= cyrillic * 2:
        return True
    # Russian pipeline: a long Latin-only layer is a broken mapping too.
    if not cyrillic and latin >= 60:
        return True
    junk = len(_JUNK_PATTERN.findall(text))
    return junk / letters > 0.15


@dataclass
class DocumentPage:
    number: int
    text: str = ""
    image: object = None
    label: str = ""
    # PDF text pages carry a lazy renderer: the service asks for the image
    # only when the text layer itself gave nothing useful.
    image_loader: object = None

    def load_image(self):
        if self.image is not None:
            return self.image
        if callable(self.image_loader):
            return self.image_loader()
        return None


def _render_pdf_page(page):
    """Render one PDF page to RGB; resolution bounded for memory and API limits."""
    scale = min(3, 2600 / max(page.get_size()))
    bitmap = page.render(scale=scale)
    try:
        return bitmap.to_pil().copy()
    finally:
        bitmap.close()


def _pdf_page_loader(path, number):
    """Lazy renderer that works after the reading generator has moved on."""
    def load():
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(str(path)) as document:
            page = document[number]
            try:
                return _render_pdf_page(page)
            finally:
                page.close()
    return load


def _read_pdf(path, cancel):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(path)) as document:
        if len(document) > 100:
            raise RuntimeError("PDF содержит больше 100 страниц.")
        for number in range(len(document)):
            check_cancel(cancel)
            page = document[number]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
                # Sparse text (e.g. a page number) does not make a scan searchable.
                if sum(c.isalnum() for c in text) >= 40:
                    if text_layer_is_suspicious(text):
                        # Broken layer: keep the raw text for diagnostics but
                        # let OCR/Vision read the rendered page.
                        yield DocumentPage(number + 1, text=text,
                                           image=_render_pdf_page(page))
                    else:
                        yield DocumentPage(
                            number + 1, text=text,
                            image_loader=_pdf_page_loader(path, number),
                        )
                else:
                    yield DocumentPage(number + 1, text=text,
                                       image=_render_pdf_page(page))
            finally:
                page.close()


def _read_image(path):
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        if image.width * image.height > 40_000_000:
            raise RuntimeError("Изображение больше 40 мегапикселей.")
        yield DocumentPage(1, image=ImageOps.exif_transpose(image).convert("RGB"))


def _read_docx(path):
    from docx import Document
    from zipfile import ZipFile
    with ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
            raise RuntimeError("Слишком большой распакованный DOCX.")
    document = Document(path)
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        parts.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
    for section in document.sections:
        parts.extend(p.text for p in section.header.paragraphs)
        parts.extend(p.text for p in section.footer.paragraphs)
    yield DocumentPage(1, text="\n".join(parts))


# ── RTF ────────────────────────────────────────────────────────────────
# Minimal reader: no macros, no embedded objects, no external references.
# Handles \uN escapes (Word) and \'hh cp1251 bytes (older editors).
_RTF_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header",
    "footer", "footnote", "field", "fldinst", "listtable", "listoverridetable",
    "rsidtbl", "latentstyles", "datastore", "themedata", "colorschememapping",
    "generator", "xmlnstbl", "shppict", "nonshppict", "bkmkstart", "bkmkend",
}
_RTF_LINE_BREAKS = {"par", "line", "cell", "row", "sect", "page", "pard"}


def _rtf_encoding(raw: bytes) -> str:
    match = re.search(rb"\\ansicpg(\d+)", raw[:4096])
    code = int(match.group(1)) if match else 1251
    if code == 65001:
        return "utf-8"
    if code in (1251, 1252):
        return f"cp{code}"
    return "cp1251"


def rtf_to_text(raw: bytes) -> str:
    """Convert RTF bytes to plain text without evaluating any content."""
    encoding = _rtf_encoding(raw)
    text = raw.decode(encoding, "replace")
    out, stack = [], []
    escape_bytes = bytearray()

    def flush_escapes():
        if escape_bytes:
            out.append(bytes(escape_bytes).decode(encoding, "replace"))
            escape_bytes.clear()

    i, length = 0, len(text)
    unicode_fallback = 1
    pending_skip = 0
    while i < length:
        ch = text[i]
        skipping = bool(stack and stack[-1])
        if pending_skip and ch not in "\\{}" and not skipping:
            pending_skip -= 1
            i += 1
            continue
        if ch == "{":
            flush_escapes()
            stack.append(skipping)
            i += 1
            continue
        if ch == "}":
            flush_escapes()
            if stack:
                stack.pop()
            i += 1
            continue
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < length else ""
            if nxt in "\\{}":
                flush_escapes()
                if not skipping:
                    out.append(nxt)
                i += 2
                continue
            if nxt == "'":
                hex_pair = text[i + 2:i + 4]
                try:
                    escape_bytes.extend(bytes.fromhex(hex_pair))
                except ValueError:
                    pass
                i += 4
                continue
            flush_escapes()
            if nxt == "*":
                if stack:
                    stack[-1] = True
                i += 2
                continue
            match = re.match(r"\\([a-zA-Z]+)(-?\d+)?[ ]?", text[i:])
            if match:
                word, argument = match.group(1), match.group(2)
                i += match.end()
                if word == "u" and argument is not None:
                    code = int(argument)
                    if code < 0:
                        code += 65536
                    if not skipping:
                        out.append(chr(code))
                        pending_skip = unicode_fallback
                elif word == "uc" and argument:
                    unicode_fallback = max(0, int(argument))
                elif word == "tab" and not skipping:
                    out.append("\t")
                elif word == "cell" and not skipping:
                    # Table cells become "label | value" pairs, as in DOCX.
                    out.append(" | ")
                elif word in _RTF_LINE_BREAKS and not skipping:
                    out.append("\n")
                elif word in _RTF_DESTINATIONS and stack:
                    stack[-1] = True
                continue
            i += 2
            continue
        flush_escapes()
        if not skipping and ch not in "\r\n":
            out.append(ch)
        i += 1
    flush_escapes()
    # Collapse control-word spacing artifacts without touching word content.
    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in "".join(out).splitlines()]
    return "\n".join(line for line in lines if line)


def _read_rtf(path):
    text = rtf_to_text(path.read_bytes())
    if sum(c.isalnum() for c in text) < 20:
        raise RuntimeError("RTF не содержит текстового слоя.")
    yield DocumentPage(1, text=text)


# ── конвертация DOC ────────────────────────────────────────────────────
def _convert_doc_with_libreoffice(path, folder, cancel, executable):
    profile = (Path(folder) / "profile").as_uri()
    process = subprocess.Popen(
        [executable, "-env:UserInstallation=" + profile,
         "--headless", "--convert-to", "docx", "--outdir", folder, str(path.resolve())],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    started = time.monotonic()
    try:
        while process.poll() is None:
            check_cancel(cancel)
            if time.monotonic() - started > 90:
                raise RuntimeError("LibreOffice: превышено время преобразования DOC.")
            cancel.wait(.1)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
    converted = Path(folder) / (path.stem + ".docx")
    if process.returncode or not converted.exists():
        raise RuntimeError("LibreOffice не смог прочитать DOC.")
    return converted


def _convert_doc_with_word(path, folder):
    """Convert legacy DOC via local Microsoft Word (COM). None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(path.resolve()), ReadOnly=True,
                                       AddToRecentFiles=False)
        try:
            converted = Path(folder) / (path.stem + ".docx")
            # 16 = wdFormatXMLDocument (docx).
            document.SaveAs2(str(converted), FileFormat=16)
        finally:
            document.Close(False)
        return converted if converted.exists() else None
    except Exception:
        # Any COM failure falls back to the caller's explicit error message.
        return None
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _read_doc(path, cancel, soffice):
    executable = soffice or shutil.which("soffice")
    with tempfile.TemporaryDirectory(prefix="contract-import-") as folder:
        if executable:
            converted = _convert_doc_with_libreoffice(path, folder, cancel, executable)
        else:
            converted = _convert_doc_with_word(path, folder)
        if not converted:
            raise RuntimeError(
                "Для DOC нужен LibreOffice (или установленный Microsoft Word): "
                "установите его и укажите soffice в настройках."
            )
        yield from read_document(converted, cancel, soffice)


# ── архивы ─────────────────────────────────────────────────────────────
def _safe_entry_name(name, index):
    base = Path(str(name).replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ]+", "_", base, flags=re.UNICODE).strip(" .") or "file"
    return f"{index:02d}_{base}"


def _is_supported_entry(name):
    return Path(str(name)).suffix.lower() in SUPPORTED - ARCHIVE_SUFFIXES


def _extract_zip(path, folder, cancel):
    import zipfile
    extracted = []
    with zipfile.ZipFile(path) as archive:
        entries = [i for i in archive.infolist() if not i.is_dir()]
        if len(entries) > ARCHIVE_MAX_FILES:
            raise RuntimeError("В архиве больше 50 файлов.")
        total = sum(i.file_size for i in entries)
        if total > ARCHIVE_MAX_BYTES:
            raise RuntimeError("Распакованный архив больше 100 МБ.")
        for index, info in enumerate(entries, 1):
            if not _is_supported_entry(info.filename):
                continue
            check_cancel(cancel)
            target = folder / _safe_entry_name(info.filename, index)
            written = 0
            with archive.open(info) as source, open(target, "wb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > ARCHIVE_MAX_BYTES:
                        raise RuntimeError("Распакованный архив больше 100 МБ.")
                    destination.write(chunk)
            extracted.append(target)
    return extracted


def _find_unrar():
    for name in ("unrar", "UnRAR.exe"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "WinRAR" / "UnRAR.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "WinRAR" / "UnRAR.exe",
    ]
    return next((str(p) for p in candidates if p.is_file()), None)


def _extract_rar(path, folder, cancel):
    executable = _find_unrar()
    if not executable:
        raise RuntimeError(
            "Для RAR нужен UnRAR (входит в WinRAR): установите его или "
            "распакуйте архив вручную."
        )
    # Extract into a private subfolder, then keep only supported files.
    destination = folder / "unpacked"
    destination.mkdir(exist_ok=True)
    process = subprocess.Popen(
        [executable, "x", "-y", "-o+", "-idq", str(path.resolve()),
         str(destination) + os.sep],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    started = time.monotonic()
    try:
        while process.poll() is None:
            check_cancel(cancel)
            if time.monotonic() - started > 120:
                raise RuntimeError("UnRAR: превышено время распаковки архива.")
            cancel.wait(.1)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
    if process.returncode not in (0, 1):
        raise RuntimeError("UnRAR не смог прочитать архив (повреждён или защищён паролем).")
    extracted = sorted(p for p in destination.rglob("*")
                       if p.is_file() and _is_supported_entry(p.name))
    if len(extracted) > ARCHIVE_MAX_FILES:
        raise RuntimeError("В архиве больше 50 файлов.")
    total = sum(p.stat().st_size for p in extracted)
    if total > ARCHIVE_MAX_BYTES:
        raise RuntimeError("Распакованный архив больше 100 МБ.")
    return extracted


def _read_archive(path, cancel, soffice):
    with tempfile.TemporaryDirectory(prefix="contract-import-") as folder:
        workdir = Path(folder)
        if path.suffix.lower() == ".zip":
            entries = _extract_zip(path, workdir, cancel)
        else:
            entries = _extract_rar(path, workdir, cancel)
        if not entries:
            raise RuntimeError("В архиве нет поддерживаемых файлов.")
        number = 0
        for entry in entries:
            check_cancel(cancel)
            for page in read_document(entry, cancel, soffice):
                number += 1
                yield DocumentPage(number, text=page.text, image=page.image,
                                   label=f"{path.name} → {entry.name}")


def read_document(path, cancel, soffice=""):
    path = Path(path)
    check_cancel(cancel)
    if path.suffix.lower() not in SUPPORTED:
        raise RuntimeError("Неподдерживаемый формат файла.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise RuntimeError("Файл больше 50 МБ. Импорт ограничен для защиты памяти.")
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        yield from _read_image(path)
    elif suffix == ".docx":
        yield from _read_docx(path)
    elif suffix == ".doc":
        yield from _read_doc(path, cancel, soffice)
    elif suffix == ".rtf":
        yield from _read_rtf(path)
    elif suffix in ARCHIVE_SUFFIXES:
        yield from _read_archive(path, cancel, soffice)
    else:
        yield from _read_pdf(path, cancel)
