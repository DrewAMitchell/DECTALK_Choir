from pathlib import Path

import mido
import yaml

from tools import choir_studio_bridge as bridge


def _patch_repo(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(bridge, "REPO_ROOT", repo)

    def load_local_settings(song: str):
        song_dir = repo / "songs" / song
        return song_dir, yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))

    monkeypatch.setattr(bridge, "load_settings", load_local_settings)


def _source_midi(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Conductor", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
    ]))
    for name, pitch in (("Melody", 60), ("Harmony", 55)):
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage("track_name", name=name, time=0),
            mido.Message("note_on", note=pitch, velocity=90, time=0),
            mido.Message("note_off", note=pitch, velocity=0, time=480),
        ]))
    midi.save(path)


def test_remove_role_preserves_midi_and_authored_artifacts_then_allows_reimport(tmp_path, monkeypatch):
    source = tmp_path / "source.mid"
    _source_midi(source)
    _patch_repo(monkeypatch, tmp_path)
    imported = bridge._scaffold_midi_song(tmp_path, source, "RoleManagement")
    song_dir = tmp_path / "songs" / "RoleManagement"
    lyric_dir = song_dir / "inputs" / "lyrics"
    lyric = lyric_dir / "Harmony.txt"
    transcript = lyric_dir / "Harmony.transcript.txt"
    alignment = lyric_dir / ".alignment" / "Harmony.json"
    candidate = song_dir / "outputs" / "lyrics_drafts" / "Harmony.txt"
    lyric.write_text("aligned words\n", encoding="utf-8")
    transcript.write_text("original words\n", encoding="utf-8")
    alignment.parent.mkdir(parents=True)
    alignment.write_text('{"phrases": []}', encoding="utf-8")
    candidate.parent.mkdir(parents=True)
    candidate.write_text("candidate words\n", encoding="utf-8")
    working_midi = Path(imported["midi_path"])
    original_midi = working_midi.read_bytes()

    removed = bridge.handle({
        "command": "remove_midi_track_role",
        "song": "RoleManagement",
        "role": "Harmony",
    })

    settings = yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))
    assert "Harmony" not in settings["Tracks"]
    assert removed["midi_preserved"] is True
    assert working_midi.read_bytes() == original_midi
    assert lyric.read_text(encoding="utf-8") == "aligned words\n"
    assert transcript.exists()
    assert alignment.exists()
    assert candidate.exists()

    added = bridge.handle({
        "command": "add_midi_track_role",
        "song": "RoleManagement",
        "track_index": 2,
        "role": "Harmony",
    })

    settings = yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))
    assert added["track_name"] == "Harmony"
    assert settings["Tracks"]["Harmony"]["TRACK_FILENAME"] == "Harmony"
    assert settings["Tracks"]["Harmony"]["RENDER_ENABLED"] is False
    assert lyric.read_text(encoding="utf-8") == "aligned words\n"
    assert working_midi.read_bytes() == original_midi


def test_last_role_can_be_removed_and_added_back(tmp_path, monkeypatch):
    source = tmp_path / "source.mid"
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Solo", time=0),
        mido.Message("note_on", note=60, velocity=90, time=0),
        mido.Message("note_off", note=60, velocity=0, time=480),
    ]))
    midi.save(source)
    _patch_repo(monkeypatch, tmp_path)
    bridge._scaffold_midi_song(tmp_path, source, "SoloSong")

    bridge.handle({"command": "remove_midi_track_role", "song": "SoloSong", "role": "Solo"})
    song_dir = tmp_path / "songs" / "SoloSong"
    assert yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))["Tracks"] == {}

    bridge.handle({
        "command": "add_midi_track_role",
        "song": "SoloSong",
        "track_index": 0,
        "role": "Solo",
    })
    assert "Solo" in yaml.safe_load((song_dir / "settings.yaml").read_text(encoding="utf-8"))["Tracks"]
