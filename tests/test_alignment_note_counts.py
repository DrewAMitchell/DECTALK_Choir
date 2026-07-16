from __future__ import annotations

from pathlib import Path
import sys
import unittest


ALIGNMENT_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "lyric_sync_assistant"
sys.path.insert(0, str(ALIGNMENT_TOOLS))

from alignment import adjust_alignment_token_note_count


def _note(index: int) -> dict[str, int]:
    return {
        "midi_pitch": 60,
        "velocity": 90,
        "start_ms": index * 500,
        "end_ms": (index + 1) * 500,
    }


def _report(counts: list[list[int]]) -> dict:
    words = [["one", "two", "three"], ["four", "five"]]
    total_notes = sum(sum(line) for line in counts)
    return {
        "notes": [_note(index) for index in range(total_notes)],
        "summary": {"phrase_gap_ms": 600, "word_gap_ms": 50},
        "token_counts": [
            {
                "line": line_index,
                "word_index": word_index,
                "word": word,
                "note_count": counts[line_index - 1][word_index - 1],
            }
            for line_index, line_words in enumerate(words, start=1)
            for word_index, word in enumerate(line_words, start=1)
        ],
    }


def _counts(report: dict, line: int) -> list[int]:
    return [
        item["note_count"]
        for item in report["token_counts"]
        if item["line"] == line
    ]


class AlignmentNoteCountTests(unittest.TestCase):
    text = "one two three\nfour five\n"

    def test_minus_returns_note_to_zero_note_word_in_same_phrase(self) -> None:
        report = _report([[2, 0, 2], [2, 2]])

        updated, _ = adjust_alignment_token_note_count(report, self.text, 1, 1, -1)

        self.assertEqual(_counts(updated, 1), [1, 1, 2])
        self.assertEqual(_counts(updated, 2), [2, 2])

    def test_plus_takes_nearest_same_phrase_surplus(self) -> None:
        report = _report([[2, 1, 3], [2, 2]])

        updated, _ = adjust_alignment_token_note_count(report, self.text, 1, 2, 1)

        self.assertEqual(_counts(updated, 1), [2, 2, 2])
        self.assertEqual(_counts(updated, 2), [2, 2])

    def test_adjustment_preserves_phrase_note_total(self) -> None:
        report = _report([[3, 1, 2], [2, 2]])

        reduced, _ = adjust_alignment_token_note_count(report, self.text, 1, 1, -1)
        increased, _ = adjust_alignment_token_note_count(reduced, self.text, 1, 1, 1)

        self.assertEqual(sum(_counts(reduced, 1)), 6)
        self.assertEqual(sum(_counts(increased, 1)), 6)
        self.assertEqual(_counts(increased, 2), [2, 2])

    def test_minus_requires_selected_word_to_keep_one_note(self) -> None:
        report = _report([[1, 2, 1], [2, 2]])

        with self.assertRaisesRegex(ValueError, "retain at least one note"):
            adjust_alignment_token_note_count(report, self.text, 1, 1, -1)

    def test_plus_requires_same_phrase_surplus(self) -> None:
        report = _report([[1, 1, 1], [4, 1]])

        with self.assertRaisesRegex(ValueError, "this phrase has a spare note"):
            adjust_alignment_token_note_count(report, self.text, 1, 2, 1)


if __name__ == "__main__":
    unittest.main()
