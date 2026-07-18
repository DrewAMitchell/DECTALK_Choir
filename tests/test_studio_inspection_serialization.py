from pyFuncs.ChoirInspection import inspect_song
from tools.choir_studio_bridge import REPO_ROOT, _jsonable, _polyphonic_split_preview


def test_studio_serializes_computed_polyphony_and_note_counts() -> None:
    payload = _jsonable(inspect_song(REPO_ROOT, "earthangel", include_audio=False))
    track = next(item for item in payload["roles"] if item["role"] == "Track_06")

    assert track["polyphony"] == 3
    assert track["note_count"] == 1160
    assert track["midi_track"]["note_count"] == 1160


def test_earthangel_track_six_split_preview_has_three_voices() -> None:
    preview = _polyphonic_split_preview("earthangel", "Track_06")

    assert preview["splittable"] is True
    assert preview["max_polyphony"] == 3
    assert len(preview["lanes"]) == 3
    assert sum(lane["note_count"] for lane in preview["lanes"]) == 1160
