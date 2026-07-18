from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pyFuncs.SongPaths import render_lyrics_path
from tools import choir_studio_bridge as bridge
from tools.lyric_sync_assistant.alignment import replace_alignment_words
from tools.migrate_lyric_transcripts import migrate_song


def _alignment_report() -> dict:
    notes = []
    assignments = [
        (1, 1, "old", 1, 2),
        (1, 1, "old", 2, 2),
        (1, 2, "first", 1, 1),
        (2, 1, "last", 1, 1),
    ]
    for index, (line, word_index, lyric, note_in_word, word_note_count) in enumerate(assignments, start=1):
        start = (index - 1) * 200
        notes.append({
            "note_index": index,
            "start_ms": start,
            "end_ms": start + 200,
            "duration_ms": 200,
            "gap_before_ms": 0,
            "midi_pitch": 60,
            "midi_name": "C4",
            "velocity": 90,
            "lyric": lyric,
            "line": line,
            "word_index": word_index,
            "note_in_word": note_in_word,
            "word_note_count": word_note_count,
            "status": "Assigned",
            "confidence": "High",
        })
    return {
        "version": 4,
        "notes": notes,
        "source_notes": [{"midi_pitch": 60, "velocity": 90, "start_ms": 0, "end_ms": 800}],
        "virtual_splits": [{"note_index": 1, "fraction": 0.5}],
        "line_timings": [None, None],
        "summary": {"phrase_gap_ms": 400, "word_gap_ms": 100, "zero_note_tokens": 0},
        "token_counts": [
            {"line": 1, "word_index": 1, "word": "old", "note_count": 2, "mode": "sing"},
            {"line": 1, "word_index": 2, "word": "first", "note_count": 1, "mode": "sing"},
            {"line": 2, "word_index": 1, "word": "last", "note_count": 1, "mode": "sing"},
        ],
    }


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


def test_editor_prefers_working_candidate_over_published_source(tmp_path: Path) -> None:
    lyrics_dir = tmp_path / "inputs" / "lyrics"
    paths = (
        lyrics_dir / "Lead.txt",
        lyrics_dir / "Lead.transcript.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.json",
    )
    paths[0].parent.mkdir(parents=True)
    paths[2].parent.mkdir(parents=True)
    paths[0].write_text("published words\n", encoding="utf-8")
    paths[1].write_text("original words\n", encoding="utf-8")
    paths[2].write_text("working candidate\n", encoding="utf-8")

    with patch.object(bridge, "_role_paths", return_value=paths):
        result = bridge._read_source("Example", "Lead")

    assert result["kind"] == "candidate"
    assert result["text"] == "working candidate\n"


def test_deleting_transcript_resets_editor_to_source(tmp_path: Path) -> None:
    lyrics_dir = tmp_path / "inputs" / "lyrics"
    paths = (
        lyrics_dir / "Lead.txt",
        lyrics_dir / "Lead.transcript.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.json",
    )
    paths[0].parent.mkdir(parents=True)
    paths[2].parent.mkdir(parents=True)
    paths[0].write_text("restart from here\n", encoding="utf-8")
    paths[2].write_text("stale candidate\n", encoding="utf-8")

    with patch.object(bridge, "_role_paths", return_value=paths):
        result = bridge._read_source("Example", "Lead")

    assert result["kind"] == "alignment"
    assert result["text"] == "restart from here\n"
    assert result["transcript_exists"] is False


def test_candidate_rewording_preserves_phrase_and_note_ownership() -> None:
    report = _alignment_report()

    updated, text = replace_alignment_words(
        report,
        "2*old first\nlast\n",
        "new, middle ending\n",
    )

    assert text == "2*new middle\nending\n"
    assert [(item["line"], item["word_index"], item["note_count"]) for item in updated["token_counts"]] == [
        (1, 1, 2),
        (1, 2, 1),
        (2, 1, 1),
    ]
    assert [item["lyric"] for item in updated["notes"]] == ["new", "new", "middle", "ending"]
    assert updated["virtual_splits"] == report["virtual_splits"]
    assert updated["line_timings"] == report["line_timings"]


def test_candidate_word_count_mismatch_does_not_write_files(tmp_path: Path) -> None:
    paths = (
        tmp_path / "inputs" / "lyrics" / "Lead.txt",
        tmp_path / "inputs" / "lyrics" / "Lead.transcript.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.json",
    )
    paths[1].parent.mkdir(parents=True)
    paths[2].parent.mkdir(parents=True)
    paths[1].write_text("old first last\n", encoding="utf-8")
    paths[2].write_text("2*old first\nlast\n", encoding="utf-8")
    paths[3].write_text(__import__("json").dumps(_alignment_report(), indent=2) + "\n", encoding="utf-8")
    candidate_before = paths[2].read_bytes()
    report_before = paths[3].read_bytes()

    with patch.object(bridge, "_role_paths", return_value=paths):
        with pytest.raises(bridge.BridgeError, match="Nothing was saved"):
            bridge._update_candidate_text("Example", "Lead", "too few")

    assert paths[2].read_bytes() == candidate_before
    assert paths[3].read_bytes() == report_before
    assert paths[1].read_text(encoding="utf-8") == "old first last\n"


def test_existing_lifecycle_cannot_be_redrafted(tmp_path: Path) -> None:
    paths = (
        tmp_path / "inputs" / "lyrics" / "Lead.txt",
        tmp_path / "inputs" / "lyrics" / "Lead.transcript.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.txt",
        tmp_path / "outputs" / "lyrics_drafts" / "Lead.json",
    )
    paths[1].parent.mkdir(parents=True)
    paths[1].write_text("preserved words\n", encoding="utf-8")

    with patch.object(bridge, "_role_paths", return_value=paths):
        with pytest.raises(bridge.BridgeError, match="already has an alignment lifecycle"):
            bridge._draft("Example", "Lead", "replacement words", False)

    assert paths[1].read_text(encoding="utf-8") == "preserved words\n"
    assert not paths[2].exists()
    assert not paths[3].exists()


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
