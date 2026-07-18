#!/usr/bin/env python3
"""Map a drafted lyric file onto the note events that will consume it.

The renderer still receives its existing count-based lyric syntax. This tool
adds a review artifact beside that file so users can inspect the exact note
assignment without putting one timing marker on every lyric token.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ASSISTANT_DIR = Path(__file__).resolve().parent
if str(ASSISTANT_DIR) not in sys.path:
    sys.path.insert(0, str(ASSISTANT_DIR))

from pyFuncs.PitchMapping import midi_pitch_name
from pyFuncs.SongPaths import lyrics_directory, outputs_directory
from assistant import (
    NoteEvent,
    format_synced_word,
    format_timestamp_ms,
    load_settings,
    load_track_notes,
    parse_lyric_token,
    read_lyric_token_lines,
    resolve_thresholds,
    resolve_track,
    sanitize_transcript_line,
)


@dataclass(frozen=True)
class AlignmentEntry:
    """One MIDI note and the lyric unit assigned to it."""

    note_index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    gap_before_ms: int
    midi_pitch: int
    midi_name: str
    velocity: int
    lyric: str | None
    line: int | None
    word_index: int | None
    note_in_word: int | None
    word_note_count: int | None
    status: str
    confidence: str


@dataclass(frozen=True)
class AlignmentToken:
    """A parsed lyric token with its source line and word position."""

    word: str
    note_count: int
    line: int
    word_index: int
    mode: str = "sing"


def _tokens_from_lines(token_lines) -> list[AlignmentToken]:
    tokens: list[AlignmentToken] = []
    for line_index, line in enumerate(token_lines, start=1):
        for word_index, token in enumerate(line, start=1):
            tokens.append(
                AlignmentToken(
                    word=token.word,
                    note_count=max(0, int(token.note_count)),
                    line=line_index,
                    word_index=word_index,
                    mode=getattr(token, "mode", "sing"),
                )
            )
    return tokens


def _note_context(notes: list[NoteEvent], note_index: int) -> tuple[int, int]:
    note = notes[note_index]
    if note_index == 0:
        return 0, 0
    previous = notes[note_index - 1]
    return round(max(0.0, note.start_ms - previous.end_ms)), round(note.start_ms)


def _placeholder_from_draft(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*#\s*draft-mode:\s*placeholder=(\S+)", line, re.IGNORECASE)
            if match:
                return match.group(1)
    except OSError:
        return None
    return None


def align_tokens(
    tokens: list[AlignmentToken],
    notes: list[NoteEvent],
    phrase_gap_ms: float,
    word_gap_ms: float,
    placeholder_word: str | None = None,
) -> tuple[list[AlignmentEntry], dict[str, int | float | str]]:
    """Assign drafted tokens sequentially to notes and classify boundaries."""

    entries: list[AlignmentEntry] = []
    note_index = 0
    overflow_tokens = 0
    zero_note_tokens = sum(1 for token in tokens if token.note_count == 0)
    phrase_boundaries = 0
    word_boundaries = 0

    for token in tokens:
        for note_in_word in range(1, token.note_count + 1):
            if note_index >= len(notes):
                overflow_tokens += 1
                break

            note = notes[note_index]
            gap_before_ms, _ = _note_context(notes, note_index)
            if gap_before_ms >= phrase_gap_ms:
                phrase_boundaries += 1
                status = "Phrase boundary"
            elif gap_before_ms >= word_gap_ms:
                if note_in_word == 1:
                    word_boundaries += 1
                    status = "Word boundary"
                else:
                    status = "Boundary inside word"
            elif gap_before_ms > 0:
                status = "Tight transition"
            else:
                status = "Aligned"
            if placeholder_word:
                confidence = "Review"
            elif status == "Aligned":
                confidence = "Confident"
            else:
                confidence = "Review"

            entries.append(
                AlignmentEntry(
                    note_index=note_index + 1,
                    start_ms=round(note.start_ms),
                    end_ms=round(note.end_ms),
                    duration_ms=round(note.duration_ms),
                    gap_before_ms=gap_before_ms,
                    midi_pitch=note.pitch,
                    midi_name=midi_pitch_name(note.pitch),
                    velocity=note.velocity,
                    lyric=token.word,
                    line=token.line,
                    word_index=token.word_index,
                    note_in_word=note_in_word,
                    word_note_count=token.note_count,
                    status=status,
                    confidence=confidence,
                )
            )
            note_index += 1

    for remaining_index in range(note_index, len(notes)):
        note = notes[remaining_index]
        gap_before_ms, _ = _note_context(notes, remaining_index)
        entries.append(
            AlignmentEntry(
                note_index=remaining_index + 1,
                start_ms=round(note.start_ms),
                end_ms=round(note.end_ms),
                duration_ms=round(note.duration_ms),
                gap_before_ms=gap_before_ms,
                midi_pitch=note.pitch,
                midi_name=midi_pitch_name(note.pitch),
                velocity=note.velocity,
                lyric=None,
                line=None,
                word_index=None,
                note_in_word=None,
                word_note_count=None,
                status="Unassigned note",
                confidence="Error",
            )
        )

    assigned_notes = min(note_index, len(notes))
    summary: dict[str, int | float | str] = {
        "status": (
            "Aligned"
            if assigned_notes == len(notes) and overflow_tokens == 0 and zero_note_tokens == 0
            else "Needs review"
        ),
        "midi_notes": len(notes),
        "assigned_notes": assigned_notes,
        "unassigned_notes": max(0, len(notes) - assigned_notes),
        "draft_tokens": len(tokens),
        "overflow_tokens": overflow_tokens,
        "zero_note_tokens": zero_note_tokens,
        "phrase_boundaries": phrase_boundaries,
        "word_boundaries": word_boundaries,
        "phrase_gap_ms": round(phrase_gap_ms, 1),
        "word_gap_ms": round(word_gap_ms, 1),
        "placeholder_word": placeholder_word or "",
    }
    return entries, summary


def _read_line_timing_tokens(path: Path) -> list[tuple[int | None, int | None] | None]:
    """Read line timing metadata without changing the token parser contract."""
    timings = []
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if parts and parts[0].startswith("[") and parts[0].endswith("]"):
                import pyFuncs.PhonemeProcessing as phoneme_processing

                parsed = phoneme_processing.parseLineTimingToken(parts[0])
                timings.append((parsed[1], parsed[2]) if parsed else None)
            else:
                timings.append(None)
    except (OSError, ValueError):
        return []
    return timings


def render_aligned_lyrics(
    token_lines,
    entries: list[AlignmentEntry],
    line_timings: list[tuple[int | None, int | None] | None] | None = None,
    include_comments: bool = False,
    include_timing: bool = False,
) -> list[str]:
    """Render renderer-ready lines; timing overrides are opt-in."""

    output: list[str] = []
    entry_index = 0
    for line_index, line in enumerate(token_lines, start=1):
        line_entries = [entry for entry in entries if entry.line == line_index]
        if line_entries and include_comments:
            output.append(
                f"# alignment line {line_index}: "
                f"{line_entries[0].start_ms}ms-{line_entries[-1].end_ms}ms, "
                f"{len(line_entries)} notes"
            )
        elif not line_entries and include_comments:
            output.append(f"# alignment line {line_index}: no MIDI notes assigned")

        rendered_tokens: list[str] = []
        for token in line:
            rendered_tokens.append(format_synced_word(token.word, token.note_count, getattr(token, "mode", "sing")))
            entry_index += token.note_count
        if not rendered_tokens:
            continue
        if line_entries and include_timing:
            prefix = format_timestamp_ms(line_entries[0].start_ms)
            if line_timings and line_index <= len(line_timings):
                duration_ms = line_timings[line_index - 1]
                if duration_ms and duration_ms[1] is not None:
                    prefix = f"[{prefix[1:-1]}|{duration_ms[1]}]"
        else:
            prefix = ""
        output.append((prefix + " " if prefix else "") + " ".join(rendered_tokens))

    if entry_index < len(entries) and include_comments:
        output.append(
            f"# alignment warning: {len(entries) - entry_index} MIDI note(s) have no lyric token"
        )
    return output


def _token_lines_from_text(text: str):
    token_lines = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if parts and parts[0].startswith("[") and parts[0].endswith("]"):
            parts = parts[1:]
        tokens = []
        for raw_token in parts:
            token = parse_lyric_token(raw_token)
            if token is not None:
                tokens.append(token)
        if tokens:
            token_lines.append(tokens)
    return token_lines


def _token_lines_from_report(report: dict, text: str):
    """Restore temporary zero-note allocations from the candidate sidecar."""

    token_lines = _token_lines_from_text(text)
    stored_counts = report.get("token_counts")
    if not isinstance(stored_counts, list):
        return token_lines
    count_map = {
        (item.get("line"), item.get("word_index")): item.get("note_count")
        for item in stored_counts
        if isinstance(item, dict)
    }
    mode_map = {
        (item.get("line"), item.get("word_index")): item.get("mode")
        for item in stored_counts
        if isinstance(item, dict)
    }
    for line_index, tokens in enumerate(token_lines, start=1):
        for word_index, token in enumerate(tokens, start=1):
            count = count_map.get((line_index, word_index))
            if isinstance(count, int) and count >= 0:
                token.note_count = count
            mode = mode_map.get((line_index, word_index))
            if mode in {"sing", "speak"}:
                token.mode = mode
    return token_lines


def _token_counts(
    token_lines,
    entries: list[AlignmentEntry] | None = None,
) -> list[dict[str, int | str]]:
    actual_counts: dict[tuple[int, int], int] | None = None
    if entries is not None:
        actual_counts = {}
        for entry in entries:
            if entry.line is None or entry.word_index is None:
                continue
            key = (entry.line, entry.word_index)
            actual_counts[key] = actual_counts.get(key, 0) + 1
    return [
        {
            "line": line_index,
            "word_index": word_index,
            "word": token.word,
            "note_count": (
                actual_counts.get((line_index, word_index), 0)
                if actual_counts is not None
                else token.note_count
            ),
            "mode": getattr(token, "mode", "sing"),
        }
        for line_index, tokens in enumerate(token_lines, start=1)
        for word_index, token in enumerate(tokens, start=1)
    ]


def reconcile_report_token_counts(report: dict) -> tuple[dict, bool]:
    """Make persisted word ownership agree with the report's mapped notes."""

    stored_counts = report.get("token_counts")
    if not isinstance(stored_counts, list):
        return report, False
    actual_counts: dict[tuple[int, int], int] = {}
    for entry in report.get("notes", []):
        if not isinstance(entry, dict):
            continue
        line = entry.get("line")
        word_index = entry.get("word_index")
        if isinstance(line, int) and isinstance(word_index, int):
            key = (line, word_index)
            actual_counts[key] = actual_counts.get(key, 0) + 1

    changed = False
    reconciled_counts = []
    for item in stored_counts:
        if not isinstance(item, dict):
            reconciled_counts.append(item)
            continue
        reconciled = dict(item)
        line = reconciled.get("line")
        word_index = reconciled.get("word_index")
        actual = actual_counts.get((line, word_index), 0)
        if reconciled.get("note_count") != actual:
            reconciled["note_count"] = actual
            changed = True
        reconciled_counts.append(reconciled)

    zero_note_tokens = sum(
        1
        for item in reconciled_counts
        if isinstance(item, dict) and item.get("note_count") == 0
    )
    summary = dict(report.get("summary") or {})
    if summary.get("zero_note_tokens") != zero_note_tokens:
        summary["zero_note_tokens"] = zero_note_tokens
        changed = True
    if zero_note_tokens and summary.get("status") != "Needs review":
        summary["status"] = "Needs review"
        changed = True
    if not changed:
        return report, False
    reconciled_report = dict(report)
    reconciled_report["token_counts"] = reconciled_counts
    reconciled_report["summary"] = summary
    return reconciled_report, True


def replace_alignment_words(
    report: dict,
    aligned_text: str,
    edited_text: str,
) -> tuple[dict, str]:
    """Replace aligned words without changing their phrase or note ownership."""

    token_lines = _token_lines_from_report(report, aligned_text)
    normalized_edited_text = "\n".join(
        sanitize_transcript_line(line) for line in edited_text.splitlines()
    )
    edited_tokens = [
        token
        for line in _token_lines_from_text(normalized_edited_text)
        for token in line
    ]
    aligned_tokens = [token for line in token_lines for token in line]
    if len(edited_tokens) != len(aligned_tokens):
        raise ValueError(
            f"This candidate has {len(aligned_tokens)} aligned words, but the edited text has "
            f"{len(edited_tokens)}. Nothing was saved. Use the Align word controls to insert "
            "or remove words before bulk rewording."
        )

    for aligned, edited in zip(aligned_tokens, edited_tokens):
        aligned.word = edited.word
        aligned.mode = getattr(edited, "mode", "sing")

    word_map = {
        (line_index, word_index): token.word
        for line_index, line in enumerate(token_lines, start=1)
        for word_index, token in enumerate(line, start=1)
    }
    note_dicts = []
    entries = []
    for raw_entry in report.get("notes", []):
        entry_data = dict(raw_entry)
        key = (entry_data.get("line"), entry_data.get("word_index"))
        if key in word_map:
            entry_data["lyric"] = word_map[key]
        note_dicts.append(entry_data)
        entries.append(AlignmentEntry(**entry_data))

    updated_report = dict(report)
    updated_report["notes"] = note_dicts
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(4, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, updated_text


def _fill_phrase_note_gaps(tokens: list[AlignmentToken]) -> None:
    """Give every word one note when this phrase already has enough notes.

    Phrase-boundary edits deliberately permit temporary zero-note words.  Once
    a phrase grows enough to cover all of its words, retain the existing
    timing as much as possible by borrowing from the nearest word with a
    surplus instead of making the user propagate boundaries one word at a
    time.
    """

    if sum(token.note_count for token in tokens) < len(tokens):
        return

    for gap_index, token in enumerate(tokens):
        if token.note_count:
            continue
        donors = [
            (abs(index - gap_index), index)
            for index, candidate in enumerate(tokens)
            if candidate.note_count > 1
        ]
        if not donors:
            return
        _, donor_index = min(donors)
        tokens[donor_index].note_count -= 1
        token.note_count += 1


def _release_phrase_notes(tokens: list[AlignmentToken], amount: int, *, from_end: bool) -> None:
    """Release surplus notes from words nearest one edge of a phrase."""

    available = sum(max(0, token.note_count - 1) for token in tokens)
    if available < amount:
        raise ValueError("This phrase does not have that many extra notes to release.")
    remaining = amount
    donors = reversed(tokens) if from_end else iter(tokens)
    for token in donors:
        transferred = min(remaining, max(0, token.note_count - 1))
        token.note_count -= transferred
        remaining -= transferred
        if not remaining:
            return


def _split_note_events(notes: list[NoteEvent], virtual_splits: list[dict]) -> list[NoteEvent]:
    by_note: dict[int, list[float]] = {}
    for item in virtual_splits:
        if not isinstance(item, dict):
            continue
        try:
            note_index = int(item["note_index"])
            fraction = float(item["fraction"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0.05 < fraction < 0.95:
            by_note.setdefault(note_index, []).append(fraction)
    split_notes: list[NoteEvent] = []
    for note_index, note in enumerate(notes, start=1):
        fractions = sorted(set(round(value, 5) for value in by_note.get(note_index, [])))
        boundaries = [0.0, *fractions, 1.0]
        for start, end in zip(boundaries, boundaries[1:]):
            duration = note.end_ms - note.start_ms
            split_notes.append(NoteEvent(
                pitch=note.pitch,
                velocity=note.velocity,
                start_ms=note.start_ms + duration * start,
                end_ms=note.start_ms + duration * end,
            ))
    return split_notes


def add_virtual_note_split(
    report: dict,
    aligned_text: str,
    note_index: int,
    fraction: float,
    target_line: int | None = None,
    target_word_index: int | None = None,
) -> tuple[dict, str]:
    """Split one source MIDI note for alignment without mutating the MIDI file."""

    source_notes = report.get("source_notes") or report.get("notes", [])
    if not isinstance(note_index, int) or note_index < 1 or note_index > len(source_notes):
        raise ValueError("Choose a valid MIDI note to split.")
    if not 0.05 < fraction < 0.95:
        raise ValueError("Virtual splits must leave a meaningful duration on both sides.")
    virtual_splits: list[dict[str, int | float]] = []
    splits_by_note: dict[int, list[float]] = {}
    for item in report.get("virtual_splits", []):
        if not isinstance(item, dict):
            continue
        try:
            existing_note_index = int(item["note_index"])
            existing_fraction = float(item["fraction"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= existing_note_index <= len(source_notes) and 0.05 < existing_fraction < 0.95:
            virtual_splits.append({"note_index": existing_note_index, "fraction": existing_fraction})
            splits_by_note.setdefault(existing_note_index, []).append(existing_fraction)
    if any(abs(existing - fraction) < 0.02 for existing in splits_by_note.get(note_index, [])):
        raise ValueError("A virtual split already exists at that position.")
    raw_notes = [
        NoteEvent(
            pitch=int(note.get("midi_pitch", note.get("pitch"))),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in source_notes
    ]
    token_lines = _token_lines_from_report(report, aligned_text)
    current_boundaries = [0.0, *sorted(set(splits_by_note.get(note_index, []))), 1.0]
    segment_index = next(
        index
        for index, (start, end) in enumerate(zip(current_boundaries, current_boundaries[1:]))
        if start < fraction < end
    )
    segment_start = current_boundaries[segment_index]
    segment_end = current_boundaries[segment_index + 1]
    note_duration_ms = raw_notes[note_index - 1].duration_ms
    if min(fraction - segment_start, segment_end - fraction) * note_duration_ms < 50:
        raise ValueError("Virtual note segments must each be at least 50 ms long.")
    display_index = sum(1 + len(set(splits_by_note.get(index, []))) for index in range(1, note_index)) + segment_index
    displayed_notes = report.get("notes", [])
    owner = displayed_notes[display_index] if display_index < len(displayed_notes) else None
    if isinstance(owner, dict):
        try:
            owner_line = int(owner["line"])
            owner_word = int(owner["word_index"])
        except (KeyError, TypeError, ValueError):
            owner_line = owner_word = 0
        destination_line = owner_line
        destination_word = owner_word
        try:
            requested_line = int(target_line) if target_line is not None else 0
            requested_word = int(target_word_index) if target_word_index is not None else 0
        except (TypeError, ValueError):
            requested_line = requested_word = 0
        if requested_line == owner_line and 1 <= requested_line <= len(token_lines):
            requested_tokens = token_lines[requested_line - 1]
            if 1 <= requested_word <= len(requested_tokens) and requested_tokens[requested_word - 1].note_count == 0:
                destination_line = requested_line
                destination_word = requested_word
        if 1 <= destination_line <= len(token_lines) and 1 <= destination_word <= len(token_lines[destination_line - 1]):
            token_lines[destination_line - 1][destination_word - 1].note_count += 1

    virtual_splits.append({"note_index": note_index, "fraction": round(fraction, 5)})
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        _split_note_events(raw_notes, virtual_splits),
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["virtual_splits"] = virtual_splits
    updated_report["source_notes"] = [
        {"midi_pitch": note.pitch, "velocity": note.velocity, "start_ms": note.start_ms, "end_ms": note.end_ms}
        for note in raw_notes
    ]
    updated_report["version"] = max(3, int(report.get("version", 1)))
    updated_text = "\n".join(render_aligned_lyrics(token_lines, entries, line_timings=report.get("line_timings"))) + "\n"
    return updated_report, updated_text


def adjust_alignment_token_note_count(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
    delta: int,
) -> tuple[dict, str]:
    """Adjust one word's note count without changing its phrase allocation."""
    if delta not in {-1, 1}:
        raise ValueError("Word note-count adjustment must be minus or plus one.")

    token_lines = _token_lines_from_report(report, aligned_text)
    line_index = line - 1
    token_index = word_index - 1
    if line_index < 0 or line_index >= len(token_lines) or token_index < 0 or token_index >= len(token_lines[line_index]):
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")

    tokens = token_lines[line_index]
    target_token = tokens[token_index]
    if delta > 0:
        donors = [
            index
            for index, token in enumerate(tokens)
            if index != token_index and token.note_count > 1
        ]
        if not donors:
            raise ValueError("No other word in this phrase has a spare note.")
        donor_index = min(
            donors,
            key=lambda index: (abs(index - token_index), index < token_index),
        )
        tokens[donor_index].note_count -= 1
        target_token.note_count += 1
    else:
        if target_token.note_count <= 1:
            raise ValueError("A word must retain at least one note.")
        recipients = [
            index
            for index, token in enumerate(tokens)
            if index != token_index and token.note_count == 0
        ]
        if not recipients:
            if token_index + 1 < len(tokens):
                recipients = [token_index + 1]
            elif token_index > 0:
                recipients = [token_index - 1]
        if not recipients:
            raise ValueError("A one-word phrase cannot return a note to another word.")
        recipient_index = min(
            recipients,
            key=lambda index: (abs(index - token_index), index < token_index),
        )
        target_token.note_count -= 1
        tokens[recipient_index].note_count += 1

    raw_notes = report.get("notes", [])
    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in raw_notes
    ]
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(3, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, updated_text


def toggle_alignment_token_mode(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
) -> tuple[dict, str]:
    """Toggle one lyric unit between pitched singing and normal DECTALK speech."""

    token_lines = _token_lines_from_report(report, aligned_text)
    line_index = line - 1
    token_index = word_index - 1
    if line_index < 0 or line_index >= len(token_lines) or token_index < 0 or token_index >= len(token_lines[line_index]):
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")
    token = token_lines[line_index][token_index]
    token.mode = "sing" if getattr(token, "mode", "sing") == "speak" else "speak"

    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in report.get("notes", [])
    ]
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(4, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(token_lines, entries, line_timings=report.get("line_timings"))
    ) + "\n"
    return updated_report, updated_text


def resize_alignment_token(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
    edge: str,
    movement: int,
) -> tuple[dict, str]:
    """Move a visible lyric boundary by one or more MIDI notes.

    Negative values move earlier/shorter and positive values move later/longer
    in timeline order. The neighboring token absorbs the inverse allocation,
    so the total MIDI note count never changes.
    """

    if edge not in {"start", "end"}:
        raise ValueError("Lyric boundary must be start or end.")
    if not isinstance(movement, int) or movement == 0:
        raise ValueError("Lyric boundary movement must be at least one note.")

    token_lines = _token_lines_from_report(report, aligned_text)
    locations = [
        (line_index, token_index)
        for line_index, tokens in enumerate(token_lines)
        for token_index, _ in enumerate(tokens)
    ]
    target = (line - 1, word_index - 1)
    if target not in locations:
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")
    target_index = locations.index(target)
    target_token = token_lines[target[0]][target[1]]

    assigned_notes = sum(token.note_count for tokens in token_lines for token in tokens)
    unassigned_tail = max(0, len(report.get("notes", [])) - assigned_notes)

    if edge == "start":
        preceding_locations = locations[:target_index]
        preceding_tokens = [token_lines[line_index][token_index] for line_index, token_index in preceding_locations]
        if not preceding_tokens:
            raise ValueError("The first lyric start cannot move without a preceding lyric unit.")
        if movement < 0:
            amount = abs(movement)
            available = sum(max(0, token.note_count - 1) for token in preceding_tokens)
            if available < amount:
                raise ValueError("Earlier lyrics cannot give that many notes.")
            target_token.note_count += amount
            remaining = amount
            for token in reversed(preceding_tokens):
                transferred = min(remaining, max(0, token.note_count - 1))
                token.note_count -= transferred
                remaining -= transferred
                if not remaining:
                    break
        else:
            if target_token.note_count <= movement:
                raise ValueError("The selected lyric unit cannot give that many notes back.")
            target_token.note_count -= movement
            preceding_tokens[-1].note_count += movement
    else:
        following_locations = locations[target_index + 1:]
        following_tokens = [token_lines[line_index][token_index] for line_index, token_index in following_locations]
        if movement > 0:
            available = sum(token.note_count for token in following_tokens) + unassigned_tail
            if available < movement:
                raise ValueError("Following lyrics cannot give that many notes.")
            target_token.note_count += movement
            remaining = movement
            # Keep following words viable until the user deliberately crosses
            # that boundary; then zero-note words become an explicit review state.
            for token in reversed(following_tokens):
                transferred = min(remaining, max(0, token.note_count - 1))
                token.note_count -= transferred
                remaining -= transferred
                if not remaining:
                    break
            for token in following_tokens:
                transferred = min(remaining, token.note_count)
                token.note_count -= transferred
                remaining -= transferred
                if not remaining:
                    break
        else:
            amount = abs(movement)
            if target_token.note_count <= amount:
                raise ValueError("The selected lyric unit cannot give that many notes back.")
            target_token.note_count -= amount
            for token in following_tokens:
                if amount and token.note_count == 0:
                    token.note_count += 1
                    amount -= 1
            if amount and following_tokens:
                following_tokens[-1].note_count += amount

    _fill_phrase_note_gaps(token_lines[target[0]])

    raw_notes = report.get("notes", [])
    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in raw_notes
    ]
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, updated_text


def resize_alignment_phrase(
    report: dict,
    aligned_text: str,
    line: int,
    edge: str,
    movement: int,
) -> tuple[dict, str]:
    """Move a phrase boundary by one or more notes without changing total allocation."""

    if edge not in {"start", "end"}:
        raise ValueError("Phrase boundary must be start or end.")
    if not isinstance(movement, int) or movement == 0:
        raise ValueError("Phrase boundary movement must be at least one note.")

    token_lines = _token_lines_from_report(report, aligned_text)
    target_index = line - 1
    if target_index < 0 or target_index >= len(token_lines) or not token_lines[target_index]:
        raise ValueError("The selected phrase is no longer present in the aligned text.")

    assigned_notes = sum(token.note_count for tokens in token_lines for token in tokens)
    unassigned_tail = max(0, len(report.get("notes", [])) - assigned_notes)

    if edge == "start":
        if target_index == 0 or not token_lines[target_index - 1]:
            raise ValueError("The first phrase start cannot move earlier or later.")
        target_token = token_lines[target_index][0]
        preceding_tokens = [
            token
            for preceding_line in token_lines[:target_index]
            for token in preceding_line
        ]
        if movement < 0:
            amount = abs(movement)
            available = sum(max(0, token.note_count - 1) for token in preceding_tokens)
            if available < amount:
                raise ValueError("Earlier phrases cannot give that many notes.")
            target_token.note_count += amount
            remaining = amount
            # Consume from the global tail so every prior word keeps one note.
            for token in reversed(preceding_tokens):
                transferred = min(remaining, token.note_count - 1)
                token.note_count -= transferred
                remaining -= transferred
                if not remaining:
                    break
        else:
            _release_phrase_notes(token_lines[target_index], movement, from_end=False)
            # Released time belongs at the preceding tail, moving this phrase later.
            preceding_tokens[-1].note_count += movement
    else:
        following_tokens = [
            token
            for following_line in token_lines[target_index + 1:]
            for token in following_line
        ]
        target_token = token_lines[target_index][-1]
        if movement > 0:
            available = sum(token.note_count for token in following_tokens) + unassigned_tail
            if available < movement:
                raise ValueError("Later phrases cannot give that many notes.")
            target_token.note_count += movement
            remaining = movement
            # Preserve the following phrase's first words, then compensate at the tail.
            for token in reversed(following_tokens):
                transferred = min(remaining, token.note_count - 1)
                token.note_count -= transferred
                remaining -= transferred
                if not remaining:
                    break
            for token in following_tokens:
                transferred = min(remaining, token.note_count)
                token.note_count -= transferred
                remaining -= transferred
                if not remaining:
                    break
        else:
            amount = abs(movement)
            _release_phrase_notes(token_lines[target_index], amount, from_end=True)
            # The unused duration settles at the tail, so every following phrase shifts earlier.
            for token in following_tokens:
                if amount and token.note_count == 0:
                    token.note_count += 1
                    amount -= 1
            if amount and following_tokens:
                following_tokens[-1].note_count += amount

    _fill_phrase_note_gaps(token_lines[target_index])

    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in report.get("notes", [])
    ]
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, updated_text


def insert_alignment_token(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
    raw_word: str,
    position: str = "after",
) -> tuple[dict, str, tuple[int, int]]:
    """Insert one lyric unit and re-fit the remaining note allocation."""

    if position not in {"before", "after"}:
        raise ValueError("A lyric unit can only be inserted before or after the selected unit.")
    parsed = parse_lyric_token(raw_word)
    if parsed is None or not parsed.word.strip():
        raise ValueError("Enter one lyric word or direct phoneme unit to insert.")

    token_lines = _token_lines_from_report(report, aligned_text)
    target = (line - 1, word_index - 1)
    if target[0] < 0 or target[0] >= len(token_lines) or target[1] < 0 or target[1] >= len(token_lines[target[0]]):
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")
    insert_index = target[1] + (1 if position == "after" else 0)
    inserted = AlignmentToken(
        word=parsed.word,
        note_count=max(1, int(parsed.note_count)),
        line=line,
        word_index=insert_index + 1,
    )
    token_lines[target[0]].insert(insert_index, inserted)

    original_summary = report.get("summary", {})
    unassigned_notes = max(
        0,
        int(original_summary.get("midi_notes", len(report.get("notes", []))))
        - int(original_summary.get("assigned_notes", 0)),
    )
    extra_notes = inserted.note_count
    if unassigned_notes:
        extra_notes = max(0, extra_notes - unassigned_notes)
    locations = [
        (line_index, token_index)
        for line_index, tokens in enumerate(token_lines)
        for token_index, _ in enumerate(tokens)
        if (line_index, token_index) != (target[0], insert_index)
    ]
    for _ in range(extra_notes):
        donor_index = next(
            (
                index
                for index in range(insert_index + 1, len(token_lines[target[0]]))
                if token_lines[target[0]][index].note_count > 1
            ),
            None,
        )
        donor = (
            token_lines[target[0]][donor_index]
            if donor_index is not None
            else next(
                (
                    token_lines[line_index][token_index]
                    for line_index, token_index in reversed(locations)
                    if token_lines[line_index][token_index].note_count > 1
                ),
                None,
            )
        )
        if donor is None:
            break
        donor.note_count -= 1

    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in report.get("notes", [])
    ]
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(original_summary.get("phrase_gap_ms", 0)),
        float(original_summary.get("word_gap_ms", 0)),
        placeholder_word=original_summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, updated_text, (line, insert_index + 1)


def delete_alignment_token(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
) -> tuple[dict, str, tuple[int, int]]:
    """Remove one word and close its note span without leaving timing holes."""

    token_lines = _token_lines_from_report(report, aligned_text)
    target_line = line - 1
    target_word = word_index - 1
    if target_line < 0 or target_line >= len(token_lines) or target_word < 0 or target_word >= len(token_lines[target_line]):
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")
    removing_phrase = len(token_lines[target_line]) == 1
    if removing_phrase:
        if sum(len(tokens) for tokens in token_lines) <= 1:
            raise ValueError("The final lyric unit cannot be removed.")
        removed = token_lines.pop(target_line)[0]
    else:
        removed = token_lines[target_line].pop(target_word)
    remaining_tokens = [token for token_line in token_lines for token in token_line]
    # Preserve all immediate following words by settling the released notes at the tail.
    remaining_tokens[-1].note_count += removed.note_count

    line_timings = report.get("line_timings")
    if removing_phrase and isinstance(line_timings, list):
        line_timings = list(line_timings)
        if target_line < len(line_timings):
            line_timings.pop(target_line)

    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in report.get("notes", [])
    ]
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    if isinstance(line_timings, list):
        updated_report["line_timings"] = line_timings
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=line_timings,
        )
    ) + "\n"
    if removing_phrase:
        selected_line_index = min(target_line, len(token_lines) - 1)
        return updated_report, updated_text, (selected_line_index + 1, 1)
    return updated_report, updated_text, (line, min(word_index, len(token_lines[target_line])))


def reorder_alignment_token(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
    target_word_index: int,
) -> tuple[dict, str, tuple[int, int]]:
    """Move a word before another word in its phrase, keeping its note claim."""

    token_lines = _token_lines_from_report(report, aligned_text)
    target_line = line - 1
    source_index = word_index - 1
    destination_index = target_word_index - 1
    if target_line < 0 or target_line >= len(token_lines):
        raise ValueError("The selected phrase is no longer present in the aligned text.")
    tokens = token_lines[target_line]
    if not (0 <= source_index < len(tokens)) or not (0 <= destination_index < len(tokens)):
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")
    if source_index == destination_index:
        return report, aligned_text, (line, word_index)

    moved = tokens.pop(source_index)
    tokens.insert(destination_index, moved)
    notes = [
        NoteEvent(
            pitch=int(note["midi_pitch"]),
            velocity=int(note.get("velocity", 0)),
            start_ms=float(note["start_ms"]),
            end_ms=float(note["end_ms"]),
        )
        for note in report.get("notes", [])
    ]
    summary = report.get("summary", {})
    entries, new_summary = align_tokens(
        _tokens_from_lines(token_lines),
        notes,
        float(summary.get("phrase_gap_ms", 0)),
        float(summary.get("word_gap_ms", 0)),
        placeholder_word=summary.get("placeholder_word") or None,
    )
    updated_report = dict(report)
    updated_report["summary"] = new_summary
    updated_report["notes"] = [asdict(entry) for entry in entries]
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(render_aligned_lyrics(token_lines, entries, line_timings=report.get("line_timings"))) + "\n"
    return updated_report, updated_text, (line, destination_index + 1)


def build_alignment(
    song_title: str,
    part_name: str,
    draft_path: Path,
    include_timing: bool = False,
) -> tuple[dict, list[str]]:
    song_dir, settings = load_settings(song_title)
    track = resolve_track(settings, part_name)
    notes, beat_ms, _, midi_file = load_track_notes(
        song_dir,
        settings,
        track["track_filename"],
    )
    token_lines = read_lyric_token_lines(draft_path)
    line_timings = _read_line_timing_tokens(draft_path)
    tokens = _tokens_from_lines(token_lines)
    placeholder_word = _placeholder_from_draft(draft_path)

    threshold_args = argparse.Namespace(
        phrase_gap_ms=None,
        word_gap_ms=None,
        tight_gap_ms=None,
    )
    phrase_gap_ms, word_gap_ms, _ = resolve_thresholds(
        threshold_args,
        settings,
        beat_ms,
    )
    entries, summary = align_tokens(
        tokens,
        notes,
        phrase_gap_ms,
        word_gap_ms,
        placeholder_word=placeholder_word,
    )
    report = {
        "version": 2,
        "song": song_title,
        "part": part_name,
        "midi_source": str(midi_file),
        "draft_source": str(draft_path),
        "source_mode": "placeholder" if placeholder_word else "transcript",
        "line_timings": line_timings,
        "token_counts": _token_counts(token_lines, entries),
        "source_notes": [
            {"midi_pitch": note.pitch, "velocity": note.velocity, "start_ms": note.start_ms, "end_ms": note.end_ms}
            for note in notes
        ],
        "virtual_splits": [],
        "summary": summary,
        "notes": [asdict(entry) for entry in entries],
    }
    return report, render_aligned_lyrics(
        token_lines,
        entries,
        line_timings=line_timings,
        include_timing=include_timing,
    )


def apply_timed_alignment_template(
    source_report: dict,
    source_text: str,
    target_report: dict,
    source_role: str,
) -> tuple[dict, str]:
    """Copy an alignment's lyric ownership onto another track by musical time.

    Tracks in a harmony frequently have different note counts.  This maps each
    target note to the nearest ordered source word window, rather than copying
    source note indices or attempting to force equal note counts.
    """

    token_lines = _token_lines_from_report(source_report, source_text)
    token_by_key = {
        (line_index, word_index): token
        for line_index, tokens in enumerate(token_lines, start=1)
        for word_index, token in enumerate(tokens, start=1)
    }
    source_words: dict[tuple[int, int], list[dict]] = {}
    for entry in source_report.get("notes", []):
        if not isinstance(entry, dict):
            continue
        line = entry.get("line")
        word_index = entry.get("word_index")
        if isinstance(line, int) and isinstance(word_index, int) and (line, word_index) in token_by_key:
            source_words.setdefault((line, word_index), []).append(entry)
    windows = []
    for key, entries in source_words.items():
        try:
            start_ms = min(float(entry["start_ms"]) for entry in entries)
            end_ms = max(float(entry["end_ms"]) for entry in entries)
        except (KeyError, TypeError, ValueError):
            continue
        windows.append({"key": key, "start_ms": start_ms, "end_ms": end_ms, "center_ms": (start_ms + end_ms) / 2})
    windows.sort(key=lambda item: (item["center_ms"], item["key"]))
    if not windows:
        raise ValueError("The source alignment has no timed lyric notes to copy.")

    raw_target_notes = target_report.get("source_notes")
    if not isinstance(raw_target_notes, list) or not raw_target_notes:
        raise ValueError("The target track has no notes to receive the template.")
    target_notes: list[NoteEvent] = []
    for note in raw_target_notes:
        if not isinstance(note, dict):
            raise ValueError("The target alignment has invalid MIDI note data.")
        try:
            target_notes.append(NoteEvent(
                pitch=int(note["midi_pitch"]),
                velocity=int(note.get("velocity", 0)),
                start_ms=float(note["start_ms"]),
                end_ms=float(note["end_ms"]),
            ))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("The target alignment has invalid MIDI note data.") from error

    for tokens in token_lines:
        for token in tokens:
            token.note_count = 0
    for note in target_notes:
        center_ms = (note.start_ms + note.end_ms) / 2
        closest = min(windows, key=lambda window: abs(window["center_ms"] - center_ms))
        token_by_key[closest["key"]].note_count += 1

    source_summary = source_report.get("summary", {})
    target_summary = target_report.get("summary", {})
    entries, summary = align_tokens(
        _tokens_from_lines(token_lines),
        target_notes,
        float(target_summary.get("phrase_gap_ms", source_summary.get("phrase_gap_ms", 0))),
        float(target_summary.get("word_gap_ms", source_summary.get("word_gap_ms", 0))),
        placeholder_word=target_summary.get("placeholder_word") or None,
    )
    entry_data = [asdict(entry) for entry in entries]
    for entry in entry_data:
        if entry["lyric"] is not None:
            entry["confidence"] = "Template"
            entry["status"] = "Template timing"
    summary["status"] = "Template timing" if not summary["zero_note_tokens"] else "Needs review"
    summary["template_source_role"] = source_role
    summary["template_source_notes"] = len(source_report.get("notes", []))
    summary["template_target_notes"] = len(target_notes)
    summary["template_mode"] = "analog_time"

    updated_report = dict(target_report)
    updated_report["version"] = max(3, int(target_report.get("version", 1)))
    updated_report["summary"] = summary
    updated_report["notes"] = entry_data
    updated_report["token_counts"] = _token_counts(token_lines, entries)
    updated_report["virtual_splits"] = []
    updated_report["template"] = {
        "source_role": source_role,
        "mode": "analog_time",
        "source_note_count": len(source_report.get("notes", [])),
        "target_note_count": len(target_notes),
    }
    text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=target_report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, text


def _default_draft(song_title: str, part_name: str, track: dict) -> Path:
    song_dir = REPO_ROOT / "songs" / song_title
    generated = outputs_directory(song_dir) / "lyrics_drafts" / f"{part_name}.txt"
    if generated.is_file():
        return generated
    return lyrics_directory(song_dir) / f"{track['lyrics_filename']}.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Align a drafted lyric file with the MIDI notes for one choir role."
    )
    parser.add_argument("song", help="Song folder under songs/")
    parser.add_argument("part", help="Output role name under Tracks:")
    parser.add_argument(
        "--draft",
        help="Drafted lyric file. Defaults to songs/<song>/outputs/lyrics_drafts/<part>.txt.",
    )
    parser.add_argument(
        "--output",
        help="Canonical aligned lyric output. Defaults to songs/<song>/outputs/lyrics_aligned/<part>.txt.",
    )
    parser.add_argument(
        "--report",
        help="JSON alignment report. Defaults beside the aligned lyric output.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the aligned lyric file to the configured songs/<song>/inputs/lyrics input.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing existing output files.",
    )
    parser.add_argument(
        "--include-timing",
        action="store_true",
        help="Preserve line timing overrides in the rendered lyric file.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _, settings = load_settings(args.song)
    track = resolve_track(settings, args.part)
    draft_path = Path(args.draft).expanduser().resolve() if args.draft else _default_draft(args.song, args.part, track)
    if not draft_path.is_file():
        raise SystemExit(f"Draft lyric file not found: {draft_path}")

    report, output_lines = build_alignment(
        args.song,
        args.part,
        draft_path,
        include_timing=args.include_timing,
    )
    output_path = (
        lyrics_directory(REPO_ROOT / "songs" / args.song) / f"{track['lyrics_filename']}.txt"
        if args.apply
        else Path(args.output).expanduser().resolve()
        if args.output
        else outputs_directory(REPO_ROOT / "songs" / args.song) / "lyrics_aligned" / f"{args.part}.txt"
    )
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else outputs_directory(REPO_ROOT / "songs" / args.song) / "lyrics_aligned" / f"{args.part}.json"
    )

    for path in (output_path, report_path):
        if path.exists() and not args.overwrite:
            raise SystemExit(f"Refusing to overwrite existing file without --overwrite: {path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(f"Song: {args.song}")
    print(f"Output part: {args.part}")
    print(f"Draft source: {draft_path}")
    print(f"MIDI source: {report['midi_source']}")
    print(
        f"Alignment: {summary['status']}  "
        f"notes={summary['assigned_notes']}/{summary['midi_notes']}  "
        f"tokens={summary['draft_tokens']}  "
        f"unassigned={summary['unassigned_notes']}  "
        f"overflow={summary['overflow_tokens']}"
    )
    print(f"Wrote lyrics: {output_path}")
    print(f"Wrote report: {report_path}")


if __name__ == "__main__":
    main()
