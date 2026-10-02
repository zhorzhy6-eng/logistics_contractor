"""Поведение плановых дат и времени в условиях договора."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QPointF, Qt, QDate, QTime
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication

from ui import theme
from ui.tabs.contract_tab import ContractTab


def wheel_event():
    return QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )


def test_plan_fields_have_calendar_and_ignore_mouse_wheel():
    app = QApplication.instance() or QApplication([])
    tab = ContractTab()

    for field in (tab.loading_plan_date, tab.unloading_plan_date):
        assert field.calendarPopup()
        assert field.calendarWidget() is not None
        assert field.styleSheet() == ""
        field.setDate(QDate(2026, 9, 27))
        event = wheel_event()
        field.wheelEvent(event)
        assert not event.isAccepted()
        assert field.date() == QDate(2026, 9, 27)

    for field in (tab.loading_plan_time_from, tab.loading_plan_time_to):
        assert field.styleSheet() == ""
        field.setTime(QTime(8, 30))
        event = wheel_event()
        field.wheelEvent(event)
        assert not event.isAccepted()
        assert field.time() == QTime(8, 30)

    assert app is not None


def test_price_fields_do_not_change_on_wheel_and_use_normal_style():
    app = QApplication.instance() or QApplication([])
    tab = ContractTab()

    assert tab.carrier_type.styleSheet() == ""
    assert tab.vat_rate.styleSheet() == ""
    original_type = tab.carrier_type.currentIndex()
    original_price = tab.price_input.value()

    for field in (tab.carrier_type, tab.price_input):
        event = wheel_event()
        field.wheelEvent(event)
        assert not event.isAccepted()

    assert tab.carrier_type.currentIndex() == original_type
    assert tab.price_input.value() == original_price

    tab.carrier_type.setCurrentIndex(2)
    tab.carrier_type.setCurrentIndex(0)
    assert tab.vat_rate.styleSheet() == ""
    assert app is not None


def test_recognition_area_and_removal_buttons_are_visible():
    app = QApplication.instance() or QApplication([])
    tab = ContractTab()

    assert tab.recognition_panel.text_edit.minimumHeight() >= 160
    assert tab.btn_add_loading.objectName() == "secondary"
    assert tab.btn_remove_loading.objectName() == "danger"
    assert tab.btn_add_unloading.objectName() == "secondary"
    assert tab.btn_remove_unloading.objectName() == "danger"
    assert tab.btn_remove_loading.minimumHeight() == tab.btn_add_loading.minimumHeight()
    assert app is not None


def test_multiline_fields_keep_readable_height_with_application_theme():
    app = QApplication.instance() or QApplication([])
    previous_stylesheet = app.styleSheet()
    app.setStyle("Fusion")
    theme.apply_theme(app)
    try:
        tab = ContractTab()
        tab.resize(1200, 900)
        tab.show()
        app.processEvents()

        assert tab.recognition_panel.text_edit.height() >= 160
        assert tab.special_conditions.height() == 200
        assert tab.special_conditions.viewport().height() > 150
        tab.close()
    finally:
        app.setStyleSheet(previous_stylesheet)
