from __future__ import annotations

import threading
import asyncio
from datetime import datetime, timezone

import h5py
import numpy as np

from catx.models.acquisition import AcquisitionMode, AcquisitionPlan, Waveform
from catx.repositories.project import ProjectRepository
from catx.services.acquisition import AcquisitionService


class FakeMenloClient:
    async def acquire(self, plan, stop_event, pause_event):
        for index in range(plan.count):
            yield Waveform(
                time_axis=np.array([0.0, 1.0, 2.0]),
                amplitude=np.array([index + 1.0, index + 2.0, index + 3.0]),
                captured_at=datetime.now(timezone.utc),
                rate=10.0,
            )


def test_acquisition_writes_calibration_and_measurements(tmp_path):
    asyncio.run(_run_acquisition_test(tmp_path))


async def _run_acquisition_test(tmp_path):
    path = tmp_path / "direct.thz"
    projects = ProjectRepository()
    projects.create(path)
    service = AcquisitionService(menlo=FakeMenloClient(), projects=projects)
    stop_event = threading.Event()
    pause_event = threading.Event()

    calibrations = {}
    await service.acquire_to_project(
        path,
        "baseline",
        AcquisitionPlan(count=1),
        {},
        stop_event,
        pause_event,
        calibration_callback=lambda data: calibrations.update(baseline=data),
    )
    await service.acquire_to_project(
        path,
        "reference",
        AcquisitionPlan(count=1),
        {},
        stop_event,
        pause_event,
        calibration_callback=lambda data: calibrations.update(reference=data),
    )
    count = await service.acquire_to_project(
        path,
        "sample",
        AcquisitionPlan(count=2),
        {
            "sample": "Silicon",
            "mode": "Transmission",
            "mdDescription": "Sample Thickness (mm)",
            "md1": 1.5,
        },
        stop_event,
        pause_event,
        reference=calibrations["reference"],
        baseline=calibrations["baseline"],
    )

    assert count == 2
    assert projects.measurement_count(path) == 2
    with h5py.File(path, "r") as handle:
        assert "calibration" not in handle
        assert list(handle) == ["Silicon_01", "Silicon_02"]
        first = handle["Silicon_01"]
        assert first["ds1"].shape == (2, 3)
        assert first["ds2"].shape == (2, 3)
        assert first["ds3"].shape == (2, 3)
        assert first.attrs["dsDescription"] == "Sample,Reference,Baseline"
        assert first.attrs["sample"] == "Silicon"
        assert first.attrs["mode"] == "Transmission"
        assert first.attrs["mdDescription"] == "Sample Thickness (mm)"
        assert first.attrs["md1"] == 1.5
        second = handle["Silicon_02"]
        np.testing.assert_array_equal(second["ds2"], first["ds2"])
        np.testing.assert_array_equal(second["ds3"], first["ds3"])


def test_measurement_count_is_zero_for_new_project(tmp_path):
    path = tmp_path / "empty.thz"
    projects = ProjectRepository()
    projects.create(path)

    assert projects.measurement_count(path) == 0


def test_clear_measurements_removes_measurement_metadata(tmp_path):
    path = tmp_path / "reset.thz"
    projects = ProjectRepository()
    projects.create(path)
    projects.update_metadata(
        path,
        {
            "sample": "Silicon",
            "description": "Test sample",
            "mode": "Transmission",
            "user": {"name": "Researcher"},
            "spectrometer": {"model": "TeraSmart"},
        },
    )
    waveform = Waveform(
        time_axis=np.array([0.0, 1.0]),
        amplitude=np.array([2.0, 3.0]),
        captured_at=datetime.now(timezone.utc),
    )
    projects.append_measurement(path, waveform, {})
    size_before_reset = path.stat().st_size

    assert projects.clear_measurements(path) == 1
    assert projects.measurement_count(path) == 0
    assert path.stat().st_size < size_before_reset
    metadata = projects.load_metadata(path)
    assert "sample" not in metadata
    assert "description" not in metadata
    assert "mode" not in metadata
    assert "user" not in metadata
    assert "spectrometer" not in metadata


def test_single_scan_reuses_current_calibration(tmp_path):
    asyncio.run(_run_single_scan_test(tmp_path))


async def _run_single_scan_test(tmp_path):
    path = tmp_path / "single.thz"
    projects = ProjectRepository()
    projects.create(path)
    service = AcquisitionService(menlo=FakeMenloClient(), projects=projects)
    stop_event = threading.Event()
    pause_event = threading.Event()
    reference = np.array([[0.0, 1.0, 2.0], [4.0, 5.0, 6.0]])
    baseline = np.array([[0.0, 1.0, 2.0], [1.0, 1.0, 1.0]])

    count = await service.acquire_to_project(
        path,
        "sample",
        AcquisitionPlan(count=1),
        {},
        stop_event,
        pause_event,
        reference=reference,
        baseline=baseline,
    )

    assert count == 1
    with h5py.File(path, "r") as handle:
        measurement = handle["measurement_01"]
        np.testing.assert_array_equal(measurement["ds2"], reference)
        np.testing.assert_array_equal(measurement["ds3"], baseline)


def test_root_group_number_width_uses_estimated_scan_count(tmp_path):
    path = tmp_path / "numbering.thz"
    projects = ProjectRepository()
    projects.create(path)
    waveform = Waveform(
        time_axis=np.array([0.0, 1.0]),
        amplitude=np.array([2.0, 3.0]),
        captured_at=datetime.now(timezone.utc),
    )

    group_path = projects.append_measurement(
        path,
        waveform,
        {"sample": "Test Sample"},
        estimated_count=9000,
    )

    assert group_path == "/Test_Sample_00001"
    with h5py.File(path, "r") as handle:
        assert list(handle) == ["Test_Sample_00001"]


def test_time_plan_estimates_numbering_width_from_duration_and_interval():
    plan = AcquisitionPlan(
        mode=AcquisitionMode.TIME,
        duration=60,
        interval_seconds=5,
    )

    assert plan.estimated_measurement_count == 12


def test_continuous_time_plan_estimates_count_from_rate_and_average():
    plan = AcquisitionPlan(
        average=10,
        mode=AcquisitionMode.TIME,
        duration=60,
    )

    assert plan.estimated_measurement_count_at_rate(50.0) == 300


def test_measurement_number_width_stays_fixed_for_series(tmp_path):
    path = tmp_path / "fixed-width.thz"
    projects = ProjectRepository()
    projects.create(path)
    waveform = Waveform(
        time_axis=np.array([0.0, 1.0]),
        amplitude=np.array([2.0, 3.0]),
        captured_at=datetime.now(timezone.utc),
    )

    first = projects.append_measurement(
        path, waveform, {"sample": "Series"}, estimated_count=300
    )
    second = projects.append_measurement(
        path, waveform, {"sample": "Series"}, estimated_count=1
    )

    assert first == "/Series_0001"
    assert second == "/Series_0002"
