"""Generates the systemd `--user` unit files for `fledermap install`.

Pure text rendering only -- no filesystem or subprocess I/O, so it needs no
mocking to test (unlike the actual install, which writes files and shells out
to `systemctl`; that part lives in `cli/main.py`'s `install` command instead).

`fledermap-serve.service` and `fledermap-worker.service` each declare
`PartOf=fledermap.target` so stopping/restarting the target propagates to
both; `fledermap.target` declares `Wants=` both so *starting* the target
starts both too. `systemctl --user restart fledermap` alone would NOT reach
this target -- systemctl assumes `.service` when no unit type is given, so
the target must be addressed explicitly as `fledermap.target` (a deliberate,
discussed tradeoff, not an oversight).
"""

from __future__ import annotations

import os
from pathlib import Path

_SERVICE_TEMPLATE = """\
[Unit]
Description=Fledermap {description}
PartOf=fledermap.target

[Service]
ExecStart={exe} {subcommand}
Restart=on-failure

[Install]
WantedBy=fledermap.target
"""

_TARGET_TEMPLATE = """\
[Unit]
Description=Fledermap (web map + worker)
Wants=fledermap-serve.service fledermap-worker.service

[Install]
WantedBy=default.target
"""


def systemd_user_dir() -> Path:
    """Where systemd itself expects `--user` unit files -- its own
    convention (respects `$XDG_CONFIG_HOME`, falls back to `~/.config`),
    unrelated to fledermap's own `platformdirs`-based config/data/cache
    directories (`config.py`), which is why this doesn't reuse those."""
    base = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(base) if base else Path.home() / ".config"
    return config_home / "systemd" / "user"


def render_unit_files(exe: str) -> dict[str, str]:
    """`exe` is the absolute path to the `fledermap` executable, resolved by
    the caller (`shutil.which`) -- baked directly into `ExecStart=` rather
    than relying on `systemctl --user`'s own (narrower than an interactive
    shell's) default PATH to find it at service-start time."""
    return {
        "fledermap-serve.service": _SERVICE_TEMPLATE.format(
            description="web map",
            exe=exe,
            subcommand="serve",
        ),
        "fledermap-worker.service": _SERVICE_TEMPLATE.format(
            description="worker",
            exe=exe,
            subcommand="worker",
        ),
        "fledermap.target": _TARGET_TEMPLATE,
    }
