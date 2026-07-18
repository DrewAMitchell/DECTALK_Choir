from pathlib import Path

import mido
import pytest

from pyFuncs.MidiPreview import MidiPreviewError, write_single_track_preview


def _absolute_messages(track: mido.MidiTrack):
    tick = 0
    output = []
    for message in track:
        tick += message.time
        output.append((tick, message))
    return output


def test_preview_replaces_program_changes_and_preserves_note_timing(tmp_path: Path):
    source_path = tmp_path / "source.mid"
    output_path = tmp_path / "preview.mid"
    source = mido.MidiFile(type=1, ticks_per_beat=480)
    source.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=960),
    ]))
    source.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead", time=0),
        mido.Message("control_change", channel=2, control=0, value=12, time=0),
        mido.Message("control_change", channel=2, control=32, value=3, time=0),
        mido.Message("program_change", channel=2, program=40, time=0),
        mido.Message("note_on", channel=2, note=60, velocity=90, time=120),
        mido.Message("program_change", channel=2, program=12, time=120),
        mido.Message("note_off", channel=2, note=60, velocity=0, time=240),
        mido.Message("note_on", channel=9, note=64, velocity=90, time=120),
        mido.Message("note_off", channel=9, note=64, velocity=0, time=240),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    source.save(source_path)

    write_single_track_preview(source_path, 1, output_path, program=73)

    preview = mido.MidiFile(output_path)
    events = _absolute_messages(preview.tracks[1])
    programs = [(tick, message.channel, message.program) for tick, message in events if message.type == "program_change"]
    bank_selects = [message for _tick, message in events if message.type == "control_change" and message.control in {0, 32}]
    notes = [(tick, message.type, message.channel, message.note) for tick, message in events if message.type in {"note_on", "note_off"}]
    assert programs == [(0, 0, 73), (0, 2, 73)]
    assert bank_selects == []
    assert notes == [
        (120, "note_on", 2, 60),
        (480, "note_off", 2, 60),
        (600, "note_on", 0, 64),
        (840, "note_off", 0, 64),
    ]


@pytest.mark.parametrize("program", [-1, 128, True])
def test_preview_rejects_invalid_general_midi_program(tmp_path: Path, program):
    source_path = tmp_path / "source.mid"
    source = mido.MidiFile()
    source.tracks.append(mido.MidiTrack([mido.MetaMessage("end_of_track", time=0)]))
    source.save(source_path)

    with pytest.raises(MidiPreviewError, match="0 through 127"):
        write_single_track_preview(source_path, 0, tmp_path / "preview.mid", program=program)
