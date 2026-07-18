from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pyFuncs.SongPaths import render_lyrics_path
from tools import choir_studio_bridge as bridge
from tools.migrate_lyric_transcripts import migrate_song


def test_render_source_never_falls_back_to_working_candidate(tmp_path: Path) -> None:
    song_dir = tmp_path / "songs" / "Example"
    candidate = song_dir / "outputs" / "lyrics_drafts" / "Lead.txt"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("`duw\n", encoding="utf-8")

    assert render_lyrics_path(song_dir, "Lead") == song_dir / "inputs" / "lyrics" / "Lead.txt"


def test_transcript_can_be_created_once_but_not_replaced(tmp_path: Path) -> None:
    lyrics_dir = tmp_path / "inputs" / "lyrics"
    paths = (
        lyrics_dir / "Lead.txt",
        lyrics_dir / "Lead.transcript.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.json",
    )
    with patch.object(bridge, "_role_paths", return_value=paths):
        created = bridge._write_transcript("Example", "Lead", "first words")
        unchanged = bridge._write_transcript("Example", "Lead", "first words")
        with pytest.raises(bridge.BridgeError, match="cannot be replaced"):
            bridge._write_transcript("Example", "Lead", "different words")

    assert created["created"] is True
    assert unchanged["created"] is False
    assert paths[1].read_text(encoding="utf-8") == "first words\n"


def test_source_sync_state_distinguishes_absent_pending_and_synced(tmp_path: Path) -> None:
    lyrics_dir = tmp_path / "inputs" / "lyrics"
    paths = (
        lyrics_dir / "Lead.txt",
        lyrics_dir / "Lead.transcript.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.json",
    )
    paths[1].parent.mkdir(parents=True)
    paths[2].parent.mkdir(parents=True)
    with patch.object(bridge, "_role_paths", return_value=paths):
        assert bridge._source_sync_state("Example", "Lead") == "absent"
        paths[1].write_text("original words\n", encoding="utf-8")
        assert bridge._source_sync_state("Example", "Lead") == "pending"
        paths[0].write_text("aligned words\n", encoding="utf-8")
        assert bridge._source_sync_state("Example", "Lead") == "synced"
        paths[2].write_text("changed alignment\n", encoding="utf-8")
        assert bridge._source_sync_state("Example", "Lead") == "pending"
        paths[0].write_text("changed alignment\n", encoding="utf-8")
        assert bridge._source_sync_state("Example", "Lead") == "synced"


def test_migration_verifies_transcript_before_removing_raw(tmp_path: Path) -> None:
    song_dir = tmp_path / "Example"
    lyrics_dir = song_dir / "inputs" / "lyrics"
    lyrics_dir.mkdir(parents=True)
    (song_dir / "settings.yaml").write_text(
        "Tracks:\n  Lead:\n    LYRICS_FILENAME: Lead\n",
        encoding="utf-8",
    )
    raw = lyrics_dir / "Lead.raw.txt"
    raw.write_text("original words\n", encoding="utf-8")

    migrate_song(song_dir, apply=True, remove_raw=True)

    assert (lyrics_dir / "Lead.transcript.txt").read_text(encoding="utf-8") == "original words\n"
    assert not raw.exists()
