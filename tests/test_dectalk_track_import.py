import mido
import pytest
import yaml

from pyFuncs.DectalkTrackImport import (
    DectalkTrackImportError,
    append_imported_track,
    parse_dectalk_track,
)
from pyFuncs.PhonemeProcessing import TONE_EVENT_MARKER, lyricsToPhonemes
from tools import choir_studio_bridge as bridge
import assistant


def test_parse_groups_same_pitch_phonemes_and_preserves_rest():
    imported = parse_dectalk_track(
        "[:phoneme arpabet speak on][:np][:dv hs 90][d<80,12>ao<500,12>ng<80,12>_<300,0>t<50,14>uw<450,14>]",
        note_offset=-48,
    )

    assert imported.setup == "[:np][:dv hs 90]"
    assert [(note.midi_pitch, note.start_ms, note.end_ms) for note in imported.notes] == [
        (60, 0, 660),
        (62, 960, 1460),
    ]
    assert imported.lyric_text == "`daong\n`tuw\n"


def test_parse_rejects_unknown_phonemes_before_writing():
    with pytest.raises(DectalkTrackImportError, match="Unsupported DECTalk phoneme"):
        parse_dectalk_track("[doo<500,12>]", note_offset=-48)


def test_parse_respects_bracket_note_boundaries_and_inherited_pitch():
    imported = parse_dectalk_track("[dah<300,30>][dah<60>][fray<400,25>dey<400,27>]", -48)

    assert [note.midi_pitch for note in imported.notes] == [78, 78, 73, 75]
    assert [note.lyric_token for note in imported.notes] == ["`dah", "`dah", "`fray", "`dey"]


def test_parse_maps_tone_frequency_and_duration_without_polluting_setup():
    imported = parse_dectalk_track("[:np][:tone 440, 200][:t 880,800]", -48)

    assert imported.setup == "[:np]"
    assert [(note.midi_pitch, note.start_ms, note.end_ms) for note in imported.notes] == [
        (69, 0, 200),
        (81, 200, 1000),
    ]
    assert imported.lyric_text == "@tone(440,200) @tone(880,800)\n"


def test_applied_tone_token_reaches_phoneme_compiler(tmp_path):
    source = tmp_path / "Tone.txt"
    source.write_text("@tone(9000,9999)\n", encoding="utf-8")

    assert lyricsToPhonemes(str(source), printInfo=False) == [
        [TONE_EVENT_MARKER, 9000.0, 9999],
        ["\n"],
    ]


def test_parse_rejects_dial_events():
    with pytest.raises(DectalkTrackImportError, match="cannot be mapped"):
        parse_dectalk_track("[:dial67589340]", -48)


def test_parse_rejects_midstream_setup_changes_instead_of_hoisting_them():
    with pytest.raises(DectalkTrackImportError, match="Midstream DECTalk command"):
        parse_dectalk_track("[:np][dah<300,12>][:nb][dah<300,14>]", -48)


def test_append_track_uses_source_tempo():
    midi = mido.MidiFile(ticks_per_beat=480)
    conductor = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ])
    midi.tracks.append(conductor)
    imported = parse_dectalk_track("[d<100,12>uw<400,12>_<500,0>d<100,14>iy<400,14>]", -48)

    append_imported_track(midi, "Imported", imported)

    messages = [message for message in midi.tracks[-1] if message.type in {"note_on", "note_off"}]
    assert [(message.type, message.note, message.time) for message in messages] == [
        ("note_on", 60, 0),
        ("note_off", 60, 480),
        ("note_on", 62, 480),
        ("note_off", 62, 480),
    ]


def test_bridge_import_publishes_source_alignment_and_midi(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    song_dir = root / "songs" / "ImportSong"
    inputs = song_dir / "inputs"
    inputs.mkdir(parents=True)
    midi_path = inputs / "ImportSong.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Timing", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.save(midi_path)
    (song_dir / "settings.yaml").write_text("noteOffset: -48\nTracks:\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "REPO_ROOT", root)
    monkeypatch.setattr(assistant, "REPO_ROOT", root)

    result = bridge._import_dectalk_track(
        "ImportSong",
        "Archive Voice",
        "[:phoneme arpabet speak on][:np][d<100,12>uw<400,12>_<300,0>m<100,14>ay<400,14>n<80,14>][:tone 440,250]",
    )

    settings = yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))
    assert settings["Tracks"]["Archive Voice"]["TRACK_FILENAME"] == "Archive Voice"
    assert [track.name for track in mido.MidiFile(midi_path).tracks] == ["Timing", "Archive Voice"]
    assert (inputs / "lyrics" / "Archive Voice.txt").read_text(encoding="utf-8") == "`duw\n`mayn @tone(440,250)\n"
    assert (inputs / "lyrics" / "Archive Voice.transcript.txt").read_text(encoding="utf-8").startswith("[:phoneme")
    assert (inputs / "lyrics" / ".alignment" / "Archive Voice.json").is_file()
    assert result["note_count"] == 3


def test_bridge_import_rolls_back_midi_settings_and_lyrics_on_alignment_failure(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    song_dir = root / "songs" / "ImportSong"
    inputs = song_dir / "inputs"
    inputs.mkdir(parents=True)
    midi_path = inputs / "ImportSong.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Timing", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.save(midi_path)
    settings_path = song_dir / "settings.yaml"
    settings_path.write_text("noteOffset: -48\nTracks:\n", encoding="utf-8")
    original_midi = midi_path.read_bytes()
    original_settings = settings_path.read_bytes()
    monkeypatch.setattr(bridge, "REPO_ROOT", root)
    monkeypatch.setattr(assistant, "REPO_ROOT", root)
    def fail_alignment(*_args):
        raise RuntimeError("alignment failed")
    monkeypatch.setattr(bridge, "build_alignment", fail_alignment)

    with pytest.raises(bridge.BridgeError, match="Could not import DECTalk track"):
        bridge._import_dectalk_track("ImportSong", "Failed Voice", "[dah<300,12>]")

    assert midi_path.read_bytes() == original_midi
    assert settings_path.read_bytes() == original_settings
    assert not (inputs / "lyrics" / "Failed Voice.txt").exists()
    assert not (inputs / "lyrics" / "Failed Voice.transcript.txt").exists()
