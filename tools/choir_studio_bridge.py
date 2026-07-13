#!/usr/bin/env python3
"""Small JSON bridge between Choir Studio and the established Python tools.

The desktop shell owns presentation and local interaction. This file deliberately
owns only request validation, durable workspace artifacts, and calls into the
same inspector/drafter/aligner that the command-line workflow uses.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, is_dataclass
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSISTANT_DIR = REPO_ROOT / "tools" / "lyric_sync_assistant"
for directory in (REPO_ROOT, ASSISTANT_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from choir_gui.inspector import _lyric_conversion_issue, inspect_song
from choir_gui.midi_workflow import write_single_track_preview
import pyFuncs.PhonemeProcessing as phonemes
from assistant import (
    read_transcript_lines,
    render_draft,
    render_placeholder_draft,
    resolve_thresholds,
    resolve_track,
    load_settings,
    load_track_notes,
    split_note_phrases,
)
from alignment import (
    build_alignment,
    insert_alignment_token,
    resize_alignment_phrase,
    resize_alignment_token,
)


SONG_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class BridgeError(ValueError):
    """A request cannot safely be applied to the local Choir workspace."""


def _song_name(value: object) -> str:
    name = str(value or "")
    if not SONG_NAME.fullmatch(name):
        raise BridgeError("Song name may contain only letters, numbers, underscores, and hyphens.")
    if not (REPO_ROOT / "songs" / name).is_dir():
        raise BridgeError(f"Song folder was not found: songs/{name}")
    return name


def _role(song: str, value: object) -> str:
    role = str(value or "")
    _, settings = load_settings(song)
    if role not in (settings.get("Tracks") or {}):
        raise BridgeError(f"Role '{role}' is not configured for {song}.")
    return role


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _role_paths(song: str, role: str) -> tuple[Path, Path, Path, Path]:
    song_dir, settings = load_settings(song)
    track = resolve_track(settings, role)
    lyric_dir = song_dir / "lyrics"
    source = lyric_dir / f"{track['lyrics_filename']}.txt"
    raw = lyric_dir / f"{track['lyrics_filename']}.raw.txt"
    output_dir = REPO_ROOT / "outputs" / song / "lyrics_drafts"
    transcript = output_dir / f"{role}.transcript.txt"
    draft = output_dir / f"{role}.txt"
    return source, raw, transcript, draft


def _read_source(song: str, role: str) -> dict[str, str]:
    source, raw, transcript, _ = _role_paths(song, role)
    active = next((path for path in (transcript, raw, source) if path.is_file()), source)
    try:
        text = active.read_text(encoding="utf-8") if active.is_file() else ""
    except OSError as error:
        raise BridgeError(f"Could not read transcript: {error}") from error
    return {"text": text, "path": str(active), "kind": "transcript"}


def _write_transcript(song: str, role: str, text: object) -> dict[str, str]:
    _, _, transcript, _ = _role_paths(song, role)
    cleaned = str(text or "").replace("\r\n", "\n")
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(cleaned.rstrip() + "\n" if cleaned.strip() else "", encoding="utf-8")
    return {"path": str(transcript), "text": cleaned}


def _words_for_validation(text: str) -> tuple[list[str], list[str]]:
    """Return invalid user words plus lines whose punctuation will be normalized."""

    invalid: list[str] = []
    normalized_lines: list[str] = []
    for source_line in text.splitlines():
        sanitized = __import__("assistant").sanitize_transcript_line(source_line)
        if source_line.strip() and source_line.strip() != sanitized:
            normalized_lines.append(source_line.strip())
        for token in sanitized.split():
            if token.startswith("[") or token.startswith("!"):
                continue
            word = token.rsplit("*", 1)[-1]
            if "|" in word:
                word = word.rsplit("|", 1)[-1]
            if not word:
                continue
            with contextlib.redirect_stdout(io.StringIO()):
                converted = phonemes.convertWordToPhonemes(
                    word.lower(), DECTALK_check=False
                )
            if not converted:
                invalid.append(word)
    return sorted(set(invalid)), normalized_lines


def _validate_transcript(text: object) -> dict[str, Any]:
    invalid, normalized_lines = _words_for_validation(str(text or ""))
    return {
        "invalid_words": invalid,
        "normalized_lines": normalized_lines,
        "ok": not invalid,
    }


def _draft(song: str, role: str, text: object, mode: object, auto_lines: object) -> dict[str, Any]:
    _write_transcript(song, role, text)
    source, _, transcript, draft_path = _role_paths(song, role)
    song_dir, settings = load_settings(song)
    track = resolve_track(settings, role)
    notes, beat_ms, _, _ = load_track_notes(song_dir, settings, track["track_filename"])
    args = type("DraftArgs", (), {"phrase_gap_ms": None, "word_gap_ms": None, "tight_gap_ms": None})()
    phrase_gap_ms, word_gap_ms, tight_gap_ms = resolve_thresholds(args, settings, beat_ms)
    phrases = split_note_phrases(notes, phrase_gap_ms)
    warnings: list[str] = []
    if mode == "placeholder":
        lines = render_placeholder_draft(notes, phrases, "daa", include_comments=False)
    else:
        transcript_lines = read_transcript_lines(transcript)
        source_lines = [list(line.words) for line in transcript_lines]
        if not source_lines:
            raise BridgeError("Transcript has no usable lyric words after normalization.")
        effective_auto_lines = bool(auto_lines) or (
            len(source_lines) == 1 and not any(line.timing_token for line in transcript_lines)
        )
        lines, warnings = render_draft(
            source_lines,
            notes,
            phrases,
            tight_gap_ms,
            word_gap_ms,
            include_comments=False,
            auto_lines=effective_auto_lines,
            transcript_lines=transcript_lines,
        )
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    output = "\n".join(lines).rstrip() + "\n"
    draft_path.write_text(output, encoding="utf-8")
    review_segments: list[dict[str, Any]] = []
    if mode != "placeholder":
        report, _ = build_alignment(song, role, draft_path)
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for entry in report["notes"]:
            if entry.get("line") is None or entry.get("word_index") is None:
                continue
            grouped.setdefault((int(entry["line"]), int(entry["word_index"])), []).append(entry)
        for (line, word_index), entries in grouped.items():
            if len(entries) < 2:
                continue
            internal_gaps = [int(entry.get("gap_before_ms") or 0) for entry in entries[1:]]
            if not internal_gaps or max(internal_gaps) > tight_gap_ms:
                continue
            review_segments.append(
                {
                    "line": line,
                    "word_index": word_index,
                    "word": entries[0].get("lyric") or "",
                    "note_count": len(entries),
                    "start_ms": int(entries[0]["start_ms"]),
                    "end_ms": int(entries[-1]["end_ms"]),
                    "largest_internal_gap_ms": max(internal_gaps),
                }
            )
    return {
        "path": str(draft_path),
        "text": output,
        "warnings": warnings,
        "review_segments": review_segments,
        "tight_gap_ms": round(tight_gap_ms),
        "source_path": str(transcript if mode != "placeholder" else source),
    }


def _align(song: str, role: str) -> dict[str, Any]:
    _, _, _, draft_path = _role_paths(song, role)
    if not draft_path.is_file():
        raise BridgeError("Draft lyrics before starting alignment.")
    report, lines = build_alignment(song, role, draft_path)
    aligned_dir = REPO_ROOT / "outputs" / song / "lyrics_aligned"
    aligned_path = aligned_dir / f"{role}.txt"
    report_path = aligned_dir / f"{role}.json"
    aligned_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    aligned_path.write_text(text, encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"text": text, "path": str(aligned_path), "report": report, "report_path": str(report_path)}


def _render(song: str) -> dict[str, Any]:
    """Run the established compiler once and return its operator-facing log."""

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "choir.py"), song],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "ok": result.returncode == 0,
    }


def _prepare_midi_preview(song: str, role: str) -> dict[str, Any]:
    """Build a fresh, selected-track-only MIDI file for the Windows sequencer."""

    inspection = inspect_song(REPO_ROOT, song)
    inspected_role = next((item for item in inspection.roles if item.role == role), None)
    if not inspection.midi_path or not inspection.midi or not inspected_role or not inspected_role.midi_track:
        raise BridgeError("The selected role has no playable MIDI source.")
    source_track = inspected_role.midi_track
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source_track.name).strip("_") or "track"
    output = (
        inspection.output_dir
        / "_midi_preview"
        / f"{source_track.index:02d}_{safe_name}_{uuid.uuid4().hex[:8]}.mid"
    )
    path = write_single_track_preview(inspection.midi_path, source_track.index, output)
    return {
        "path": str(path),
        "duration_ms": round(inspection.midi.duration_seconds * 1000),
        "track": source_track.name,
    }


def _write_alignment(song: str, role: str, report: dict[str, Any], text: str) -> dict[str, Any]:
    aligned_dir = REPO_ROOT / "outputs" / song / "lyrics_aligned"
    aligned_path = aligned_dir / f"{role}.txt"
    report_path = aligned_dir / f"{role}.json"
    aligned_dir.mkdir(parents=True, exist_ok=True)
    normalized_text = text.rstrip() + "\n"
    aligned_path.write_text(normalized_text, encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"text": normalized_text, "path": str(aligned_path), "report": report, "report_path": str(report_path)}


def _apply_alignment(song: str, role: str, text: object) -> dict[str, str | None]:
    """Validate and apply a saved alignment to the compiler's configured input."""

    normalized_text = str(text or "").rstrip()
    if not normalized_text:
        raise BridgeError("Aligned lyric text is empty.")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as temporary:
            temporary.write(normalized_text + "\n")
            temporary_path = Path(temporary.name)
        issue = _lyric_conversion_issue(temporary_path)
    except OSError as error:
        raise BridgeError(f"Could not validate aligned lyrics: {error}") from error
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    if issue:
        raise BridgeError(f"Aligned lyrics were not applied: {issue}")

    source, _, _, _ = _role_paths(song, role)
    source.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if source.is_file():
        backup = source.with_name(f"{source.stem}.original.txt")
        if not backup.exists():
            try:
                backup.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError as error:
                raise BridgeError(f"Could not back up configured lyric input: {error}") from error
    try:
        source.write_text(normalized_text + "\n", encoding="utf-8")
    except OSError as error:
        raise BridgeError(f"Could not apply aligned lyrics: {error}") from error
    return {"path": str(source), "backup_path": str(backup) if backup else None}


def _alignment_request(request: dict[str, Any]) -> tuple[dict[str, Any], str, int, int]:
    report = request.get("report")
    text = request.get("text")
    if not isinstance(report, dict) or not isinstance(text, str):
        raise BridgeError("Alignment state is missing; rebuild alignment first.")
    try:
        line = int(request.get("line"))
        word_index = int(request.get("word_index"))
    except (TypeError, ValueError) as error:
        raise BridgeError("Choose one lyric word before adjusting it.") from error
    return report, text, line, word_index


def _phrase_alignment_request(request: dict[str, Any]) -> tuple[dict[str, Any], str, int]:
    report = request.get("report")
    text = request.get("text")
    if not isinstance(report, dict) or not isinstance(text, str):
        raise BridgeError("Alignment state is missing; rebuild alignment first.")
    try:
        line = int(request.get("line"))
    except (TypeError, ValueError) as error:
        raise BridgeError("Choose one lyric phrase before adjusting it.") from error
    return report, text, line


def handle(request: dict[str, Any]) -> Any:
    command = request.get("command")
    if command == "list_songs":
        song_dir = REPO_ROOT / "songs"
        return sorted(path.name for path in song_dir.iterdir() if path.is_dir() and (path / "settings.yaml").is_file())

    song = _song_name(request.get("song"))
    if command == "inspect_song":
        return _jsonable(inspect_song(REPO_ROOT, song))

    role = _role(song, request.get("role"))
    if command == "read_transcript":
        return _read_source(song, role)
    if command == "save_transcript":
        return _write_transcript(song, role, request.get("text"))
    if command == "validate_transcript":
        return _validate_transcript(request.get("text"))
    if command == "draft":
        return _draft(song, role, request.get("text"), request.get("mode"), request.get("auto_lines"))
    if command == "align":
        return _align(song, role)
    if command == "render":
        return _render(song)
    if command == "prepare_midi_preview":
        return _prepare_midi_preview(song, role)
    if command == "apply_alignment":
        return _apply_alignment(song, role, request.get("text"))
    if command == "resize_alignment":
        report, text, line, word_index = _alignment_request(request)
        try:
            updated_report, updated_text = resize_alignment_token(
                report,
                text,
                line,
                word_index,
                str(request.get("edge")),
                int(request.get("movement")),
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        return _write_alignment(song, role, updated_report, updated_text)
    if command == "resize_phrase":
        report, text, line = _phrase_alignment_request(request)
        try:
            updated_report, updated_text = resize_alignment_phrase(
                report,
                text,
                line,
                str(request.get("edge")),
                int(request.get("movement")),
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        return _write_alignment(song, role, updated_report, updated_text)
    if command == "insert_alignment":
        report, text, line, word_index = _alignment_request(request)
        try:
            updated_report, updated_text, selected = insert_alignment_token(
                report,
                text,
                line,
                word_index,
                str(request.get("word") or ""),
                str(request.get("position") or "after"),
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        result = _write_alignment(song, role, updated_report, updated_text)
        result["selected"] = {"line": selected[0], "word_index": selected[1]}
        return result
    raise BridgeError(f"Unknown Choir Studio command: {command}")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise BridgeError("Bridge request must be a JSON object.")
        print(json.dumps({"ok": True, "data": handle(payload)}))
    except (BridgeError, OSError, SystemExit, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
