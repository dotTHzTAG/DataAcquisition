from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout


class LogDialog(QDialog):
    def __init__(self, log_path: Path, parent=None):
        super().__init__(parent)
        self.log_path = log_path
        self.setWindowTitle("Application Logs")
        self.resize(800, 450)
        self.viewer = QPlainTextEdit(self)
        self.viewer.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.viewer)

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)

    def refresh(self) -> None:
        text = self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else ""
        self.viewer.setPlainText(text)
        self.viewer.verticalScrollBar().setValue(self.viewer.verticalScrollBar().maximum())
