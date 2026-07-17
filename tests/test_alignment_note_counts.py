from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ALIGNMENT_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "lyric_sync_assistant"
sys.path.insert(0, str(ALIGNMENT_TOOLS))

from alignment import add_virtual_note_split, adjust_alignment_token_note_count, delete_alignment_token
from pyFuncs.PhonemeProcessing import lyricsToPhonemes


def _note(index: int) -> dict[str, int]:
    return {
        "midi_pitch": 60,
        "velocity": 90,
        "start_ms": index * 500,
        "end_ms": (index + 1) * 500,
    }


def _report(counts: list[list[int]], words: list[list[str]] | None = None) -> dict:
    words = words or [["one", "two", "three"], ["four", "five"]]
    notes = []
    note_index = 0
    for line_index, line_counts in enumerate(counts, start=1):
        for word_index, note_count in enumerate(line_counts, start=1):
            for _ in range(note_count):
                note = _note(note_index)
                note.update({"line": line_index, "word_index": word_index, "lyric": words[line_index - 1][word_index - 1]})
                notes.append(note)
                note_index += 1
    return {
        "notes": notes,
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

    def test_deleting_a_phrase_last_word_removes_the_phrase(self) -> None:
        report = _report([[2], [2, 2]], [["intro"], ["four", "five"]])
        report["line_timings"] = [(0, 1000), (1000, 2000)]

        updated, text, selected = delete_alignment_token(report, "2*intro\n2*four 2*five\n", 1, 1)

        self.assertEqual(text, "2*four 4*five\n")
        self.assertEqual(selected, (1, 1))
        self.assertEqual(_counts(updated, 1), [2, 4])
        self.assertFalse(any(item["line"] == 2 for item in updated["token_counts"]))
        self.assertEqual(updated["line_timings"], [(1000, 2000)])

    def test_final_phrase_word_cannot_be_deleted(self) -> None:
        report = _report([[2]], [["only"]])

        with self.assertRaisesRegex(ValueError, "final lyric unit"):
            delete_alignment_token(report, "2*only\n", 1, 1)

    def test_virtual_splits_preserve_count_based_lyric_syntax(self) -> None:
        report = _report([[2, 1, 1], [2, 2]])

        first, first_text = add_virtual_note_split(report, self.text, 2, 0.5)
        second, second_text = add_virtual_note_split(first, first_text, 2, 0.75)

        self.assertEqual(first_text, "3*one two three\n2*four 2*five\n")
        self.assertEqual(second_text, "4*one two three\n2*four 2*five\n")
        self.assertEqual(len(first["notes"]), len(report["notes"]) + 1)
        self.assertEqual(len(second["notes"]), len(report["notes"]) + 2)
        self.assertEqual(second["virtual_splits"], [
            {"note_index": 2, "fraction": 0.5},
            {"note_index": 2, "fraction": 0.75},
        ])

    def test_virtual_split_preserves_direct_phoneme_token_syntax(self) -> None:
        report = _report([[1, 1]], [["`duw", "`duw"]])

        updated, text = add_virtual_note_split(report, "`duw `duw\n", 1, 0.5)

        self.assertEqual(text, "2*`duw `duw\n")
        self.assertEqual(_counts(updated, 1), [2, 1])
        with tempfile.TemporaryDirectory() as directory:
            lyric_path = Path(directory) / "virtual-split.txt"
            lyric_path.write_text(text, encoding="utf-8")
            phonemes = lyricsToPhonemes(str(lyric_path), printInfo=False)
        self.assertEqual(phonemes[0][0], 2)
        self.assertEqual(phonemes[0][1:], ["d", "uw1"])

    def test_virtual_split_can_fill_selected_zero_note_word(self) -> None:
        report = _report([[2, 0, 1], [2, 2]])

        updated, text = add_virtual_note_split(
            report,
            self.text,
            1,
            0.5,
            target_line=1,
            target_word_index=2,
        )

        self.assertEqual(_counts(updated, 1), [2, 1, 1])
        self.assertEqual(text, "2*one two three\n2*four 2*five\n")

    def test_virtual_split_does_not_cross_phrase_for_selected_target(self) -> None:
        report = _report([[2, 0, 1], [0, 2]])

        updated, _ = add_virtual_note_split(
            report,
            self.text,
            1,
            0.5,
            target_line=2,
            target_word_index=1,
        )

        self.assertEqual(_counts(updated, 1), [3, 0, 1])
        self.assertEqual(_counts(updated, 2), [0, 2])


if __name__ == "__main__":
    unittest.main()
