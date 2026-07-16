from __future__ import annotations

from pathlib import Path
import sys


ASSISTANT_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "lyric_sync_assistant"
sys.path.insert(0, str(ASSISTANT_TOOLS))

from assistant import NoteEvent, render_placeholder_draft
from pyFuncs.PhonemeProcessing import unsupportedDectalkPhonemes


def _notes(count: int) -> list[NoteEvent]:
    return [
        NoteEvent(pitch=60, velocity=90, start_ms=index * 250, end_ms=(index + 1) * 250)
        for index in range(count)
    ]


def test_note_skeleton_caps_uninterrupted_phrases_at_eight_notes() -> None:
    notes = _notes(19)

    lines = render_placeholder_draft(notes, [notes], "duw", include_comments=False)

    assert [len(line.split()) for line in lines] == [8, 8, 3]
    assert all(token == "`duw" for line in lines for token in line.split())


def test_note_skeleton_preserves_short_rest_derived_phrases() -> None:
    notes = _notes(11)

    lines = render_placeholder_draft(notes, [notes[:5], notes[5:]], "duw", include_comments=False)

    assert [len(line.split()) for line in lines] == [5, 6]


def test_final_dectalk_command_rejects_unknown_phoneme_symbols() -> None:
    assert unsupportedDectalkPhonemes(["d", "uw", "_"]) == []
    assert unsupportedDectalkPhonemes(["d", "oo", "uw"]) == ["oo"]
