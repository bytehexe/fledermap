# Sound-level spectrum: major local maxima with always-on frequency labels — Design

Extends the Analysis view's sound-level spectrum
(`2026-09-18-fledermap-analysis-view-design.md` §5–§6), which today marks only the single global
peak, unlabelled apart from a legend entry.

## Goals

1. Mark the **major** local maxima of the sound-level spectrum, not every wiggle, in addition to
   the global peak.
2. Give every marked peak (global or local) an always-on label stating only its frequency
   (`45.2 kHz`).

## Decisions

- **Scope: always the span currently shown, as is.** Peaks are computed from the same samples as
  the curve — Whole file or Selection, Denoise on or off — by the same endpoint call. No separate
  "peaks over the whole file" pass.
- **Peak criteria** (`scipy.signal.find_peaks` on the curve **above the low-band cutoff**, the
  same `peak_min_freq_hz` region the global-peak search already uses):
  - `prominence >= 8 dB` — drops bumps that do not stand out from their surroundings.
  - `height >= -20 dB` (re the search peak, which is what `db` is already normalised to) — a
    prominent hump can still sit at the noise floor (reference 2's 21 kHz hump: 10 dB prominent,
    at −34 dB). Prominence alone cannot reject it; a level floor does.
  - **At most 5 peaks**, keeping the most prominent, drawn in frequency order.
  - The global peak (`peak_freq_hz`, when not `None`) is always among them.
  - `find_peaks` never returns an endpoint bin, so the boundary artifact fixed in `baebb22` (the
    cutoff bin reported as a peak) cannot recur for the local maxima.
- **Response:** the endpoint keeps `peak_freq_hz` and adds `peaks_freq_hz` (ascending list, always
  a superset of the global peak when that is not `None`).
- **Labels:** frequency only, one decimal, in kHz, same rounding as the legend today. Drawn above
  the marker, centred, clamped to the chart area. A label that would overlap the previous one
  horizontally is raised by one text height rather than dropped or hidden ("always-on").
  `layout.padding.top` leaves room so the label of a 0 dB peak is not clipped.
- **Legend:** the single `Peak frequency (x kHz, bin width …)` entry becomes `Peaks (bin width …
  kHz)`; the frequencies now live on the chart. The bin-width note stays — a peak can only be
  located to within one FFT bin, which matters most for a short Selection.
- **Marker style:** every peak uses the existing red marker.
- **No new dependency.** Labels are drawn by a small inline Chart.js plugin (`afterDatasetsDraw`,
  `ctx.fillText`), text colour read from `--color-text` via `getComputedStyle` so dark mode works
  (a canvas cannot resolve CSS variables itself).

## Placement (style-guide "decide element placement once")

The label joins a chart region that holds only the curve, the marker and a legend row — below the
"2+ existing elements" mockup threshold, and nothing else in the app labels chart points. The
placement above (over the marker, centred) is the decision; no other page has a comparable
element to sweep.

## Reference cases

Real recordings on Janna's instance (identified by `audio_hash`); Whole file, Denoise off. The
expected counts are Janna's reading of the curves. The curves themselves are frozen as fixtures in
`tests/data/sound_level_spectra/` (bin width, bin count, dB values — the audio is not in the repo).

| # | Fixture | `audio_hash` | Expected | Peaks found (≥ 8 dB prom., ≥ −20 dB) |
|---|---|---|---|---|
| 1 | `three_peaks_11_21_45khz.json` | `34c42c3f6fd94bb335d892979db292f7eabb5e39c011b414aa29840fc141cdfc` | 3 incl. global | 11.0 kHz (0 dB), 20.9 kHz (−15 dB), 44.9 kHz (−3 dB) |
| 2 | `one_dominant_peak_53khz.json` | `b4d1f3016445b45413411dc024d295c3e69734b1942898942e0dbe3a831d29be` | 1 or 2 incl. global | 53.0 kHz (0 dB) only |
| 3 | `two_peaks_11_27khz.json` | `bf283f750fab62c2108ede994d41e38b0859f10b72d5c612464fe54e24d4fd65` | 2 incl. global | 10.9 kHz (0 dB), 27.0 kHz (−8 dB) |

What each case constrains:

- **1** — needs the 45 kHz step-shaped maximum (a steep rise, 17 dB prominent) *and* the broad
  21 kHz hump (11 dB), while rejecting the 34/36/38 kHz jitter (prominence 4–5 dB, sitting at
  −20 dB). The margin against that jitter is why the threshold is 8 dB, not 6.
- **2** — rejects the 21 kHz noise-floor hump (−34 dB, 10 dB prominent); the level floor is what
  does it. The 67 kHz shoulder (−15 dB, only 4 dB prominent) is the "1 or 2" ambiguity: excluded
  at 8 dB, admitted at 4 dB, and either reading satisfies the expectation.
- **3** — the easy case; both peaks are far above every threshold.

Prototype sweep (prominence 3–10 dB × level floor none/−20/−25 dB) over the three curves, judged
on which peaks come out, not just how many: below 6 dB reference 1 gains the jitter peaks; with no
floor, 6–8 dB gives reference 2 an acceptable count but the wrong peak (the −34 dB hump), and 10 dB
only passes by leaving 1 dB of margin against reference 1's 21 kHz hump (11 dB). A floor plus
6–10 dB prominence gets all three right; 8 dB / −20 dB was picked for the widest margin on both
sides (jitter at 5 dB, that hump at 11 dB). Three whole-file spectra is a thin basis — treat 8 dB / −20 dB / 5 as a starting point to
re-check against Selection spans (short spans have few Welch segments and a noisier curve) and
against more recordings.

## Testing

- **Python** (`tests/test_sound_level_spectrum.py`): the three fixtures assert the peak sets above
  (frequencies within one bin); synthetic multi-tone signals cover the cap of 5 (keeps the most
  prominent), the level floor, the global peak always being present, and no peak on the cutoff
  boundary.
- **Endpoint:** `peaks_freq_hz` present, ascending, and containing `peak_freq_hz`.
- **JS (`node --test`):** `analysis_chart.js` builds one marker point per peak and the new legend
  label.
- **Live (headless Chrome, mandatory — canvas drawing):** every label present, none clipped at the
  top or sides, staggering works for two close peaks, dark mode text colour, Whole file ↔
  Selection switch redraws labels.

## Files touched

- `src/fledermap/media/sound_level_spectrum.py` — peak finder; `SoundLevelSpectrum.peaks_freq_hz`.
- `src/fledermap/web/views/media.py` — add `peaks_freq_hz` to the JSON.
- `src/fledermap/web/static/analysis_chart.js` — markers for all peaks, legend text, label plugin
  and top padding; pure reshaping stays DOM-free and node-testable, the plugin's drawing is not.
- `tests/data/sound_level_spectra/*.json`, `tests/test_sound_level_spectrum.py`, JS tests.
- `docs/style-guide.md` — only if the label plugin's contract warrants a note.
