# Set up Fledermap

Fledermap is self-hosted: you run it on your own machine or server, against
your own PostgreSQL database and your own folder of detector recordings.
There's no separate "install" and "configure" phase you can do in either
order — the systemd services this guide sets up don't inherit your shell's
environment variables, so writing the config file comes before installing
those services, not after.

## 1. Prerequisites

- **PostgreSQL ≥ 13 with the PostGIS ≥ 3 extension.** The test suite pins
  `postgis/postgis:16-3.4`, so that combination is the most exercised.
- **`ffmpeg` and `ffprobe` on `PATH`.** Fledermap shells out to `ffmpeg` to
  produce the compressed audio previews (including heterodyne/time-expanded
  playback) you'll actually listen to.
- **Linux with a systemd `--user` session**, for the persistent
  install this guide walks through. (Fledermap itself isn't
  Linux-specific, but `fledermap install` is.)
- **A Wildlife Acoustics EMT recording archive** — a folder your detector
  syncs its `.wav` files into (see the README for which devices are
  currently recognized).

There's no published package yet, so install from a checkout:

```bash
git clone https://github.com/bytehexe/fledermap.git
cd fledermap
pipx install .
```

## 2. Create the database

```bash
sudo -u postgres createuser --pwprompt fledermap
sudo -u postgres createdb --owner=fledermap bats_db
sudo -u postgres psql -d bats_db -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

`sudo -u postgres` is needed because a fresh PostgreSQL install only trusts
the `postgres` OS user to connect without a password, and only the
`postgres` role can create roles/databases by default. Once `fledermap`'s
own role exists, everything from here on connects as that role directly.

Fledermap builds its own schema on first run — there's no separate
migration step to run by hand.

## 3. Write a config file

Create `~/.config/fledermap/config.toml` (the default location; see
[Configuration](../reference/configuration.md) if you'd rather point at a
different file or use environment variables instead):

```toml
database_url = "postgresql://fledermap:<password>@localhost/bats_db"

# archive_roots -- where your recordings actually live. Fledermap only
# ever reads from here, never moves, renames, or writes anything into it.
archive_roots = ["/path/to/your/detector/archive"]
```

That's everything required — every other setting has a default. If you
want Fledermap's generated data (spectrograms, audio previews) stored
somewhere specific rather than its default location, see `media_root` in
[Configuration](../reference/configuration.md). For what the
archive/database/media split actually means, see
[How Fledermap is organized](../explanation/how-fledermap-is-organized.md).

## 4. Install the services

```bash
fledermap install
```

This generates and enables three systemd `--user` units —
`fledermap-serve.service` (the web map), `fledermap-worker.service` (the
background job that ingests new recordings and renders their media), and
`fledermap.target` grouping both — so they survive logout and reboot
without a terminal staying open. It's safe to re-run any time (e.g. after
upgrading).

Once installed, `fledermap-worker` scans your archive on its own, every few
minutes, with no further action from you. Add new recordings to the
archive folder and they'll appear on the map on their own; you never need
to run an ingest command by hand for normal use (see
[CLI commands](../reference/cli.md) if you ever do — a fresh manual scan
after moving the archive to a new path, for instance).

## 5. Open the map

By default, `http://127.0.0.1:5000`. The first time `fledermap-serve`
starts, it fetches the (small) JS/CSS libraries the map page needs and
caches them locally — this needs real internet access once, and nothing
further afterward.

## Upgrading

```bash
cd fledermap   # your checkout
git pull
pipx install --force .
fledermap install --restart
```

`pipx install --force .` reinstalls into the same pipx-managed virtualenv
the services already point at; `--restart` (rather than a plain re-run of
`fledermap install`) is what actually picks up the new code, since
`systemctl --user enable --now` alone only starts units that aren't already
running.
