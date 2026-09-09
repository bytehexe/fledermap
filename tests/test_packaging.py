"""Confirms files the running package needs actually reach a real install --
this project has already been bitten twice by code/data that exists in the
repo but never made it into the built wheel: `fetch_vendor_assets.py` living
under `scripts/` (never part of a built wheel by design), and `alembic/`'s
migration scripts living outside `src/fledermap/` (an accident, not a design
choice -- hatchling's default src-layout packaging only ships `src/fledermap/**`,
so anything outside it is invisible to a real `pip`/`pipx` install even though
`fledermap serve`/`worker`/etc. need it at every startup to run migrations).

This builds the wheel and inspects it directly rather than trusting the source
tree -- matching this project's own documented verification convention
(CLAUDE.md's "scripts/" bullet: "Verify with `hatch build -t wheel` +
`python3 -m zipfile -l dist/*.whl`, not by inspecting the source tree.").
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wheel_ships_the_alembic_migration_scripts(tmp_path: Path) -> None:
    """Regression test for the packaging bug where `alembic/` lived outside
    `src/fledermap/` and never made it into the built wheel: a real (pipx or
    `pip install`) install of `fledermap` then failed every command that
    touches the database with
    `alembic.util.exc.CommandError: Path doesn't exist: .../alembic. Please
    use the 'init' command to create a new scripts folder.` -- because there
    was no `alembic/` directory anywhere in the installed environment for any
    path computation to find, correct or not."""
    dist_dir = tmp_path / "dist"
    # This test runs inside `hatch test`'s own managed environment, which sets
    # HATCH_ENV_ACTIVE (e.g. "hatch-test.py3.14") in this process's environment.
    # The nested `hatch build` below inherits that by default and tries to
    # reuse it as its build environment, which newer hatch rejects with
    # "Environment `hatch-test.py3.14` is not a builder environment" --
    # confirmed on hatch 1.18.0 in CI (unpinned `pip install hatch`), though
    # not on the 1.14.2 pinned locally, which predates the check. Strip it so
    # the nested build always picks its own isolated build environment.
    env = {k: v for k, v in os.environ.items() if k != "HATCH_ENV_ACTIVE"}
    result = subprocess.run(
        ["hatch", "build", "-t", "wheel", str(dist_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"hatch build failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

    wheel_path = next(dist_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as whl:
        names = whl.namelist()

    assert "fledermap/alembic/env.py" in names
    version_scripts = [
        name
        for name in names
        if name.startswith("fledermap/alembic/versions/") and name.endswith(".py")
    ]
    assert version_scripts, (
        "no migration scripts shipped in the wheel -- fledermap/alembic/versions/ "
        f"is missing or empty; wheel contents: {names}"
    )
