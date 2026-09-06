"""Writes IdSource.MANUAL identification rows from the recording-details
page's classifier box (design spec 2026-09-05-fledermap-manual-classification-
design.md, §4). The only place in the codebase that writes IdSource.MANUAL --
every other source is written by services/ingest.py's commit_scan during a
scan, never here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import Recording


def set_manual_classification(
    session: OrmSession,
    recording: Recording,
    *,
    verdict: Verdict | None,
    taxon_ids: Sequence[int] = (),
) -> None:
    """Replaces every standing MANUAL claim with exactly what the new state
    calls for -- the classifier box always submits its full current state
    rather than a diff, and replace_claims (services/identifications.py) is
    the safe way to apply that: no history kept, so an edited-many-times
    claim never accumulates dead rows.

    `verdict=None` means "clear to no manual opinion" -- a real, explicit
    input, not an implicit consequence of SPECIES with an empty taxon_ids
    (that combination raises instead, so a client bug sending an empty list
    by accident fails loudly rather than silently clearing a classification).
    """
    if verdict == Verdict.SPECIES and not taxon_ids:
        msg = "verdict=SPECIES requires at least one taxon_id"
        raise ValueError(msg)
    if verdict != Verdict.SPECIES and taxon_ids:
        msg = "taxon_ids is only meaningful for verdict=SPECIES"
        raise ValueError(msg)

    now = datetime.now(UTC)
    desired: list[ClaimInput] = []
    if verdict in (Verdict.NO_ID, Verdict.NOISE):
        desired.append(ClaimInput(taxon_id=None, verdict=verdict))
    elif verdict == Verdict.SPECIES:
        desired.extend(
            ClaimInput(taxon_id=taxon_id, verdict=Verdict.SPECIES)
            for taxon_id in taxon_ids
        )
    # verdict is None: "clear" -- desired stays empty, replace_claims removes
    # every standing MANUAL row outright.

    replace_claims(session, recording, IdSource.MANUAL, desired, now)
    session.commit()


def current_manual_state(recording: Recording) -> tuple[Verdict | None, frozenset[int]]:
    """The classifier box's own displayed/edited state -- deliberately
    independent of `current_best_identification`'s cross-source precedence
    walk (Task 5 review finding, 2026-09-05). The box must reflect and edit
    ONLY the recording's own standing MANUAL claims:

    - If it read `best` instead, an automatic classifier's winning SPECIES
      claim would render as editable manual chips -- adding one more chip
      would then resubmit the full chip set as new MANUAL claims, silently
      converting an automatic identification into a manual one the user
      never asked to make, and "Clear" would look broken (the chips just
      come back from the still-there automatic claim).
    - It would also let an always-active AUTOMATIC NOISE/NO_ID claim
      disable the search input with no way to enter a correction --
      defeating the core use case of overriding a wrong automatic call.

    Returns `(None, frozenset())` when there is no standing manual claim at
    all -- the same "no manual opinion" state `set_manual_classification`
    produces for `verdict=None`.
    """
    manual_claims = [
        i for i in recording.identifications if i.source == IdSource.MANUAL
    ]
    manual_verdict = manual_claims[0].verdict if manual_claims else None
    manual_taxon_ids = frozenset(
        i.taxon_id for i in manual_claims if i.taxon_id is not None
    )
    return manual_verdict, manual_taxon_ids
