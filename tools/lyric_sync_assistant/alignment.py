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


def _tokens_from_lines(token_lines) -> list[AlignmentToken]:
    tokens: list[AlignmentToken] = []
    for line_index, line in enumerate(token_lines, start=1):
        for word_index, token in enumerate(line, start=1):
            tokens.append(
                AlignmentToken(
                    word=token.word,
                    note_count=max(1, int(token.note_count)),
                    line=line_index,
                    word_index=word_index,
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
            if assigned_notes == len(notes) and overflow_tokens == 0
            else "Needs review"
        ),
        "midi_notes": len(notes),
        "assigned_notes": assigned_notes,
        "unassigned_notes": max(0, len(notes) - assigned_notes),
        "draft_tokens": len(tokens),
        "overflow_tokens": overflow_tokens,
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
            rendered_tokens.append(format_synced_word(token.word, token.note_count))
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

    if entry_index < len(entries):
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


def shift_alignment_token(
    report: dict,
    aligned_text: str,
    line: int,
    word_index: int,
    direction: int,
) -> tuple[dict, str]:
    """Move one lyric unit by one MIDI note while preserving total allocation."""
    if direction not in {-1, 1}:
        raise ValueError("Alignment movement must be one note earlier or later.")
    token_lines = _token_lines_from_text(aligned_text)
    locations = [
        (line_index, token_index)
        for line_index, tokens in enumerate(token_lines)
        for token_index, _ in enumerate(tokens)
    ]
    target = (line - 1, word_index - 1)
    if target not in locations:
        raise ValueError("The selected lyric unit is no longer present in the aligned text.")
    target_index = locations.index(target)
    neighbor_index = target_index + direction
    if neighbor_index < 0 or neighbor_index >= len(locations):
        raise ValueError("The selected lyric unit cannot move beyond the lyric.")

    target_token = token_lines[target[0]][target[1]]
    neighbor_location = locations[neighbor_index]
    neighbor_token = token_lines[neighbor_location[0]][neighbor_location[1]]
    if direction < 0:
        if neighbor_token.note_count <= 1:
            raise ValueError("The preceding lyric unit has only one note to preserve.")
        neighbor_token.note_count -= 1
        target_token.note_count += 1
    else:
        if target_token.note_count <= 1:
            raise ValueError("The selected lyric unit has only one note to give back.")
        target_token.note_count -= 1
        neighbor_token.note_count += 1

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
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
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
    """Move one visible lyric boundary by one MIDI note.

    ``edge`` is ``start`` or ``end`` and ``movement`` is -1 for earlier/shorter
    or +1 for later/longer in timeline order. The neighboring token absorbs the
    inverse allocation, so the total MIDI note count never changes.
    """

    if edge not in {"start", "end"}:
        raise ValueError("Lyric boundary must be start or end.")
    if movement not in {-1, 1}:
        raise ValueError("Lyric boundary movement must be one note at a time.")

    token_lines = _token_lines_from_text(aligned_text)
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

    if edge == "start":
        neighbor_index = target_index - 1
        if neighbor_index < 0 or locations[neighbor_index][0] != target[0]:
            raise ValueError("The phrase start is fixed; adjust the phrase range separately.")
        neighbor_location = locations[neighbor_index]
        neighbor_token = token_lines[neighbor_location[0]][neighbor_location[1]]
        if movement < 0:
            if neighbor_token.note_count <= 1:
                raise ValueError("The preceding word has only one note to preserve.")
            neighbor_token.note_count -= 1
            target_token.note_count += 1
        else:
            if target_token.note_count <= 1:
                raise ValueError("The selected lyric unit has only one note to give back.")
            target_token.note_count -= 1
            neighbor_token.note_count += 1
    else:
        neighbor_index = target_index + 1
        if neighbor_index >= len(locations) or locations[neighbor_index][0] != target[0]:
            raise ValueError("The phrase end is fixed; adjust the phrase range separately.")
        neighbor_location = locations[neighbor_index]
        neighbor_token = token_lines[neighbor_location[0]][neighbor_location[1]]
        if movement > 0:
            if neighbor_token.note_count <= 1:
                raise ValueError("The following word has only one note to preserve.")
            neighbor_token.note_count -= 1
            target_token.note_count += 1
        else:
            if target_token.note_count <= 1:
                raise ValueError("The selected lyric unit has only one note to give back.")
            target_token.note_count -= 1
            neighbor_token.note_count += 1

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
    """Move one phrase boundary while preserving the total MIDI allocation."""

    if edge not in {"start", "end"}:
        raise ValueError("Phrase boundary must be start or end.")
    if movement not in {-1, 1}:
        raise ValueError("Phrase boundary movement must be one note at a time.")

    token_lines = _token_lines_from_text(aligned_text)
    target_index = line - 1
    if target_index < 0 or target_index >= len(token_lines) or not token_lines[target_index]:
        raise ValueError("The selected phrase is no longer present in the aligned text.")

    if edge == "start":
        if target_index == 0 or not token_lines[target_index - 1]:
            raise ValueError("The first phrase start cannot move earlier or later.")
        target_token = token_lines[target_index][0]
        neighbor_token = token_lines[target_index - 1][-1]
        if movement < 0:
            if neighbor_token.note_count <= 1:
                raise ValueError("The preceding phrase has no note to give this phrase.")
            neighbor_token.note_count -= 1
            target_token.note_count += 1
        else:
            if target_token.note_count <= 1:
                raise ValueError("This phrase has no leading note to give back.")
            target_token.note_count -= 1
            neighbor_token.note_count += 1
    else:
        if target_index >= len(token_lines) - 1 or not token_lines[target_index + 1]:
            raise ValueError("The final phrase end cannot move earlier or later.")
        target_token = token_lines[target_index][-1]
        neighbor_token = token_lines[target_index + 1][0]
        if movement > 0:
            if neighbor_token.note_count <= 1:
                raise ValueError("The following phrase has no note to give this phrase.")
            neighbor_token.note_count -= 1
            target_token.note_count += 1
        else:
            if target_token.note_count <= 1:
                raise ValueError("This phrase has no trailing note to give back.")
            target_token.note_count -= 1
            neighbor_token.note_count += 1

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

    token_lines = _token_lines_from_text(aligned_text)
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
    updated_report["version"] = max(2, int(report.get("version", 1)))
    updated_text = "\n".join(
        render_aligned_lyrics(
            token_lines,
            entries,
            line_timings=report.get("line_timings"),
        )
    ) + "\n"
    return updated_report, updated_text, (line, insert_index + 1)


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
        "summary": summary,
        "notes": [asdict(entry) for entry in entries],
    }
    return report, render_aligned_lyrics(
        token_lines,
        entries,
        line_timings=line_timings,
        include_timing=include_timing,
    )


def _default_draft(song_title: str, part_name: str, track: dict) -> Path:
    generated = REPO_ROOT / "outputs" / song_title / "lyrics_drafts" / f"{part_name}.txt"
    if generated.is_file():
        return generated
    return REPO_ROOT / "songs" / song_title / "lyrics" / f"{track['lyrics_filename']}.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Align a drafted lyric file with the MIDI notes for one choir role."
    )
    parser.add_argument("song", help="Song folder under songs/")
    parser.add_argument("part", help="Output role name under Tracks:")
    parser.add_argument(
        "--draft",
        help="Drafted lyric file. Defaults to outputs/<song>/lyrics_drafts/<part>.txt.",
    )
    parser.add_argument(
        "--output",
        help="Canonical aligned lyric output. Defaults to outputs/<song>/lyrics_aligned/<part>.txt.",
    )
    parser.add_argument(
        "--report",
        help="JSON alignment report. Defaults beside the aligned lyric output.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the aligned lyric file to the configured songs/<song>/lyrics input.",
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
        REPO_ROOT / "songs" / args.song / "lyrics" / f"{track['lyrics_filename']}.txt"
        if args.apply
        else Path(args.output).expanduser().resolve()
        if args.output
        else REPO_ROOT / "outputs" / args.song / "lyrics_aligned" / f"{args.part}.txt"
    )
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else REPO_ROOT / "outputs" / args.song / "lyrics_aligned" / f"{args.part}.json"
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
