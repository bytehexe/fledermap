# Fledermap — batdetect2 classifier integration design

Status: proposed, not yet implemented. Scope: **batdetect2 only**. BattyBirdNET-Analyzer was
investigated alongside it and is deliberately deferred to its own future spec — it isn't
pip-installable (git-clone-and-run, TFLite model, CLI/REST only, no Python import API), and
carries a stricter CC-BY-NC-**SA**-4.0 license (ShareAlike, not just non-commercial). Mixing both
integration shapes into one spec would conflate two different mechanisms; this spec covers only
the one (batdetect2) that fits the project's existing pip-installable-dependency model cleanly.

## 1. Licensing (must be stated plainly, not left implicit)

batdetect2's PyPI package is licensed **CC-BY-NC-4.0** — non-commercial use only, with
attribution required. Fledermap itself is public and MIT-licensed, which grants anyone the right
to use, modify, or sell Fledermap with no further permission. A CC-BY-NC-licensed classifier
bundled or required would contradict that the moment a downstream operator tried to run Fledermap
commercially with the feature enabled — the NC obligation falls on whoever enables and runs the
classifier, not on Fledermap's own MIT grant, but a public MIT repo silently wiring in an
NC-restricted dependency by default is a trap for a commercial adopter regardless of who is
technically liable for the violation.

CC licenses have no GPL-style "linking taints the caller" clause, and (irrelevant here since
batdetect2's license has no ShareAlike component, but noted for when BattyBirdNET is eventually
speced) ShareAlike only triggers on redistributed *adaptations of the licensed material itself* —
so an unmodified, non-vendored, opt-in dependency does not turn Fledermap's own code non-MIT.
This is not legal advice; if real certainty is wanted, a lawyer's read beats this reasoning.

**Precedent for the mechanism** (not identical, but the closest sane analogy found): Pillow
(permissive HPND license) excludes only `libimagequant` (GPLv3-or-later, dual-licensed with a
paid commercial alternative) from its official builds, via a compile-time flag
(`--enable-imagequant`/`--disable-imagequant`) — not a `pip install pillow[imagequant]` extra,
because `libimagequant` is a native library linked at C-extension build time, not an importable
Python package. That mechanism doesn't transfer directly: batdetect2 *is* a normal
pip-installable, pure-Python-importable package, so `extras_require` is the mechanically correct
equivalent here, and it satisfies the same policy Pillow's mechanism demonstrates — nothing
restrictively-licensed present in a default `pip install fledermap`.

**Requirements:**

- `batdetect2` is its own named optional-dependencies group in `pyproject.toml`
  (`fledermap[batdetect2]`), never a base dependency. A comment directly on that group in
  `pyproject.toml` itself states the CC-BY-NC-4.0/non-commercial restriction — not just prose
  documentation elsewhere, so it's visible to anyone reading the dependency declaration.
- Off by default in config (`FLEDERMAP_BATDETECT2_ENABLED` defaults to `false`).
- Attribution to the batdetect2 authors, and an explicit non-commercial-use notice, wherever the
  feature is documented/toggled (README, config reference docs).
- No vendoring: the model/weights/code are never copied into Fledermap's own repo or built wheel.
  A user who opts in fetches batdetect2 from PyPI under its own terms.

## 2. Data model

### 2.1 New table: `classifier_detection`

Raw model output, independent of the currently configured verdict threshold — the durable record
of "what did the model actually say." Columns: `id`, `recording_id` (FK, cascade delete),
`source`, `taxon_id` (FK, nullable), `raw_label` (string, nullable), `confidence` (float).

**Deviation, 2026-09-16 (implementation):** `raw_label` was not in this spec's original column
list and was added during the branch's final review. Without it, a detection whose class name has
no `TaxonCode` mapping could only be stored as `taxon_id NULL` with nothing to say *what* the
model had named — indistinguishable from "the model said nothing", and so dropped at materialize
time, leaving the recording with a confident-looking `NO_ID` (see §2.3). Keeping the label makes
an unmapped classifier label behave like an unmapped EMT code: a real `SPECIES` claim with
`taxon_id NULL`, visible as "unmapped species", and reconnectable later by adding the mapping.

`source` is an open string/enum column with **no CHECK constraint** — identical reasoning to
`Identification.source` (spec D9: "further classifiers are coming," a CHECK would force a
migration per classifier). This table is deliberately generic (not `batdetect2_detection`) so a
future BattyBirdNET/Kaleidoscope integration reuses it without a new migration.

Holds every candidate batdetect2 emits above a fixed, non-configurable, internal sanity floor
(proposed 0.05 — well below any plausible verdict threshold; discards pure-noise peaks, keeps row
counts sane). This floor is **not** user-facing config: exposing it would let someone set it above
their own verdict threshold and silently break the "recompute without rerunning the model"
property below.

### 2.2 `Identification` rows are materialized from `classifier_detection`, not written directly

The classification job (§4) only ever writes to `classifier_detection`. A separate, idempotent
materialize step (§4) reads `classifier_detection` rows for `source=BATDETECT2`, filters by the
**current** `FLEDERMAP_BATDETECT2_THRESHOLD`, resolves each surviving `taxon_id` into a
`ClaimInput` (verdict `SPECIES`), and calls the existing `services/identifications.py`
`replace_claims` — the same shared claim-sync algorithm every other automatic source already
uses.

This split is what makes threshold changes free: the classification job (expensive, one torch
forward pass per recording) needs to run at most once per recording ever. The materialize step
(cheap — a filtered read plus `replace_claims`) can re-run every cycle against whatever the
threshold config currently says, with no model re-invocation. Reusing the existing `confidence:
float | None` column already on `Identification` for the materialized claim's own score is not
needed for this mechanism (the row's presence/verdict already encodes "passed the current
threshold"); `classifier_detection.confidence` is the source of truth for recompute.

### 2.3 Verdict semantics

- A recording with no `classifier_detection` rows for `BATDETECT2` has never been processed — no
  `Identification` row from that source exists, same as any not-yet-run classifier.
- A recording with `classifier_detection` rows but none clearing the threshold gets an explicit
  `NO_ID` claim (taxon_id `None`) — "batdetect2 ran and found nothing," distinguishable from "not
  yet processed," matching how EMT sources represent the same distinction.
- This automatic `NO_ID` needs no special-casing in `current_best.py`: the existing rule ("a
  source's claims are skipped when they are purely automatic `NO_ID`") already covers it, since
  batdetect2 is not `MANUAL`.
- A detection that *does* clear the threshold but whose label has no taxon mapping is a `SPECIES`
  claim with `taxon_id NULL` and `raw_label` set — **not** a `NO_ID`, and never dropped
  (deviation, 2026-09-16: the first implementation filtered these out). An unmapped label is a
  review-queue item by design (§3), and the drop was unrecoverable in practice: `NO_ID` is a
  confident-looking answer, and the enqueue sweep never re-runs the model for a recording that
  already has `classifier_detection` rows.

**Which of batdetect2's two scores is which** (established 2026-09-16 against the real installed
package, batdetect2 1.3.1): each annotation carries `det_prob` ("a call is present") and
`class_prob` ("and it is this species"), with `class_prob <= det_prob`. The internal sanity floor
(§2.1) gates on `det_prob`, matching batdetect2's own CLI `--detection-threshold`; the value
stored in `classifier_detection.confidence`, and therefore the one the user-configured
`FLEDERMAP_BATDETECT2_THRESHOLD` filters against, is `class_prob`, since that threshold is a
statement about species confidence.

## 3. Species code mapping

batdetect2's model uses its own class-name vocabulary, not Wildlife Acoustics/NABat codes. Per
`taxa_eu.yaml`'s existing per-taxon `codes: {source: code}` shape (e.g. `codes: {emt: PIPPIP}`),
each of the ~17 UK/EU species batdetect2 was trained on needs a `batdetect2:` entry added to its
existing taxon's `codes` dict in that file (not a new file, not a Python-side mapping table).

**The `codes.batdetect2` value must be its own independent string, not derived from
`scientific_name`.** This mirrors why `emt`'s codes are already independent of `scientific_name`
(WA's own naming convention, frozen regardless of taxonomic revision) — it lets `scientific_name`
be updated freely to reflect current taxonomic consensus (e.g. the already-planned *Cnephaeus*
reclassification) without that touching or breaking batdetect2 resolution, and vice versa: if
batdetect2's checkpoint uses an older or different naming convention than Fledermap's own
`scientific_name` for a given species, that's expected and handled by the two being decoupled,
not a bug to reconcile.

**Species identity vs. exact label strings — different confidence levels.** Research
cross-referencing (Bat Conservation Trust's standard "17 of 18 UK species are known to be
breeding here" list, corroborated by a field report naming several non-UK species batdetect2
misclassifies, all absent from that list) gives reasonable confidence on *which* 17 species are
covered, for scoping purposes. It does **not** establish batdetect2's actual internal class-name
strings — those live inside a binary PyTorch checkpoint (`batdetect2_uk_same.ckpt`) not readable
without loading it through `batdetect2.api`. Per this project's own rule ("never hand-enter a
species code" — `MYOSPP` was invented once and had to be removed), the exact `codes.batdetect2`
strings must be pulled from the real, loaded checkpoint's class list at implementation time (e.g.
`api.from_checkpoint()` then inspect its classes), never typed from this research. An unmapped
label resolves to `taxon_id=None` and lands in the review queue like any other unmapped source
label — not a failure, existing convention (`resolve_code` returning `None`).

## 4. Pipeline

Two new pieces in `jobs/tasks.py`, following the existing derived-media job shape
(`render_spectrogram_task` et al.):

- **`classify_batdetect2_task`** — per-recording, locked by `audio_hash` (same pattern as
  `spectrogram_lock_key`), `retry=_RETRY` (the same `RetryStrategy(max_attempts=3,
  exponential_wait=2)` every other job task already uses. Runs `batdetect2.api.process_file()` on
  the recording's resolved WAV path, writes every detection above the internal floor into
  `classifier_detection`. Enqueued the same way `services/media.py`'s `enqueue_media` enqueues
  spectrogram/oscillogram jobs: on ingest for newly created recordings, plus a backfill sweep
  (mirroring `backfill_media`) that runs once when the classifier transitions from disabled to
  enabled, covering every existing recording lacking a `classifier_detection` row for
  `BATDETECT2`.
- **A materialize step** — reads the current `FLEDERMAP_BATDETECT2_THRESHOLD`, filters
  `classifier_detection`, calls `replace_claims` per recording (§2.2/§2.3). Piggybacks on the
  existing `run_ingest_cycle` (`*/5 * * * *`) cadence as one more idempotent step, rather than a
  new periodic task with its own lock — consistent with "just run it every cycle, idempotent,"
  the same shape `replace_claims` already runs in for EMT sources every cycle.

**Known, deliberately deferred limitation:** the materialize step is O(n) over every
`classifier_detection` row every cycle. Solving this properly (skip the sweep when the threshold
hasn't changed since last run) requires new persisted state this project doesn't currently have —
everything config-related is stateless env/TOML, re-read fresh each time — plus new questions
(race handling across workers, first-run behavior). At this project's actual data volumes (a
single-operator archive, not millions of recordings), the naive full-sweep cost is not
measurable — one indexed `SELECT` plus a `replace_claims` call per recording, every five minutes,
the same shape of work `run_ingest_cycle` already does unconditionally for every EMT source. Not
solved now; revisit only if an actual `run_ingest_cycle` duration measurement shows it mattering.

## 5. Config

New fields on `Config` (`config.py`), following the existing `FLEDERMAP_*` env-var/TOML-file
layering pattern:

- `FLEDERMAP_BATDETECT2_ENABLED` (bool, default `false`).
- `FLEDERMAP_BATDETECT2_THRESHOLD` (float, default around **0.8** — batdetect2's own CLI
  documents `--detection-threshold` as a required 0–1 argument with no stated default; 0.8 is
  this project's own starting point given the field-report evidence that low-confidence output is
  frequently wrong, not a value pulled from batdetect2's own docs).

**Enabled but not installed** (`FLEDERMAP_BATDETECT2_ENABLED=true` without `pip install
fledermap[batdetect2]` having been run): matches this project's existing precedent for a
configured-but-broken optional integration, rather than inventing new machinery. Checked against
`services/site_naming.py`'s `ensure_connected`/`name_site_task`: when `FLEDERMAP_
POIIDX_DATABASE_URL` is set but the database is actually unreachable, there is **no** global
disable-and-surface behavior today — `ensure_connected` simply raises inside the job, the existing
`_RETRY` policy (3 attempts, exponential backoff) retries it, Procrastinate logs the traceback
each time, and after retries exhaust the job just fails; nothing disables the feature globally and
nothing surfaces in the UI. `classify_batdetect2_task` follows the same shape: if `batdetect2` is
enabled but `import batdetect2` fails, that's an ordinary job failure, retried and logged the same
way, no config-time check and no global disable added. A richer "detect once, degrade to
`enabled=false`, surface an error in the UI" behavior was considered and deliberately deferred —
see §7 (Out of scope) — as a possible future improvement to both this integration and poiidx's,
not something to build bespoke for batdetect2 alone.

**Enabling** (transition from disabled to enabled): enqueues `classify_batdetect2_task` for every
recording lacking a `classifier_detection` row for `BATDETECT2` (the backfill sweep, §4), and
starts enqueuing it for newly-ingested recordings going forward.

**Disabling**: deletes all `classifier_detection` and `Identification` rows for
`source=BATDETECT2` — a scoped delete (`WHERE source = 'batdetect2'`), never touching any other
source's rows, and never cascading into `recording`/`session`/`site`. Stops future enqueues.
Re-enabling later is a clean regenerate from scratch (the backfill sweep finds every recording
missing a `classifier_detection` row again, since they were all just deleted) — never a partial or
stale state. Exact handling of an already-in-flight job that completes just after a disable (it
would write a `classifier_detection` row the next materialize cycle should not treat as live) is
an implementation-time race-handling detail, not fully specced here — flagged for the
implementation plan to address explicitly (e.g. the materialize/backfill logic re-checking the
enabled flag, or the disable action re-running its delete once more after in-flight jobs settle).

## 6. Precedence and existing-code impact

`current_best.py`'s `_PRECEDENCE` becomes:

```
MANUAL, EMT_MANUAL, EMT_GUANO, EMT_WAMD, EMT_FILENAME, BATDETECT2, FLEDERMAP_NOISE
```

Below all EMT sources (an on-device vendor classifier outranks a general-purpose research model),
above `FLEDERMAP_NOISE` (batdetect2 can produce real `SPECIES` claims, unlike the noise classifier
which only ever emits `NOISE`/`NO_ID` and is deliberately always-last so it never shadows a real
answer).

**One existing test's premise changes as a direct consequence:**
`tests/test_current_best.py::test_sort_identifications_by_precedence_puts_an_unranked_source_last`
currently asserts `IdSource.BATDETECT2` sorts last as an unranked/not-yet-implemented source. Once
`BATDETECT2` is wired into `_PRECEDENCE`, that assertion is no longer testing what it's meant to
test — it needs to switch to a source that genuinely remains unranked (e.g. `IdSource.
BATTYBIRDNET`, appropriately, since that one is explicitly out of scope for this spec).

## 7. Out of scope for this spec

- BattyBirdNET-Analyzer integration (deferred, per the scope note above).
- Region-dependent classifier activation (e.g. only running batdetect2 for European recordings).
  Deferred: no region/location classification field exists on `Recording` today (only raw
  coordinates), and deriving one is its own project. batdetect2 runs unconditionally on every
  recording when enabled, regardless of location — for a non-European recording this just means
  its species output is meaningless, which is an accepted tradeoff for now.
- Region-dependent scoping of the manual-classification species dropdowns (a related future idea,
  noted but out of scope here — its own rabbit hole).
- Solving the O(n) materialize-sweep cost (§4).
- A global "detect a broken optional integration once, degrade to disabled, surface an error in
  the UI" mechanism (§5). No such mechanism exists today even for poiidx's own
  configured-but-unreachable case (job-level retry-and-log only, per the existing `_RETRY`
  policy) — this spec matches that existing precedent rather than building one bespoke for
  batdetect2. Worth a future spec of its own if/when it's wanted, most usefully covering poiidx
  and batdetect2 (and any later classifier) together rather than one at a time, including where
  in the UI such a status/error would live (no settings/status page exists yet).

## 8. Reconnecting an unmapped label after the fact

Found during this feature's final whole-branch review: `reresolve_unmapped_identifications`
(the existing mechanism that retroactively reconnects an unmapped EMT claim once a code
mapping is added) patches the `Identification` row's `taxon_id` directly — but that alone
would not help a batdetect2 claim, since `services/batdetect2.py`'s `materialize_claims`
rebuilds `Identification` from `classifier_detection` every cycle regardless, which would
silently undo the patch on the very next run.

Fixed by having `materialize_claims` re-attempt `resolve_code` against a still-unmapped
detection's `raw_label` itself, every cycle, rather than only trusting the `taxon_id` cached
on the detection row from whenever the classification job last ran — see the function's own
docstring in `services/batdetect2.py` for the mechanism. This is what lets a taxonomy mapping
added after the model already ran reach an already-cached, already-unmapped detection, not
only a not-yet-classified one.
