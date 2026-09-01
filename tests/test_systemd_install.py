from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.services.systemd_install import render_unit_files, systemd_user_dir


def test_systemd_user_dir_respects_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/config")

    assert systemd_user_dir() == Path("/custom/config/systemd/user")


def test_systemd_user_dir_falls_back_to_home_dot_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/janna"))

    assert systemd_user_dir() == Path("/home/janna/.config/systemd/user")


def test_render_unit_files_names_the_three_units() -> None:
    units = render_unit_files("/home/janna/.local/bin/fledermap")

    assert set(units) == {
        "fledermap.target",
        "fledermap-serve.service",
        "fledermap-worker.service",
    }


def test_render_unit_files_bakes_the_absolute_exe_path_into_each_service() -> None:
    units = render_unit_files("/home/janna/.local/bin/fledermap")

    assert (
        "ExecStart=/home/janna/.local/bin/fledermap serve"
        in units["fledermap-serve.service"]
    )
    assert (
        "ExecStart=/home/janna/.local/bin/fledermap worker"
        in units["fledermap-worker.service"]
    )


def test_render_unit_files_services_restart_together_via_partof() -> None:
    units = render_unit_files("/usr/bin/fledermap")

    assert "PartOf=fledermap.target" in units["fledermap-serve.service"]
    assert "PartOf=fledermap.target" in units["fledermap-worker.service"]


def test_render_unit_files_target_wants_both_services() -> None:
    units = render_unit_files("/usr/bin/fledermap")

    target = units["fledermap.target"]
    assert "Wants=fledermap-serve.service fledermap-worker.service" in target
    assert "WantedBy=default.target" in target
