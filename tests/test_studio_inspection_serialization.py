from pyFuncs.ChoirInspection import (
    MidiNote,
    MidiOverlapRegion,
    MidiTrackInfo,
    _overlap_metrics,
)
from tools.choir_studio_bridge import _jsonable


def test_studio_serializes_computed_polyphony_and_note_counts() -> None:
    track = MidiTrackInfo(
        index=6,
        name="Bass Chords",
        notes=(
            MidiNote(0, 1000, 48, 90, 0),
            MidiNote(0, 1000, 55, 90, 0),
            MidiNote(200, 600, 60, 90, 0),
        ),
        notes_below_150ms=0,
        max_polyphony=3,
        overlap_regions=1,
        total_overlap_ms=1000.0,
        longest_overlap_ms=1000.0,
        overlap_totals=(
            MidiOverlapRegion(2, 600.0),
            MidiOverlapRegion(3, 400.0),
        ),
        duplicate_note_spans=0,
        warnings=(),
    )
    payload = _jsonable(track)

    assert payload["max_polyphony"] == 3
    assert payload["note_count"] == 3
    assert payload["notes_below_150ms"] == 0
    assert payload["overlap_regions"] == 1
    overlap_counts = [item["note_count"] for item in payload["overlap_totals"]]
    assert overlap_counts == sorted(set(overlap_counts))


def test_overlap_summary_times_each_exact_concurrency_level() -> None:
    notes = (
        MidiNote(0, 1000, 60, 90, 0),
        MidiNote(0, 1000, 64, 90, 0),
        MidiNote(200, 600, 67, 90, 0),
    )

    regions, total_ms, longest_ms, summary = _overlap_metrics(
        notes, lambda tick: float(tick)
    )

    assert (regions, total_ms, longest_ms) == (1, 1000.0, 1000.0)
    assert [(item.note_count, item.duration_ms) for item in summary] == [
        (2, 600.0),
        (3, 400.0),
    ]
