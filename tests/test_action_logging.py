"""Глобальный журнал кнопок работает в главном окне и поздних диалогах."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout

from ui.action_logging import install_action_logging


def test_logs_each_click_once_and_hides_dynamic_text(caplog):
    app = QApplication.instance() or QApplication([])
    tracker = install_action_logging(app)
    assert install_action_logging(app) is tracker

    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    known = QPushButton("Сохранить")
    private = QPushButton("Сохранить для Иванова +7 999 123-45-67")
    layout.addWidget(known)
    layout.addWidget(private)
    dialog.show()
    app.processEvents()

    with caplog.at_level("INFO", logger="ui.action_logging"):
        known.click()
        private.click()

    messages = [record.getMessage() for record in caplog.records
                if record.name == "ui.action_logging" and "Нажата кнопка:" in record.getMessage()]
    assert len(messages) == 2
    assert "Сохранить | окно=QDialog" in messages[0]
    assert "QPushButton | окно=QDialog" in messages[1]
    assert all("Иванова" not in message and "123-45-67" not in message for message in messages)
    dialog.close()
