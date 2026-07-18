from pathlib import Path

import mido
import pytest
import yaml

from tools.choir_studio_bridge import BridgeError, _scaffold_midi_song


def _write_source(path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    tempo = mido.MidiTrack()
    tempo.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    midi.tracks.append(tempo)
    for name, pitch in (("Lead/Vocal", 60), ("Lead/Vocal", 48)):
        track = mido.MidiTrack()
        track.name = name
        track.append(mido.Message("note_on", note=pitch, velocity=90, time=0))
        track.append(mido.Message("note_off", note=pitch, velocity=0, time=480))
        midi.tracks.append(track)
    midi.save(path)


def test_scaffold_midi_song_creates_deterministic_roles(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    repo = tmp_path / "repo"
    (repo / "songs").mkdir(parents=True)
    _write_source(source)

    result = _scaffold_midi_song(repo, source, "NewSong")

    assert result["roles"] == ["Lead_Vocal", "Lead_Vocal_2"]
    song = repo / "songs" / "NewSong"
    settings = yaml.safe_load((song / "settings.yaml").read_text(encoding="utf-8"))
    assert list(settings["Tracks"]) == result["roles"]
    assert all(not profile["RENDER_ENABLED"] for profile in settings["Tracks"].values())
    assert all((song / "inputs" / "lyrics" / f"{role}.txt").is_file() for role in result["roles"])
    copied = mido.MidiFile(result["midi_path"])
    assert [copied.tracks[index].name for index in (1, 2)] == result["roles"]


def test_scaffold_midi_song_refuses_existing_destination_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source.mid"
    repo = tmp_path / "repo"
    destination = repo / "songs" / "Existing"
    destination.mkdir(parents=True)
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    _write_source(source)

    with pytest.raises(BridgeError, match="already exists"):
        _scaffold_midi_song(repo, source, "Existing")

    assert marker.read_text(encoding="utf-8") == "keep"
