"""The `fledermap.noise` classifier (Obsidian backlog "Noise" section, Step
2): classifies every recording with a known `snr_db` as NOISE or NO_ID
against a configurable threshold. Lowest precedence
(`services/current_best.py`'s `_PRECEDENCE`) -- any real identification from
another source always wins over it.

Recomputes EVERY recording with a known `snr_db`, not just ones missing a
`fledermap.noise` claim: `replace_claims` already no-ops when nothing
changed, so this is cheap, and it's what makes a changed
`noise_snr_threshold_db` take effect automatically the next time this runs
(`jobs/tasks.py`'s `run_ingest_cycle`, or `fledermap classify-noise` on
demand) -- no separate "did the config change" tracking needed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import Recording


@dataclass(frozen=True)
class NoiseClassifyReport:
    classified: int


def classify_noise(session: OrmSession, threshold_db: float) -> NoiseClassifyReport:
    """Set each recording's `fledermap.noise` claim to NOISE if `snr_db <
    threshold_db`, else NO_ID. Recordings with `snr_db is None` (not yet
    computed, or unreadable) are skipped entirely -- there's nothing to base
    a claim on yet."""
    recordings = session.scalars(
        select(Recording).where(Recording.snr_db.is_not(None)),
    ).all()

    now = datetime.now(UTC)
    classified = 0
    for recording in recordings:
        assert recording.snr_db is not None  # guaranteed by the query above
        verdict = Verdict.NOISE if recording.snr_db < threshold_db else Verdict.NO_ID
        replace_claims(
            session,
            recording,
            IdSource.FLEDERMAP_NOISE,
            [ClaimInput(taxon_id=None, verdict=verdict)],
            now,
        )
        classified += 1

    return NoiseClassifyReport(classified=classified)
