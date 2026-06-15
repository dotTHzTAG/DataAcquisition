from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import uic
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QTableWidgetItem,
    QWidget,
)

from catx.core.paths import APPLICATION_LOG, UI_DIR
from catx.core.version import about_text
from catx.models.acquisition import AcquisitionMode, AcquisitionPlan, TimeUnit
from catx.repositories.project import ProjectRepository
from catx.services.acquisition import AcquisitionService
from catx.services.application_log import (
    MAX_APPLICATION_LOG_BYTES,
    QtLogHandler,
    application_log_exceeds_limit,
    clear_application_log,
)
from ui.log_dialog import LogDialog
from ui.profile_manager import ProfileManagerDialog

if TYPE_CHECKING:
    from ui.data_manager import DataManagerWindow


class ScanControlConnectionWorker(QThread):
    connected = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, acquisition_service: AcquisitionService, parent=None):
        super().__init__(parent)
        self.acquisition_service = acquisition_service

    def run(self) -> None:
        try:
            status = asyncio.run(self.acquisition_service.connect())
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.connected.emit(status)


class AcquisitionWorker(QThread):
    progress = pyqtSignal(object)
    calibration_acquired = pyqtSignal(str, object)
    completed = pyqtSignal(str, int)
    failed = pyqtSignal(str)

    def __init__(
        self,
        acquisition_service: AcquisitionService,
        project_path: Path,
        kind: str,
        plan: AcquisitionPlan,
        attributes: dict,
        reference=None,
        baseline=None,
        parent=None,
    ):
        super().__init__(parent)
        self.acquisition_service = acquisition_service
        self.project_path = project_path
        self.kind = kind
        self.plan = plan
        self.attributes = attributes
        self.reference = reference
        self.baseline = baseline
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.progress_acknowledged = threading.Event()

    def run(self) -> None:
        try:
            count = asyncio.run(
                self.acquisition_service.acquire_to_project(
                    project_path=self.project_path,
                    kind=self.kind,
                    plan=self.plan,
                    attributes=self.attributes,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                    reference=self.reference,
                    baseline=self.baseline,
                    calibration_callback=lambda data: self.calibration_acquired.emit(
                        self.kind, data
                    ),
                    progress_callback=self._emit_progress,
                )
            )
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            self.failed.emit(message)
            return
        self.completed.emit(self.kind, count)

    def _emit_progress(self, progress) -> None:
        self.progress_acknowledged.clear()
        self.progress.emit(progress)
        self.progress_acknowledged.wait(timeout=1.0)


class AcquisitionMainWindow(QMainWindow):
    METADATA_CATEGORIES = (
        "Concentration",
        "Diameter",
        "Molar Mass",
        "Refractive Index",
        "Temperature",
        "Thickness",
        "Volume",
        "Weight",
    )
    LENGTH_UNITS = ("cm", "mm", "\N{MICRO SIGN}m")
    VOLUME_UNITS = ("mL", "\N{MICRO SIGN}L", "nL")
    MASS_UNITS = ("g", "mg", "\N{MICRO SIGN}g", "ng")
    METADATA_UNITS = (
        "%",
        *LENGTH_UNITS,
        "g/mol",
        "K",
        *VOLUME_UNITS,
        *MASS_UNITS,
    )
    CATEGORY_UNITS = {
        "Concentration": ("%",),
        "Diameter": LENGTH_UNITS,
        "Molar Mass": ("g/mol",),
        "Refractive Index": (),
        "Temperature": ("K",),
        "Thickness": LENGTH_UNITS,
        "Volume": VOLUME_UNITS,
        "Weight": MASS_UNITS,
    }

    def __init__(
        self,
        acquisition_service: AcquisitionService,
        data_manager_factory,
        logger: logging.Logger,
        log_handler: QtLogHandler,
    ):
        super().__init__()
        uic.loadUi(str(UI_DIR / "acquisition_main.ui"), self)
        self.acquisition_service = acquisition_service
        self.data_manager_factory = data_manager_factory
        self.logger = logger
        self.project_repository = ProjectRepository()
        self.project_path: Path | None = None
        self.user_system_selection: dict[str, dict[str, str]] = {}
        self.baseline_waveform = None
        self.reference_waveform = None
        self.data_manager: DataManagerWindow | None = None
        self.connection_worker: ScanControlConnectionWorker | None = None
        self.acquisition_worker: AcquisitionWorker | None = None
        self.scancontrol_connected = False
        self._log_size_warning_shown = False
        self.status_field = self.lineEdit_sys_status
        self.log_dialog = LogDialog(APPLICATION_LOG, self)
        self._build_status_bar(log_handler)
        self._connect_signals()
        self._initialize_controls()
        self.logger.info("Data acquisition application started")

    def _build_status_bar(self, log_handler: QtLogHandler) -> None:
        self.statusbar.clearMessage()
        self.statusbar.setStyleSheet("QStatusBar::item { border: none; }")
        self.log_label = QLabel("Ready")
        self.log_label.setMinimumWidth(250)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximumWidth(320)
        self.progress_bar.setMinimumHeight(20)
        self.progress_bar.setStyleSheet(
            "QProgressBar {"
            "  border: none;"
            "  text-align: center;"
            "}"
            "QProgressBar::chunk {"
            "  background-color: palette(highlight);"
            "  margin: 0px;"
            "}"
        )
        self.progress_bar.hide()
        self.statusbar.addWidget(self.log_label, 1)
        self.statusbar.addWidget(self.progress_bar, 0)
        log_handler.message_emitted.connect(self._show_log_event)

    def _connect_signals(self) -> None:
        self.actionNew.triggered.connect(self.new_project)
        self.actionOpen.triggered.connect(self.open_project)
        self.actionSave.triggered.connect(self.save_project)
        self.actionSave_As.triggered.connect(self.save_project_as)
        self.actionExit.triggered.connect(self.close)
        self.actionShow_Logs.triggered.connect(self.show_logs)
        self.actionDump_Logs.triggered.connect(self.dump_logs)
        self.actionAbout_Data_Acquisition.triggered.connect(self._show_about)
        profile_action = getattr(self, "actionOpen_Profile_Manager", None)
        if profile_action is None:
            profile_action = self.actionOpen_Manager
        profile_action.triggered.connect(self.open_profile_manager)
        self.pushButton_data_manager.clicked.connect(self.open_data_manager)
        profile_button = getattr(self, "pushButton_profile_manager", None)
        if profile_button is None:
            profile_button = self.pushButton_user_manager
        profile_button.clicked.connect(self.open_profile_manager)
        self.pushButton_connect.clicked.connect(self.connect_scancontrol)
        self.radioButton.toggled.connect(self._sync_mode_visibility)
        self.pushButton_add_row.clicked.connect(self._add_metadata_row)
        self.pushButton_remove_row.clicked.connect(self._remove_metadata_row)
        self.pushButton_reset_metatable.clicked.connect(self._reset_metadata_table)
        self.pushButton_reset_data.clicked.connect(self._reset_project_data)
        self.pushButton_single_scan.clicked.connect(lambda: self._start_acquisition("sample", single=True))
        self.pushButton_multi_scan.clicked.connect(lambda: self._start_acquisition("sample"))
        self.pushButton_pause.clicked.connect(self._toggle_pause)
        self.pushButton_stop.clicked.connect(self._stop_requested)
        self.pushButton_baseline_acquire.clicked.connect(lambda: self._start_acquisition("baseline"))
        self.pushButton_ref_acquire.clicked.connect(lambda: self._start_acquisition("reference"))
        self.pushButton_baseline_remove.clicked.connect(lambda: self._remove_calibration("baseline"))
        self.pushButton_ref_remove.clicked.connect(lambda: self._remove_calibration("reference"))

    def _initialize_controls(self) -> None:
        self.spinBox_2.setMinimum(1)
        self.spinBox_4.setMinimum(1)
        self.lineEdit_2.setReadOnly(True)
        self.lineEdit_3.setReadOnly(True)
        self.lineEdit_scan_count.setText("0")
        self._initialize_metadata_table()
        self._set_scan_controls_enabled(False)
        self._refresh_calibration_controls()
        self._sync_mode_visibility()

    def new_project(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Create acquisition project", "", "dotTHz Project (*.thz)"
        )
        if not filename:
            return
        try:
            self.project_path = self.project_repository.create(filename)
        except (OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Project", f"Could not create project:\n{exc}")
            self.logger.exception("Project creation failed")
            return
        self._update_project_display()
        self.logger.info("Created project %s", self.project_path)
        self.user_system_selection = {}
        self._clear_session_calibration()
        self._update_user_system_display()
        self._refresh_calibration_controls()
        self.open_profile_manager(initial_setup=True)

    def open_project(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open acquisition project", "", "dotTHz Project (*.thz)"
        )
        if not filename:
            return

        project_path = Path(filename)
        try:
            metadata = self.project_repository.load_metadata(project_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Open project",
                f"Could not open the selected .thz project:\n{exc}",
            )
            self.logger.error("Could not open project %s: %s", project_path, exc)
            return

        self.project_path = project_path
        self._clear_session_calibration()
        self.user_system_selection = {
            "user": metadata.get("user", {}),
            "spectrometer": metadata.get("spectrometer", {}),
        }
        self.lineEdit_sample.setText(str(metadata.get("sample", "")))
        self.lineEdit_description.setText(str(metadata.get("description", "")))
        mode = str(metadata.get("mode", ""))
        mode_index = self.comboBox_mode.findText(mode)
        if mode_index >= 0:
            self.comboBox_mode.setCurrentIndex(mode_index)
        self._update_project_display()
        self._update_user_system_display()
        self._refresh_calibration_controls()
        self.logger.info("Opened project %s", self.project_path)

    def open_profile_manager(self, checked: bool = False, initial_setup: bool = False) -> None:
        manager = ProfileManagerDialog(self)
        if manager.exec() != manager.DialogCode.Accepted:
            if initial_setup:
                self.logger.info("User and spectrometer information left blank")
            else:
                self.logger.info("Profile manager closed without applying changes")
            return

        self.user_system_selection = manager.selection
        self._update_user_system_display()
        if self.project_path is not None:
            self.project_repository.update_metadata(
                self.project_path, self.user_system_selection
            )
            self.logger.info("Updated project user and spectrometer information")
        else:
            self.logger.info("Applied user and spectrometer selection; no project is open")

    def _update_user_system_display(self) -> None:
        user = self.user_system_selection.get("user", {})
        spectrometer = self.user_system_selection.get("spectrometer", {})

        self.lineEdit_user.setText(user.get("name", ""))
        self.lineEdit_user.setToolTip(
            " / ".join(
                value
                for value in (
                    user.get("name", ""),
                    user.get("institute", ""),
                    user.get("id", ""),
                )
                if value
            )
        )

        self.lineEdit_spectrometer.setText(spectrometer.get("model", ""))
        self.lineEdit_spectrometer.setToolTip(
            " / ".join(
                value
                for value in (
                    spectrometer.get("manufacturer", ""),
                    spectrometer.get("model", ""),
                    spectrometer.get("id", ""),
                )
                if value
            )
        )

    def save_project(self) -> None:
        if not self._require_project():
            return
        metadata = {
            "sample": self.lineEdit_sample.text().strip(),
            "description": self.lineEdit_description.text().strip(),
            "mode": self.comboBox_mode.currentText(),
        }
        self.project_repository.update_metadata(self.project_path, metadata)
        self.logger.info("Saved project metadata")

    def save_project_as(self) -> None:
        if not self._require_project():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save acquisition project as", str(self.project_path), "dotTHz Project (*.thz)"
        )
        if filename:
            self.project_path = self.project_repository.copy(self.project_path, filename)
            self._update_project_display()
            self.logger.info("Saved project as %s", self.project_path)

    def open_data_manager(self) -> None:
        self.data_manager = self.data_manager_factory(self.project_path, self)
        self.data_manager.show()
        self.data_manager.raise_()
        self.logger.info("Opened data manager")

    def connect_scancontrol(self) -> None:
        if self.connection_worker is not None and self.connection_worker.isRunning():
            return
        self.pushButton_connect.setEnabled(False)
        self.pushButton_connect.setText("Connecting...")
        self.status_field.setText("Connecting to ScanControl...")
        self.logger.info("Connecting to Menlo Systems ScanControl")
        worker = ScanControlConnectionWorker(self.acquisition_service, self)
        worker.connected.connect(self._scancontrol_connected)
        worker.failed.connect(self._scancontrol_connection_failed)
        worker.finished.connect(self._connection_worker_finished)
        self.connection_worker = worker
        worker.start()

    def read_status(self) -> None:
        self.connect_scancontrol()

    def _scancontrol_connected(self, status) -> None:
        self.scancontrol_connected = True
        self.status_field.setText(status.name)
        self.pushButton_connect.setText("Refresh Status")
        self._set_scan_controls_enabled(True)
        self._set_acquisition_running(False)
        self.logger.info("ScanControl connected; status: %s", status.name)

    def _scancontrol_connection_failed(self, message: str) -> None:
        self.scancontrol_connected = False
        self.status_field.setText("Disconnected")
        self.pushButton_connect.setText("Connect")
        self._set_scan_controls_enabled(False)
        self.logger.error("ScanControl connection failed: %s", message)
        QMessageBox.warning(
            self,
            "ScanControl connection",
            f"Could not connect to Menlo Systems ScanControl.\n\n{message}",
        )

    def _connection_worker_finished(self) -> None:
        self.pushButton_connect.setEnabled(True)
        if self.connection_worker is not None:
            self.connection_worker.deleteLater()
        self.connection_worker = None

    def _set_scan_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.pushButton_baseline_acquire,
            self.pushButton_ref_acquire,
            self.pushButton_single_scan,
            self.pushButton_multi_scan,
            self.pushButton_pause,
            self.pushButton_stop,
        ):
            button.setEnabled(enabled)
        self._refresh_calibration_controls()

    def show_logs(self) -> None:
        self.log_dialog.refresh()
        self.log_dialog.show()
        self.log_dialog.raise_()

    def dump_logs(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset application log",
            "Clear all entries from the application log?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_application_log(self.logger)
        self._log_size_warning_shown = False
        self.log_dialog.refresh()
        self.log_label.setText("Application log reset")
        self.log_label.setToolTip("Application log reset")

    def _sync_mode_visibility(self) -> None:
        self.widget_count.setVisible(self.radioButton.isChecked())
        self.widget_time.setVisible(self.radioButton_2.isChecked())

    def _show_log_event(self, message: str) -> None:
        self.log_label.setText(message)
        self.log_label.setToolTip(message)
        if self.log_dialog.isVisible():
            self.log_dialog.refresh()
        self._warn_if_log_is_large()

    def _warn_if_log_is_large(self) -> None:
        if self._log_size_warning_shown or not application_log_exceeds_limit(
            APPLICATION_LOG
        ):
            return
        self._log_size_warning_shown = True
        limit_mb = MAX_APPLICATION_LOG_BYTES // (1024 * 1024)
        QMessageBox.warning(
            self,
            "Application log is large",
            f"The application log has reached {limit_mb} MB. "
            "Use Logs > Dump Logs to clear it.",
        )

    def _initialize_metadata_table(self) -> None:
        self.metadata_row_count = 0
        self.lineEdit_mdDescription.setReadOnly(True)

        self.tableWidget.clear()
        self.tableWidget.setColumnCount(5)
        self.tableWidget.setRowCount(7)
        self.tableWidget.setHorizontalHeaderLabels(
            ["Attribute", "Sample/Reference", "Category", "Unit", "Value"]
        )
        header = self.tableWidget.horizontalHeader()
        for column in (0, 1, 3):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        for column in (2, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.metadata_editors = []
        for index in range(7):
            item = QTableWidgetItem(f"md{index + 1}")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tableWidget.setItem(index, 0, item)

            target = QComboBox(self.tableWidget)
            target.addItems(["-", "Sample", "Reference"])
            category = self._editable_combo(self.METADATA_CATEGORIES)
            unit = self._editable_combo(())
            value = QLineEdit(self.tableWidget)
            value.setValidator(QDoubleValidator(value))

            self.tableWidget.setCellWidget(index, 1, target)
            self.tableWidget.setCellWidget(index, 2, category)
            self.tableWidget.setCellWidget(index, 3, unit)
            self.tableWidget.setCellWidget(index, 4, value)
            self.metadata_editors.append((target, category, unit, value))

            target.currentTextChanged.connect(self._update_metadata_description)
            category.currentTextChanged.connect(
                lambda text, unit_editor=unit: self._category_changed(
                    text, unit_editor
                )
            )
            unit.currentTextChanged.connect(self._update_metadata_description)

        self._metadata_count_changed(0)

    def _editable_combo(self, values) -> QComboBox:
        combo = QComboBox(self.tableWidget)
        combo.setEditable(True)
        combo.addItem("-")
        combo.addItems(values)
        return combo

    def _category_changed(self, category: str, unit: QComboBox) -> None:
        current_unit = unit.currentText().strip()
        choices = self.CATEGORY_UNITS.get(
            category.strip(), self.METADATA_UNITS
        )
        unit.blockSignals(True)
        unit.clear()
        unit.addItem("-")
        unit.addItems(choices)
        if current_unit in choices:
            unit.setCurrentText(current_unit)
        elif choices:
            unit.setCurrentIndex(1)
        unit.blockSignals(False)
        self._update_metadata_description()

    def _add_metadata_row(self) -> None:
        self.metadata_row_count = min(7, self.metadata_row_count + 1)
        self._metadata_count_changed(self.metadata_row_count)

    def _remove_metadata_row(self) -> None:
        self.metadata_row_count = max(0, self.metadata_row_count - 1)
        self._metadata_count_changed(self.metadata_row_count)

    def _reset_metadata_table(self) -> None:
        self.metadata_row_count = 0
        for target, category, unit, value in self.metadata_editors:
            target.setCurrentIndex(0)
            category.setCurrentIndex(0)
            unit.setCurrentIndex(0)
            value.clear()
        self._metadata_count_changed(0)
        self._update_metadata_description()
        self.logger.info("Metadata table reset")

    def _metadata_count_changed(self, count: int) -> None:
        for index, editors in enumerate(self.metadata_editors):
            active = index < count
            self.tableWidget.setRowHidden(index, not active)
            for editor in editors:
                editor.setEnabled(active)
        self._update_metadata_description()

    def _update_metadata_description(self) -> None:
        descriptions = []
        for target, category, unit, _value in self.metadata_editors[
            : self.metadata_row_count
        ]:
            target_text = target.currentText().strip()
            category_text = category.currentText().strip()
            unit_text = unit.currentText().strip()
            if target_text in {"", "-"} or category_text in {"", "-"}:
                continue
            description = f"{target_text} {category_text}"
            if unit_text not in {"", "-"}:
                description += f" ({unit_text})"
            descriptions.append(description)
        self.lineEdit_mdDescription.setText(", ".join(descriptions))

    def _reset_project_data(self) -> None:
        if not self._require_project():
            return
        if self.acquisition_worker is not None and self.acquisition_worker.isRunning():
            QMessageBox.warning(
                self,
                "Reset project data",
                "Stop the current acquisition before resetting project data.",
            )
            return

        project_name = self.project_path.name
        answer = QMessageBox.warning(
            self,
            "Reset project data",
            f"Delete all measurements from {project_name}?\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Reset:
            return

        try:
            removed_count = self.project_repository.clear_measurements(
                self.project_path
            )
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Reset project data",
                f"Could not reset the project file:\n{exc}",
            )
            self.logger.exception("Could not reset project data")
            return

        self._clear_session_calibration()
        self._refresh_calibration_controls()
        self._update_scan_count()
        self.lineEdit_sample.clear()
        self.lineEdit_description.clear()
        self.comboBox_mode.setCurrentIndex(0)
        self.lineEdit_2.clear()
        self.lineEdit_3.clear()
        self.progress_bar.setValue(0)
        self.status_field.setText("Project data reset")
        self.logger.info(
            "Reset project data in %s; removed %d measurement(s)",
            self.project_path,
            removed_count,
        )

    def _start_acquisition(self, kind: str, single: bool = False) -> None:
        if not self._require_project():
            return
        if self.acquisition_worker is not None and self.acquisition_worker.isRunning():
            return
        plan = self._current_acquisition_plan(kind, single)
        try:
            self.acquisition_service.validate_plan(plan)
        except ValueError as exc:
            QMessageBox.warning(self, "Acquisition settings", str(exc))
            return
        try:
            attributes = self._measurement_attributes(plan)
        except ValueError as exc:
            QMessageBox.warning(self, "Measurement metadata", str(exc))
            return
        reference = (
            self.reference_waveform.copy()
            if self.reference_waveform is not None
            else None
        )
        baseline = (
            self.baseline_waveform.copy()
            if self.baseline_waveform is not None
            else None
        )
        worker = AcquisitionWorker(
            self.acquisition_service,
            self.project_path,
            kind,
            plan,
            attributes,
            reference=reference,
            baseline=baseline,
            parent=self,
        )
        worker.progress.connect(self._acquisition_progress)
        worker.calibration_acquired.connect(self._store_session_calibration)
        worker.completed.connect(self._acquisition_completed)
        worker.failed.connect(self._acquisition_failed)
        worker.finished.connect(self._acquisition_worker_finished)
        self.acquisition_worker = worker
        self._set_acquisition_running(True)
        self.lineEdit_3.setText("Estimating...")
        self.status_field.setText("Starting ScanControl...")
        self.logger.info("%s acquisition started", kind.capitalize())
        worker.start()

    def _current_acquisition_plan(self, kind: str, single: bool) -> AcquisitionPlan:
        if kind in {"baseline", "reference"}:
            return AcquisitionPlan(
                average=self.spinBox_ref_scan_avg.value(),
                mode=AcquisitionMode.COUNT,
                count=1,
            )
        interval = self._seconds(
            self.spinBox_3.value(), self.comboBox_interval_time_unit.currentText()
        )
        if single:
            return AcquisitionPlan(
                average=self.spinBox_sample_scan_avg.value(),
                mode=AcquisitionMode.COUNT,
                count=1,
            )
        return AcquisitionPlan(
            average=self.spinBox_sample_scan_avg.value(),
            mode=(AcquisitionMode.COUNT if self.radioButton.isChecked() else AcquisitionMode.TIME),
            count=self.spinBox_2.value(),
            duration=self.spinBox_4.value(),
            duration_unit=TimeUnit(self.comboBox_total_time_unit.currentText()),
            interval_seconds=interval,
            interval_inclusive=self.checkBox_interval_inclusive.isChecked(),
        )

    @staticmethod
    def _seconds(value: int, unit: str) -> int:
        return value * {"seconds": 1, "minutes": 60, "hours": 3600}[unit]

    def _measurement_attributes(self, plan: AcquisitionPlan) -> dict:
        spectrometer = self.user_system_selection.get("spectrometer", {})
        attributes = {
            "sample": self.lineEdit_sample.text().strip(),
            "description": self.lineEdit_description.text().strip(),
            "mode": self.comboBox_mode.currentText(),
            "user": self.user_system_selection.get("user", {}),
            "spectrometer": spectrometer,
            "instrument": spectrometer,
            "scan_average": plan.average,
            "interval_seconds": plan.interval_seconds,
            "interval_inclusive": plan.interval_inclusive,
        }
        descriptions = []
        for index, (target, category, unit, value) in enumerate(
            self.metadata_editors[: self.metadata_row_count], start=1
        ):
            target_text = target.currentText().strip()
            category_text = category.currentText().strip()
            unit_text = unit.currentText().strip()
            value_text = value.text().strip()
            if (
                target_text in {"", "-"}
                or category_text in {"", "-"}
                or not value_text
            ):
                continue
            description = f"{target_text} {category_text}"
            if unit_text not in {"", "-"}:
                description += f" ({unit_text})"
            descriptions.append(description)
            attributes[f"md{index}"] = self._metadata_value(value_text)
        if descriptions:
            attributes["mdDescription"] = ", ".join(descriptions)
        return attributes

    @staticmethod
    def _metadata_value(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"Metadata value must be a number: {value!r}") from None

    def _acquisition_progress(self, progress) -> None:
        try:
            self.progress_bar.setValue(progress.percent)
            self.lineEdit_2.setText(str(progress.completed))
            self.lineEdit_3.setText(self._format_duration(progress.remaining_seconds))
            self.status_field.setText(f"Acquiring: {progress.completed} captured")
            self.lineEdit_2.repaint()
            self.progress_bar.repaint()
        finally:
            if self.acquisition_worker is not None:
                self.acquisition_worker.progress_acknowledged.set()

    def _acquisition_completed(self, kind: str, count: int) -> None:
        self.progress_bar.setValue(100)
        self.lineEdit_3.setText("0 s")
        self.status_field.setText(f"{kind.capitalize()} complete")
        if kind == "sample":
            self._update_scan_count()
        self._refresh_calibration_controls()
        self.logger.info("%s acquisition completed: %d waveform(s)", kind.capitalize(), count)

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None:
            return "Estimating..."
        total_seconds = max(0, round(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:d} h {minutes:02d} min {seconds:02d} s"
        if minutes:
            return f"{minutes:d} min {seconds:02d} s"
        return f"{seconds:d} s"

    def _acquisition_failed(self, message: str) -> None:
        self.status_field.setText("Acquisition failed")
        self.logger.error("Acquisition failed: %s", message)
        QMessageBox.critical(self, "Acquisition", message)

    def _acquisition_worker_finished(self) -> None:
        self._set_acquisition_running(False)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        if self.acquisition_worker is not None:
            self.acquisition_worker.deleteLater()
        self.acquisition_worker = None

    def _set_acquisition_running(self, running: bool) -> None:
        for button in (
            self.pushButton_baseline_acquire,
            self.pushButton_ref_acquire,
            self.pushButton_single_scan,
            self.pushButton_multi_scan,
        ):
            button.setEnabled(self.scancontrol_connected and not running)
        self.pushButton_pause.setEnabled(running)
        self.pushButton_stop.setEnabled(running)
        self.pushButton_pause.setText("Pause")
        self._refresh_calibration_controls(running=running)
        if running:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.show()

    def _toggle_pause(self) -> None:
        if self.acquisition_worker is None:
            return
        paused = not self.acquisition_worker.pause_event.is_set()
        if paused:
            self.acquisition_worker.pause_event.set()
        else:
            self.acquisition_worker.pause_event.clear()
        self.pushButton_pause.setText("Resume" if paused else "Pause")
        self.logger.info("Acquisition %s", "paused" if paused else "resumed")

    def _stop_requested(self) -> None:
        if self.acquisition_worker is not None:
            self.acquisition_worker.stop_event.set()
            self.acquisition_worker.pause_event.clear()
            self.status_field.setText("Stopping acquisition...")
        self.logger.info("Acquisition stop requested")

    def _remove_calibration(self, kind: str) -> None:
        if kind == "baseline":
            self.baseline_waveform = None
        elif kind == "reference":
            self.reference_waveform = None
        self._refresh_calibration_controls()
        self.logger.info("In-memory %s removed", kind)

    def _store_session_calibration(self, kind: str, waveform) -> None:
        if kind == "baseline":
            self.baseline_waveform = waveform
        elif kind == "reference":
            self.reference_waveform = waveform
        self._refresh_calibration_controls(running=True)

    def _clear_session_calibration(self) -> None:
        self.baseline_waveform = None
        self.reference_waveform = None

    def _refresh_calibration_controls(self, running: bool | None = None) -> None:
        if running is None:
            running = bool(
                self.acquisition_worker is not None
                and self.acquisition_worker.isRunning()
            )
        has_baseline = self.baseline_waveform is not None
        has_reference = self.reference_waveform is not None
        self.pushButton_baseline_remove.setEnabled(has_baseline and not running)
        self.pushButton_ref_remove.setEnabled(has_reference and not running)

    def _require_project(self) -> bool:
        if self.project_path is not None:
            return True
        QMessageBox.information(self, "Project required", "Create a .thz project before acquiring or saving data.")
        return False

    def _update_project_display(self) -> None:
        if self.project_path is None:
            self.lineEdit_project.clear()
            self.lineEdit_project.setToolTip("")
            self.lineEdit_scan_count.setText("0")
            return
        self.lineEdit_project.setText(self.project_path.stem)
        self.lineEdit_project.setToolTip(str(self.project_path))
        self._update_scan_count()

    def _update_scan_count(self) -> None:
        count = (
            self.project_repository.measurement_count(self.project_path)
            if self.project_path is not None
            else 0
        )
        self.lineEdit_scan_count.setText(str(count))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Data Acquisition",
            about_text(
                "Menlo Systems® Data Acquisition",
                "THz Acquisition and dotTHz Project Data Management",
            ),
        )
