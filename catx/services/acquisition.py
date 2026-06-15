from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import numpy as np

from catx.menlo.client import MenloScanControlClient, MenloStatus
from catx.models.acquisition import AcquisitionMode, AcquisitionPlan, AcquisitionProgress
from catx.repositories.project import ProjectRepository


@dataclass
class AcquisitionService:
    menlo: MenloScanControlClient | None = None
    projects: ProjectRepository | None = None

    def __post_init__(self) -> None:
        if self.menlo is None:
            self.menlo = MenloScanControlClient()
        if self.projects is None:
            self.projects = ProjectRepository()

    async def read_status(self) -> MenloStatus:
        if self.menlo is None:
            raise RuntimeError("Menlo client is not configured")
        return await self.menlo.status()

    async def connect(self) -> MenloStatus:
        return await self.read_status()

    def validate_plan(self, plan: AcquisitionPlan) -> None:
        if plan.average < 1:
            raise ValueError("Average count must be at least 1")
        if plan.count < 1:
            raise ValueError("Measurement count must be at least 1")
        if plan.interval_seconds < 0:
            raise ValueError("Interval time cannot be negative")
        if plan.mode is AcquisitionMode.TIME and plan.duration_seconds < 1:
            raise ValueError("Measurement duration must be at least 1 second")

    async def acquire_to_project(
        self,
        project_path: Path,
        kind: str,
        plan: AcquisitionPlan,
        attributes: dict,
        stop_event: threading.Event,
        pause_event: threading.Event,
        reference: np.ndarray | None = None,
        baseline: np.ndarray | None = None,
        calibration_callback: Callable[[np.ndarray], None] | None = None,
        progress_callback: Callable[[AcquisitionProgress], None] | None = None,
    ) -> int:
        self.validate_plan(plan)
        if self.menlo is None or self.projects is None:
            raise RuntimeError("Acquisition service is not configured")

        started_at = monotonic()
        completed = 0
        async for waveform in self.menlo.acquire(plan, stop_event, pause_event):
            if kind in {"baseline", "reference"}:
                if calibration_callback is not None:
                    calibration_callback(
                        np.vstack((waveform.time_axis, waveform.amplitude))
                    )
            else:
                self.projects.append_measurement(
                    project_path,
                    waveform,
                    attributes,
                    reference=reference,
                    baseline=baseline,
                    estimated_count=plan.estimated_measurement_count_at_rate(
                        waveform.rate
                    ),
                )
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    AcquisitionProgress(
                        completed=completed,
                        total=plan.count if plan.mode is AcquisitionMode.COUNT else None,
                        elapsed_seconds=monotonic() - started_at,
                        duration_seconds=(
                            plan.duration_seconds if plan.mode is AcquisitionMode.TIME else None
                        ),
                    )
                )
        return completed
