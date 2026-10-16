# -*- coding: utf-8 -*-
"""
Живая проверка окна Экспедиторства после Этапа 6 (диагностика, не тест).

Строит НАСТОЯЩЕЕ окно в offscreen-режиме и печатает карту виджетов: шапка,
селектор типа, кнопки панели действий, шесть вкладок, сайдбар, статус-бар с
тремя зонами и прогресс-бар. Так проверяется то, что нельзя проверить
импортом: что `_init_ui` действительно создал ВСЕ виджеты и что сигналы
подключены (иначе кнопки были бы «мёртвыми»).

Запуск:
    $env:QT_QPA_PLATFORM = "offscreen"
    python tools/_smoke_main_window.py

Выход: код 0, если окно собралось и все ожидаемые виджеты на месте.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

#: Виджеты, которые обязана создать раскладка (`LayoutMixin._init_ui`).
EXPECTED_WIDGETS = (
    "selector",
    "btn_create_contract", "btn_exit",
    "btn_recognize", "btn_import_documents", "btn_cancel",
    "btn_save_db", "btn_open_db", "btn_settings",
    "tabs", "side_nav", "progress_bar",
    "driver_tab", "customer_tab", "carrier_tab",
    "vehicles_tab", "trailer_tab", "contract_tab",
    "statusBar",
)

#: Зоны статус-бара и служебные поля, которые заводит `_build_status_bar`.
EXPECTED_ATTRS = (
    "_status_tab_label", "_status_fields_label", "_status_saved_label",
    "_status_timer", "_last_save_at",
    "settings_service", "contract_generator", "thread_pool",
    "recognition_task", "system_theme_watcher",
)


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # Ключа GigaChat в offscreen-прогоне нет: диалог показывать некому.
    from ui.main_window import MainWindow
    MainWindow._init_gigachat_client = lambda self, show_dialog=True: False
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))

    window = MainWindow()
    window.show()
    app.processEvents()

    problems = []

    for name in EXPECTED_WIDGETS:
        if not hasattr(window, name):
            problems.append(f"нет виджета {name}")

    for name in EXPECTED_ATTRS:
        if not hasattr(window, name):
            problems.append(f"нет атрибута {name}")

    if window.tabs.count() != 6:
        problems.append(f"вкладок {window.tabs.count()}, ожидалось 6")

    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    expected_titles = [
        "Перевозчик", "Водитель", "Тягач и полуприцеп",
        "Перевозимые авто", "Договор", "Заказчик",
    ]
    if titles != expected_titles:
        problems.append(f"порядок вкладок {titles} != {expected_titles}")

    if window.side_nav.count() != 6:
        problems.append(f"пунктов сайдбара {window.side_nav.count()}, ожидалось 6")

    # Сигналы должны быть ПОДКЛЮЧЕНЫ, иначе кнопки «мёртвые».
    checks = (
        ("btn_create_contract", "clicked"),
        ("btn_recognize", "clicked"),
        ("btn_save_db", "clicked"),
        ("btn_open_db", "clicked"),
        ("btn_settings", "clicked"),
        ("btn_exit", "clicked"),
        ("selector", "contract_type_selected"),
        ("tabs", "currentChanged"),
        ("side_nav", "navigate"),
    )
    for attr, signal in checks:
        widget = getattr(window, attr)
        receivers = widget.receivers(getattr(widget, signal))
        if not receivers:
            problems.append(f"{attr}.{signal} ни к чему не подключён")

    print("окно:", window.windowTitle())
    print("вкладки:", titles)
    print("пунктов сайдбара:", window.side_nav.count())
    print("подключённых сигналов проверено:", len(checks))

    window.force_close()
    app.processEvents()

    if problems:
        print("ПРОБЛЕМЫ:")
        for problem in problems:
            print("   -", problem)
        return 1
    print("OK: окно собрано, все виджеты и сигналы на месте")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
