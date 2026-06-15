from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from catx.core.paths import PROJECT_ROOT


@dataclass(frozen=True)
class ReleaseInfo:
    version: str = "unknown"
    release_date: str = "unknown"
    build: str = "unknown"

    @property
    def display_date(self) -> str:
        try:
            return date.fromisoformat(self.release_date).strftime("%d %B %Y")
        except ValueError:
            return self.release_date


def load_release_info() -> ReleaseInfo:
    try:
        values = json.loads((PROJECT_ROOT / "version.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ReleaseInfo()
    return ReleaseInfo(
        version=str(values.get("version", "unknown")),
        release_date=str(values.get("release_date", "unknown")),
        build=str(values.get("build", "unknown")),
    )


RELEASE_INFO = load_release_info()


def about_text(product_name: str, description: str) -> str:
    return (
        f"{product_name}\n\n"
        f"{description}\n\n"
        f"Version: {RELEASE_INFO.version}\n"
        f"Release date: {RELEASE_INFO.display_date}\n"
        f"Build: {RELEASE_INFO.build}"
    )
