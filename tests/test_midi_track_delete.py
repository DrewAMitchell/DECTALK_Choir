from pathlib import Path

import mido
import pytest
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
    conductor = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Conductor", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
    ])
    melody = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Melody", time=0),
        mido.Message("note_on", note=60, velocity=90, time=0),
        mido.Message("note_off", note=60, velocity=0, time=480),
    ])
    harmony = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Harmony", time=0),
        mido.Message("note_on", note=55, velocity=90, time=0),
        mido.Message("note_off", note=55, velocity=0, time=480),
    ])
    midi.tracks.extend([conductor, melody, harmony])
    midi.save(path)


def test_inline_track_delete_preserves_role_without_retaining_backup(tmp_path, monkeypatch):
    source = tmp_path / "source.mid"
    _source_midi(source)
    _patch_repo(monkeypatch, tmp_path)
    imported = bridge._scaffold_midi_song(tmp_path, source, "DeleteTrackTest")

    result = bridge.handle({
        "command": "delete_midi_track",
        "song": "DeleteTrackTest",
        "role": "Melody",
        "confirm_delete": True,
    })

    working = Path(imported["midi_path"])
    assert len(mido.MidiFile(working).tracks) == 2
    assert "backup_path" not in result
    assert not list(working.parent.glob("*.before-track-delete-*.bak"))
    inspection = bridge.inspect_song(tmp_path, "DeleteTrackTest")
    roles = {item.role: item for item in inspection.roles}
    assert roles["Melody"].midi_track is None
    assert roles["Harmony"].midi_track is not None


def test_inline_track_delete_requires_ctrl_confirmation(tmp_path, monkeypatch):
    source = tmp_path / "source.mid"
    _source_midi(source)
    _patch_repo(monkeypatch, tmp_path)
    bridge._scaffold_midi_song(tmp_path, source, "DeleteTrackTest")

    with pytest.raises(bridge.BridgeError, match="Hold Ctrl"):
        bridge.handle({
            "command": "delete_midi_track",
            "song": "DeleteTrackTest",
            "role": "Melody",
            "confirm_delete": False,
        })
