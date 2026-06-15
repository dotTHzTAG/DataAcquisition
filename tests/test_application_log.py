from pathlib import Path

from catx.services.application_log import application_log_exceeds_limit


def test_application_log_exceeds_limit(tmp_path: Path) -> None:
    log_path = tmp_path / "application.log"
    log_path.write_bytes(b"12345")

    assert application_log_exceeds_limit(log_path, max_bytes=5)
    assert not application_log_exceeds_limit(log_path, max_bytes=6)


def test_missing_application_log_does_not_exceed_limit(tmp_path: Path) -> None:
    assert not application_log_exceeds_limit(tmp_path / "missing.log", max_bytes=1)
