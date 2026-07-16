from pathlib import Path

import mido

from tools.split_polyphonic_midi import parse_track, split_midi


def _source_midi(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=96)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Timing", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=384))
    midi.tracks.append(conductor)

    chords = mido.MidiTrack()
    chords.append(mido.MetaMessage("track_name", name="Bass Chords", time=0))
    chords.append(mido.Message("note_on", note=48, velocity=90, time=0))
    chords.append(mido.Message("note_on", note=55, velocity=80, time=0))
    chords.append(mido.Message("note_off", note=48, velocity=0, time=96))
    chords.append(mido.Message("note_off", note=55, velocity=0, time=0))
    chords.append(mido.Message("note_on", note=50, velocity=90, time=48))
    chords.append(mido.Message("note_off", note=50, velocity=0, time=96))
    chords.append(mido.MetaMessage("end_of_track", time=144))
    midi.tracks.append(chords)

    untouched = mido.MidiTrack()
    untouched.append(mido.MetaMessage("track_name", name="Lead", time=0))
    untouched.append(mido.Message("note_on", note=72, velocity=70, time=24))
    untouched.append(mido.Message("note_off", note=72, velocity=0, time=120))
    untouched.append(mido.MetaMessage("end_of_track", time=240))
    midi.tracks.append(untouched)
    midi.save(path)


def test_targeted_split_preserves_source_role_name_and_notes(tmp_path: Path) -> None:
    source = tmp_path / "song.mid"
    output = tmp_path / "song_split.mid"
    _source_midi(source)
    source_bytes = source.read_bytes()

    mappings = split_midi(source, output, target_track_indices=[1])

    assert source.read_bytes() == source_bytes
    assert len(mappings) == 1
    assert [len(lane.notes) for lane in mappings[0][1]] == [2, 1]

    result = mido.MidiFile(output)
    assert [track.name for track in result.tracks] == [
        "Timing",
        "Bass Chords",
        "Bass Chords - Voice 2",
        "Lead",
    ]
    assert [len(parse_track(track, index).notes) for index, track in enumerate(result.tracks)] == [0, 2, 1, 1]
