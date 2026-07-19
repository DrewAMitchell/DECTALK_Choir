import mido
import pytest
import yaml

from pyFuncs.DectalkTrackImport import (
    DectalkTrackImportError,
    append_imported_track,
    parse_dectalk_track,
)
from pyFuncs.ChoirInspection import _lyric_conversion_issue
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


def test_parse_attaches_natural_duration_trailing_codas_to_timed_note():
    imported = parse_dectalk_track("[ssteh<300,28>pp][ngaw<300,33>nn]", -48)

    assert [note.midi_pitch for note in imported.notes] == [76, 81]
    assert [note.lyric_token for note in imported.notes] == ["`sstehpp", "`ngawnn"]
    assert imported.duration_ms == 600


def test_parse_assigns_untimed_codas_and_onsets_between_timed_notes():
    imported = parse_dectalk_track(
        "[kae<400,15>n yu<400,18>w][keh<400,28>ts r eh<400,30>d][spae<400,20>ngel<400,22>d]",
        -48,
    )

    assert [note.lyric_token for note in imported.notes] == [
        "`kaen", "`yuw", "`kehts", "`rehd", "`spae", "`ngeld",
    ]


def test_alignment_validation_accepts_explicit_consonant_only_note(tmp_path):
    source = tmp_path / "Consonants.txt"
    source.write_text("`rrll\n", encoding="utf-8")

    assert _lyric_conversion_issue(source) is None


def test_imported_visual_phrases_cap_at_eight_notes_without_adding_rest():
    source = "[" + "".join(f"dah<100,{12 + index % 2}>" for index in range(9)) + "]"
    imported = parse_dectalk_track(source, -48)

    assert [len(line.split()) for line in imported.lyric_text.rstrip().splitlines()] == [8, 1]
    assert imported.notes[7].end_ms == imported.notes[8].start_ms == 800


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


def test_bridge_persists_phoneme_export_and_removes_disabled_stale_file(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    song_dir = root / "songs" / "ExportSong"
    (song_dir / "outputs" / "_phonemes").mkdir(parents=True)
    settings_path = song_dir / "settings.yaml"
    settings_path.write_text("Tracks:\n  Lead:\n    TRACK_FILENAME: Lead\n", encoding="utf-8")
    stale = song_dir / "outputs" / "_phonemes" / "Lead.txt"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(bridge, "REPO_ROOT", root)
    monkeypatch.setattr(assistant, "REPO_ROOT", root)

    enabled = bridge._update_phoneme_string_export("ExportSong", "Lead", True)
    assert enabled["enabled"] is True
    assert yaml.safe_load(settings_path.read_text(encoding="utf-8"))["Tracks"]["Lead"]["EXPORT_PHONEME_STRING"] is True
    assert stale.is_file()

    disabled = bridge._update_phoneme_string_export("ExportSong", "Lead", False)
    assert disabled["enabled"] is False
    assert yaml.safe_load(settings_path.read_text(encoding="utf-8"))["Tracks"]["Lead"]["EXPORT_PHONEME_STRING"] is False
    assert not stale.exists()


def test_bridge_creates_one_track_song_from_dectalk_string(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    monkeypatch.setattr(bridge, "REPO_ROOT", root)
    monkeypatch.setattr(assistant, "REPO_ROOT", root)

    result = bridge._create_dectalk_song(
        "SoloSong",
        "Solo Voice",
        "[:np][:dv hs 100][d<100,12>uw<400,12>_<250,0>m<100,14>ay<400,14>n<80,14>]",
    )

    song_dir = root / "songs" / "SoloSong"
    settings = yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))
    midi = mido.MidiFile(song_dir / "inputs" / "SoloSong.mid")
    assert list(settings["Tracks"]) == ["Solo Voice"]
    assert [track.name for track in midi.tracks] == ["Timing", "Solo Voice"]
    assert (song_dir / "inputs" / "lyrics" / "Solo Voice.txt").read_text(encoding="utf-8") == "`duw\n`mayn\n"
    assert (song_dir / "inputs" / "lyrics" / ".alignment" / "Solo Voice.json").is_file()
    assert result["song"] == "SoloSong"
    assert result["role"] == "Solo Voice"


def test_new_dectalk_song_is_removed_when_alignment_fails(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    monkeypatch.setattr(bridge, "REPO_ROOT", root)
    monkeypatch.setattr(assistant, "REPO_ROOT", root)

    def fail_alignment(*_args):
        raise RuntimeError("alignment failed")

    monkeypatch.setattr(bridge, "build_alignment", fail_alignment)

    with pytest.raises(bridge.BridgeError, match="Could not create DECTalk song"):
        bridge._create_dectalk_song("FailedSong", "Solo Voice", "[dah<300,12>]")

    assert not (root / "songs" / "FailedSong").exists()
    assert not list((root / "songs").glob(".FailedSong.*.tmp"))
