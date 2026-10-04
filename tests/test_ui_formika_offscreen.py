#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Оффскрин-прогон окна Формики и точки входа (ЭТАП 3.1.B.7).

Здесь проверяется не логика вкладок (она в test_ui_formika_data.py,
test_ui_formika_tabs.py и test_ui_formika_window.py), а ЖИЗНЕННЫЙ ЦИКЛ:

  * окно показывается, все четыре действия проходят подряд — «Распознать
    данные», «Создать договор», «Очистить форму», закрытие;
  * после закрытия окна не остаётся ни виджетов, ни «висящих» C++ объектов,
    из-за которых в логе появляются «QObject::...» и «RuntimeError:
    wrapped C/C++ object of type ... has been deleted»;
  * `main.py` запускается целиком в offscreen-режиме и завершается сам
    (QTimer), с кодом выхода 0 и без исключений.

Почему это отдельный файл: обычные тесты окон дёргают слоты напрямую.
Здесь окно показывается и живёт внутри цикла событий — именно так
проявляются циклы ссылок Python ↔ Qt, из-за которых процесс падает при
выходе (грабли ЭТАПА 2B, шаг 2B.7).

Две тонкости Qt, без которых проверки на утечку дают ложный результат:

  * `QApplication.processEvents()` НЕ обрабатывает отложенные удаления
    (`DeferredDelete`): события `deleteLater()` доходят только в настоящем
    цикле событий. Поэтому после закрытия окна очередь прокручивается
    `QTest.qWait()`, иначе уничтоженные окна выглядят как утечка;
  * пока на окно держит ссылку сам тест (список созданных окон), окно
    живо со всеми 376 виджетами. Ссылку надо снять, иначе рост числа
    виджетов — артефакт теста, а не дефект продукта.

Qt — в offscreen-режиме, сеть не трогается: клиент GigaChat подменяется
заглушкой. Все данные синтетические, реальных ПДн нет.
"""

import gc
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.contracts.formika.generator import FormikaGenerator  # noqa: E402
from ui.windows.formika import FormikaWindow  # noqa: E402

#: Сколько миллисекунд крутить настоящий цикл событий после закрытия окна.
EVENT_LOOP_MS = 100

#: Признаки проблемного Qt-объекта в выводе Qt.
QT_TROUBLE_MARKERS = ("QObject::", "wrapped C/C++ object")

#: Сколько виджетов у одного свежего окна Формики (шесть вкладок).
WIDGETS_PER_WINDOW = 376

#: Ответ модели, которого хватает, чтобы заполнить вкладки Формики.
#: Ключи — ровно те, что обещает схема промпта (core/prompts/formika.py):
#: блок «tractor» идёт БЕЗ префикса tractor_, «trailer» — без trailer_.
RECOGNITION_ANSWER = {
    "contract": {
        "number": "ФМ-2026-1",
        "date": "2026-09-23",
        "route": "Мурманск - Пятигорск",
        "amount": 122000.0,
        "payment_days": 10,
        "loading_address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
        "loading_plan_date": "2026-09-24",
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
    },
    "vehicles": [{"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608"}],
    "driver": {
        "full_name": "Иванов Иван Иванович",
        "passport_series": "1822",
        "passport_number": "926830",
    },
    "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                "vehicle_type": "Седельный тягач"},
    "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18"},
}


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    """Единственный QApplication на модуль: второй создать нельзя."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_messages(monkeypatch):
    """
    Глушит модальные диалоги: offscreen-прогон не должен ждать клика.

    :return: словарь со списками текстов information / warning / critical.
    """
    seen = {"information": [], "warning": [], "critical": []}

    def recorder(kind):
        def _record(parent, title, text, *args, **kwargs):
            seen[kind].append(text)
            return QMessageBox.Ok
        return staticmethod(_record)

    for kind in seen:
        monkeypatch.setattr(QMessageBox, kind, recorder(kind))

    # Диалог проверки данных (QMessageBox.exec_) по умолчанию отвечает
    # «Исправить»: тест, которому нужен «Создать договор», берёт фикстуру
    # confirm_creation.
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    return seen


@pytest.fixture
def confirm_creation(monkeypatch):
    """Диалог проверки данных отвечает «Создать договор» (QMessageBox.Yes)."""
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Yes)


@pytest.fixture
def qt_warnings():
    """
    Ловит сообщения Qt на время теста («QObject::...» и подобные).

    :return: список строк вида «ТИП: текст».
    """
    captured = []

    def handler(msg_type, context, message):
        captured.append(f"{QtMsgType(msg_type).name}: {message}")

    previous = qInstallMessageHandler(handler)
    yield captured
    qInstallMessageHandler(previous)


@pytest.fixture
def window_factory(qt_app):
    """
    Создаёт окна Формики и гарантированно убирает их после теста.

    Окно закрывается по-настоящему (force_close) и уничтожается
    (deleteLater). Ссылки снимаются: пока список созданных окон жив,
    окно и его 376 виджетов остаются в QApplication.allWidgets(), и
    проверка на утечку в соседнем тесте стала бы ложной.
    """
    created = []

    def _make() -> FormikaWindow:
        win = FormikaWindow()
        created.append(win)
        return win

    yield _make

    while created:
        win = created.pop()
        win.thread_pool.waitForDone(5000)
        win.force_close()
        win.deleteLater()
        del win
    settle(qt_app)


def settle(qt_app, ms: int = EVENT_LOOP_MS) -> None:
    """
    Доводит очередь событий до конца, включая отложенные удаления.

    `processEvents()` не обрабатывает DeferredDelete, поэтому после
    deleteLater() нужен настоящий цикл событий — QTest.qWait его крутит.
    """
    gc.collect()
    qt_app.processEvents()
    if ms:
        QTest.qWait(ms)
    qt_app.processEvents()


class FakeClient:
    """Заглушка GigaChat: в сеть не ходит, отвечает заранее данным словарём."""

    def __init__(self, answer=None):
        self.answer = answer if answer is not None else {}
        self.calls = []

    def recognize_text(self, text, prompt=None):
        self.calls.append({"text": text, "prompt": prompt})
        return self.answer


def _fill_all_tabs(win, cars: int = 1) -> None:
    """Заполняет шесть вкладок данными, которых хватает валидатору Формики."""
    win.customer_tab.fill_data({"number": "ФМ-2026-1", "date": "2026-09-23"})
    win.cargo_tab.fill_data({"vehicles": [
        {"brand_model": f"МОДЕЛЬ {number}",
         "vin": f"EC3TEUMB0T000{number:04d}"}
        for number in range(1, cars + 1)
    ]})
    win.route_tab.fill_data({
        "route": "Мурманск - Пятигорск",
        "loading_address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
        "loading_plan_date": "2026-09-24",
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
    })
    win.driver_tab.fill_data({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "1822",
        "passport_number": "926830",
    })
    win.vehicle_tab.fill_data({
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_type": "Седельный тягач",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
    })
    win.price_tab.fill_data({"amount": 122000.0, "payment_days": 10})


def _answer_recognition(win, data) -> None:
    """
    Отдаёт окну ответ модели так же, как это сделала бы задача распознавания.

    Своя копия помощника из test_ui_formika_window.py: импортировать оттуда
    нельзя — один тестовый модуль не должен зависеть от другого, иначе
    прогон одного файла ломается при правке соседнего.
    """
    from ui.windows.formika.window import RecognitionTask

    task = RecognitionTask(FakeClient(data), "текст", prompt="промпт")
    win.recognition_task = task
    win._on_recognition_finished(data, task=task)


# ─────────────────────────────────────────────────────────────
# 1. Полный проход по действиям окна в цикле событий
# ─────────────────────────────────────────────────────────────

def test_full_action_pass_runs_offscreen(
    qt_app, quiet_messages, confirm_creation, monkeypatch, window_factory
):
    """
    Показ окна → «Распознать данные» → «Создать договор» → «Очистить форму» → закрытие.

    Договор пишется в рабочую папку output/ — файл за тестом удаляется,
    чтобы прогон не оставлял мусора среди реальных договоров.
    """
    client = FakeClient(RECOGNITION_ANSWER)
    monkeypatch.setattr(FormikaWindow, "_ensure_gigachat", lambda self: client)

    created = []
    monkeypatch.setattr(
        FormikaWindow, "_show_contract_created",
        lambda self, path: created.append(path),
    )

    win = window_factory()
    win.show()
    settle(qt_app)
    assert win.isVisible() is True

    # ── «Распознать данные»: сигнал вкладки → пул потоков → заполнение ──
    win.route_tab.recognition_panel.text_edit.setPlainText(
        "Договор-заявка ФМ-2026-1, Мурманск - Пятигорск"
    )
    win.route_tab.recognition_panel.btn_recognize.click()

    assert win.recognition_task is not None, "задача распознавания не создана"
    win.thread_pool.waitForDone(5000)
    settle(qt_app)

    assert len(client.calls) == 1
    assert client.calls[0]["text"].startswith("Договор-заявка ФМ-2026-1")
    assert win.customer_tab.get_data()["number"] == "ФМ-2026-1"
    assert win.cargo_tab.get_data()["vehicles"]
    assert win.recognition_task is None, "состояние задачи не сброшено"

    # Блок исполнителя распознавание раскладывает по вкладке «ТС» само:
    # ключи промпта (brand_model, plate_number) переводит карта ключей
    # _TRACTOR_KEYS / _TRAILER_KEYS.
    vehicle = win.vehicle_tab.get_data()
    assert vehicle["tractor_brand"] == "Foton Auman", vehicle
    assert vehicle["tractor_plate"] == "O844XY196", vehicle
    assert vehicle["trailer_brand"] == "YANGMINDA", vehicle

    # ── «Создать договор»: кнопка шапки → валидатор → генератор ──
    win.btn_create_contract.click()
    settle(qt_app)

    assert len(created) == 1, "договор не создан"
    contract_path = Path(created[0])
    assert contract_path.exists()
    assert contract_path.name.startswith("Договор-заявка_Формика_")

    # ── «Очистить форму»: чистится только вкладка-отправитель ──
    win.driver_tab.btn_clear_form.click()
    settle(qt_app)

    assert win.driver_tab.get_data()["full_name"] == ""
    assert win.customer_tab.get_data()["number"] == "ФМ-2026-1"
    assert win.cargo_tab.get_data()["vehicles"]

    # ── Закрытие: hide() не теряет данные, force_close() закрывает ──
    win.close()
    assert win.isVisible() is False
    assert win.customer_tab.get_data()["number"] == "ФМ-2026-1"

    win.force_close()
    assert win.isVisible() is False
    assert win._force_close is True

    # За собой убираем: тестовый договор не должен остаться в output/.
    contract_path.unlink(missing_ok=True)

    assert contract_path.exists() is False


def test_recognition_fills_tractor_fields(
    qt_app, quiet_messages, window_factory
):
    """
    Распознанный тягач попадает на вкладку «ТС».

    Регресс на дефект: схема промпта Формики отдаёт блок «tractor» с
    ключами БЕЗ префикса (brand_model / plate_number / vehicle_type), а
    вкладка ждёт tractor_brand / tractor_plate / tractor_type.
    """
    win = window_factory()
    win.show()

    _answer_recognition(win, RECOGNITION_ANSWER)
    settle(qt_app)

    vehicle = win.vehicle_tab.get_data()
    assert vehicle["tractor_brand"] == "Foton Auman", vehicle
    assert vehicle["tractor_plate"] == "O844XY196", vehicle
    assert vehicle["tractor_type"] == "Седельный тягач", vehicle
    # Прицеп раскладывался и раньше — проверяем, что не сломался.
    assert vehicle["trailer_brand"] == "YANGMINDA", vehicle
    assert vehicle["trailer_plate"] == "71ABF18", vehicle


def test_recognition_alone_is_enough_to_create_contract(
    qt_app, quiet_messages, window_factory
):
    """
    Данных распознавания хватает валидатору: ошибок «тягач» больше нет.

    Регресс на дефект: пустой блок тягача давал ошибки «Не заполнена марка
    тягача» и «Не заполнен госномер тягача», и договор создать было нельзя.
    Замечания (не ошибки) валидатора договор не блокируют — например, про
    неполные данные водителя: их в этом ответе модели и нет.
    """
    from core.contracts.formika.validator import FormikaValidator

    win = window_factory()
    win.show()

    _answer_recognition(win, RECOGNITION_ANSWER)
    settle(qt_app)

    report = FormikaValidator().check(win._collect_data())

    assert report.errors == [], report.errors
    assert not [error for error in report.errors if "тягач" in error.lower()]
    # Ошибок нет — значит, диалог проверки не остановит создание договора.
    assert report.has_errors is False


def test_generation_in_offscreen_run_goes_to_test_dir(
    qt_app, quiet_messages, confirm_creation, monkeypatch, window_factory, work_dir
):
    """
    Полный путь «Создать договор», но папка вывода — тестовая.

    Так проверяется, что генерация действительно пишет DOCX, а рабочая
    output/ при прогоне тестов не пополняется.
    """
    output_dir = work_dir / "formika_offscreen_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        FormikaGenerator, "default_output_dir",
        classmethod(lambda cls: str(output_dir)),
    )
    monkeypatch.setattr(
        FormikaWindow, "_show_contract_created", lambda self, path: None
    )

    win = window_factory()
    win.show()
    _fill_all_tabs(win, cars=2)
    settle(qt_app)

    win.btn_create_contract.click()
    settle(qt_app)

    try:
        produced = sorted(p.name for p in output_dir.iterdir())
        assert len(produced) == 1, f"ожидался один договор, получено: {produced}"
        assert produced[0].startswith("Договор-заявка_Формика_")
    finally:
        for path in output_dir.iterdir():
            path.unlink(missing_ok=True)
        output_dir.rmdir()


# ─────────────────────────────────────────────────────────────
# 2. Ни виджетов, ни C++ объектов после закрытия
# ─────────────────────────────────────────────────────────────

def test_widgets_do_not_grow_after_close_all_windows(
    qt_app, quiet_messages, monkeypatch
):
    """
    После closeAllWindows список виджетов возвращается к исходному.

    Окно создаётся и уничтожается прямо здесь, без фикстуры: её список
    созданных окон держал бы ссылку и «утечка» была бы артефактом теста.
    """
    from PyQt5 import sip

    monkeypatch.setattr(
        FormikaWindow, "_ensure_gigachat",
        lambda self: FakeClient(RECOGNITION_ANSWER),
    )

    settle(qt_app)
    baseline = set(qt_app.allWidgets())

    win = FormikaWindow()
    win.show()
    win.route_tab.recognition_panel.text_edit.setPlainText("текст для распознавания")
    win.route_tab.recognition_panel.btn_recognize.click()
    win.thread_pool.waitForDone(5000)
    settle(qt_app)

    assert win.tabs.count() == 6
    assert win.thread_pool.waitForDone(5000) is True
    assert win.recognition_task is None, "состояние задачи не сброшено"

    win.force_close()
    qt_app.closeAllWindows()

    # Ссылку снимаем ДО замера: живую ссылку держал бы только тест.
    win.deleteLater()
    del win

    settle(qt_app, ms=0)
    assert len(qt_app.allWidgets()) > len(baseline), "окно не было создано"

    settle(qt_app)
    after = set(qt_app.allWidgets())

    leaked = [type(widget).__name__ for widget in after - baseline]
    assert leaked == [], f"после закрытия остались виджеты: {leaked}"

    deleted = [type(widget).__name__
               for widget in qt_app.allWidgets() if sip.isdeleted(widget)]
    assert deleted == [], f"обёртки удалённых C++ объектов: {deleted}"


def test_repeated_open_and_close_does_not_accumulate_widgets(
    qt_app, quiet_messages, monkeypatch, work_dir
):
    """
    Пять окон подряд: число виджетов не растёт от прогона к прогону.

    Это и есть проверка «ни один C++ объект не остался висеть»: если бы
    окно или его вкладки не уничтожались, счётчик рос бы линейно.
    """
    monkeypatch.setattr(
        FormikaWindow, "_show_contract_created", lambda self, path: None
    )
    monkeypatch.setattr(
        FormikaGenerator, "default_output_dir",
        classmethod(lambda cls: str(work_dir)),
    )

    settle(qt_app)
    baseline = len(qt_app.allWidgets())
    counts = []

    for _ in range(5):
        win = FormikaWindow()
        win.show()
        settle(qt_app, ms=0)

        assert win.tabs.count() == 6
        assert len(qt_app.allWidgets()) >= baseline + WIDGETS_PER_WINDOW

        win.force_close()
        win.deleteLater()
        del win
        settle(qt_app)

        counts.append(len(qt_app.allWidgets()))

        for leftover in work_dir.glob("Договор-заявка_Формика_*.docx"):
            leftover.unlink(missing_ok=True)

    assert counts == [baseline] * 5, f"виджеты накапливаются: {counts}"


def test_window_quits_cleanly_inside_event_loop(
    qt_app, quiet_messages, monkeypatch
):
    """
    Окно переживает полный цикл «показать → живой цикл событий → закрыть».

    Проверяется то, ради чего затевался оффскрин-прогон: окно работает в
    настоящем цикле событий (QTest.qWait, а не только processEvents) и после
    закрытия не оставляет сообщений Qt о проблемных объектах, а пул потоков
    останавливается — поток, переживший окно, роняет процесс при выходе.

    Окно закрывается ПОСЛЕ выхода из цикла событий: закрытие изнутри цикла
    удаляет C++ объект (closeEvent → accept → delete), и обращение к нему
    после qWait дало бы «wrapped C/C++ object ... has been deleted».
    """
    captured = []

    def handler(msg_type, context, message):
        captured.append(message)

    monkeypatch.setattr(
        FormikaWindow, "_ensure_gigachat",
        lambda self: FakeClient(RECOGNITION_ANSWER),
    )

    previous = qInstallMessageHandler(handler)
    try:
        win = FormikaWindow()
        win.show()

        win.route_tab.recognition_panel.text_edit.setPlainText("текст")
        win.route_tab.recognition_panel.btn_recognize.click()

        # Настоящий цикл событий: сигналы из пула приходят именно сюда.
        QTest.qWait(200)

        assert win.isVisible() is True
        assert win.thread_pool.waitForDone(5000) is True
        assert win.customer_tab.get_data()["number"] == "ФМ-2026-1"

        win.force_close()
        assert win.isVisible() is False
        assert win._force_close is True

        win.deleteLater()
        del win
        QTest.qWait(EVENT_LOOP_MS)
    finally:
        qInstallMessageHandler(previous)

    trouble = [line for line in captured
               if any(marker in line for marker in QT_TROUBLE_MARKERS)]
    assert trouble == [], f"Qt сообщил о проблемных объектах: {trouble}"


# ─────────────────────────────────────────────────────────────
# 3. Точка входа: main.py целиком, offscreen, с самозавершением
# ─────────────────────────────────────────────────────────────

#: Программа запуска main.py: диалог выбора типа подменяется, выход — по
#: таймеру. Пишется во временную папку, а не рядом с тестами.
#:
#: Выход выполняется ТЕМ ЖЕ путём, что у кнопки «Выход»: WindowManager.close_all()
#: и только потом app.quit() (main._do_exit). Просто app.quit() с открытым
#: окном Формики роняет процесс по access violation при завершении
#: интерпретатора — грабли шага 2B.7 (цикл ссылок Python ↔ Qt живёт, пока
#: окно не закрыто по-настоящему).
#:
#: QApplication подменяется подклассом, который ставит таймер выхода в
#: момент входа в цикл событий. Иначе не выходит:
#:   * QTimer.singleShot ДО создания QApplication даёт «QObject::startTimer:
#:     Timers can only be used with threads started with QThread» и не
#:     срабатывает никогда — приложение висит до таймаута теста;
#:   * обернуть QApplication.exec_ снаружи нельзя: это sip-слот
#:     (sip.methoddescriptor), сохранить и вызвать оригинал не получается.
LAUNCH_SCRIPT = '''
# -*- coding: utf-8 -*-
"""Оффскрин-запуск main.py: тип «Формика» выбирается без диалога."""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtCore import QTimer
import PyQt5.QtWidgets as QtWidgets
from PyQt5.QtWidgets import QApplication, QDialog

import ui.contract_picker as picker

# Диалог выбора типа не показываем: offscreen его не отрисовать, а ждать
# клика нельзя. Возвращаем «Формика» так же, как это сделал бы пользователь.
def _auto_accept(self):
    self.select_type("formika")
    return QDialog.Accepted

picker.ContractPickerDialog.exec_ = _auto_accept

import main

# Менеджер окон прячется в замыканиях главной функции, а выход обязан идти
# через него: close_all() закрывает окна по-настоящему. Запоминаем его,
# обернув фабрику приёмника сигналов.
_manager = {}

_original_receiver = main._make_signals_receiver


def _capturing_receiver(manager, app):
    _manager["value"] = manager
    return _original_receiver(manager, app)


main._make_signals_receiver = _capturing_receiver


def _exit_like_user():
    """Выход кнопкой «Выход»: close_all() и затем завершение цикла событий."""
    main._do_exit(_manager.get("value"), QApplication.instance())


class StoppingApplication(QApplication):
    """QApplication, который сам завершает работу через 3 секунды."""

    def exec_(self):
        # Таймер ставится здесь: QApplication уже существует, и таймер
        # привязан к его потоку — значит, сработает.
        QTimer.singleShot(3000, _exit_like_user)
        return super().exec_()


# main.py берёт QApplication из модуля в момент вызова, поэтому подмена
# видна и ему.
QtWidgets.QApplication = StoppingApplication

sys.exit(main.main())
'''


@pytest.fixture(scope="module")
def launch_script_path(tmp_path_factory):
    """
    Кладёт программу запуска во временную папку и убирает её после прогона.

    Фикстура модульная (как и qt_app): функция в соседнем тесте не должна
    зависеть от порядка тестов внутри модуля.
    """
    directory = tmp_path_factory.mktemp("formika_offscreen")
    path = directory / "_formika_offscreen_launch.py"
    path.write_text(LAUNCH_SCRIPT, encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


def test_main_py_runs_offscreen_and_exits_cleanly(launch_script_path, project_root):
    """
    `python main.py` в offscreen-режиме: код выхода 0 и никаких исключений.

    Приложение завершается само по QTimer, поэтому тест не зависит от
    интерактивного выбора типа договора. Вывод процесса читается целиком:
    это единственная проверка, которая видит сообщения Qt из C++ без
    посредничества Python.
    """
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(project_root)

    try:
        result = subprocess.run(
            [sys.executable, str(launch_script_path)],
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("main.py не завершился сам за 120 с — QTimer не сработал")

    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, (
        f"main.py завершился с кодом {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    trouble = [line for line in combined.splitlines()
               if any(marker in line for marker in QT_TROUBLE_MARKERS)]
    assert trouble == [], f"Qt сообщил о проблемных объектах: {trouble}"

    assert "Traceback" not in combined, f"исключение при запуске:\n{combined}"
    assert "Критическая ошибка при запуске" not in combined, combined
