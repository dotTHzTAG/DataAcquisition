from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
)

from catx.core.paths import UI_DIR
from catx.core.version import about_text
from catx.repositories.project import ProjectRepository
from catx.services.acquisition import AcquisitionService
try:
    from matplotlib.backends.backend_qtagg import (
        FigureCanvasQTAgg,
        NavigationToolbar2QT,
    )
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasQTAgg = None
    NavigationToolbar2QT = None
    Figure = None


class DataManagerWindow(QMainWindow):
    HDF5_PATH_ROLE = Qt.ItemDataRole.UserRole
    HDF5_TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
    ATTRIBUTE_ROWS = (
        ("Name", "sample"),
        ("Description", "description"),
        ("Instrument Profile", "instrument"),
        ("User Profile", "user"),
        ("Date and Time", "time"),
        ("Timestamp", "scancontrol_timestamp"),
        ("Measurement Mode", "mode"),
        ("Coordinates", "coordinate"),
        ("Metadata Description", "mdDescription"),
        ("Metadata 1", "md1"),
        ("Metadata 2", "md2"),
        ("Metadata 3", "md3"),
        ("Metadata 4", "md4"),
        ("Metadata 5", "md5"),
        ("Metadata 6", "md6"),
        ("Metadata 7", "md7"),
        ("dotTHz Version", "thzVer"),
        ("Dataset Description", "dsDescription"),
    )
    EDITABLE_ATTRIBUTES = {
        "sample",
        "description",
        "instrument",
        "user",
        "mode",
        "mdDescription",
        "md1",
        "md2",
        "md3",
        "md4",
        "md5",
        "md6",
        "md7",
    }

    def __init__(
        self,
        acquisition_service: AcquisitionService,
        project_path=None,
        acquisition_window=None,
    ):
        super().__init__()
        uic.loadUi(str(UI_DIR / "data_manager.ui"), self)
        self.acquisition_service = acquisition_service
        self.acquisition_window = acquisition_window
        self.project_repository = ProjectRepository()
        self.project_path = Path(project_path) if project_path else None
        self.current_plot_group_path: str | None = None
        self.current_attribute_group_path: str | None = None
        self.pending_attribute_edits: dict[str, dict[str, str]] = {}
        self._loading_attributes = False
        self.tree_model = QStandardItemModel(self)
        self.tree_model.setHorizontalHeaderLabels(["HDF5 Structure"])
        self.treeView_groups.setModel(self.tree_model)
        self._initialize_attribute_table()
        self._initialize_plot()
        self._connect_signals()
        self.statusbar.showMessage("Ready")
        if self.project_path:
            self._display_project()
        else:
            self.setWindowTitle("Data Manager")

    def _connect_signals(self) -> None:
        self.actionExit.triggered.connect(self.close)
        self.actionTeraSmart.triggered.connect(self.open_acquisition_window)
        self.actionAbout_DataAcquisition.triggered.connect(self._show_about)
        self.actionLoad.triggered.connect(self._load_project)
        self.pushButton_load_project.clicked.connect(self._load_project)
        self.pushButton_reload.clicked.connect(self._reload_project)
        self.pushButton_attribute_update.clicked.connect(
            self._update_measurement_attributes
        )
        self.pushButton_measurement_remove.clicked.connect(
            self._remove_measurement
        )
        self.pushButton_exit.clicked.connect(self.close)
        self.treeView_groups.selectionModel().currentChanged.connect(
            self._tree_selection_changed
        )
        self.tabWidget.currentChanged.connect(self._refresh_selection_view)
        self.radioButton_waveform.toggled.connect(self._refresh_current_plot)
        self.radioButton_spectrum.toggled.connect(self._refresh_current_plot)
        self.checkBox_subtract_baseline.toggled.connect(self._refresh_current_plot)
        self.tableWidget_attribute.itemChanged.connect(
            self._attribute_item_changed
        )

    def _initialize_attribute_table(self) -> None:
        self.tableWidget_attribute.setColumnCount(2)
        self.tableWidget_attribute.setHorizontalHeaderLabels(["Attribute", "Value"])
        self.tableWidget_attribute.setEditTriggers(
            self.tableWidget_attribute.EditTrigger.DoubleClicked
            | self.tableWidget_attribute.EditTrigger.EditKeyPressed
            | self.tableWidget_attribute.EditTrigger.SelectedClicked
        )
        self.tableWidget_attribute.setAlternatingRowColors(True)
        header = self.tableWidget_attribute.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def _initialize_plot(self) -> None:
        layout = self.widget_dataset_plot.layout()
        if layout is None:
            layout = QVBoxLayout(self.widget_dataset_plot)
            layout.setContentsMargins(0, 0, 0, 0)
        if (
            FigureCanvasQTAgg is None
            or NavigationToolbar2QT is None
            or Figure is None
        ):
            self.plot_canvas = None
            message = QLabel(
                "Matplotlib is required to display datasets.\n"
                "Install the dependencies from requirements.txt.",
                self.widget_dataset_plot,
            )
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(message)
            return
        self.plot_figure = Figure(tight_layout=True)
        self.plot_canvas = FigureCanvasQTAgg(self.plot_figure)
        self.plot_toolbar = NavigationToolbar2QT(
            self.plot_canvas, self.widget_dataset_plot
        )
        self.plot_axes = self.plot_figure.add_subplot(111)
        self.plot_axes.set_xlabel("Time (ps)")
        self.plot_axes.set_ylabel("Amplitude (a.u.)")
        layout.addWidget(self.plot_toolbar)
        layout.addWidget(self.plot_canvas)

    def open_acquisition_window(self) -> None:
        if self.acquisition_window is None:
            self.statusbar.showMessage("Acquisition window is unavailable")
            return
        self.acquisition_window.show()
        self.acquisition_window.raise_()
        self.acquisition_window.activateWindow()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Data Manager",
            about_text(
                "Data Acquisition Data Manager",
                "dotTHz project browsing, metadata, and dataset analysis",
            ),
        )

    def _load_project(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load dotTHz project", "", "dotTHz Project (*.thz)"
        )
        if not filename:
            return
        self.project_path = Path(filename)
        self._display_project()

    def _reload_project(self) -> None:
        if self.project_path is not None:
            self.pending_attribute_edits.clear()
            self._display_project()

    def _display_project(self, clear_pending: bool = True) -> None:
        if self.project_path is None:
            return
        try:
            with h5py.File(self.project_path, "r") as handle:
                self._populate_tree(handle)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Load project", f"Could not read the project file:\n{exc}"
            )
            self.statusbar.showMessage("Could not load project")
            return
        modified = datetime.fromtimestamp(self.project_path.stat().st_mtime)
        self.lineEdit_project_name.setText(self.project_path.name)
        self.lineEdit_project_name.setToolTip(str(self.project_path))
        self.lineEdit_update_date.setText(modified.strftime("%Y-%m-%d %H:%M:%S"))
        self.setWindowTitle(f"Data Manager - {self.project_path.name}")
        self.tableWidget_attribute.setRowCount(0)
        self.current_plot_group_path = None
        self.current_attribute_group_path = None
        if clear_pending:
            self.pending_attribute_edits.clear()
        self._clear_plot()
        self.statusbar.showMessage(f"Loaded {self.project_path}")

    def _populate_tree(self, handle: h5py.File) -> None:
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        root = QStandardItem(self.project_path.name)
        root.setEditable(False)
        root.setData("/", self.HDF5_PATH_ROLE)
        root.setData("file", self.HDF5_TYPE_ROLE)
        self.tree_model.appendRow(root)
        for name, item in handle.items():
            root.appendRow(self._hdf5_item(name, item))
        self.treeView_groups.expand(root.index())

    def _hdf5_item(self, name: str, hdf5_object) -> QStandardItem:
        item_type = "group" if isinstance(hdf5_object, h5py.Group) else "dataset"
        item = QStandardItem(name)
        item.setEditable(False)
        item.setData(hdf5_object.name, self.HDF5_PATH_ROLE)
        item.setData(item_type, self.HDF5_TYPE_ROLE)
        if isinstance(hdf5_object, h5py.Group):
            for child_name, child in hdf5_object.items():
                item.appendRow(self._hdf5_item(child_name, child))
        return item

    def _tree_selection_changed(self, current, _previous) -> None:
        self._refresh_selection_view()

    def _refresh_selection_view(self, _index: int | None = None) -> None:
        current = self.treeView_groups.currentIndex()
        if not current.isValid() or self.project_path is None:
            return
        path = current.data(self.HDF5_PATH_ROLE)
        item_type = current.data(self.HDF5_TYPE_ROLE)
        if not path:
            return
        try:
            with h5py.File(self.project_path, "r") as handle:
                hdf5_object = handle[path]
                if self.tabWidget.currentWidget() == self.attributes:
                    self.current_attribute_group_path = (
                        hdf5_object.name if item_type == "group" else None
                    )
                    self._show_attributes(
                        hdf5_object.attrs,
                        editable=item_type == "group",
                    )
                elif self.tabWidget.currentWidget() == self.datasets:
                    if item_type == "group":
                        self.current_plot_group_path = hdf5_object.name
                        self._plot_group_datasets(hdf5_object)
                    elif item_type == "dataset":
                        parent = hdf5_object.parent
                        self.current_plot_group_path = parent.name
                        self._plot_group_datasets(parent)
                    else:
                        self.current_plot_group_path = None
                        self._clear_plot()
        except (OSError, KeyError, ValueError) as exc:
            self.statusbar.showMessage(f"Could not read selection: {exc}")

    def _show_attributes(self, attributes, editable: bool) -> None:
        self._loading_attributes = True
        self.tableWidget_attribute.setRowCount(len(self.ATTRIBUTE_ROWS))
        pending = self.pending_attribute_edits.get(
            self.current_attribute_group_path or "", {}
        )
        for row, (label, attribute_name) in enumerate(self.ATTRIBUTE_ROWS):
            value = pending.get(
                attribute_name,
                self._display_value(attributes.get(attribute_name, "")),
            )
            label_item = QTableWidgetItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item = QTableWidgetItem(str(value))
            value_item.setData(Qt.ItemDataRole.UserRole, attribute_name)
            if not (editable and attribute_name in self.EDITABLE_ATTRIBUTES):
                value_item.setFlags(
                    value_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
            self.tableWidget_attribute.setItem(row, 0, label_item)
            self.tableWidget_attribute.setItem(row, 1, value_item)
        self._loading_attributes = False

    def _attribute_item_changed(self, item: QTableWidgetItem) -> None:
        if (
            self._loading_attributes
            or item.column() != 1
            or self.current_attribute_group_path is None
        ):
            return
        attribute_name = item.data(Qt.ItemDataRole.UserRole)
        if attribute_name not in self.EDITABLE_ATTRIBUTES:
            return
        edits = self.pending_attribute_edits.setdefault(
            self.current_attribute_group_path, {}
        )
        edits[attribute_name] = item.text().strip()
        self.statusbar.showMessage("Attribute changes pending")

    def _update_measurement_attributes(self) -> None:
        if self.project_path is None or self.current_attribute_group_path is None:
            QMessageBox.information(
                self, "Update attributes", "Select a measurement group first."
            )
            return
        edits = self.pending_attribute_edits.get(
            self.current_attribute_group_path, {}
        )
        if not edits:
            self.statusbar.showMessage("No pending attribute changes")
            return
        values = {
            key: self._attribute_value(value) for key, value in edits.items()
        }
        try:
            self.project_repository.update_measurement_attributes(
                self.project_path, self.current_attribute_group_path, values
            )
        except (OSError, KeyError, ValueError) as exc:
            QMessageBox.critical(
                self, "Update attributes", f"Could not update attributes:\n{exc}"
            )
            return
        self.pending_attribute_edits.pop(self.current_attribute_group_path, None)
        self.lineEdit_update_date.setText(
            datetime.fromtimestamp(self.project_path.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
        self.statusbar.showMessage("Measurement attributes updated")

    def _remove_measurement(self) -> None:
        current = self.treeView_groups.currentIndex()
        if not current.isValid() or self.project_path is None:
            return
        item_type = current.data(self.HDF5_TYPE_ROLE)
        group_path = current.data(self.HDF5_PATH_ROLE)
        if item_type == "dataset":
            group_path = str(group_path).rsplit("/", 1)[0]
        elif item_type != "group":
            QMessageBox.information(
                self, "Remove measurement", "Select a measurement group first."
            )
            return
        group_name = str(group_path).rsplit("/", 1)[-1]
        answer = QMessageBox.warning(
            self,
            "Remove measurement",
            f"Delete measurement {group_name}?\n\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.project_repository.remove_measurement(
                self.project_path, str(group_path)
            )
        except (OSError, KeyError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Remove measurement",
                f"Could not remove the measurement:\n{exc}",
            )
            return
        self.pending_attribute_edits.pop(str(group_path), None)
        self._display_project(clear_pending=False)
        self.statusbar.showMessage(f"Removed measurement {group_name}")

    @staticmethod
    def _attribute_value(value: str):
        if value == "":
            return ""
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, np.ndarray):
            return np.array2string(value, threshold=20)
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
            if isinstance(decoded, dict):
                return " / ".join(
                    str(item) for item in decoded.values() if str(item).strip()
                )
            if isinstance(decoded, list):
                return " / ".join(str(item) for item in decoded)
        return str(value)

    def _plot_group_datasets(self, group: h5py.Group) -> None:
        if self.plot_canvas is None:
            return
        self.plot_axes.clear()
        plotted = 0
        descriptions = str(group.attrs.get("dsDescription", "")).split(",")
        datasets = {
            name: np.asarray(dataset)
            for name, dataset in group.items()
            if isinstance(dataset, h5py.Dataset)
        }
        subtract_baseline = self.checkBox_subtract_baseline.isChecked()
        baseline = datasets.get("ds3") if subtract_baseline else None
        for index, name in enumerate(sorted(datasets)):
            if subtract_baseline and name == "ds3":
                continue
            data = datasets[name]
            waveform = self._waveform_components(data)
            if waveform is None:
                continue
            time_axis, amplitude = waveform
            label = descriptions[index].strip() if index < len(descriptions) else name
            if baseline is not None and name in {"ds1", "ds2"}:
                baseline_waveform = self._waveform_components(baseline)
                if baseline_waveform is not None and self._same_axis(
                    time_axis, baseline_waveform[0]
                ):
                    amplitude = amplitude - baseline_waveform[1]
                    label += " - Baseline"
                else:
                    self.statusbar.showMessage(
                        f"Could not subtract baseline from {name}: axes do not match"
                    )
            x_values, y_values = self._display_trace(time_axis, amplitude)
            self.plot_axes.plot(x_values, y_values, label=f"{name}: {label}")
            plotted += 1
        if self.radioButton_spectrum.isChecked():
            self.plot_axes.set_xlabel("Frequency (THz)")
            self.plot_axes.set_ylabel("Magnitude (a.u.)")
        else:
            self.plot_axes.set_xlabel("Time (ps)")
            self.plot_axes.set_ylabel("Amplitude (a.u.)")
        self.plot_axes.grid(True, alpha=0.25)
        if plotted:
            self.plot_axes.legend()
        else:
            self.plot_axes.text(
                0.5,
                0.5,
                "No plottable datasets",
                ha="center",
                va="center",
                transform=self.plot_axes.transAxes,
            )
        self.plot_canvas.draw_idle()

    def _refresh_current_plot(self, _checked: bool = False) -> None:
        if self.project_path is None or self.current_plot_group_path is None:
            return
        try:
            with h5py.File(self.project_path, "r") as handle:
                group = handle[self.current_plot_group_path]
                if isinstance(group, h5py.Group):
                    self._plot_group_datasets(group)
        except (OSError, KeyError, ValueError) as exc:
            self.statusbar.showMessage(f"Could not refresh plot: {exc}")

    def _display_trace(
        self, time_axis: np.ndarray, amplitude: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        if not self.radioButton_spectrum.isChecked():
            return time_axis, amplitude
        if time_axis.size < 2 or amplitude.size < 2:
            return np.array([]), np.array([])
        time_step_ps = float(np.mean(np.diff(time_axis)))
        if not np.isfinite(time_step_ps) or time_step_ps <= 0:
            return np.array([]), np.array([])
        frequency_thz = np.fft.rfftfreq(amplitude.size, d=time_step_ps)
        magnitude = np.abs(np.fft.rfft(amplitude))
        return frequency_thz, magnitude

    @staticmethod
    def _waveform_components(data: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        if data.ndim != 2:
            return None
        if data.shape[1] == 2 and data.shape[0] != 2:
            return (
                np.asarray(data[:, 0], dtype=np.float64),
                np.asarray(data[:, 1], dtype=np.float64),
            )
        if data.shape[0] >= 2:
            return (
                np.asarray(data[0], dtype=np.float64),
                np.asarray(data[1], dtype=np.float64),
            )
        if data.shape[1] >= 2:
            return (
                np.asarray(data[:, 0], dtype=np.float64),
                np.asarray(data[:, 1], dtype=np.float64),
            )
        return None

    @staticmethod
    def _same_axis(first: np.ndarray, second: np.ndarray) -> bool:
        return first.shape == second.shape and np.allclose(first, second)

    def _clear_plot(self) -> None:
        if self.plot_canvas is None:
            return
        self.plot_axes.clear()
        self.plot_axes.set_xlabel("Time (ps)")
        self.plot_axes.set_ylabel("Amplitude (a.u.)")
        self.plot_canvas.draw_idle()
