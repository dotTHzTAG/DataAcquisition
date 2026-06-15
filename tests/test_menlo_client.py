from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import numpy as np

from catx.menlo.client import MenloScanControlClient
from catx.models.acquisition import AcquisitionPlan
from scancontrolclient import ScanControlStatus


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, data):
        for callback in list(self.callbacks):
            callback(data)


class FakeScanControl:
    def __init__(self):
        self.status = ScanControlStatus.Idle
        self.currentAverages = 1
        self.rate = 10.0
        self.pulseReady = FakeSignal()

    def setDesiredAverages(self, average):
        self.currentAverages = average

    def resetAveraging(self):
        self.currentAverages = 0

    def start(self):
        self.status = ScanControlStatus.Acquiring
        self.currentAverages = 4
        asyncio.get_running_loop().call_soon(
            self.pulseReady.emit,
            {
                "timeaxis": np.array([0.0, 1.0]),
                "amplitude": [np.array([2.0, 3.0])],
                "timestamp": 123456,
                "flags": 16,
            },
        )

    def stop(self):
        self.status = ScanControlStatus.Idle


class BurstingScanControl(FakeScanControl):
    def __init__(self, target_count):
        super().__init__()
        self.target_count = target_count
        self.emitted_cycles = 0

    def resetAveraging(self):
        self.currentAverages = 0
        if self.status is ScanControlStatus.Acquiring:
            asyncio.get_running_loop().call_later(0.01, self._emit_cycle)

    def start(self):
        self.status = ScanControlStatus.Acquiring
        self._emit_cycle()

    def _emit_cycle(self):
        if self.emitted_cycles >= self.target_count:
            return
        self.emitted_cycles += 1
        self.currentAverages = 4
        loop = asyncio.get_running_loop()
        for _ in range(3):
            loop.call_soon(
                self.pulseReady.emit,
                {
                    "timeaxis": np.array([0.0, 1.0]),
                    "amplitude": [np.array([2.0, 3.0])],
                },
            )


class SingleAverageScanControl(FakeScanControl):
    def resetAveraging(self):
        self.currentAverages = 1

    def start(self):
        self.status = ScanControlStatus.Acquiring
        self.currentAverages = 1
        asyncio.get_running_loop().call_soon(
            self.pulseReady.emit,
            {
                "timeaxis": np.array([0.0, 1.0]),
                "amplitude": [np.array([2.0, 3.0])],
            },
        )


class TimedScanControl(FakeScanControl):
    def __init__(self, averaging_seconds):
        super().__init__()
        self.averaging_seconds = averaging_seconds
        self.reset_times = []

    def resetAveraging(self):
        self.currentAverages = 0
        self.reset_times.append(asyncio.get_running_loop().time())
        if self.status is ScanControlStatus.Acquiring:
            asyncio.get_running_loop().call_later(
                self.averaging_seconds, self._emit_cycle
            )

    def start(self):
        self.status = ScanControlStatus.Acquiring
        asyncio.get_running_loop().call_later(
            self.averaging_seconds, self._emit_cycle
        )

    def _emit_cycle(self):
        self.currentAverages = 4
        self.pulseReady.emit(
            {
                "timeaxis": np.array([0.0, 1.0]),
                "amplitude": [np.array([2.0, 3.0])],
            }
        )


def test_idle_scancontrol_starts_and_emits_pulse():
    asyncio.run(_run_idle_scancontrol_test())


async def _run_idle_scancontrol_test():
    client = MenloScanControlClient()
    scancontrol = FakeScanControl()
    captured = []

    await client._acquire_on_client_loop(
        SimpleNamespace(scancontrol=scancontrol),
        AcquisitionPlan(average=4, count=1),
        threading.Event(),
        threading.Event(),
        captured.append,
    )

    assert len(captured) == 1
    np.testing.assert_allclose(captured[0].amplitude, [2.0, 3.0])
    assert captured[0].scancontrol_timestamp == 123456
    assert captured[0].pulse_flags == 16
    assert scancontrol.status is ScanControlStatus.Idle


def test_burst_signals_produce_one_waveform_per_average_cycle():
    asyncio.run(_run_burst_test())


async def _run_burst_test():
    client = MenloScanControlClient()
    scancontrol = BurstingScanControl(target_count=10)
    captured = []

    await client._acquire_on_client_loop(
        SimpleNamespace(scancontrol=scancontrol),
        AcquisitionPlan(average=4, count=10),
        threading.Event(),
        threading.Event(),
        captured.append,
    )

    assert len(captured) == 10


def test_single_average_accepts_one_as_reset_state():
    asyncio.run(_run_single_average_test())


async def _run_single_average_test():
    client = MenloScanControlClient()
    scancontrol = SingleAverageScanControl()
    captured = []

    await client._acquire_on_client_loop(
        SimpleNamespace(scancontrol=scancontrol),
        AcquisitionPlan(average=1, count=1),
        threading.Event(),
        threading.Event(),
        captured.append,
    )

    assert len(captured) == 1


def test_inclusive_interval_is_measured_start_to_start():
    asyncio.run(_run_interval_test(inclusive=True))


def test_exclusive_interval_starts_after_measurement_completion():
    asyncio.run(_run_interval_test(inclusive=False))


async def _run_interval_test(inclusive):
    client = MenloScanControlClient()
    scancontrol = TimedScanControl(averaging_seconds=0.05)
    captured = []

    await client._acquire_on_client_loop(
        SimpleNamespace(scancontrol=scancontrol),
        AcquisitionPlan(
            average=4,
            count=2,
            interval_seconds=0.1,
            interval_inclusive=inclusive,
        ),
        threading.Event(),
        threading.Event(),
        captured.append,
    )

    assert len(captured) == 2
    start_interval = scancontrol.reset_times[1] - scancontrol.reset_times[0]
    if inclusive:
        assert 0.08 <= start_interval < 0.13
    else:
        assert 0.13 <= start_interval < 0.20
