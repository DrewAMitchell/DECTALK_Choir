from pyFuncs.ChoirInspection import MidiNote, _overlap_metrics, inspect_song
from tools.choir_studio_bridge import REPO_ROOT, _jsonable, _polyphonic_split_preview


def test_studio_serializes_computed_polyphony_and_note_counts() -> None:
    payload = _jsonable(inspect_song(REPO_ROOT, "earthangel", include_audio=False))
    track = next(item for item in payload["roles"] if item["role"] == "Track_06")

    assert track["polyphony"] == 3
    assert track["note_count"] == 1160
    assert track["midi_track"]["note_count"] == 1160
    assert track["midi_track"]["overlap_regions"] > 0
    assert track["midi_track"]["overlap_totals"]
    overlap_counts = [item["note_count"] for item in track["midi_track"]["overlap_totals"]]
    assert overlap_counts == sorted(set(overlap_counts))


def test_earthangel_track_six_split_preview_has_three_voices() -> None:
    preview = _polyphonic_split_preview("earthangel", "Track_06")

    assert preview["splittable"] is True
    assert preview["max_polyphony"] == 3
    assert len(preview["lanes"]) == 3
    assert sum(lane["note_count"] for lane in preview["lanes"]) == 1160


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
