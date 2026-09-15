"""The one test that checks Fledermap's parsing against the REAL batdetect2
package rather than a mock of it.

Every other batdetect2 test stubs `api.process_file`, which can only ever
confirm that the code agrees with the stub. That is exactly how the original
implementation shipped reading `result.detections`,
`detection.detection_score` and `detection.class_scores` -- none of which
batdetect2 has ever had -- with its mocked test passing. So this module feeds
`annotations_to_detections` a genuine `api.process_file` result.

Skipped unless the optional `batdetect2` extra is installed, i.e. everywhere
except `hatch run batdetect2:...`. Deliberately NOT `pytest.mark.db`: the
parsing takes a plain label-to-taxon-id callable rather than a session, so
this needs no database and no testcontainer.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

pytest.importorskip("batdetect2")

from batdetect2 import api  # type: ignore[import-not-found]  # noqa: E402

from fledermap.jobs.tasks import (  # noqa: E402
    _BATDETECT2_FLOOR,
    annotations_to_detections,
)

_SAMPLE_RATE = 256_000


def _synthetic_call_wav(path: Path) -> None:
    """A few short frequency-downsweeps at bat-call rates. Not a real bat --
    the point is only to get the model to emit annotations at all, since what
    is under test is the SHAPE of what it returns, not whether it identifies
    anything correctly."""
    total = int(_SAMPLE_RATE * 0.3)
    frames = bytearray()
    for i in range(total):
        t = i / _SAMPLE_RATE
        amplitude = 0.0
        for start in (0.05, 0.15, 0.25):
            if start <= t < start + 0.004:
                progress = (t - start) / 0.004
                freq = 90_000 - 45_000 * progress
                envelope = math.sin(math.pi * progress)
                amplitude += 0.8 * envelope * math.sin(2 * math.pi * freq * (t - start))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, amplitude)) * 32767))

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(bytes(frames))


@pytest.fixture(scope="module")
def real_annotations(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    wav_path = tmp_path_factory.mktemp("batdetect2") / "synthetic.wav"
    _synthetic_call_wav(wav_path)
    result = api.process_file(str(wav_path))
    # Exactly the two lookups `classify_batdetect2_task` performs on the
    # result -- if either key ever moves, this fails here rather than in
    # production.
    annotations = result["pred_dict"]["annotation"]
    assert annotations, "the model returned no annotations for the test signal"
    return list(annotations)


def test_every_real_annotation_has_the_fields_the_parser_reads(
    real_annotations: list[dict],
) -> None:
    for annotation in real_annotations:
        assert isinstance(annotation, dict)
        assert isinstance(annotation["class"], str)
        assert annotation["class"]
        assert isinstance(annotation["class_prob"], float)
        assert isinstance(annotation["det_prob"], float)


def test_parsing_real_output_keeps_the_label_and_the_class_probability(
    real_annotations: list[dict],
) -> None:
    """The real end-to-end check: the production parser, over real model
    output, with an always-unmapped resolver (no database, no seeded taxa)."""
    detections = annotations_to_detections(real_annotations, lambda _label: None)

    expected = [a for a in real_annotations if a["det_prob"] >= _BATDETECT2_FLOOR]
    assert len(detections) == len(expected)
    for detection, annotation in zip(detections, expected, strict=True):
        assert detection.raw_label == annotation["class"]
        # `class_prob` is stored, since the user's configured threshold is a
        # statement about species confidence; `det_prob` only gates the floor.
        assert detection.confidence == annotation["class_prob"]
        # Unmapped resolves to None rather than being dropped.
        assert detection.taxon_id is None


def test_the_floor_gates_on_det_prob_not_class_prob(
    real_annotations: list[dict],
) -> None:
    """Pins which of the two real scores the floor is compared against. The
    model's own scores make this observable: `class_prob` <= `det_prob` for
    every annotation, so gating on `class_prob` would keep a strict subset."""
    for annotation in real_annotations:
        assert annotation["class_prob"] <= annotation["det_prob"]

    detections = annotations_to_detections(real_annotations, lambda _label: None)
    by_det_prob = [a for a in real_annotations if a["det_prob"] >= _BATDETECT2_FLOOR]
    assert len(detections) == len(by_det_prob)
