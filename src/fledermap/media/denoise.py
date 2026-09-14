"""Highpass filtering, spectrogram-only background-noise subtraction, and
audio-domain spectral gating (`spectral_gate`, which produces real denoised
audio for playback, not just a cleaner spectrogram image) for the "Denoise"
toggle (design specs
docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md and
docs/superpowers/specs/2026-09-14-fledermap-audio-denoise-design.md). Pure:
no DB, no queue awareness, matching every other `media/` module.

`DEFAULT_CUTOFF_HZ` is the one shared constant every caller (SpectrogramParams,
OscillogramParams, the TE/HET routes) defaults to -- a single definition so the
cutoff can't drift between callers."""

from __future__ import annotations

import numpy as np
from scipy import ndimage, signal

DEFAULT_CUTOFF_HZ = 5000.0

# A conventional order balancing rolloff steepness against filter-design edge
# cases at a low cutoff-to-Nyquist ratio (this project's recordings run at
# 256kHz+ sample rates, so a 5kHz cutoff is a small fraction of Nyquist).
_HIGHPASS_ORDER = 4


def highpass_filter(
    samples: np.ndarray,
    samplerate_hz: float,
    cutoff_hz: float,
) -> np.ndarray:
    """Zero-phase Butterworth highpass (`sosfiltfilt`, not `sosfilt` -- a
    causal filter would time-shift a sharp transient, desyncing the filtered
    audio from the unfiltered spectrogram/oscillogram's own time axis).
    Mirrors `heterodyne.py`'s existing `butter` + `sosfiltfilt` lowpass
    idiom, the same established pattern in this codebase."""
    sos = signal.butter(
        _HIGHPASS_ORDER,
        cutoff_hz,
        btype="high",
        fs=samplerate_hz,
        output="sos",
    )
    result: np.ndarray = signal.sosfiltfilt(sos, samples)
    return result


def subtract_background_noise(
    sxx: np.ndarray,
    percentile: float = 50.0,
    factor: float = 2.0,
    median_size: tuple[int, int] = (7, 7),
    preserve_threshold: float = 2.0,
) -> np.ndarray:
    """Per frequency-bin (per row) background-noise subtraction (the classic
    "over-subtraction" spectral-subtraction idiom), followed by a selective
    (edge-preserving) 2D median filter. Operates on the STFT's linear power
    matrix, before any dB conversion -- spectrogram-image-only (see the
    design spec's "Why highpass and background-subtraction use different
    techniques": this is not reconstructed back to audio, so no phase/COLA
    concern applies).

    Step 1, over-subtraction: estimate each bin's noise floor as its own
    `percentile`-th percentile power across all time columns, subtract
    `factor` times that floor from every column in that row, clamp at zero.
    `factor=1.0` (subtracting exactly the floor) only zeroes the bottom
    `percentile`% of pixels in each row -- the noise floor's own variance,
    which is what actually reads as visual "noise" and sits well above that
    single percentile, survives untouched. Confirmed visually 2026-09-14
    against a real field recording (percentile=10, factor=1.0 was barely
    distinguishable from no denoising at all). Over-subtracting pushes that
    whole variance band down towards zero too, at the cost of also eating
    into weak real signal close to the floor -- percentile=50 (the row's
    median) + factor=2.0 visibly cleaned the background while leaving actual
    calls' shape and brightness intact in that same comparison.

    Step 2, selective median filter: over-subtraction alone still leaves
    substantial per-pixel speckle (a single STFT frame's power estimate has
    high variance regardless of the true noise floor -- subtracting a
    scalar shifts brightness down but doesn't reduce that per-pixel
    randomness). A plain 2D median filter removes the speckle but is
    edge-blind: it smooths a call's thin sweep the same as a noise pixel,
    visibly smudging call shape even at a small kernel size -- confirmed
    live 2026-09-14, 7x7 got the background very quiet but blobbed every
    call. The fix is to use the median-filtered estimate only where a pixel
    actually agrees with it (real background); anywhere a pixel stands out
    well above its own smoothed neighborhood -- which is exactly what a
    call pixel does -- the original, unsmoothed value passes through
    untouched. `median_size=(7, 7)` and `preserve_threshold=2.0` were
    chosen by rendering several kernel sizes and thresholds against a real
    field recording side by side; pushing the kernel up to 21x21 and the
    threshold up to 6.0 produced no further visible improvement, so this
    isn't a compromise short of a better result further out -- it's close
    to this technique's practical ceiling on real recordings.

    This function's sole current production caller, `spectrogram.py`, disables this
    step (`median_size=(1, 1)`): the audio is already denoised upstream by
    `spectral_gate` now, and the median filter's marginal contribution on top of that
    measured negligible."""
    noise_floor = np.percentile(sxx, percentile, axis=1, keepdims=True)
    subtracted = np.maximum(sxx - factor * noise_floor, 0.0)
    smoothed = ndimage.median_filter(subtracted, size=median_size)
    result: np.ndarray = np.where(
        subtracted > preserve_threshold * smoothed, subtracted, smoothed
    )
    return result


# Own STFT window for the gate, independent of SpectrogramParams.window_ms -- that value is
# tuned for the spectrogram's visual time/frequency tradeoff, not for gating accuracy or clean
# reconstruction, and the two purposes aren't guaranteed to want the same window size (design
# spec's "Algorithm" section). Hann window + exactly 50% overlap (noverlap = nperseg // 2) is a
# COLA-satisfying combination, which is what makes istft's reconstruction exact on unmodified
# bins -- the actual hard part naive spectral subtraction struggles with.
_GATE_WINDOW_MS = 4.0
# Noise floor = each frequency bin's own 20th-percentile magnitude across every time frame in the
# recording -- low enough that a genuine transient call (present in only a few frames) doesn't
# drag its own bin's floor estimate upward, matching subtract_background_noise's percentile-based
# per-bin idiom, just applied to STFT magnitude instead of the power spectrogram.
_GATE_PERCENTILE = 20.0
# Gain never drops below this, even for a bin that's pure noise floor -- the standard fix for
# "musical noise" (an isolated bin snapping fully to zero and back between frames, heard as
# chirping artifacts) that a hard gate (gain_floor=0) would produce. This is the floor on the
# clipped ratio BEFORE `_GATE_EXPONENT` is applied, not the effective floor on the gain actually
# multiplied into the signal -- that's `_GATE_GAIN_FLOOR ** _GATE_EXPONENT` (currently
# 0.1 ** 1.5 ~= 0.0316, about -30dB, not -20dB). Tune by that compounded number, not this one.
_GATE_GAIN_FLOOR = 0.1
# Exponent applied to the clipped linear gain -- steepens the transition between "clearly noise"
# and "clearly signal" beyond what the raw soft-threshold ratio gives alone.
_GATE_EXPONENT = 1.5


def spectral_gate(samples: np.ndarray, samplerate_hz: float) -> np.ndarray:
    """Phase-coherent spectral-gating denoise: STFT, a soft per-bin gain derived from each bin's
    own noise-floor estimate, then `istft` reconstruction -- unlike `subtract_background_noise`
    (which discards phase and is never reconstructed back to audio), this function's whole job is
    producing real, listenable denoised audio.

    Expects a signal shaped like a real bat-call recording: mostly quiet, with the actual call(s)
    as brief, louder transients -- NOT a sustained/continuous tone. A per-bin percentile treats
    whatever a bin does MOST of the time as its own noise floor; a transient call's bin is mostly
    silent so its floor stays low and the call passes through close to unchanged, but a bin that's
    continuously loud (a long constant-frequency call, or a test signal that's just a sustained
    tone) looks exactly like its own noise floor and gets suppressed along with it. This is the
    same fundamental tradeoff `subtract_background_noise` already accepts for the same reason
    (spec's "why highpass and background-subtraction use different techniques"), now inherited by
    the audio-domain version -- worth specifically checking during live listening QA against a
    real constant-frequency-call recording if one is available (e.g. a Rhinolophus species)."""
    nperseg = min(max(int(samplerate_hz * _GATE_WINDOW_MS / 1000), 8), len(samples))
    noverlap = nperseg // 2
    # float32 before the STFT: measured ~36% less peak memory and ~30% less time than float64
    # for this step, with no meaningful precision loss -- the source is 16-bit PCM audio, which
    # float32's 24-bit mantissa represents exactly with enormous headroom to spare.
    _freqs, _times, stft = signal.stft(
        samples.astype(np.float32),
        fs=samplerate_hz,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
    )
    magnitude = np.abs(stft)
    noise_floor = np.percentile(magnitude, _GATE_PERCENTILE, axis=1, keepdims=True)
    ratio = np.divide(
        noise_floor,
        magnitude,
        out=np.zeros_like(magnitude),
        where=magnitude > 0,
    )
    gain = np.clip(1.0 - ratio, _GATE_GAIN_FLOOR, 1.0) ** _GATE_EXPONENT
    gated = stft * gain
    _, reconstructed = signal.istft(
        gated, fs=samplerate_hz, window="hann", nperseg=nperseg, noverlap=noverlap
    )
    # istft can return a handful more or fewer samples than the original signal depending on how
    # the window tiles across its length -- trim or zero-pad back to the exact input length so
    # every caller can treat this as a drop-in replacement for its input samples array, the same
    # contract highpass_filter already has.
    result: np.ndarray = reconstructed[: len(samples)]
    if len(result) < len(samples):
        result = np.pad(result, (0, len(samples) - len(result)))
    # Match highpass_filter's float64 output convention -- callers chain this straight after
    # highpass_filter and shouldn't see the internal float32 STFT precision as a new dtype.
    return result.astype(np.float64)
