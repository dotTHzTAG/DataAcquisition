from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from catx.models.acquisition import Waveform


class ProjectRepository:
    EXTENSION = ".thz"
    PROJECT_METADATA_ATTRIBUTE = "project_metadata"
    NUMBERING_WIDTHS_ATTRIBUTE = "numbering_widths"

    @classmethod
    def normalize_path(cls, path: str | Path) -> Path:
        result = Path(path)
        if result.suffix.lower() != cls.EXTENSION:
            result = result.with_suffix(cls.EXTENSION)
        return result

    def create(self, path: str | Path) -> Path:
        project_path = self.normalize_path(path)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(project_path, "w") as handle:
            handle.attrs["format"] = "dotTHz"
            handle.attrs["created_utc"] = datetime.now(timezone.utc).isoformat()
        return project_path

    def update_metadata(self, path: Path, values: dict[str, Any]) -> None:
        with h5py.File(path, "a") as handle:
            metadata = self._decode_project_metadata(handle)
            metadata.update(values)
            handle.attrs[self.PROJECT_METADATA_ATTRIBUTE] = json.dumps(
                metadata, ensure_ascii=True
            )

    def load_metadata(self, path: str | Path) -> dict[str, Any]:
        project_path = Path(path)
        with h5py.File(project_path, "r") as handle:
            return self._decode_project_metadata(handle)

    def measurement_count(self, path: str | Path) -> int:
        with h5py.File(path, "r") as handle:
            return sum(isinstance(item, h5py.Group) for item in handle.values())

    def clear_measurements(self, path: str | Path) -> int:
        project_path = Path(path)
        with h5py.File(project_path, "r") as handle:
            count = sum(isinstance(item, h5py.Group) for item in handle.values())
            root_attributes = {
                key: value
                for key, value in handle.attrs.items()
                if key
                not in {
                    self.PROJECT_METADATA_ATTRIBUTE,
                    self.NUMBERING_WIDTHS_ATTRIBUTE,
                }
            }

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=project_path.parent,
                prefix=f".{project_path.stem}-reset-",
                suffix=project_path.suffix,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

            with h5py.File(temporary_path, "w") as handle:
                for key, value in root_attributes.items():
                    handle.attrs[key] = value
                handle.flush()

            os.replace(temporary_path, project_path)
            return count
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def copy(self, source: Path, destination: str | Path) -> Path:
        destination_path = self.normalize_path(destination)
        shutil.copy2(source, destination_path)
        return destination_path

    def update_measurement_attributes(
        self, path: str | Path, group_path: str, values: dict[str, Any]
    ) -> None:
        with h5py.File(path, "a") as handle:
            group = handle[group_path]
            if not isinstance(group, h5py.Group):
                raise ValueError(f"{group_path} is not a measurement group")
            for key, value in values.items():
                if value in (None, ""):
                    if key in group.attrs:
                        del group.attrs[key]
                else:
                    group.attrs[key] = self._attribute_value(value)
            handle.flush()

    def remove_measurement(self, path: str | Path, group_path: str) -> None:
        with h5py.File(path, "a") as handle:
            group = handle[group_path]
            if not isinstance(group, h5py.Group):
                raise ValueError(f"{group_path} is not a measurement group")
            del handle[group_path]
            handle.flush()

    def append_measurement(
        self,
        path: Path,
        waveform: Waveform,
        attributes: dict[str, Any],
        reference: np.ndarray | None = None,
        baseline: np.ndarray | None = None,
        estimated_count: int = 1,
    ) -> str:
        with h5py.File(path, "a") as handle:
            prefix = self._safe_group_prefix(attributes.get("sample", "measurement"))
            existing_suffixes = [
                match.group(1)
                for name, item in handle.items()
                if isinstance(item, h5py.Group)
                and (match := re.fullmatch(rf"{re.escape(prefix)}_(\d+)", name))
            ]
            existing_indices = [int(suffix) for suffix in existing_suffixes]
            index = max(existing_indices, default=0) + 1
            widths = self._decode_json_attribute(
                handle.attrs.get(self.NUMBERING_WIDTHS_ATTRIBUTE)
            )
            width = int(widths.get(prefix, 0))
            if width < 1:
                width = max(
                    1,
                    len(str(max(estimated_count, 1))) + 1,
                    max((len(suffix) for suffix in existing_suffixes), default=0),
                )
                widths[prefix] = width
                handle.attrs[self.NUMBERING_WIDTHS_ATTRIBUTE] = json.dumps(
                    widths, ensure_ascii=True
                )
            group_name = f"{prefix}_{index:0{width}d}"
            group = handle.create_group(group_name)
            group.create_dataset(
                "ds1", data=np.vstack((waveform.time_axis, waveform.amplitude))
            )
            descriptions = ["Sample"]
            if reference is not None:
                group.create_dataset("ds2", data=reference)
                descriptions.append("Reference")
            if baseline is not None:
                group.create_dataset("ds3", data=baseline)
                descriptions.append("Baseline")
            group.attrs["dsDescription"] = ",".join(descriptions)
            group.attrs["time"] = waveform.captured_at.isoformat()
            if waveform.rate is not None:
                group.attrs["rate"] = waveform.rate
            if waveform.scancontrol_timestamp is not None:
                group.attrs["scancontrol_timestamp"] = waveform.scancontrol_timestamp
            group.attrs["pulse_flags"] = waveform.pulse_flags
            for key, value in attributes.items():
                group.attrs[key] = self._attribute_value(value)
            handle.flush()
            return f"/{group_name}"

    @classmethod
    def _decode_project_metadata(cls, handle: h5py.File) -> dict[str, Any]:
        return cls._decode_json_attribute(
            handle.attrs.get(cls.PROJECT_METADATA_ATTRIBUTE)
        )

    @staticmethod
    def _decode_json_attribute(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            decoded = json.loads(str(value))
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _safe_group_prefix(value: Any) -> str:
        prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
        prefix = prefix.strip("._-")
        return prefix or "measurement"

    @staticmethod
    def _attribute_value(value: Any):
        if value is None:
            return ""
        if isinstance(value, (str, bytes, bool, int, float, np.number)):
            return value
        return json.dumps(value, ensure_ascii=True)
