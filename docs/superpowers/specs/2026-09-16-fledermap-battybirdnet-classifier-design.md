# Fledermap — BattyBirdNET-Analyzer classifier integration design

Status: proposed, not yet implemented. Scope: BattyBirdNET-Analyzer as a second acoustic
classifier, alongside the already-shipped batdetect2 (docs/superpowers/specs/2026-09-15-
fledermap-batdetect2-classifier-design.md). That earlier spec deliberately deferred
BattyBirdNET-Analyzer because it isn't pip-installable and carries a stricter
CC-BY-NC-**SA**-4.0 license (ShareAlike, not just non-commercial). This spec resolves both
problems with a different integration shape: Fledermap talks HTTP to a server the *operator*
runs and builds themselves (e.g. https://github.com/bytehexe/battybirdnet-docker, a Docker
Compose wrapper around the upstream https://github.com/rdz-oss/BattyBirdNET-Analyzer REST
server), rather than importing, vendoring, or bundling any of that code.

## 1. Licensing

BattyBirdNET-Analyzer (by R.D. Zinck) is licensed **CC-BY-NC-SA-4.0** — non-commercial,
ShareAlike. Fledermap never imports, vendors, patches, or bundles any of its code: this
integration is a plain HTTP client (`requests`) that POSTs a WAV file to a server URL the
operator configures and runs entirely outside Fledermap's own process and install. No
"adaptation" of CC-BY-NC-SA-licensed material exists inside Fledermap at any point, so
ShareAlike never triggers — this is a cleaner licensing position than batdetect2's in-process
package inclusion, which needed its own `pyproject.toml` extras group specifically to keep the
NC-licensed package out of a default install. Here, nothing NC-licensed enters Fledermap's
dependency tree at all; `requests` is an ordinary MIT/Apache-licensed HTTP client with zero
entanglement, so it lives as a normal base dependency, not an opt-in extra.

**Requirements:**

- Attribution to BattyBirdNET-Analyzer / R.D. Zinck wherever the feature is documented or
  toggled (mirrors batdetect2's own attribution requirement).
- An explicit note wherever the feature is configured that the *server* the operator points
  Fledermap at is non-commercial-use-only — that obligation is the operator's, running their own
  separate process, not something Fledermap's own MIT grant touches, but a public MIT repo
  silently pointing at a NC-restricted service by default would be exactly the same kind of trap
  for a commercial adopter that batdetect2's spec already reasoned through.
- Disabling the classifier deletes all its `classifier_detection`/`Identification` rows, same
  disable=delete/enable=regenerate contract as batdetect2 (§5 below).

This is not legal advice; if real certainty is wanted, a lawyer's read beats this reasoning.

## 2. Architecture

`services/batdetect2.py`'s `record_detections`, `materialize_claims`, and `purge_source` are
already generic over `IdSource` — nothing about their signatures or logic is batdetect2-specific,
only their current module name is. Now that a second classifier needs them:

- **Rename** `services/batdetect2.py` → **`services/classifier_detections.py`**, moving those
  three functions and their shared module docstring verbatim. `enqueue_batdetect2` stays in a
  (renamed or newly split) batdetect2-specific module, since it dispatches
  `classify_batdetect2_task` specifically.
- **New `services/battybirdnet.py`**: `enqueue_battybirdnet`, mirroring `enqueue_batdetect2`'s
  recording-scan-and-lock pattern (not shared code — the two dispatch different task functions
  with different lock-key namespaces).
- **`jobs/tasks.py`** gains `classify_battybirdnet_task`, same three-phase shape as
  `classify_batdetect2_task` (resolve recording + WAV path in one session → the slow external
  call with no session open → a second session to write results via `record_detections`) — the
  "slow external call" is a `requests.post(...)` to the operator's server instead of an in-process
  torch forward pass, but the idle-in-transaction reasoning from CLAUDE.md's Database section
  applies identically.
- **`services/ingest.py`**'s `run_ingest_cycle` gains a `battybirdnet_enabled` block parallel to
  the existing batdetect2 one: enqueue + materialize when enabled, `purge_source` when not.
- `IdSource.BATTYBIRDNET` already exists in `domain/codes.py` — no enum change needed.

Every existing `from fledermap.services.batdetect2 import record_detections/materialize_claims/
purge_source` call site (jobs/tasks.py, tests) moves to `services.classifier_detections`.

## 3. Data flow

`classify_battybirdnet_task(context, audio_hash)`:

1. Resolve the recording and its WAV path (`services/media.py`'s `resolve_recording`/
   `resolve_wav_path`, same helpers `classify_batdetect2_task` already uses).
2. Close the session, then `POST {battybirdnet_url}/analyze` with the WAV as a multipart `audio`
   field, via `requests`. No `meta` fields are set beyond the server's own defaults — no
   `lat`/`lon`, since `Recording` has no region field (same accepted gap as batdetect2's own
   region-blind operation, §7 below).
3. Parse the JSON response's `results` field — confirmed from `analyze.py`'s `analyzeFile`
   (the function `server.py`'s `/analyze` route calls and returns verbatim) to be a dict shaped
   `{"{segment_start}-{segment_end}": [[label, score_str], ...], ...}`: one key per 3-second
   analysis window, each value a list of `[label, score]` pairs for **every** class in the
   label vocabulary (all ~30 species plus `NOISE` for the EU checkpoint), sorted by score
   descending but never filtered — `score` is a JSON string, not a number, and must be cast with
   `float()`. Flatten every window's list into one stream of `(label, confidence)` pairs across
   the whole recording, drop anything below `_BATTYBIRDNET_FLOOR = 0.05` (the same value as
   `_BATDETECT2_FLOOR`, chosen for the same reason: a low sanity floor, not the user's
   configured threshold, is what keeps a later threshold change free of any model re-run — see
   §7's storage-vs-free-recompute discussion, resolved in favor of the low-floor approach despite
   battybirdnet's response being considerably larger per recording than batdetect2's), and turn
   each survivor into a `DetectionInput`: if
   `domain/codes.py`'s existing `sentinel_verdict(label)` returns a `Verdict` (it already maps
   `"noise"`, case/whitespace-insensitive, to `Verdict.NOISE` — built for EMT's own sentinel
   strings, reused here as-is with zero changes), carry `taxon_id=None` with that meaning;
   otherwise split `label` on the first `"_"` and resolve the prefix (the scientific name) via
   `resolve_code(session, IdSource.BATTYBIRDNET, prefix)`, keeping the full original `label`
   string as `raw_label` for display/audit (matching batdetect2's convention of storing the
   verbatim model output). This dict shape (confirmed from upstream source, 2026-09-16) still
   needs confirming against a real running server's actual JSON at implementation time, same
   discipline as batdetect2's own Task 5 precedent — reading the source is a legitimate
   verification step, not a substitute for it.
4. Write the full flattened list via `record_detections` (full replace, unchanged function).
5. On any request failure (`requests.exceptions.RequestException`, non-200 response, or a
   response `analyze` reports as unsuccessful): let the job raise and retry via Procrastinate's
   existing `_RETRY` policy. No healthcheck step, no circuit breaker, no global "disable and
   surface a UI error" mechanism — same precedent as `services/site_naming.py`'s
   `ensure_connected` and batdetect2's own spec (its §7 already deferred building this kind of
   mechanism generally).

A `_BATTYBIRDNET_FLOOR` constant (mirroring `_BATDETECT2_FLOOR`) is needed here: `server.py`'s
`/analyze` route returns `results` straight from `analyze.analyzeFile()`, a plain dict
`{"{start}-{end}": [[label, score_str], ...]}` — one entry per 3-second segment, with **every**
class in the label vocabulary scored and included, sorted by score but never filtered by
`MIN_CONFIDENCE`. That constant is only ever compared inside `saveResultFile()` (the CLI-only
file-export function), which the API server never calls — confirmed by reading `analyze.py`'s
source directly. So every segment's full, unfiltered score distribution across the whole label
vocabulary reaches Fledermap, mostly near-zero — exactly what batdetect2's own floor exists to
contain, and the same reasoning applies here.

`materialize_claims` (the shared, now-generic module) needs one small, targeted addition: check
`sentinel_verdict(raw_label)` before attempting `resolve_code`, and if it returns a verdict,
build the claim with that verdict directly (`taxon_id=None`) instead of routing through the
unmapped-species path. This is reuse of an existing, already-source-agnostic helper (previously
only called from EMT's ingest path), not new battybirdnet-specific logic in the shared module.

## 4. Species-code mapping

- New `codes.battybirdnet` entries in `taxa_eu.yaml` (and `taxa_na.yaml`, if a USA-region
  checkpoint is ever configured), storing only the scientific-name prefix of each label (e.g.
  `"Myotis daubentonii"`, not the full `"Myotis daubentonii_Daubentons bat"`) — the common-name
  half is locale-dependent (German/French/Swedish label files exist for the same species) and
  therefore not a stable matching key. Sourced from the upstream repo's own checkpoint label
  file for whichever `AREA` the operator's server actually runs (confirmed 2026-09-16 for the EU
  checkpoint directly from `rdz-oss/BattyBirdNET-Analyzer`'s
  `checkpoints/bats/v1.0/BattyBirdNET-EU-256kHz_Labels.txt` at its current `main` ref — a real,
  verbatim, pinned source, not hand-typed or guessed) — cross-checked against the operator's
  actual running server's `/analyze` response at implementation time to confirm the deployed
  `battybirdnet-docker` build's `REPO_REF` matches.
- `Plecotus spec.` (a genus-level candidate, not species-level — the EU checkpoint doesn't
  distinguish *Plecotus auritus* from *Plecotus austriacus* acoustically) maps its
  scientific-name prefix `"Plecotus"` onto the **already-existing** genus-level `Plecotus` taxon
  in `taxa_groups.yaml` — no new taxonomy needed, just a new `codes.battybirdnet` entry on that
  existing row.
- `NOISE` never gets a taxa-YAML entry — it resolves via `sentinel_verdict`, never `resolve_code`.

## 5. Config

Mirrors `Config`'s existing `batdetect2_enabled`/`batdetect2_threshold` shape, plus a URL
(there's no equivalent for batdetect2, which runs in-process):

- `battybirdnet_enabled: bool = False`.
- `battybirdnet_url: str | None = None` — base URL of the operator's own server (e.g.
  `http://localhost:7667`). `Config.from_env()` raises `ConfigError` if `battybirdnet_enabled`
  is true but `battybirdnet_url` is unset — mirrors the existing `poiidx_database_url`
  validation shape (a required companion setting for an optional integration).
- `battybirdnet_threshold: float = 0.6` — matches the upstream tool's own default
  `MIN_CONFIDENCE` (its CLI's own suggested confidence bar, `config.py`'s `MIN_CONFIDENCE: float
  = 0.6`), even though that constant is never actually enforced server-side for the API path
  (§3/§7) — it's still a reasonable starting point since it's the tool author's own judgment of a
  sensible default, distinct from batdetect2's 0.8 (this project's own conservative choice, no
  equivalent upstream number to anchor to).

## 6. Precedence

`current_best.py`'s `_PRECEDENCE`: all EMT sources first (unchanged), then `BATDETECT2`, then
`BATTYBIRDNET` — both below every EMT source (an on-device classifier paired with the actual
recording hardware outranks any general-purpose acoustic classifier run after the fact, same
reasoning as batdetect2's original spec), and batdetect2 above battybirdnet specifically because
it's purpose-built for bat-call classification, while BattyBirdNET-Analyzer is a transfer-learned
repurposing of a bird-call classifier (BirdNET) onto ultrasonic frequencies.

## 7. Known limitations (documented, not solved here)

- **`_BATTYBIRDNET_FLOOR = 0.05` (§3) means considerably more `classifier_detection` rows per
  recording than batdetect2 stores.** Considered filtering at write-time to the currently
  configured `battybirdnet_threshold` instead, which would shrink storage substantially — but
  rejected, since `enqueue_battybirdnet` (like `enqueue_batdetect2`) never re-runs the model for a
  recording that already has a `classifier_detection` row, so filtering at the configured
  threshold would mean a later *lowered* threshold could never surface anything that was
  discarded, without deleting rows and forcing a re-classification. The low-floor approach keeps
  "changing the threshold takes effect on the next materialize cycle, no model re-run" true for
  battybirdnet exactly as it already is for batdetect2, at the cost of more storage per recording.
- **Region is fixed for the whole server process at startup**, and only one region (`AREA`) can
  be active at a time — the operator picks one when building/running their container. Fledermap
  has no region/location field on `Recording` (same gap already noted in the batdetect2 spec's
  out-of-scope section) and no way to select or verify the server's configured region per
  recording. Same accepted tradeoff as batdetect2's unconditional, region-blind operation: a
  mismatch just means meaningless output for a recording outside the server's configured area.
- **A `NOISE` candidate can compete with a genuinely-unmapped-species candidate for the single
  `taxon_id IS NULL` claim slot** `uq_identification_source_claim` allows per `(recording,
  source)`. `materialize_claims`'s existing highest-confidence-wins dedup (already the accepted
  behavior for multiple distinct unmapped species labels competing for that one slot) now also
  includes `NOISE` in that same competition, so which one wins is decided by raw confidence
  comparison across two semantically different categories. Accepted as-is for this pass — a
  natural extension of an already-accepted limitation, not a new one — and left for a future spec
  if it turns out to matter in practice.

## 8. Out of scope for this spec

- A healthcheck/circuit-breaker mechanism using the server's `GET /healthcheck` endpoint before
  treating it as broken — matches batdetect2's spec, which already deferred building any such
  mechanism generally (its §7).
- Region-dependent classifier activation (unchanged from batdetect2's spec — no region field on
  `Recording` exists yet).
- Any change to `materialize_claims`'s dedup behavior for the NOISE/unmapped-species slot
  competition noted in §7 above.
- Surfacing "server unreachable" anywhere in the UI beyond normal job-retry logging.
