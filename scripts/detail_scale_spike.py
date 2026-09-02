"""One-off visual comparison for the recording-details page's locked scale and FFT window (backlog:
'FFT params for the detail page', 'reconsider the locked scale itself'). Not part of the shipped
package -- dev-only, run manually against a real field recording, same category as this
directory's other git-hook tooling per CLAUDE.md.

Detail-page-only (2026-09-02 ruling): these candidates tune `services/recording_detail.py`'s
detail-only `SpectrogramParams` construction, never `media/spectrogram.py`'s shared class
defaults -- the drawer/overview's cached renders are a separate tuning question, deliberately not
assumed to want the same answer (the overview compresses far more time into the same screen
width).

Renders a fixed ~1s slice of a real recording at several `(window_ms, overlap, px_per_ms)`
combinations side by side as rows in one contact-sheet PNG, so the choice is a visual comparison,
not a numbers-only guess.

Usage:
    hatch run python scripts/detail_scale_spike.py <path-to-wav> [--start-s 0.0] [--duration-s 0.05]

Note on --duration-s: `duration_s * px_per_ms` must stay under WebP's 16383px encode limit for
every candidate (the highest `px_per_ms` in CANDIDATES governs) -- the default here is deliberately
short (one call, not a whole recording) both for that reason and because zooming into a single call
is a better test of sharpness than a wide, mostly-silent window anyway.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram

# (window_ms, overlap, px_per_ms) -- the shipped pre-2026-09-02 baseline first, then the two-round
# investigation's key points (full history, including the rejected middle candidates, is in the
# plan doc: docs/superpowers/plans/2026-09-02-fledermap-detail-page-wav-guard-and-scale-spike.md).
# Round 1 swept window_ms/px_per_ms and picked window_ms=1.5/px_per_ms=12.0; round 2 held those
# fixed and swept `overlap` upward, picking 0.85 (0.85->0.95 was only marginally sharper, for more
# render CPU per tile -- diminishing returns).
CANDIDATES: list[tuple[float, float, float]] = [
    (
        3.0,
        0.5,
        19.0,
    ),  # shipped baseline before this task (SpectrogramParams' own class defaults)
    (1.5, 0.5, 12.0),  # round 1's pick: narrower window + smaller scale
    (
        1.5,
        0.85,
        12.0,
    ),  # round 2's pick, and this task's final shipped DETAIL_WINDOW_MS/OVERLAP
]

ROW_LABEL_HEIGHT_PX = 24


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav_path", type=Path)
    parser.add_argument("--start-s", type=float, default=0.0)
    parser.add_argument("--duration-s", type=float, default=0.05)
    parser.add_argument(
        "--out", type=Path, default=Path("detail_scale_spike_contact_sheet.png")
    )
    args = parser.parse_args()

    rows: list[Image.Image] = []
    for window_ms, overlap, px_per_ms in CANDIDATES:
        width_px = round(args.duration_s * 1000 * px_per_ms)
        params = SpectrogramParams(
            window_ms=window_ms,
            overlap=overlap,
            width_px=width_px,
            height_px=282,  # half the shipped DETAIL_PX_PER_KHZ height -- plenty to compare shape
        )
        tmp_out = Path(f"_spike_tile_{window_ms}_{overlap}_{px_per_ms}.webp")
        render_spectrogram(
            args.wav_path,
            tmp_out,
            params=params,
            time_range_s=(args.start_s, args.start_s + args.duration_s),
        )
        tile = Image.open(tmp_out).convert("RGB")
        tmp_out.unlink()

        row = Image.new(
            "RGB", (tile.width, tile.height + ROW_LABEL_HEIGHT_PX), (255, 255, 255)
        )
        row.paste(tile, (0, ROW_LABEL_HEIGHT_PX))
        draw = ImageDraw.Draw(row)
        draw.text(
            (4, 4),
            f"window_ms={window_ms} overlap={overlap} px_per_ms={px_per_ms}",
            fill=(0, 0, 0),
        )
        rows.append(row)

    max_width = max(r.width for r in rows)
    total_height = sum(r.height for r in rows)
    sheet = Image.new("RGB", (max_width, total_height), (255, 255, 255))
    y = 0
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height
    sheet.save(args.out)
    print(f"wrote {args.out} ({sheet.width}x{sheet.height})")


if __name__ == "__main__":
    main()
