#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты парсера правовой базы (core/legal_parser.py, db/crud/legal_metadata.py).

ВСЕ тесты работают на MOCK: сеть не трогается ни одним запросом. Парсер
принимает готовую ``requests.Session``, поэтому вместо неё подставляется
``FakeSession`` с заранее описанными ответами портала. Реальные адреса
pravo.gov.ru в тестах не запрашиваются — это требование задания и заодно
защита от того, что тест начнёт зависеть от доступности портала.

Что проверяется
---------------
* список источников (config/legal_sources.json) — состав и категории;
* разбор данных: даты, имена файлов, HTML, RTF, ZIP;
* клиент портала опубликования: параметры запроса, справочники, повторы;
* клиент банка «Законодательство России»: CP1251, заголовок, ошибки;
* поиск, отбор конкретного акта (номер без даты и названия не принимается),
  мониторинг обновлений;
* база метаданных: upsert без затирания, статистика, поиск по кириллице;
* скачивание: IPS → HTML+TXT, портал → PDF, «ручные» документы;
* массовое скачивание: пропуск уже скачанных, отчёт об ошибках;
* интерфейс: диалог «Правовая база» и раздел настроек (offscreen).
"""

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import legal_parser as lp  # noqa: E402
from db.crud import legal_metadata  # noqa: E402

# ─────────────────────────────────────────────────────────────
# MOCK сети
# ─────────────────────────────────────────────────────────────


class FakeResponse:
    """Ответ портала: JSON или байты (как у requests.Response)."""

    def __init__(self, status_code=200, payload=None, content=b"", encoding="utf-8", headers=None):
        """
        :param status_code: код ответа;
        :param payload: объект для .json();
        :param content: тело ответа байтами;
        :param encoding: кодировка для .text;
        :param headers: заголовки.
        """
        self.status_code = status_code
        self._payload = payload
        if content:
            self.content = content
        elif payload is not None:
            self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            self.content = b""
        self.encoding = encoding
        self.headers = headers or {}

    def json(self):
        """Разбирает тело как JSON (как requests)."""
        if self._payload is None:
            raise ValueError("тело ответа не JSON")
        return self._payload

    @property
    def text(self) -> str:
        """Тело как строка в своей кодировке."""
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def iter_content(self, chunk_size=65536):
        """Отдаёт тело блоками (для потокового скачивания)."""
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


class FakeSession:
    """
    Подмена requests.Session: отвечает по первому совпавшему шаблону URL.

    Каждый вызов записывается в ``calls`` — тесты проверяют не только
    результат, но и то, какие параметры ушли на портал.
    """

    def __init__(self, routes=None, default=None):
        """
        :param routes: список пар «подстрока URL → ответ или исключение»;
        :param default: ответ, если ни один шаблон не совпал.
        """
        self.routes = list(routes or [])
        self.default = default if default is not None else FakeResponse(404)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, stream=False, **kwargs):
        """Возвращает заготовленный ответ и запоминает вызов."""
        self.calls.append({"url": url, "params": params, "timeout": timeout, "stream": stream})
        for fragment, response in self.routes:
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return self.default

    def calls_for(self, fragment: str):
        """Вызовы, чей URL содержит подстроку."""
        return [call for call in self.calls if fragment in call["url"]]


# ─────────────────────────────────────────────────────────────
# Образцы ответов портала
# ─────────────────────────────────────────────────────────────

DOCUMENT_TYPES = [
    {"name": "Федеральный закон", "id": "82a8bf1c-3bc7-47ed-827f-7affd43a7f27"},
    {"name": "Постановление", "id": "fd5a8766-f6fd-4ac2-8fd9-66f414d314ac"},
    {"name": "Приказ", "id": "2dddb344-d3e2-4785-a899-7aa12bd47b6f"},
]

AUTHORITIES = [
    {"name": "Правительство Российской Федерации", "id": "8005d8c9-4b6d-48d3-861a-2a37e69fccb3"},
]


def api_item(eo: str, number: str, name: str, doc_date: str, publish_date: str = "") -> dict:
    """Запись API портала опубликования (формат /api/Documents)."""
    return {
        "eoNumber": eo,
        "complexName": f"Постановление Правительства Российской Федерации от {doc_date} № {number}\n \"{name}\"",
        "name": name,
        "number": number,
        "documentDate": f"{doc_date}T00:00:00",
        "publishDateShort": f"{(publish_date or doc_date)}T00:00:00",
        "viewDate": (publish_date or doc_date),
        "signatoryAuthorityId": "8005d8c9-4b6d-48d3-861a-2a37e69fccb3",
        "pagesCount": 12,
        "pdfFileLength": 123456,
        "id": f"uuid-{eo}",
    }


IPS_HTML = (
    "<html><head><meta charset='windows-1251'><title>Гражданский кодекс "
    "Российской Федерации. Часть вторая</title></head><body>"
    "<div id='t_normal'><p>Глава 40. Перевозка</p>"
    "<p>Статья 793. Ответственность перевозчика за неподачу транспортных средств "
    "и отправителя за неиспользование транспортных средств</p>"
    "<p>Статья 794. Ответственность перевозчика за утрату, недостачу и повреждение "
    "груза, а также за просрочку доставки.</p></div></body></html>"
)


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def legal_db(tmp_path, monkeypatch):
    """Изолированная база метаданных: путь задаётся переменной окружения."""
    path = tmp_path / "legal_metadata.db"
    monkeypatch.setenv(legal_metadata.LEGAL_DB_ENV, str(path))
    legal_metadata.set_legal_db_path(None)
    yield path
    legal_metadata.set_legal_db_path(None)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль: без него виджеты не создаются."""
    from PyQt5.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def sources_file(tmp_path):
    """Мини-список источников вместо боевого config/legal_sources.json."""
    data = {
        "storage": {"dir": "legal", "ocr": False, "page_size": 100, "timeout": 5, "retries": 2,
                    "retry_pause": 0.0},
        "categories": {
            "codes": [
                {"id": "gk2", "short": "ГК ч.2", "title": "ГК РФ. Часть вторая",
                 "number": "14-ФЗ", "date": "1996-01-26", "doc_type": "Кодекс",
                 "nd": "102039276", "query": ""},
                {"id": "koap", "short": "КоАП", "title": "КоАП РФ", "number": "195-ФЗ",
                 "date": "2001-12-30", "doc_type": "Кодекс", "unavailable": True,
                 "note": "нет открытого источника"},
            ],
            "government_decrees": [
                {"id": "pp2200", "short": "ПП 2200", "title": "Правила перевозок грузов",
                 "number": "2200", "date": "2020-12-21", "doc_type": "Постановление",
                 "eo": "0001202012230048", "required": True},
            ],
        },
        "keywords": ["перевозка грузов", "путевой лист"],
    }
    path = tmp_path / "legal_sources.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def build_parser(sources_file, tmp_path, routes=None, **kwargs):
    """Собирает парсер на MOCK-сессии и во временном каталоге."""
    session = FakeSession(routes)
    data_dir = tmp_path / "knowledge"
    parser = lp.LegalParser(sources_path=str(sources_file), data_dir=str(data_dir),
                            session=session, **kwargs)
    parser._fake_session = session
    return parser


@pytest.fixture
def parser(legal_db, sources_file, tmp_path):
    """Парсер со стандартным набором ответов портала."""
    routes = [
        ("publication.pravo.gov.ru/api/DocumentTypes", FakeResponse(payload=DOCUMENT_TYPES)),
        ("publication.pravo.gov.ru/api/SignatoryAuthorities", FakeResponse(payload=AUTHORITIES)),
        ("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251"))),
        ("file/pdf/", FakeResponse(content=b"%PDF-1.4 mock scan without text layer")),
    ]
    return build_parser(sources_file, tmp_path, routes=routes)


# ─────────────────────────────────────────────────────────────
# Список источников
# ─────────────────────────────────────────────────────────────


def test_real_sources_file_is_valid():
    """Боевой config/legal_sources.json разбирается и содержит нужные категории."""
    sources = lp.load_sources()
    assert set(sources["categories"]) >= {
        "codes", "federal_laws", "government_decrees", "ministry_orders", "regional"
    }
    for _category, item in lp.category_items(sources):
        assert item.get("id"), "у каждой записи должен быть id"
        assert item.get("title"), f"у записи {item.get('id')} должно быть название"


def test_real_sources_have_expected_documents():
    """В списке есть ключевые для перевозчика акты: ГК ч.2, ПП 2200, приказы Минтранса."""
    sources = lp.load_sources()
    ids = {item.get("id") for _category, item in lp.category_items(sources)}
    assert {"gk_rf_p2", "pp_2200", "mt_368", "mt_141", "mt_440", "fz_259"} <= ids


def test_real_sources_keywords():
    """Ключевые слова из задания присутствуют в списке."""
    keywords = lp.load_sources()["keywords"]
    for expected in ("перевозка грузов", "транспортная экспедиция", "автомобильный транспорт",
                     "транспортная безопасность", "путевой лист", "тахограф"):
        assert expected in keywords


def test_category_items_filters(sources_file):
    """category_items отдаёт либо всё, либо одну категорию."""
    sources = lp.load_sources(str(sources_file))
    assert len(lp.category_items(sources)) == 3
    assert len(lp.category_items(sources, "codes")) == 2
    assert lp.category_items(sources, "нет-такой") == []


def test_parser_categories_and_keywords(parser):
    """Парсер показывает категории и ключевые слова из своего списка."""
    assert parser.sources_categories() == {"codes": 2, "government_decrees": 1}
    assert parser.keywords() == ["перевозка грузов", "путевой лист"]


# ─────────────────────────────────────────────────────────────
# Разбор данных
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, expected", [
    ("2020-12-21T00:00:00", "2020-12-21"),
    ("21.12.2020", "2020-12-21"),
    ("2020-12-21", "2020-12-21"),
    ("", ""),
    (None, ""),
    ("не дата", ""),
])
def test_normalize_date(raw, expected):
    """Даты портала приводятся к ISO, мусор — к пустой строке."""
    assert lp.normalize_date(raw) == expected


def test_translit_and_slugify():
    """Имя файла собирается из даты, номера и названия и остаётся латиницей."""
    document = lp.LegalDocument(id="cfg:x", title="Постановление № 2200 «О правилах»",
                                number="2200", doc_date="2020-12-21")
    slug = lp.slugify(document)
    assert slug.startswith("2020-12-21-2200-")
    assert slug.isascii()
    assert " " not in slug


def test_html_to_text_drops_scripts():
    """HTML превращается в текст, скрипты и стили выбрасываются."""
    html = ("<html><head><style>p{color:red}</style><script>var a=1;</script></head>"
            "<body><p>Перевозка грузов</p><p>Статья 793</p></body></html>")
    text = lp.html_to_text(html)
    assert "Перевозка грузов" in text
    assert "Статья 793" in text
    assert "color:red" not in text
    assert "var a" not in text


def test_rtf_to_text():
    """RTF разбирается без внешних зависимостей, кириллица раскрывается."""
    rtf = r"{\rtf1\ansi\deff0 {\fonttbl{\f0 Times;}}\f0\fs24 Перевозка \'e3\'f0\'f3\'e7\'ee\'e2\par Статья 793}"
    text = lp.rtf_to_text(rtf)
    assert "Перевозка грузов" in text
    assert "Статья 793" in text
    assert "fonttbl" not in text


def test_extract_text_from_txt(tmp_path):
    """TXT читается как есть."""
    path = tmp_path / "doc.txt"
    path.write_text("Статья 793. Ответственность перевозчика", encoding="utf-8")
    result = lp.extract_text(str(path))
    assert result.error == ""
    assert "Ответственность перевозчика" in result.text


def test_extract_text_from_html_cp1251(tmp_path):
    """HTML в CP1251 (как отдаёт банк «Законодательство России») читается верно."""
    path = tmp_path / "gk.html"
    path.write_bytes(IPS_HTML.encode("cp1251"))
    result = lp.extract_text(str(path))
    assert "Глава 40. Перевозка" in result.text
    assert "Статья 793" in result.text


def test_extract_text_missing_file(tmp_path):
    """Отсутствующий файл — понятная ошибка, а не исключение."""
    result = lp.extract_text(str(tmp_path / "нет.txt"))
    assert result.text == ""
    assert "не найден" in result.error


def test_extract_text_unsupported_format(tmp_path):
    """Неизвестный формат не роняет извлечение."""
    path = tmp_path / "doc.bin"
    path.write_bytes(b"\x00\x01\x02")
    result = lp.extract_text(str(path))
    assert "не поддерживается" in result.error


def test_extract_text_from_zip(tmp_path):
    """Из ZIP берётся текстовый файл, а не PDF."""
    archive = tmp_path / "doc.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("scan.pdf", b"%PDF-1.4 no text")
        zf.writestr("act.html", "<p>Правила перевозок грузов</p>")
    result = lp.extract_text(str(archive))
    assert "Правила перевозок грузов" in result.text


def test_extract_text_from_broken_zip(tmp_path):
    """Битый архив сообщает об ошибке."""
    path = tmp_path / "doc.zip"
    path.write_bytes(b"not a zip at all")
    result = lp.extract_text(str(path))
    assert "ZIP" in result.error


def test_extract_text_from_pdf_without_layer_without_ocr(tmp_path):
    """PDF без текстового слоя и с выключенным OCR даёт понятную ошибку."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4 mock")
    result = lp.extract_text(str(path), allow_ocr=False)
    assert result.text == ""
    assert result.error


def test_file_checksum(tmp_path):
    """SHA-256 считается и различает разные файлы."""
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("один", encoding="utf-8")
    second.write_text("два", encoding="utf-8")
    assert len(lp.file_checksum(str(first))) == 64
    assert lp.file_checksum(str(first)) != lp.file_checksum(str(second))
    assert lp.file_checksum(str(tmp_path / "нет.txt")) == ""


def test_human_size():
    """Размер показывается в удобочитаемом виде (запятая — русская дробь)."""
    assert lp.human_size(0) == "0,0 Б"
    assert "КБ" in lp.human_size(2048)
    assert "МБ" in lp.human_size(5 * 1024 * 1024)


def test_save_text_returns_length(tmp_path):
    """save_text пишет UTF-8 и возвращает число символов."""
    path = tmp_path / "out" / "doc.txt"
    count = lp.save_text("Перевозка", str(path))
    assert count == 9
    assert path.read_text(encoding="utf-8") == "Перевозка"


# ─────────────────────────────────────────────────────────────
# Клиент портала опубликования
# ─────────────────────────────────────────────────────────────


def test_publication_page_coerces_page_size():
    """API принимает только 10/30/100 — иное значение подтягивается к ближайшему."""
    session = FakeSession([("Documents", FakeResponse(payload={"items": []}))])
    api = lp.PublicationApi(session=session, pause=0.0)
    api.page(index=2, page_size=17)
    assert session.calls[0]["params"]["pageSize"] == 10
    assert session.calls[0]["params"]["index"] == 2


def test_publication_page_passes_filters():
    """Фильтры (название, номер, вид документа) уходят на портал."""
    session = FakeSession([("Documents", FakeResponse(payload={"items": []}))])
    api = lp.PublicationApi(session=session, pause=0.0)
    api.page(name="перевозка", number="2200", document_types=["abc"], sorted_by=1)
    params = session.calls[0]["params"]
    assert params["name"] == "перевозка"
    assert params["number"] == "2200"
    assert params["DocumentTypes"] == ["abc"]
    assert params["SortedBy"] == 1


def test_publication_type_id(parser):
    """Вид документа находится и по названию, и по готовому id."""
    api = parser.publication
    assert api.type_id("Федеральный закон") == "82a8bf1c-3bc7-47ed-827f-7affd43a7f27"
    assert api.type_id("82a8bf1c-3bc7-47ed-827f-7affd43a7f27") == "82a8bf1c-3bc7-47ed-827f-7affd43a7f27"
    assert api.type_id("Постановление") == "fd5a8766-f6fd-4ac2-8fd9-66f414d314ac"
    assert api.type_id("Такого вида нет") == ""
    assert api.type_id("") == ""


def test_publication_authority_name(parser):
    """Название органа подставляется по идентификатору."""
    assert parser.publication.authority_name("8005d8c9-4b6d-48d3-861a-2a37e69fccb3") == \
        "Правительство Российской Федерации"
    assert parser.publication.authority_name("") == ""
    assert parser.publication.authority_name("нет-такого") == ""


def test_publication_retries_then_raises():
    """При 503 запрос повторяется, затем поднимается понятная ошибка."""
    session = FakeSession([("Documents", FakeResponse(status_code=503))])
    api = lp.PublicationApi(session=session, retries=3, pause=0.0)
    with pytest.raises(RuntimeError) as error:
        api.page()
    assert "Портал опубликования" in str(error.value)
    assert len(session.calls) == 3


def test_publication_does_not_retry_on_400():
    """Ошибка клиента (400) не повторяется — это бессмысленно."""
    session = FakeSession([("Documents", FakeResponse(status_code=400))])
    api = lp.PublicationApi(session=session, retries=3, pause=0.0)
    with pytest.raises(RuntimeError):
        api.page()
    assert len(session.calls) == 1


def test_publication_urls():
    """Адреса файла и страницы документа собираются из номера опубликования."""
    api = lp.PublicationApi(session=FakeSession(), pause=0.0)
    assert api.pdf_url("0001202012230048").endswith("/file/pdf/0001202012230048")
    assert api.document_url("0001202012230048").endswith("/document/0001202012230048")


# ─────────────────────────────────────────────────────────────
# Клиент банка «Законодательство России»
# ─────────────────────────────────────────────────────────────


def test_ips_html_decoded_as_cp1251():
    """Страница банка читается в CP1251, а не в UTF-8."""
    session = FakeSession([("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251")))])
    api = lp.IpsApi(session=session, pause=0.0)
    html = api.document_html("102039276")
    assert "Гражданский кодекс" in html
    assert session.calls[0]["params"]["nd"] == "102039276"


def test_ips_document_title():
    """Заголовок документа берётся из <title> страницы банка."""
    session = FakeSession([("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251")))])
    api = lp.IpsApi(session=session, pause=0.0)
    assert api.document_title("102039276") == "Гражданский кодекс Российской Федерации. Часть вторая"


def test_ips_error_raises():
    """Пустой ответ банка — понятная ошибка, а не пустой документ."""
    session = FakeSession([("pravo.gov.ru", FakeResponse(status_code=204, content=b""))])
    api = lp.IpsApi(session=session, retries=2, pause=0.0)
    with pytest.raises(RuntimeError) as error:
        api.document_html("1")
    assert "Законодательство России" in str(error.value)


def test_ips_url_builder():
    """Адрес документа банка собирается по nd."""
    assert "nd=102039276" in lp.IpsApi.url("102039276")


# ─────────────────────────────────────────────────────────────
# Поиск и отбор документов
# ─────────────────────────────────────────────────────────────


def test_search_returns_documents_with_metadata(parser):
    """Поиск отдаёт документы с номером, датой, органом и адресом файла."""
    items = [api_item("0001202012230048", "2200", "Об утверждении Правил перевозок грузов",
                      "2020-12-21")]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    found = parser.search("Правила перевозок")
    assert len(found) == 1
    document = found[0]
    assert document.number == "2200"
    assert document.doc_date == "2020-12-21"
    assert document.authority == "Правительство Российской Федерации"
    assert document.eo_number == "0001202012230048"
    assert "file/pdf/0001202012230048" in document.file_url
    assert document.id == "eo:0001202012230048"


def test_search_filters_by_date_from(parser):
    """Граница даты отсекает старые документы."""
    items = [
        api_item("0001202012230048", "2200", "Правила перевозок грузов", "2020-12-21"),
        api_item("0001201001010001", "1", "Старый акт", "2010-01-01"),
    ]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    found = parser.search("перевозок", date_from="2015-01-01")
    assert [item.number for item in found] == ["2200"]


def test_search_uses_cache(parser):
    """Повторный поиск с теми же условиями не идёт в сеть второй раз."""
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": []})))
    parser.search("перевозка")
    before = len(parser._fake_session.calls_for("api/Documents"))
    parser.search("перевозка")
    assert len(parser._fake_session.calls_for("api/Documents")) == before


def test_search_respects_limit(parser):
    """Предел числа документов соблюдается."""
    items = [api_item(f"0001202012{i:02d}0001", str(i), f"Акт {i}", "2020-01-01") for i in range(1, 6)]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    assert len(parser.search("Акт", limit=2)) == 2


def test_find_document_rejects_number_only_match(parser):
    """Номер без даты и названия — не совпадение: чужой акт в базу не попадёт."""
    items = [api_item("0001201607040044", "259-ФЗ", "О внесении изменений в отдельные акты",
                      "2016-07-04")]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    assert parser.find_document("259-ФЗ", date_hint="2007-11-08",
                                name_hint="Устав автомобильного транспорта") is None


def test_find_document_accepts_number_with_date(parser):
    """Номер плюс совпавшая дата — уверенное совпадение."""
    items = [api_item("0001202012230048", "2200", "Об утверждении Правил перевозок грузов",
                      "2020-12-21")]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    found = parser.find_document("2200", date_hint="2020-12-21")
    assert found is not None and found.number == "2200"


def test_find_document_accepts_number_with_name(parser):
    """Номер плюс совпавшее название — тоже уверенное совпадение."""
    items = [api_item("0001202012230048", "2200", "Об утверждении Правил перевозок грузов "
                                                  "автомобильным транспортом", "2020-12-21")]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    found = parser.find_document("2200", name_hint="Правил перевозок грузов автомобильным")
    assert found is not None


def test_find_document_without_arguments(parser):
    """Без номера и названия искать нечего."""
    assert parser.find_document("", name_hint="") is None


def test_list_updates_requires_date(parser):
    """Мониторинг не начинается без понятной даты."""
    with pytest.raises(ValueError):
        parser.list_updates("позавчера")
    with pytest.raises(ValueError):
        parser.list_updates("")


def test_list_updates_accepts_russian_date_format(parser):
    """Дату можно написать и точками — она приводится к ISO."""
    items = [api_item("0001202610140033", "1344", "Свежий акт", "2026-10-14")]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    assert len(parser.list_updates("01.01.2026")) == 1


def test_list_updates_stops_on_old_document(parser):
    """Обход прекращается, как только встретился документ старше порога."""
    items = [
        api_item("0001202610140033", "1344", "Свежий акт", "2026-10-14"),
        api_item("0001202610130033", "1343", "Тоже свежий", "2026-10-13"),
        api_item("0001201001010001", "1", "Старый акт", "2010-01-01"),
    ]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    found = parser.list_updates("2026-01-01")
    assert [item.number for item in found] == ["1344", "1343"]


def test_list_updates_returns_number_of_pages_only_once(parser):
    """Одна страница API не превращается в несколько обходов."""
    items = [api_item("0001202610140033", "1344", "Свежий акт", "2026-10-14")]
    parser._fake_session.routes.insert(0, ("api/Documents", FakeResponse(payload={"items": items})))
    parser.list_updates("2026-01-01")
    assert len(parser._fake_session.calls_for("api/Documents")) == 1


# ─────────────────────────────────────────────────────────────
# База метаданных
# ─────────────────────────────────────────────────────────────


def test_db_schema_created(legal_db):
    """Схема создаётся при первом обращении и содержит нужные таблицы."""
    conn = legal_metadata.get_connection()
    try:
        legal_metadata.init_db(conn)
        names = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"legal_documents", "legal_meta"} <= names
    assert legal_db.exists()


def test_save_and_get_document(legal_db):
    """Документ сохраняется и читается обратно со всеми полями."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.save_document({
        "id": "eo:1", "title": "Правила перевозок", "number": "2200",
        "doc_date": "2020-12-21", "category": "government_decrees",
        "text_chars": 100, "extra": {"topics": ["перевозка"]},
    })
    row = legal_metadata.get_document("eo:1")
    assert row["number"] == "2200"
    assert row["text_chars"] == 100
    assert row["extra"]["topics"] == ["перевозка"]
    assert row["ocr_used"] is False


def test_save_document_requires_id(legal_db):
    """Без id документ не сохраняется — иначе запись потеряется."""
    legal_metadata.set_legal_db_path(str(legal_db))
    with pytest.raises(ValueError):
        legal_metadata.save_document({"title": "Без идентификатора"})


def test_save_document_does_not_erase_paths(legal_db):
    """Повторное сохранение без файлов не стирает уже скачанные пути."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.save_document({"id": "eo:1", "title": "Акт", "original_path": "C:/a.pdf",
                                  "text_path": "C:/a.txt", "text_chars": 50})
    legal_metadata.save_document({"id": "eo:1", "title": "Акт (уточнено)", "number": "5"})
    row = legal_metadata.get_document("eo:1")
    assert row["original_path"] == "C:/a.pdf"
    assert row["text_chars"] == 50
    assert row["title"] == "Акт (уточнено)"


def test_meta_values(legal_db):
    """Служебные значения (время последней проверки) сохраняются."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.set_meta("last_update_check", "2026-10-15 10:00:00")
    assert legal_metadata.get_meta("last_update_check") == "2026-10-15 10:00:00"
    assert legal_metadata.get_meta("нет-ключа") == ""


def test_find_by_number(legal_db):
    """Поиск по номеру и дате находит нужную запись."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.save_document({"id": "eo:1", "title": "Акт", "number": "2200",
                                  "doc_date": "2020-12-21"})
    assert legal_metadata.find_by_number("2200", "2020-12-21")["id"] == "eo:1"
    assert legal_metadata.find_by_number("2200")["id"] == "eo:1"
    assert legal_metadata.find_by_number("9999") is None


def test_list_and_search_documents(legal_db):
    """Список фильтруется по категории, поиск идёт по номеру и названию."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.save_documents([
        {"id": "eo:1", "title": "Правила перевозок грузов", "number": "2200",
         "category": "government_decrees", "original_path": "C:/a.pdf"},
        {"id": "nd:2", "title": "Гражданский кодекс. Часть вторая", "number": "14-ФЗ",
         "category": "codes", "original_path": ""},
    ])
    assert len(legal_metadata.list_documents()) == 2
    assert len(legal_metadata.list_documents(category="codes")) == 1
    assert len(legal_metadata.list_documents(only_downloaded=True)) == 1
    assert legal_metadata.search_documents("2200")[0]["id"] == "eo:1"
    # Поиск по кириллице: встроенный lower() в SQLite её не знает.
    assert legal_metadata.search_documents("гражданский")[0]["id"] == "nd:2"
    assert legal_metadata.search_documents("ГРАЖДАНСКИЙ")[0]["id"] == "nd:2"
    assert legal_metadata.search_documents("") != []


def test_delete_document(legal_db):
    """Удаление убирает запись и сообщает об этом."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.save_document({"id": "eo:1", "title": "Акт"})
    assert legal_metadata.delete_document("eo:1") is True
    assert legal_metadata.delete_document("eo:1") is False
    assert legal_metadata.get_document("eo:1") is None


def test_stats_by_category(legal_db):
    """Сводка считает документы, скачанные файлы и символы текста."""
    legal_metadata.set_legal_db_path(str(legal_db))
    legal_metadata.save_documents([
        {"id": "eo:1", "title": "Акт 1", "category": "codes", "original_path": "C:/a.pdf",
         "text_chars": 1000},
        {"id": "eo:2", "title": "Акт 2", "category": "codes", "text_chars": 500},
    ])
    data = legal_metadata.stats()
    assert data["documents"] == 2
    assert data["downloaded"] == 1
    assert data["text_chars"] == 1500
    assert data["by_category"] == {"codes": 2}
    assert data["db_path"] == str(legal_db)


def test_db_path_override(legal_db, monkeypatch):
    """Путь к базе задаётся явно и переменной окружения."""
    monkeypatch.setenv(legal_metadata.LEGAL_DB_ENV, "")
    legal_metadata.set_legal_db_path(str(legal_db))
    assert legal_metadata.legal_db_path() == str(legal_db)
    legal_metadata.set_legal_db_path(None)
    assert legal_metadata.legal_db_path() == legal_metadata.DEFAULT_LEGAL_DB_PATH


# ─────────────────────────────────────────────────────────────
# Отбор источников и скачивание
# ─────────────────────────────────────────────────────────────


def test_resolve_item_prefers_nd(parser):
    """Запись с nd превращается в документ банка «Законодательство России»."""
    document = parser.resolve_item({"id": "gk2", "title": "ГК РФ ч.2", "nd": "102039276"}, "codes")
    assert document.source == "ips"
    assert document.id == "cfg:gk2"
    assert "nd=102039276" in document.url
    assert document.extra["ips_title"].startswith("Гражданский кодекс")


def test_resolve_item_uses_eo_from_config(parser):
    """Запись с eo не ходит в поиск: метаданные берутся из списка источников."""
    document = parser.resolve_item({
        "id": "pp2200", "title": "Правила перевозок грузов", "number": "2200",
        "date": "2020-12-21", "eo": "0001202012230048", "doc_type": "Постановление",
    }, "government_decrees")
    assert document.source == "publication"
    assert document.eo_number == "0001202012230048"
    assert document.doc_date == "2020-12-21"
    assert document.file_url.endswith("/file/pdf/0001202012230048")


def test_resolve_item_marks_unavailable(parser):
    """Акт, которого нет на портале, помечается как «ручной», а не выдумывается."""
    document = parser.resolve_item({"id": "koap", "title": "КоАП РФ", "unavailable": True,
                                    "note": "нет открытого источника"}, "codes")
    assert document.source == "manual"
    assert document.extra["unavailable"] is True
    assert document.extra["note"] == "нет открытого источника"


def test_resolve_item_without_source_returns_none(parser):
    """Запись без nd, eo и query разрешить нечем, а недоступный портал не выдумывается."""
    assert parser.resolve_item({"id": "x", "title": "", "query": ""}, "codes") is None
    # Портал отвечает 404 на поиск — это не «документ найден», а «не смогли узнать».
    assert parser.resolve_item({"id": "y", "title": "Акт", "query": "Акт"}, "codes") is None


def test_download_ips_saves_html_and_text(legal_db, sources_file, tmp_path):
    """Документ банка сохраняется как HTML, текст — в TXT, метаданные обновляются."""
    routes = [("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251")))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    document = lp.LegalDocument(id="cfg:gk2", title="ГК РФ. Часть вторая", number="14-ФЗ",
                                doc_date="1996-01-26", source="ips", nd="102039276",
                                category="codes")
    result = parser.download_document(document)
    assert result.ok
    assert result.original_path.endswith(".html")
    assert result.text_path.endswith(".txt")
    assert result.text_chars > 0
    assert result.ocr_used is False
    assert os.path.exists(result.text_path)
    assert "Статья 793" in Path(result.text_path).read_text(encoding="utf-8")

    row = legal_metadata.get_document("cfg:gk2")
    assert row["text_chars"] == result.text_chars
    assert row["original_path"] == result.original_path
    assert row["checksum"]


def test_download_ips_reports_error(legal_db, sources_file, tmp_path):
    """Недоступный банк не создаёт пустой документ, а пишет ошибку."""
    routes = [("pravo.gov.ru", FakeResponse(status_code=503))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    document = lp.LegalDocument(id="cfg:gk2", title="ГК РФ", source="ips", nd="1")
    result = parser.download_document(document)
    assert not result.ok
    assert result.failed is True
    assert "Законодательство России" in result.error
    assert legal_metadata.get_document("cfg:gk2")["extra"]["last_error"]


def test_download_publication_saves_file(legal_db, sources_file, tmp_path):
    """PDF портала скачивается и лежит на диске; отсутствие текста — в отчёте."""
    routes = [("file/pdf/", FakeResponse(content=b"%PDF-1.4 mock scan"))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    document = lp.LegalDocument(id="eo:1", title="Правила перевозок", number="2200",
                                doc_date="2020-12-21", source="publication",
                                eo_number="0001202012230048")
    result = parser.download_document(document)
    assert os.path.exists(result.original_path)
    assert result.original_path.endswith(".pdf")
    # Мок — не настоящий PDF: текста нет, и это честно написано в отчёте.
    assert result.text_chars == 0
    assert result.error


def test_download_publication_without_eo(legal_db, sources_file, tmp_path):
    """Без номера опубликования скачивать нечего."""
    parser = build_parser(sources_file, tmp_path, routes=[])
    document = lp.LegalDocument(id="eo:0", title="Акт", source="publication")
    result = parser.download_document(document)
    assert not result.ok
    assert "опубликования" in result.error


def test_download_manual_document_explains_itself(legal_db, sources_file, tmp_path):
    """«Ручной» документ объясняет, как его добавить, вместо тихой пустоты."""
    parser = build_parser(sources_file, tmp_path, routes=[])
    document = lp.LegalDocument(id="cfg:koap", title="КоАП РФ", source="manual",
                                extra={"unavailable": True})
    result = parser.download_document(document)
    assert not result.ok
    assert result.unavailable is True
    assert result.failed is False, "акт до 2011 года — не ошибка скачивания"
    assert "вручную" in result.error


def test_result_flags():
    """Флаги результата различают успех, пропуск, отсутствие источника и ошибку."""
    document = lp.LegalDocument(id="x", title="Акт")
    assert lp.DownloadResult(document=document, original_path="C:/a.pdf").ok is True
    assert lp.DownloadResult(document=document, skipped=True).failed is False
    assert lp.DownloadResult(document=document, error="сеть", unavailable=True).failed is False
    assert lp.DownloadResult(document=document, error="сеть").failed is True


def test_download_all_skips_existing(legal_db, sources_file, tmp_path):
    """Уже скачанный документ не скачивается второй раз."""
    routes = [("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251")))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    first = parser.download_all(category="codes")
    assert any(item.ok for item in first)

    before = sorted(
        (name, os.path.getsize(os.path.join(parser.original_dir, name)))
        for name in os.listdir(parser.original_dir)
    )
    second = parser.download_all(category="codes")
    after = sorted(
        (name, os.path.getsize(os.path.join(parser.original_dir, name)))
        for name in os.listdir(parser.original_dir)
    )

    assert any(item.skipped for item in second), "повторный проход должен пропускать скачанное"
    assert before == after, "повторный проход не должен переписывать файлы"
    # В отчёте у пропущенного документа стоят настоящие значения из базы,
    # а не нули из свежей записи списка источников.
    skipped = [item for item in second if item.skipped and item.document.id == "cfg:gk2"]
    assert skipped and skipped[0].text_chars > 0


def test_download_all_reports_missing_document(legal_db, sources_file, tmp_path):
    """Запись, которую не удалось разрешить, попадает в отчёт как ошибка."""
    parser = build_parser(sources_file, tmp_path, routes=[])
    parser.sources["categories"]["codes"].append(
        {"id": "no_source", "short": "Акт без источника", "title": "Акт без источника",
         "number": "1", "date": "1990-01-01", "query": ""}
    )
    results = parser.download_all(category="codes")
    failed = [item for item in results if item.error]
    assert any("не найден" in item.error for item in failed)


def test_download_all_progress_callback(legal_db, sources_file, tmp_path):
    """Ход работы сообщается обработчику интерфейса."""
    routes = [("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251")))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    seen = []

    def progress(stage, done, total):
        seen.append((stage, done, total))

    parser.progress = progress
    parser.download_all(category="codes")
    assert any(stage == "download" for stage, _done, _total in seen)


def test_update_summary(legal_db, sources_file, tmp_path):
    """Мониторинг возвращает сводку и сохраняет время проверки."""
    items = [api_item("0001202610140033", "1344", "Свежий акт", "2026-10-14")]
    routes = [("api/Documents", FakeResponse(payload={"items": items})),
              ("file/pdf/", FakeResponse(content=b"%PDF-1.4 mock"))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    summary = parser.update(days=30, query="перевозка")
    assert summary["found"] >= 1
    assert summary["since"] < "2100-01-01"
    assert legal_metadata.get_meta("last_update_check")


def test_stats_reports_directories(legal_db, sources_file, tmp_path):
    """Сводка парсера показывает каталоги и объёмы."""
    parser = build_parser(sources_file, tmp_path, routes=[])
    data = parser.stats()
    assert data["documents"] == 0
    assert data["original_bytes"] == 0
    assert os.path.isdir(data["data_dir"])


def test_documents_listing_and_filter(legal_db, sources_file, tmp_path):
    """Список документов парсера фильтруется по запросу и категории."""
    parser = build_parser(sources_file, tmp_path, routes=[])
    legal_metadata.save_documents([
        {"id": "eo:1", "title": "Правила перевозок грузов", "number": "2200",
         "category": "government_decrees", "text_chars": 10},
        {"id": "nd:2", "title": "Гражданский кодекс", "number": "14-ФЗ", "category": "codes"},
    ])
    assert len(parser.documents()) == 2
    assert len(parser.documents(query="2200")) == 1
    assert len(parser.documents(category="codes")) == 1


# ─────────────────────────────────────────────────────────────
# Интерфейс (offscreen)
# ─────────────────────────────────────────────────────────────


def test_legal_base_dialog_lists_documents(legal_db, sources_file, tmp_path, qapp):
    """Диалог «Правовая база» показывает документы и сводку."""
    from ui.legal_base_dialog import LegalBaseDialog

    routes = [("pravo.gov.ru", FakeResponse(content=IPS_HTML.encode("cp1251")))]
    parser = build_parser(sources_file, tmp_path, routes=routes)
    legal_metadata.save_documents([
        {"id": "eo:1", "title": "Правила перевозок грузов", "number": "2200",
         "category": "government_decrees", "text_chars": 1234},
    ])
    dialog = LegalBaseDialog(parser=parser)
    try:
        assert dialog.table.rowCount() == 1
        assert "Правила перевозок грузов" in dialog.table.item(0, 0).text()
        assert dialog.categories_list.count() == 2
        assert "Документов в базе" in dialog.summary_label.text()
    finally:
        dialog.deleteLater()


def test_legal_base_dialog_filters(legal_db, sources_file, tmp_path, qapp):
    """Фильтр «только скачанные» сокращает список, поиск — тоже."""
    from ui.legal_base_dialog import LegalBaseDialog

    parser = build_parser(sources_file, tmp_path, routes=[])
    legal_metadata.save_documents([
        {"id": "eo:1", "title": "Правила перевозок грузов", "number": "2200",
         "original_path": "C:/a.pdf", "text_chars": 10},
        {"id": "eo:2", "title": "Другой акт", "number": "1"},
    ])
    dialog = LegalBaseDialog(parser=parser)
    try:
        assert dialog.table.rowCount() == 2
        dialog.downloaded_only.setChecked(True)
        assert dialog.table.rowCount() == 1
        dialog.search_edit.setText("2200")
        dialog.refresh()
        assert dialog.table.rowCount() == 1
    finally:
        dialog.deleteLater()


def test_legal_settings_section(legal_db, sources_file, tmp_path, qapp):
    """Раздел настроек собирается, показывает сводку и хранит параметры RAG."""
    from ui import legal_settings

    parser = build_parser(sources_file, tmp_path, routes=[])
    section = legal_settings.LegalSettingsSection(parser=parser)
    try:
        assert "Документов" in section.status_label.text()
        assert section.top_k_spin.value() == legal_settings.DEFAULT_LEGAL_SETTINGS["top_k"]
        assert section.chunk_spin.value() == legal_settings.DEFAULT_LEGAL_SETTINGS["chunk_size"]
    finally:
        section.deleteLater()


def test_normalize_legal_settings_defaults():
    """Пустые или негодные настройки заменяются значениями по умолчанию."""
    from ui import legal_settings

    assert legal_settings.normalize_legal_settings(None) == legal_settings.DEFAULT_LEGAL_SETTINGS
    assert legal_settings.normalize_legal_settings({"top_k": "нет"})["top_k"] == 5
    assert legal_settings.normalize_legal_settings({"auto_index": 1})["auto_index"] is True


@pytest.mark.parametrize("raw, expected", [
    ({"top_k": 100}, 20),
    ({"top_k": 0}, 1),
    ({"chunk_size": 100}, 300),
    ({"chunk_size": 9000}, 4000),
    ({"chunk_overlap": 5000}, 450),
])
def test_normalize_legal_settings_limits(raw, expected):
    """Границы значений соблюдаются: слишком большие и малые подтягиваются."""
    from ui import legal_settings

    result = legal_settings.normalize_legal_settings(raw)
    assert result[list(raw)[0]] == expected


def test_settings_json_snippet():
    """Фрагмент settings.json содержит раздел legal с рабочими значениями."""
    from ui import legal_settings

    snippet = json.loads(legal_settings.settings_json_snippet())
    assert snippet["legal"]["chunk_size"] == 900
    assert snippet["legal"]["chunk_overlap"] == 100
