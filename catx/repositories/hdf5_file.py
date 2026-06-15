from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py


class Hdf5FileRepository:
    """Store small application records as JSON in an HDF5 container."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load_records(self, group_name: str) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with h5py.File(self.path, "r") as handle:
            group = handle.get(group_name)
            if group is None:
                return []
            records = []
            for key in sorted(group.keys()):
                value = group[key][()]
                if isinstance(value, bytes):
                    value = value.decode("utf-8")
                records.append(json.loads(str(value)))
            return records

    def save_records(self, group_name: str, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(self.path, "a") as handle:
            if group_name in handle:
                del handle[group_name]
            group = handle.create_group(group_name)
            string_type = h5py.string_dtype(encoding="utf-8")
            for index, record in enumerate(records):
                group.create_dataset(
                    f"{index:04d}",
                    data=json.dumps(record, ensure_ascii=True),
                    dtype=string_type,
                )
