from pathlib import Path

import mido

from tools.create_octave_boost_reference_song import CHROMATIC_OCTAVE, TRACKS


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_octave_reference_covers_four_complete_octaves_without_plus_three() -> None:
    assert CHROMATIC_OCTAVE == list(range(13))
    assert [track["name"] for track in TRACKS] == [
        "OctaveDown1",
        "Center",
        "OctaveUp1",
        "OctaveUp2",
    ]
    assert [track["octave_boost"] for track in TRACKS] == [-12, 0, 12, 24]
    assert [(min(track["notes"]), max(track["notes"]), len(track["notes"])) for track in TRACKS] == [
        (36, 48, 13),
        (48, 60, 13),
        (60, 72, 13),
        (72, 84, 13),
    ]


def test_checked_in_octave_reference_matches_generator() -> None:
    midi = mido.MidiFile(REPO_ROOT / "songs" / "OctaveBoostReference" / "inputs" / "OctaveBoostReference.mid")
    actual = []
    for track in midi.tracks:
        notes = [message.note for message in track if message.type == "note_on" and message.velocity > 0]
        actual.append((track.name, notes))

    assert actual == [(track["name"], track["notes"]) for track in TRACKS]
