from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from catx.models.acquisition import AcquisitionMode, AcquisitionPlan, Waveform
from scancontrolclient import ScanControlClient, ScanControlStatus


@dataclass(frozen=True)
class MenloStatus:
    code: int
    name: str


class MenloScanControlClient:
    """Thin boundary around the existing Menlo ScanControl websocket client."""

    def __init__(self, host: str = "localhost", port: str = "8002"):
        self.host = host
        self.port = port
        self._client: ScanControlClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._connection_error: BaseException | None = None
        self._lock = threading.Lock()

    def connect(self, timeout: float = 5.0) -> ScanControlClient:
        with self._lock:
            if self._client is not None:
                return self._client
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._connection_error = None
                self._thread = threading.Thread(
                    target=self._run_client_loop,
                    name="MenloScanControl",
                    daemon=True,
                )
                self._thread.start()

        if not self._ready.wait(timeout):
            raise TimeoutError(
                f"ScanControl did not respond at {self.host}:{self.port} within {timeout:g} seconds"
            )
        if self._connection_error is not None:
            raise ConnectionError(
                f"Could not connect to ScanControl at {self.host}:{self.port}"
            ) from self._connection_error
        if self._client is None:
            raise ConnectionError("ScanControl connection ended before initialization")
        return self._client

    def _run_client_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            client = ScanControlClient(loop=loop)
            client.connect(host=self.host, port=self.port)
            self._client = client
        except BaseException as exc:
            self._connection_error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        loop.run_forever()

    async def status(self) -> MenloStatus:
        client = await asyncio.to_thread(self.connect)
        if self._loop is None:
            raise RuntimeError("ScanControl event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self._read_status(client), self._loop
        )
        return await asyncio.wrap_future(future)

    @staticmethod
    async def _read_status(client: ScanControlClient) -> MenloStatus:
        await asyncio.sleep(0.1)
        code = int(client.scancontrol.status)
        return MenloStatus(code=code, name=ScanControlStatus(code).name)

    async def acquire(
        self,
        plan: AcquisitionPlan,
        stop_event: threading.Event,
        pause_event: threading.Event,
    ):
        client = await asyncio.to_thread(self.connect)
        if self._loop is None:
            raise RuntimeError("ScanControl event loop is unavailable")
        caller_loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def emit(waveform: Waveform) -> None:
            caller_loop.call_soon_threadsafe(queue.put_nowait, waveform)

        future = asyncio.run_coroutine_threadsafe(
            self._acquire_on_client_loop(
                client, plan, stop_event, pause_event, emit
            ),
            self._loop,
        )

        def completed(done_future) -> None:
            try:
                done_future.result()
            except BaseException as exc:
                caller_loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                caller_loop.call_soon_threadsafe(queue.put_nowait, None)

        future.add_done_callback(completed)
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def _acquire_on_client_loop(
        self,
        client: ScanControlClient,
        plan: AcquisitionPlan,
        stop_event: threading.Event,
        pause_event: threading.Event,
        emit,
    ) -> None:
        scancontrol = client.scancontrol
        pulse_queue: asyncio.Queue = asyncio.Queue()
        accepting_pulse = False

        def pulse_ready(data) -> None:
            nonlocal accepting_pulse
            if accepting_pulse and int(scancontrol.currentAverages) == plan.average:
                accepting_pulse = False
                pulse_queue.put_nowait(data)

        scancontrol.pulseReady.connect(pulse_ready)
        completed = 0
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        measurement_started_at = started_at
        scan_started = False
        try:
            status = ScanControlStatus(int(scancontrol.status))
            if status is not ScanControlStatus.Idle:
                raise RuntimeError(
                    f"ScanControl must be Idle before acquisition; current status is {status.name}"
                )
            await self._call(scancontrol.setDesiredAverages, plan.average)
            await self._reset_averaging(scancontrol, plan.average)
            measurement_started_at = loop.time()
            accepting_pulse = True
            await self._call(scancontrol.start)
            scan_started = True
            await self._wait_for_status(
                scancontrol, ScanControlStatus.Acquiring, timeout=5.0
            )
            while not stop_event.is_set():
                elapsed = asyncio.get_running_loop().time() - started_at
                if plan.mode is AcquisitionMode.COUNT and completed >= plan.count:
                    break
                if plan.mode is AcquisitionMode.TIME and elapsed >= plan.duration_seconds:
                    break
                while pause_event.is_set() and not stop_event.is_set():
                    await asyncio.sleep(0.1)
                try:
                    data = await asyncio.wait_for(pulse_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                amplitudes = data.get("amplitude", [])
                time_axis = data.get("timeaxis")
                if not amplitudes or time_axis is None:
                    continue
                waveform = Waveform(
                    time_axis=np.asarray(time_axis, dtype=np.float64).copy(),
                    amplitude=np.asarray(amplitudes[0], dtype=np.float64).copy(),
                    captured_at=datetime.now(timezone.utc),
                    rate=float(scancontrol.rate) if scancontrol.rate is not None else None,
                    scancontrol_timestamp=(
                        int(data["timestamp"])
                        if data.get("timestamp") is not None
                        else None
                    ),
                    pulse_flags=int(data.get("flags", 0)),
                )
                completed += 1
                emit(waveform)
                elapsed = loop.time() - started_at
                if plan.mode is AcquisitionMode.COUNT and completed >= plan.count:
                    break
                if plan.mode is AcquisitionMode.TIME and elapsed >= plan.duration_seconds:
                    break
                if plan.interval_seconds > 0:
                    if plan.interval_inclusive:
                        next_start = measurement_started_at + plan.interval_seconds
                        await self._wait_until(next_start, stop_event, pause_event)
                    else:
                        await self._wait_until(
                            loop.time() + plan.interval_seconds,
                            stop_event,
                            pause_event,
                        )
                if stop_event.is_set():
                    break
                await self._reset_averaging(scancontrol, plan.average)
                measurement_started_at = loop.time()
                accepting_pulse = True
        finally:
            scancontrol.pulseReady.disconnect(pulse_ready)
            await self._call(scancontrol.setDesiredAverages, 1)
            if scan_started:
                await self._call(scancontrol.stop)

    @staticmethod
    async def _wait_until(
        deadline: float,
        stop_event: threading.Event,
        pause_event: threading.Event,
    ) -> None:
        loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            if pause_event.is_set():
                await asyncio.sleep(0.05)
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.05, remaining))

    @staticmethod
    async def _wait_for_status(scancontrol, expected: ScanControlStatus, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if ScanControlStatus(int(scancontrol.status)) is expected:
                return
            await asyncio.sleep(0.05)
        current = ScanControlStatus(int(scancontrol.status))
        raise TimeoutError(
            f"ScanControl did not enter {expected.name}; current status is {current.name}"
        )

    @staticmethod
    async def _reset_averaging(
        scancontrol, desired_averages: int, attempts: int = 2
    ) -> None:
        for attempt in range(attempts):
            await MenloScanControlClient._call(scancontrol.resetAveraging)
            if desired_averages == 1:
                # ScanControl may report 1 as both the reset and completed state.
                await asyncio.sleep(0.05)
                return
            try:
                await MenloScanControlClient._wait_for_averaging_reset(
                    scancontrol, desired_averages
                )
                return
            except TimeoutError:
                if attempt + 1 == attempts:
                    raise

    @staticmethod
    async def _wait_for_averaging_reset(
        scancontrol, desired_averages: int, timeout: float = 2.0
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if int(scancontrol.currentAverages) < desired_averages:
                return
            await asyncio.sleep(0.01)
        raise TimeoutError(
            "ScanControl did not confirm that the averaging buffer was reset"
        )

    @staticmethod
    async def _call(method, *args) -> None:
        # The bundled PyWebChannel wrapper sends remote void methods immediately.
        # They still run on ScanControlClient's event loop, as required by the ICD.
        method(*args)
        await asyncio.sleep(0)
