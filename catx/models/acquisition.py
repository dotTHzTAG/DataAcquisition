from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import ceil

import numpy as np


class AcquisitionMode(str, Enum):
    COUNT = "count"
    TIME = "time"


class TimeUnit(str, Enum):
    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"


@dataclass(frozen=True)
class AcquisitionPlan:
    average: int = 1
    mode: AcquisitionMode = AcquisitionMode.COUNT
    count: int = 1
    duration: int = 0
    duration_unit: TimeUnit = TimeUnit.SECONDS
    interval_seconds: int = 0
    interval_inclusive: bool = False
    autosave: bool = True

    @property
    def duration_seconds(self) -> int:
        multipliers = {
            TimeUnit.SECONDS: 1,
            TimeUnit.MINUTES: 60,
            TimeUnit.HOURS: 3600,
        }
        return self.duration * multipliers[self.duration_unit]

    @property
    def estimated_measurement_count(self) -> int:
        if self.mode is AcquisitionMode.COUNT:
            return self.count
        if self.interval_seconds > 0:
            return max(1, ceil(self.duration_seconds / self.interval_seconds))
        return max(1, self.duration_seconds)

    def estimated_measurement_count_at_rate(self, rate: float | None) -> int:
        if self.mode is AcquisitionMode.COUNT or self.interval_seconds > 0:
            return self.estimated_measurement_count
        if rate is None or rate <= 0:
            return self.estimated_measurement_count
        return max(1, ceil(self.duration_seconds * rate / self.average))


@dataclass(frozen=True)
class Waveform:
    time_axis: np.ndarray
    amplitude: np.ndarray
    captured_at: datetime
    rate: float | None = None
    scancontrol_timestamp: int | None = None
    pulse_flags: int = 0


@dataclass(frozen=True)
class AcquisitionProgress:
    completed: int
    total: int | None
    elapsed_seconds: float
    duration_seconds: float | None

    @property
    def remaining_seconds(self) -> float | None:
        if self.total and self.completed:
            seconds_per_measurement = self.elapsed_seconds / self.completed
            return max(0.0, seconds_per_measurement * (self.total - self.completed))
        if self.duration_seconds is not None:
            return max(0.0, self.duration_seconds - self.elapsed_seconds)
        return None

    @property
    def percent(self) -> int:
        if self.total:
            return min(100, round(self.completed / self.total * 100))
        if self.duration_seconds:
            return min(100, round(self.elapsed_seconds / self.duration_seconds * 100))
        return 0
