from __future__ import annotations

import json
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from catx.core.paths import PROFILE_DATABASE, UI_DIR
from catx.repositories.hdf5_file import Hdf5FileRepository


class ProfileManagerDialog(QDialog):
    SETTINGS_ORGANIZATION = "Menlo Systems"
    SETTINGS_APPLICATION = "Data Acquisition"

    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi(str(UI_DIR / "profile_manager.ui"), self)
        self.database_path = PROFILE_DATABASE
        self.users: list[dict[str, str]] = []
        self.systems: list[dict[str, str]] = []
        self.selection: dict[str, dict[str, str]] = {}
        self._connect_signals()
        self._configure_tab_order()
        self._load_database(self.database_path)

    def _connect_signals(self) -> None:
        self.pushButton_load.clicked.connect(self._choose_database)
        self.comboBox_user.currentIndexChanged.connect(self._show_user)
        self.comboBox_spectrometer.currentIndexChanged.connect(self._show_system)
        self.comboBox_user.activated.connect(
            lambda: self.tabWidget.setCurrentWidget(self.user)
        )
        self.comboBox_spectrometer.activated.connect(
            lambda: self.tabWidget.setCurrentWidget(self.spectrometer)
        )
        self.pushButton_user_new.clicked.connect(self._clear_user)
        self.pushButton_user_add.clicked.connect(self._add_user)
        self.pushButton_user_remove.clicked.connect(self._remove_user)
        self.pushButton_sys_new.clicked.connect(self._clear_system)
        self.pushButton_sys_add.clicked.connect(self._add_system)
        self.pushButton_sys_remove.clicked.connect(self._remove_system)
        self.pushButton_apply.clicked.connect(self._apply)
        self.pushButton_exit.clicked.connect(self.reject)

    def _configure_tab_order(self) -> None:
        self.setTabOrder(self.lineEdit_sys_model, self.lineEdit_sys_id)
        self.setTabOrder(self.lineEdit_sys_id, self.lineEdit_sys_manufacturer)
        self.setTabOrder(self.lineEdit_sys_manufacturer, self.lineEdit_sys_note)

    def _repository(self) -> Hdf5FileRepository:
        return Hdf5FileRepository(self.database_path)

    def _load_database(self, path: Path) -> None:
        self.database_path = path
        repository = self._repository()
        self.users = repository.load_records("users")
        self.systems = repository.load_records("spectrometers")
        self.lineEdit_preset_file.setText(path.name)
        self.lineEdit_preset_file.setToolTip(str(path))
        self._refresh_combos()

    def _choose_database(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load user and spectrometer data", str(self.database_path.parent), "HDF5 Files (*.h5 *.hdf5)"
        )
        if filename:
            self._load_database(Path(filename))

    def _refresh_combos(self) -> None:
        self.comboBox_user.blockSignals(True)
        self.comboBox_spectrometer.blockSignals(True)
        self.comboBox_user.clear()
        self.comboBox_spectrometer.clear()
        self.comboBox_user.addItems(item.get("name", "") for item in self.users)
        self.comboBox_spectrometer.addItems(item.get("model", "") for item in self.systems)
        self.comboBox_user.setCurrentIndex(self._saved_index("user", self.users))
        self.comboBox_spectrometer.setCurrentIndex(
            self._saved_index("spectrometer", self.systems)
        )
        self.comboBox_user.blockSignals(False)
        self.comboBox_spectrometer.blockSignals(False)
        self._show_user()
        self._show_system()

    def _settings(self) -> QSettings:
        return QSettings(self.SETTINGS_ORGANIZATION, self.SETTINGS_APPLICATION)

    def _saved_index(self, name: str, records: list[dict[str, str]]) -> int:
        if not records:
            return -1
        saved_value = self._settings().value(f"profile_manager/last_{name}", "")
        if saved_value:
            try:
                saved_record = json.loads(str(saved_value))
            except (TypeError, json.JSONDecodeError):
                saved_record = None
            if saved_record in records:
                return records.index(saved_record)
        return len(records) - 1

    def _show_user(self) -> None:
        index = self.comboBox_user.currentIndex()
        item = self.users[index] if 0 <= index < len(self.users) else {}
        self.lineEdit_user_name.setText(item.get("name", ""))
        self.lineEdit_user_institute.setText(item.get("institute", ""))
        self.lineEdit_user_id.setText(item.get("id", ""))
        self.lineEdit_user_note.setText(item.get("note", ""))

    def _show_system(self) -> None:
        index = self.comboBox_spectrometer.currentIndex()
        item = self.systems[index] if 0 <= index < len(self.systems) else {}
        self.lineEdit_sys_model.setText(item.get("model", ""))
        self.lineEdit_sys_manufacturer.setText(item.get("manufacturer", ""))
        self.lineEdit_sys_id.setText(item.get("id", ""))
        self.lineEdit_sys_note.setText(item.get("note", ""))

    def _clear_user(self) -> None:
        self.comboBox_user.setCurrentIndex(-1)
        for field in (self.lineEdit_user_name, self.lineEdit_user_institute, self.lineEdit_user_id, self.lineEdit_user_note):
            field.clear()

    def _clear_system(self) -> None:
        self.comboBox_spectrometer.setCurrentIndex(-1)
        for field in (
            self.lineEdit_sys_model,
            self.lineEdit_sys_manufacturer,
            self.lineEdit_sys_id,
            self.lineEdit_sys_note,
        ):
            field.clear()

    def _add_user(self) -> None:
        record = {
            "name": self.lineEdit_user_name.text().strip(),
            "institute": self.lineEdit_user_institute.text().strip(),
            "id": self.lineEdit_user_id.text().strip(),
            "note": self.lineEdit_user_note.text().strip(),
        }
        if not record["name"]:
            QMessageBox.warning(self, "User", "A user name is required.")
            return
        index = self.comboBox_user.currentIndex()
        if 0 <= index < len(self.users):
            self.users[index] = record
        else:
            self.users.append(record)
        self._repository().save_records("users", self.users)
        self._refresh_combos()

    def _add_system(self) -> None:
        record = {
            "model": self.lineEdit_sys_model.text().strip(),
            "manufacturer": self.lineEdit_sys_manufacturer.text().strip(),
            "id": self.lineEdit_sys_id.text().strip(),
            "note": self.lineEdit_sys_note.text().strip(),
        }
        if not record["model"]:
            QMessageBox.warning(self, "Spectrometer", "A spectrometer model is required.")
            return
        index = self.comboBox_spectrometer.currentIndex()
        if 0 <= index < len(self.systems):
            self.systems[index] = record
        else:
            self.systems.append(record)
        self._repository().save_records("spectrometers", self.systems)
        self._refresh_combos()

    def _remove_user(self) -> None:
        index = self.comboBox_user.currentIndex()
        if 0 <= index < len(self.users):
            self.users.pop(index)
            self._repository().save_records("users", self.users)
            self._refresh_combos()

    def _remove_system(self) -> None:
        index = self.comboBox_spectrometer.currentIndex()
        if 0 <= index < len(self.systems):
            self.systems.pop(index)
            self._repository().save_records("spectrometers", self.systems)
            self._refresh_combos()

    def _apply(self) -> None:
        user_index = self.comboBox_user.currentIndex()
        system_index = self.comboBox_spectrometer.currentIndex()
        self.selection = {
            "user": self.users[user_index] if 0 <= user_index < len(self.users) else {},
            "spectrometer": self.systems[system_index] if 0 <= system_index < len(self.systems) else {},
        }
        settings = self._settings()
        settings.setValue(
            "profile_manager/last_user", json.dumps(self.selection["user"], sort_keys=True)
        )
        settings.setValue(
            "profile_manager/last_spectrometer",
            json.dumps(self.selection["spectrometer"], sort_keys=True),
        )
        self.accept()
