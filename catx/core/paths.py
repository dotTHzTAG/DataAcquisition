from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
UI_DIR = PROJECT_ROOT / "ui"
RESOURCE_DIR = PROJECT_ROOT / "resources"

PROFILE_DATABASE = PROJECT_ROOT / "profile_database.h5"
APPLICATION_LOG = PROJECT_ROOT / "data_acquisition.log"
