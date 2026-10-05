"""Task wrappers bridging `Recording` rows to `media/`'s pure functions.

This is the ONLY module that imports both `procrastinate` and the ORM models
-- `media/` stays pure (design spec §3). `app` here is the ONE Procrastinate
App for the whole process (design spec §6) -- constructed via
`jobs.app.make_job_app()` with no engine bound yet; every consumer (CLI
commands, tests) must `app.open(engine)` before deferring, or
`app.replace_connector(...)` before running a worker.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import procrastinate
import requests
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config
from fledermap.derive.sessions import partition_sessions
from fledermap.domain.codes import IdSource
from fledermap.jobs.app import make_job_app
from fledermap.media.oscillogram import (
    DEFAULT_OSCILLOGRAM_PARAMS,
    render_oscillogram,
)
from fledermap.media.paths import (
    PREVIEW_VERSION,
    oscillogram_path,
    preview_path,
    spectrogram_path,
)
from fledermap.media.preview import make_preview
from fledermap.media.spectrogram import DEFAULT_SPECTROGRAM_PARAMS, render_spectrogram
from fledermap.services.classifier_detections import DetectionInput
from fledermap.services.derive import derive_sites
from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    commit_scan,
    reresolve_unmapped_identifications,
    scan_all_roots,
    sweep_missing,
)
from fledermap.services.noise import classify_noise
from fledermap.store.geo import decode_point
from fledermap.store.models import Site
from fledermap.store.seed import seed_taxonomy

app = make_job_app()

logger = logging.getLogger(__name__)

# Shared by both scheduling paths onto the SAME job -- the periodic
# registration below, and the event-triggered `defer_async()` -- so
# `queueing_lock` coalesces a burst of either kind into at most one pending
# run, and `lock` keeps that run from ever overlapping one already executing
# (design spec §5, Global Constraints above).
_INGEST_CYCLE_LOCK = "ingest_cycle"
_INGEST_CYCLE_CRON = "*/5 * * * *"

# Disk hygiene, not correctness -- unlike ingest, nothing depends on this
# running promptly, so it's much coarser than the 5-minute ingest cron.
# 03:00 UTC daily, off-peak for a typical self-hosted install.
_CLEAN_MEDIA_CYCLE_LOCK = "clean_media_cycle"
_CLEAN_MEDIA_CYCLE_CRON = "0 3 * * *"

# Daily, off-peak, same reasoning as _CLEAN_MEDIA_CYCLE_CRON above -- mining is disk/CPU
# hygiene work with no reason to run more often than once a day at this project's real
# scale (spec §6).
_CLASSIFY_IMPLICATION_MINING_LOCK = "classify_implication_mining"
_CLASSIFY_IMPLICATION_MINING_CRON = "0 3 * * *"

# Design spec §7 asks for "a small fixed retry count (e.g. 3, exponential
# backoff)". A bare `retry=3` resolves to `RetryStrategy(max_attempts=3)`
# with EVERY wait parameter left at 0, so all three attempts fire
# back-to-back -- no backoff at all, which is the opposite of what a retry is
# for when the cause is a transient resource problem. `exponential_wait=2`
# spaces them 2s, 4s, 8s apart (procrastinate's formula is
# `wait + linear_wait * attempts + exponential_wait ** (attempts + 1)`).
#
# `max_attempts` counts RETRIES, not runs: the job executes once, then up to
# 3 more times, and `procrastinate_jobs.attempts` lands on 4 when it finally
# fails. Asserted in `tests/test_jobs_tasks.py` so the off-by-one is pinned.
#
# `retry_exceptions` is deliberately NOT set: choosing which failures are
# worth retrying needs operational experience this project does not have yet,
# and retrying everything a small fixed number of times is the safe default
# until it does.
_RETRY = procrastinate.RetryStrategy(max_attempts=3, exponential_wait=2)


def spectrogram_lock_key(audio_hash: str) -> str:
    return f"spectrogram:{audio_hash}:{DEFAULT_SPECTROGRAM_PARAMS.params_hash}"


def oscillogram_lock_key(audio_hash: str) -> str:
    return f"oscillogram:{audio_hash}:{DEFAULT_OSCILLOGRAM_PARAMS.params_hash}"


def preview_lock_key(audio_hash: str) -> str:
    return f"preview:{audio_hash}:{PREVIEW_VERSION}"


def batdetect2_lock_key(audio_hash: str) -> str:
    return f"batdetect2:{audio_hash}"


# A fixed, non-configurable sanity floor -- distinct from the (configurable)
# threshold `services/classifier_detections.py`'s `materialize_claims` applies when
# turning cached detections into claims. This floor only keeps the
# `classifier_detection` cache itself from filling up with the model's
# near-zero noise-level scores; raising the real threshold later must not
# require a re-run of the model, so it stays generous.
#
# It is compared against each annotation's `det_prob`, NOT its `class_prob`:
# batdetect2's two scores answer different questions, and its own CLI applies
# `--detection-threshold` to `det_prob` for exactly this gate ("is there a
# call here at all"). `class_prob` is the species-conditional score and is
# always the smaller of the two, so gating on it would discard real calls the
# model was merely undecided about the species of.
_BATDETECT2_FLOOR = 0.05


def battybirdnet_lock_key(audio_hash: str) -> str:
    return f"battybirdnet:{audio_hash}"


# A fixed, non-configurable sanity floor, same reasoning as _BATDETECT2_FLOOR
# above: the operator's server returns EVERY class's score for EVERY
# fixed-width analysis window, unfiltered (confirmed against analyze.py's actual
# analyzeFile()/saveResultFile() split -- MIN_CONFIDENCE is only ever
# applied inside saveResultFile(), a CLI-only file-export function the
# /analyze route never calls). Without this floor, classifier_detection
# would fill up with near-zero scores for every one of the label
# vocabulary's ~30 classes, for every segment, in every recording.
_BATTYBIRDNET_FLOOR = 0.05


def results_to_detections(
    results: dict[str, list[list[str]]],
    resolve_label: Callable[[str], int | None],
) -> list[DetectionInput]:
    """Turn a BattyBirdNET-Analyzer `/analyze` response's `results` field
    into `DetectionInput`s, one per window at most, dropping anything below
    `_BATTYBIRDNET_FLOOR`.

    `results` is `{"{segment_start}-{segment_end}": [[label, score_str],
    ...], ...}` -- one entry per fixed-width analysis window (a plain tiling
    of the whole recording by `analyze.py`'s `getRawAudioFromFile`/
    `audio.splitSignal`, unrelated to where any actual call falls; NOT
    per-call), every class in the label vocabulary scored (confirmed from
    analyze.py's `analyzeFile`, which `server.py`'s `/analyze` route returns
    verbatim). Confirmed against the real upstream source (`rdz-oss/
    BattyBirdNET-Analyzer`, `config.py`): for the bat models this server
    serves, the window is `cfg.SIG_LENGTH = 144000 / cfg.SAMPLE_RATE` seconds
    (~0.5625s at the default `SAMPLE_RATE = 256000`, since none of the bat
    area branches in `server.py` override it -- only its `BIRDS`/
    `CUSTOM_BIRD` branches force `SIG_LENGTH = 3`), with no overlap by
    default (`server.py`'s `/analyze` route sets `cfg.SIG_OVERLAP` from the
    request's own `overlap` field, defaulting to `0.0` if the request omits
    it -- not `config.py`'s own `SIG_LENGTH / 4.0` module-level default).
    Exact duration and overlap therefore depend on the operator's running
    server/model area and the request, not a fixed "3 seconds" this
    docstring used to assume. A window is one bat call, which can only be
    one species (a real call pulse is a few ms to tens of ms, well inside
    even the shortest of these windows), so only the single
    highest-scoring candidate in each window becomes a detection -- keeping
    every candidate above the floor (the original behavior) let one call
    cast a claim for every species the model gave a non-trivial score to,
    which is why BattyBirdNET over-reported species relative to batdetect2:
    batdetect2's own annotations already carry a single top-1 `class` per
    detected call (see `annotations_to_detections` below), so it never had
    this problem. Across different windows of the same recording, a
    different label can still legitimately win -- that per-window stream is
    still flattened into one, same as `annotations_to_detections` flattens
    across its own per-call annotation list.

    Separate from the task, and taking a plain label-to-taxon-id callable
    rather than a `Session`, so this parsing can be tested directly against
    a real captured response with no database anywhere in reach -- same
    reasoning as `annotations_to_detections`'s own docstring: batdetect2's
    parsing bug (reading attributes that never existed) shipped because
    nothing checked the parsing against the real package's actual output
    shape, and a hand-written mock of the same wrong shape passed happily.

    UNVERIFIED AGAINST A REAL SERVER: the `results` shape assumed here is
    derived entirely from reading BattyBirdNET-Analyzer's upstream
    `analyze.py`/`server.py` source (design spec 2026-09-16-fledermap-
    battybirdnet-classifier-design.md §3) -- no live `battybirdnet-docker`
    instance was available to confirm it against a genuine `/analyze`
    response. Whoever first enables this classifier against a real server
    should capture one real response and add a fixture-based test proving
    this shape matches, mirroring `tests/test_batdetect2_real_api.py`'s
    precedent for the sibling classifier (which exists precisely because a
    source-read-only assumption about batdetect2's own output shape once
    shipped wrong).

    Each surviving window's `"{start}-{end}"` key is also parsed into
    `time_start_s`/`time_end_s`, so the annotation layer (design spec
    2026-09-24-fledermap-batdetect2-annotation-layer-design.md, extended to
    this source per Janna's 2026-09-24 request) has geometry to draw. Unlike
    batdetect2's genuinely per-call boxes, a raw window is an arbitrary tile
    of the recording, not a call boundary -- so CONSECUTIVE windows (the
    previous window's end exactly equals the next window's start) sharing
    the same winning raw label are merged into one wider `DetectionInput`
    spanning their combined time range, rather than emitting one box per
    ~0.56s tile regardless of whether neighbours agree. A gap (an
    intervening window that was empty, below the floor, or won by a
    different label) breaks the run -- merging across a gap would claim
    detection across audio nothing actually says the species was calling in.
    The merged detection's `confidence` is the highest of its constituent
    windows', not an average: mirrors `materialize_claims`'s own
    keep-the-highest-confidence rule for collapsing several detections into
    one, and represents "this span contains a window BattyBirdNET was this
    confident about" rather than diluting a strong center window's score
    with its quieter attack/decay neighbours. No frequency bounds are set
    (BattyBirdNET's fixed-width windows carry no per-call frequency
    information at all) -- `record_detections` still writes an `Annotation`
    for a `None`-frequency detection, and the frontend (`annotation_geometry.js`'s
    `annotationBoxPx`) renders it as a full-height vertical band instead of
    a point.
    """
    windows: list[tuple[float, float, str, float]] = []
    for key, candidates in results.items():
        if not candidates:
            continue
        label, score_str = max(candidates, key=lambda candidate: float(candidate[1]))
        confidence = float(score_str)
        if confidence < _BATTYBIRDNET_FLOOR:
            continue
        start_str, end_str = key.split("-", 1)
        windows.append((float(start_str), float(end_str), label, confidence))
    windows.sort(key=lambda w: w[0])

    resolved: dict[str, int | None] = {}
    merged: list[tuple[float, float, str, float]] = []
    for start, end, label, confidence in windows:
        if (
            merged
            and merged[-1][2] == label
            and math.isclose(merged[-1][1], start, abs_tol=1e-6)
        ):
            prev_start, _prev_end, _prev_label, prev_confidence = merged[-1]
            merged[-1] = (prev_start, end, label, max(prev_confidence, confidence))
        else:
            merged.append((start, end, label, confidence))

    detections: list[DetectionInput] = []
    for start, end, label, confidence in merged:
        if label not in resolved:
            # A label repeats across different (merged) windows of a
            # recording, so the same lookup would otherwise run once per
            # window instead of once per distinct label. Scoped to one call,
            # like annotations_to_detections's identical cache: a
            # longer-lived one would go stale the moment a code mapping is
            # added.
            resolved[label] = resolve_label(label)
        detections.append(
            DetectionInput(
                taxon_id=resolved[label],
                raw_label=label,
                confidence=confidence,
                time_start_s=start,
                time_end_s=end,
            ),
        )
    return detections


@app.task(queue="classify", pass_context=True, retry=_RETRY)
def classify_battybirdnet_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    """POST one recording's WAV to the operator's configured BattyBirdNET-
    Analyzer server and cache every candidate above `_BATTYBIRDNET_FLOOR` in
    `classifier_detection` -- never writes `Identification` directly (see
    `services/classifier_detections.py`'s `materialize_claims`, run
    separately every ingest cycle).

    On any request failure, or a response the server itself reports as
    unsuccessful, this raises and lets Procrastinate's `_RETRY` policy
    handle it -- no healthcheck step, no circuit breaker, matching
    batdetect2's own precedent (design spec 2026-09-16-fledermap-
    battybirdnet-classifier-design.md §3).
    """
    from fledermap.services.classifier_detections import record_detections
    from fledermap.services.media import resolve_recording, resolve_wav_path
    from fledermap.store.seed import resolve_code

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    engine = context.additional_context["engine"]
    config: Config = context.additional_context["config"]

    if config.battybirdnet_url is None:
        # Can only happen if this job was deferred while the classifier was
        # enabled, then the config changed (disabled, or the URL cleared)
        # before it ran -- mirrors `name_site_task`'s analogous
        # `poiidx_database_url is None` guard above. Nothing to do, and
        # nothing to retry usefully; without this check the f-string below
        # would silently build the URL "None/analyze" and fail later with a
        # confusing `requests.exceptions.MissingSchema` instead.
        return

    # Three phases, and the session is deliberately CLOSED across the middle
    # one: an HTTP round-trip to an external server is slow, and holding a
    # connection open across it leaves it `idle in transaction` for that
    # whole time, holding locks that can block unrelated DDL indefinitely
    # (CLAUDE.md's Database section) -- same reasoning as
    # `classify_batdetect2_task`.
    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    with open(wav_path, "rb") as audio_file:
        response = requests.post(
            f"{config.battybirdnet_url}/analyze",
            files={"audio": audio_file},
            # `server.py`'s handler unconditionally does
            # `json.loads(bottle.request.forms.get("meta", {}))` -- its
            # default for a MISSING "meta" field is a dict, not a JSON
            # string, so json.loads crashes with a 500 on every request that
            # omits it (confirmed against a real battybirdnet-docker
            # instance, 2026-09-17). Sending an empty JSON object as a
            # literal string works around the server's own bug without
            # patching or vendoring its code (design spec §1).
            data={"meta": "{}"},
            timeout=300,
        )
    response.raise_for_status()
    payload = response.json()
    # `server.py`'s `/analyze` handler builds `data = {"msg": "success",
    # "results": results, ...}` on success (confirmed by reading its source
    # directly) -- any other value means the server itself reports failure.
    if payload.get("msg") != "success":
        msg = f"battybirdnet server returned {payload.get('msg')!r} for {audio_hash}"
        raise RuntimeError(msg)
    results: dict[str, list[list[str]]] = payload["results"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)

        def resolve_label(label: str) -> int | None:
            scientific_name = label.split("_", 1)[0]
            taxon = resolve_code(session, IdSource.BATTYBIRDNET, scientific_name)
            return taxon.id if taxon is not None else None

        detections = results_to_detections(results, resolve_label)
        record_detections(session, recording, IdSource.BATTYBIRDNET, detections)
        session.commit()


def annotations_to_detections(
    annotations: Iterable[Mapping[str, Any]],
    resolve_label: Callable[[str], int | None],
) -> list[DetectionInput]:
    """Turn `api.process_file(...)["pred_dict"]["annotation"]` into
    `DetectionInput`s, dropping anything below `_BATDETECT2_FLOOR`.

    Separate from the task, and taking a plain label-to-taxon-id callable
    rather than a `Session`, so this parsing can be tested directly against
    the REAL package's output: a test can feed it a genuine `process_file`
    result with no database anywhere in reach. The previous version of this
    parsing read `.detections`/`.detection_score`/`.class_scores` attributes
    that batdetect2 has never had, and a hand-written mock of that same wrong
    shape passed happily -- the shape has to be checked against the package
    itself or it isn't checked at all.
    """
    resolved: dict[str, int | None] = {}
    detections: list[DetectionInput] = []
    for annotation in annotations:
        if annotation["det_prob"] < _BATDETECT2_FLOOR:
            continue
        raw_label = str(annotation["class"])
        if raw_label not in resolved:
            # One recording yields dozens of annotations drawn from a 17-class
            # vocabulary, so the same label is looked up over and over. The
            # cache is scoped to one call: a longer-lived one would go stale
            # the moment a code mapping is added.
            resolved[raw_label] = resolve_label(raw_label)
        detections.append(
            DetectionInput(
                # An unmapped label is stored as a detection all the same,
                # with taxon_id None and the label preserved -- it becomes an
                # "unmapped species" claim rather than being discarded (see
                # `services/classifier_detections.py`'s `materialize_claims`).
                taxon_id=resolved[raw_label],
                raw_label=raw_label,
                # `class_prob`, not `det_prob`: this is the number the user's
                # FLEDERMAP_BATDETECT2_THRESHOLD is compared against, and that
                # threshold expresses "how sure must the model be of the
                # SPECIES before I treat this as a claim". `det_prob` only
                # says a call is present, and has already done its job at
                # `_BATDETECT2_FLOOR` above.
                confidence=float(annotation["class_prob"]),
                time_start_s=float(annotation["start_time"]),
                time_end_s=float(annotation["end_time"]),
                freq_min_hz=float(annotation["low_freq"]),
                freq_max_hz=float(annotation["high_freq"]),
            ),
        )
    return detections


@app.task(queue="classify", pass_context=True, retry=_RETRY)
def classify_batdetect2_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    """Run batdetect2 once over one recording and cache every candidate above
    `_BATDETECT2_FLOOR` in `classifier_detection` -- never writes
    `Identification` directly (see `services/classifier_detections.py`'s
    `materialize_claims`, run separately every ingest cycle).
    """
    # Local import, not a top-level one: batdetect2 is an optional extra
    # (`pip install fledermap[batdetect2]`), and a top-level import here
    # would crash this entire module -- and therefore every other job in the
    # app -- on worker startup if it isn't installed. Matches this project's
    # existing precedent for a configured-but-broken optional integration
    # (`services/site_naming.py`'s `ensure_connected`): let this job fail
    # naturally and retry via `_RETRY`, no config-time check, no global
    # disable (design spec §5, §7).
    # mypy: not added to the `types` env's extra-dependencies like other
    # untyped third-party imports (see "Tooling" in CLAUDE.md) -- batdetect2
    # is CC-BY-NC-licensed (pyproject.toml's [project.optional-dependencies]
    # comment) and deliberately excluded even from dev tooling installs, not
    # just the base package, so it can never land in an env every
    # contributor's `hatch run types:check` pulls in unconditionally.
    from batdetect2 import api as batdetect2_api  # type: ignore[import-not-found]

    from fledermap.services.classifier_detections import record_detections
    from fledermap.services.media import resolve_recording, resolve_wav_path
    from fledermap.store.seed import resolve_code

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    engine = context.additional_context["engine"]

    # Three phases, and the session is deliberately CLOSED across the middle
    # one: a torch forward pass over a full recording is slow, and holding a
    # connection open across it leaves it `idle in transaction` for that whole
    # time, holding locks that can block unrelated DDL indefinitely (see
    # CLAUDE.md's Database section). The recording is re-fetched by
    # `audio_hash` afterwards rather than carried across the gap, because the
    # first session's instance is detached once that block exits.
    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    # `api.process_file` returns a plain dict (a `RunResults` TypedDict), whose
    # "pred_dict"."annotation" is a list of plain dicts -- verified against the
    # real installed package, not assumed. Each annotation names exactly ONE
    # class; there is no per-detection map of scores across candidate species.
    result = batdetect2_api.process_file(str(wav_path))
    annotations = result["pred_dict"]["annotation"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)

        def resolve_label(label: str) -> int | None:
            taxon = resolve_code(session, "batdetect2", label)
            return taxon.id if taxon is not None else None

        detections = annotations_to_detections(annotations, resolve_label)
        record_detections(session, recording, IdSource.BATDETECT2, detections)
        session.commit()


_NAME_SITE_LOCK = "poiidx-name-site"


def name_site_queueing_lock(cache_key: str, *, force: bool = False) -> str:
    """A forced refresh gets its own lock namespace -- otherwise it would
    collide with (and get silently dropped as an AlreadyEnqueued duplicate
    of) an already-pending normal job for the same coordinate, which is
    exactly the collision the plain lock relies on for legitimate
    derive_sites rebuilds of the SAME real site."""
    suffix = ":force" if force else ""
    return f"name_site:{cache_key}{suffix}"


@app.task(queue="geo", pass_context=True, retry=_RETRY)
def name_site_task(
    context: procrastinate.JobContext,
    site_id: int,
    force: bool = False,
) -> None:
    """Resolve one Site's name via poiidx, off the request path entirely
    (design spec Goals: "never a web handler"). `_NAME_SITE_LOCK` -- a
    single static value shared by every name_site job, applied at defer
    time by `enqueue_site_naming` -- serializes execution across all of
    them, so two never-before-touched-region downloads can never race each
    other (design spec §3's corrected performance note)."""
    # Local import: `site_naming` imports FROM this module at ITS top
    # level, so a top-level import here would be circular. Safe here
    # because by the time this function actually runs, module import has
    # long finished (same reasoning as `run_ingest_cycle`'s own local
    # `enqueue_media` import).
    from fledermap.services import site_naming

    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]
    if config.poiidx_database_url is None:
        # Can only happen if a job was deferred, then the config changed
        # before it ran -- nothing to do, and nothing to retry usefully.
        return
    site_naming.ensure_connected(config.poiidx_database_url)

    with OrmSession(engine) as session:
        site = session.get(Site, site_id)
        if site is None:
            # derive_sites rebuilt again since this job was enqueued and
            # this row no longer exists -- not an error.
            return
        point = decode_point(site.centroid)
        if point is None:
            return
        lon, lat = point
        resolved = site_naming.name_site(
            session,
            lon,
            lat,
            radius_m=config.site_naming_radius_m,
            site_radius_m=site.radius_m,
            force=force,
        )
        if resolved is not None:
            site.name, site.admin_path = resolved
        session.commit()


_RESOLVE_REGIONS_LOCK = "poiidx-resolve-regions"


@app.task(queue="geo", pass_context=True, retry=_RETRY)
def resolve_regions_task(context: procrastinate.JobContext) -> None:
    """Resolve region membership for every pending recording, then refresh
    which regions are distinct (design spec
    2026-10-04-fledermap-region-filter-design.md). One job per cycle, not one
    per recording: `list_regions` only reads poiidx's region index, so
    batching is cheap and keeps the queue small."""
    # Local import for the same circularity reason as `name_site_task`'s.
    from fledermap.services import regions, site_naming

    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]
    if config.poiidx_database_url is None:
        # Config changed after this job was deferred -- nothing to do.
        return
    site_naming.ensure_connected(config.poiidx_database_url)

    with OrmSession(engine) as session:
        regions.resolve_regions(session)
        session.commit()


@app.task(queue="media", pass_context=True, retry=_RETRY)
def render_spectrogram_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    # Local import: `services.media` imports task objects FROM this module
    # at ITS top level, so a top-level import here would be circular (same
    # reasoning as `run_ingest_cycle`'s own local `enqueue_media` import,
    # below).
    from fledermap.services.media import resolve_recording, resolve_wav_path

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    out_path = spectrogram_path(media_root, audio_hash)
    render_spectrogram(wav_path, out_path, params=DEFAULT_SPECTROGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=_RETRY)
def render_oscillogram_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    # Local import: see `render_spectrogram_task` above.
    from fledermap.services.media import resolve_recording, resolve_wav_path

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    out_path = oscillogram_path(media_root, audio_hash)
    render_oscillogram(wav_path, out_path, params=DEFAULT_OSCILLOGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=_RETRY)
def make_preview_task(context: procrastinate.JobContext, audio_hash: str) -> None:
    # Local import: see `render_spectrogram_task` above.
    from fledermap.services.media import resolve_recording, resolve_wav_path

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    out_path = preview_path(media_root, audio_hash)
    make_preview(wav_path, out_path)


@app.periodic(
    cron=_INGEST_CYCLE_CRON,
    lock=_INGEST_CYCLE_LOCK,
    queueing_lock=_INGEST_CYCLE_LOCK,
)
@app.task(queue="ingest", pass_context=True)
def run_ingest_cycle(context: procrastinate.JobContext, timestamp: int) -> None:
    """One full ingest+derive pass across every configured archive root.

    `timestamp` is unused directly -- Procrastinate's periodic-task machinery
    requires it as the first parameter (confirmed against
    `procrastinate/periodic.py`: `PeriodicDeferrer.defer_jobs` always injects
    it), and Task 4's manual `defer_async()` call supplies it too so both
    scheduling paths share one task signature.

    `MassDisappearanceError`/`IncompleteScanError` (the same two conditions
    `ingest`'s CLI turns into `EXIT_SWEEP_REFUSED`) are caught and logged --
    the job still "succeeds" from Procrastinate's point of view, so the next
    cycle (cron or event) retries automatically; there's no process to exit
    non-zero against any more (design spec §6). Anything else propagates:
    Procrastinate marks the job failed, subject to its own retry policy.
    """
    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]

    # Local import: `services.media` imports task objects FROM this module at
    # ITS top level, so a top-level import here would be circular (Global
    # Constraints above). Safe here because by the time this function
    # actually runs, module import has long finished.
    from fledermap.services.batdetect2 import enqueue_batdetect2
    from fledermap.services.battybirdnet import enqueue_battybirdnet
    from fledermap.services.classifier_detections import (
        materialize_claims,
        purge_source,
    )
    from fledermap.services.media import enqueue_media

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        reconnected = reresolve_unmapped_identifications(session)
        session.commit()

        scanned, seen, skipped, incomplete_skips, unchanged_by_fingerprint = (
            scan_all_roots(
                config.archive_roots,
                timestamp_source=config.timestamp_source,
                default_timezone=config.default_timezone,
                session=session,
            )
        )
        report = commit_scan(session, scanned, archive_roots=config.archive_roots)
        report.unchanged += unchanged_by_fingerprint
        session.commit()
        enqueue_media(report.created_hashes, engine)

        ingest_summary = (
            f"ingest cycle: created {report.created} unchanged {report.unchanged} "
            f"updated {report.updated} moved {report.moved} "
            f"replaced {report.replaced} duplicates {report.duplicates} "
            f"skipped {skipped} identifications added "
            f"{report.identifications_added} updated "
            f"{report.identifications_updated} removed "
            f"{report.identifications_removed} reconnected {reconnected}"
        )

        # A refused sweep (spec §10 decision 2) must NOT skip derive: it's
        # idempotent and cheap, and skipping it would let a single
        # permanently-unreadable file (UNREADABLE/UNPARSEABLE are both
        # permanent INCOMPLETE_SCAN_REASONS) silently wedge session/site
        # derivation forever, with only a 5-minutely error log to explain why
        # new recordings stop appearing on the map. `flagged` stays `None`
        # when the sweep was refused so the summary line below can say so
        # explicitly, rather than reporting a misleading `0` as if the sweep
        # had actually run and found nothing missing.
        flagged: int | None
        try:
            flagged = sweep_missing(session, seen, skipped=incomplete_skips)
            session.commit()
        except (MassDisappearanceError, IncompleteScanError) as exc:
            flagged = None
            logger.error("%s -- %s", ingest_summary, exc)

        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
        )
        session.commit()
        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()

        classify_noise(session, config.noise_snr_threshold_db)
        session.commit()

        if config.batdetect2_enabled:
            enqueue_batdetect2(session, engine)
            materialize_claims(
                session, IdSource.BATDETECT2, config.batdetect2_threshold
            )
        else:
            purge_source(session, IdSource.BATDETECT2)
        session.commit()

        if config.battybirdnet_enabled:
            enqueue_battybirdnet(session, engine)
            materialize_claims(
                session, IdSource.BATTYBIRDNET, config.battybirdnet_threshold
            )
        else:
            purge_source(session, IdSource.BATTYBIRDNET)
        session.commit()

        from fledermap.services.site_naming import enqueue_site_naming

        enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
        )
        session.commit()

        from fledermap.services.regions import enqueue_region_resolution

        enqueue_region_resolution(
            engine,
            poiidx_database_url=config.poiidx_database_url,
        )

        logger.info(
            "%s flagged_missing %s -- sessions created %d extended %d "
            "merge_proposals %d -- sites %d unclustered %d",
            ingest_summary,
            "refused" if flagged is None else flagged,
            session_report.created,
            session_report.extended,
            session_report.merge_proposals,
            site_report.site_count,
            site_report.unclustered,
        )


@app.periodic(
    cron=_CLEAN_MEDIA_CYCLE_CRON,
    lock=_CLEAN_MEDIA_CYCLE_LOCK,
    queueing_lock=_CLEAN_MEDIA_CYCLE_LOCK,
)
@app.task(queue="maintenance", pass_context=True)
def run_clean_media_cycle(context: procrastinate.JobContext, timestamp: int) -> None:
    """The periodic half of the `clean-media` backlog item (`timestamp`
    unused -- see `run_ingest_cycle`'s docstring for why Procrastinate's
    periodic machinery requires it as the first parameter regardless).
    `fledermap clean-media` (`cli/main.py`) is the same work, on demand.

    Its own `"maintenance"` queue, deliberately distinct from `"media"`:
    Procrastinate's periodic deferrer creates a job for EVERY registered
    periodic task on any worker run, not just the one(s) the test/caller
    cares about (see `_run_worker`'s docstring in `tests/test_jobs_tasks.py`
    for the `run_ingest_cycle`/`"ingest"` precedent this follows) -- sharing
    `"media"` would make this run, and delete files, in the middle of every
    existing spectrogram/oscillogram/preview test that scopes its worker to
    `queues=["media"]`.
    """
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    # Local import: same circularity reasoning as `run_ingest_cycle`'s own
    # local `enqueue_media` import above.
    from fledermap.services.media import clean_media

    with OrmSession(engine) as session:
        result = clean_media(session, media_root)

    logger.info(
        "clean-media cycle: removed %d orphaned director%s, %d stale file(s), "
        "freed %d bytes",
        result.dirs_removed,
        "y" if result.dirs_removed == 1 else "ies",
        result.files_removed,
        result.bytes_freed,
    )


@app.periodic(
    cron=_CLASSIFY_IMPLICATION_MINING_CRON,
    lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
    queueing_lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
)
@app.task(queue="maintenance", pass_context=True)
def run_classify_implication_mining_cycle(
    context: procrastinate.JobContext, timestamp: int
) -> None:
    """The periodic half of the classify-implication mining feature (part b spec §6;
    `timestamp` unused -- see `run_ingest_cycle`'s docstring for why Procrastinate's
    periodic machinery requires it as the first parameter regardless).
    `fledermap mine-classify-implications` (`cli/main.py`) is the same work, on demand. Also
    deferred unconditionally at every `fledermap worker` startup (`cli/main.py`'s
    `_run_worker_async`) -- NOT relying on Procrastinate's own periodic catch-up, whose
    max_delay (10 minutes) is nowhere near enough for a daily job on a machine that isn't
    always on (spec §6).

    Shares the `"maintenance"` queue with `run_clean_media_cycle` -- both are low-priority,
    non-destructive-to-recording-data background jobs, same reasoning as that task's own
    docstring."""
    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]

    from fledermap.services.classify_implications import mine_classify_implications

    with OrmSession(engine) as session:
        count = mine_classify_implications(
            session,
            min_support=config.classify_implication_min_support,
            min_confidence=config.classify_implication_min_confidence,
            min_lift=config.classify_implication_min_lift,
        )
        session.commit()

    logger.info("classify-implication mining cycle: stored %d rule(s)", count)
