"""Exercises `scripts/check_yaml.py` as a subprocess — the same way pre-commit
invokes it. `sys.executable` is the hatch-test env's own interpreter (not a
bare `python3`), so this stays within the project's `hatch`-only rule."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_yaml.py"


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_yaml_passes(tmp_path: Path) -> None:
    f = tmp_path / "ok.yaml"
    f.write_text("a: 1\nb: 2\n")

    result = _run(f)

    assert result.returncode == 0
    assert result.stderr == ""


def test_invalid_yaml_fails(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text("a: [1, 2\n")  # unclosed bracket

    result = _run(f)

    assert result.returncode == 1
    assert "invalid YAML" in result.stderr


def test_duplicate_key_fails(tmp_path: Path) -> None:
    f = tmp_path / "dup.yaml"
    f.write_text("a: 1\na: 2\n")

    result = _run(f)

    assert result.returncode == 1
    assert "duplicate key" in result.stderr
    assert "'a'" in result.stderr


def test_multiple_files_reports_only_the_bad_one(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text("a: 1\n")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: 1\na: 2\n")

    result = _run(good, bad)

    assert result.returncode == 1
    assert "good.yaml" not in result.stderr
    assert "bad.yaml" in result.stderr


def test_no_files_passes(tmp_path: Path) -> None:
    result = _run()

    assert result.returncode == 0
