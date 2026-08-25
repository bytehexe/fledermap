"""Exercises `scripts/check_commit_msg.py` as a subprocess — the same way
pre-commit's `commit-msg` stage invokes it (one argument: the path to a file
holding the drafted commit message)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_commit_msg.py"


def _run(message: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(message)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(msg_file)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "subject",
    [
        "feat: add site clustering",
        "fix: guard root-relative hidden-path check",
        "docs: record phase 2's database invariants",
        "test: cover the same NaN guard for site_eps_m",
        "chore: correct stale comments",
        "feat(cli): add derive command",
        "fix!: breaking change to the config schema",
    ],
)
def test_conventional_subject_passes(subject: str, tmp_path: Path) -> None:
    result = _run(f"{subject}\n\nbody text\n", tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "subject",
    [
        "added site clustering",
        "Fix: wrong capitalisation of the type",
        "WIP",
        "feat added site clustering",  # missing colon
        "unknowntype: add site clustering",
    ],
)
def test_non_conventional_subject_fails(subject: str, tmp_path: Path) -> None:
    result = _run(f"{subject}\n\nbody text\n", tmp_path)

    assert result.returncode == 1
    assert subject in result.stderr


def test_wrong_argument_count_is_a_usage_error(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "usage" in result.stderr
