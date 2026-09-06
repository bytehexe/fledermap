# Listen to and read a recording

Every recording shows two synchronized views: an **oscillogram** (waveform
over time, above) and a **spectrogram** (frequency over time, color for
loudness, below) — the spectrogram is usually the more useful one for
telling calls apart, since a bat's call shape (its frequency sweep, its
pulse spacing) is exactly what a spectrogram makes visible. Move your
mouse over either and a small box next to the cursor shows the exact
time/frequency under it.

Click anywhere on either graph to start playback from that point; a moving
line tracks the current playback position.

## Time Expansion vs. heterodyne playback

Bat calls are mostly above human hearing, so both playback modes make them
audible in different ways:

- **TE (×10)** — the default. Playback is slowed down tenfold, which also
  drops every frequency by the same factor into human hearing range. This
  preserves the call's whole shape, just stretched in time.
- **HET** — heterodyne: mixes a tunable frequency band down to audible
  range in real time, at normal speed, the way a bat detector's built-in
  speaker works in the field. Set the kHz value by hand, or click **⟲** to
  reset it to the frequency Fledermap auto-tuned for this recording. HET
  only makes the chosen band audible — a call outside it won't be heard at
  all — so TE is the better choice for actually hearing everything in a
  recording, and HET for confirming a specific frequency.

## Recording-detail page tools

Open a recording's own page (**Details**, from the map drawer) for a
larger, locked-scale view with its own toolbar:

- **Default** — click to play from that point, drag to pan across a long
  recording.
- **Ruler** — drag to draw a measurement box, showing elapsed time (ms),
  the equivalent pulse-repetition rate (Hz), and frequency span (kHz)
  between two points. A plain click clears it.
- **Lock view** — freezes horizontal scrolling to the current window, so
  repeated playback (e.g. replaying a short stretch several times to pick
  out one call's detail) always plays exactly the same range instead of
  auto-following into whatever's next.
