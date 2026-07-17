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
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any
import uuid

import mido


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSISTANT_DIR = REPO_ROOT / "tools" / "lyric_sync_assistant"
for directory in (REPO_ROOT, ASSISTANT_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import pyFuncs.PhonemeProcessing as phonemes
from pyFuncs.ChoirInspection import _has_lyric_content, _lyric_conversion_issue, inspect_song
from pyFuncs.MidiPreview import write_single_track_preview
from pyFuncs.SongPaths import lyrics_directory, outputs_directory
from assistant import (
    normalize_placeholder_word,
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
    add_virtual_note_split,
    apply_timed_alignment_template,
    adjust_alignment_token_note_count,
    build_alignment,
    delete_alignment_token,
    insert_alignment_token,
    reorder_alignment_token,
    resize_alignment_phrase,
    resize_alignment_token,
    toggle_alignment_token_mode,
)
from tools.split_polyphonic_midi import (
    MidiSplitError,
    max_polyphony,
    parse_track,
    split_into_lanes,
    split_midi,
    write_summary,
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
    lyric_dir = lyrics_directory(song_dir)
    source = lyric_dir / f"{track['lyrics_filename']}.txt"
    raw = lyric_dir / f"{track['lyrics_filename']}.raw.txt"
    draft_dir = outputs_directory(song_dir) / "lyrics_drafts"
    candidate = draft_dir / f"{role}.txt"
    report = draft_dir / f"{role}.json"
    return source, raw, candidate, report


def _legacy_transcript_path(song: str, role: str) -> Path:
    return outputs_directory(REPO_ROOT / "songs" / song) / "lyrics_drafts" / f"{role}.transcript.txt"


def _read_source(song: str, role: str) -> dict[str, Any]:
    source, raw, candidate, _ = _role_paths(song, role)
    # The Studio editor is the active lyric draft. Prefer its aligned candidate so
    # returning to Lyrics always shows the text that Review and rendering use.
    active = next(
        (
            path
            for path in (candidate, raw, _legacy_transcript_path(song, role), source)
            if path.is_file() and (path != candidate or _has_lyric_content(path))
        ),
        source,
    )
    try:
        text = active.read_text(encoding="utf-8") if active.is_file() else ""
    except OSError as error:
        raise BridgeError(f"Could not read transcript: {error}") from error
    return {
        "text": text,
        "path": str(active),
        "kind": "candidate" if active == candidate else "transcript",
        "candidate_exists": _has_lyric_content(candidate),
    }


def _write_transcript(song: str, role: str, text: object) -> dict[str, str]:
    _, raw, _, _ = _role_paths(song, role)
    cleaned = str(text or "").replace("\r\n", "\n")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(cleaned.rstrip() + "\n" if cleaned.strip() else "", encoding="utf-8")
    return {"path": str(raw), "text": cleaned}


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


def _create_note_skeleton(song: str, role: str, placeholder: object) -> dict[str, Any]:
    """Create editable note-per-syllable text without changing an alignment candidate."""

    try:
        normalized = normalize_placeholder_word(str(placeholder or "duw"))
    except ValueError as error:
        raise BridgeError(str(error)) from error
    song_dir, settings = load_settings(song)
    track = resolve_track(settings, role)
    notes, beat_ms, _, _ = load_track_notes(song_dir, settings, track["track_filename"])
    args = type("DraftArgs", (), {"phrase_gap_ms": None, "word_gap_ms": None, "tight_gap_ms": None})()
    phrase_gap_ms, _, _ = resolve_thresholds(args, settings, beat_ms)
    proposed_text = "\n".join(
        render_placeholder_draft(notes, split_note_phrases(notes, phrase_gap_ms), normalized, include_comments=False)
    ).rstrip() + "\n"
    return {
        "text": proposed_text,
        "note_count": len(notes),
    }


def _draft(song: str, role: str, text: object, auto_lines: object) -> dict[str, Any]:
    _, raw, draft_path, report_path = _role_paths(song, role)
    # Keep the editable pre-draft text as a recovery source, then write the
    # generated timing as the one active candidate used by Review and choir.py.
    _write_transcript(song, role, text)
    song_dir, settings = load_settings(song)
    track = resolve_track(settings, role)
    notes, beat_ms, _, _ = load_track_notes(song_dir, settings, track["track_filename"])
    args = type("DraftArgs", (), {"phrase_gap_ms": None, "word_gap_ms": None, "tight_gap_ms": None})()
    phrase_gap_ms, word_gap_ms, tight_gap_ms = resolve_thresholds(args, settings, beat_ms)
    phrases = split_note_phrases(notes, phrase_gap_ms)
    warnings: list[str] = []
    transcript_lines = read_transcript_lines(raw)
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
        "source_path": str(raw),
    }


def _align(song: str, role: str) -> dict[str, Any]:
    _, _, draft_path, _ = _role_paths(song, role)
    if not draft_path.is_file():
        raise BridgeError("Draft lyrics before starting alignment.")
    report, lines = build_alignment(song, role, draft_path)
    text = "\n".join(lines).rstrip() + "\n"
    return _write_candidate_alignment(song, role, report, text)


def _update_render_enabled_roles(song: str, requested_roles: object) -> dict[str, Any]:
    """Persist Studio render selection without allowing incomplete roles into a run."""
    if not isinstance(requested_roles, list):
        raise BridgeError("Render roles must be a list of configured role names.")
    selected_roles = list(dict.fromkeys(str(role) for role in requested_roles if str(role)))
    song_dir, settings = load_settings(song)
    configured_roles = list((settings.get("Tracks") or {}).keys())
    unknown_roles = [role for role in selected_roles if role not in configured_roles]
    if unknown_roles:
        raise BridgeError(f"Unknown render role(s): {', '.join(unknown_roles)}")
    inspection = inspect_song(REPO_ROOT, song, include_audio=False)
    eligible_roles = {item.role for item in inspection.roles if item.render_eligible}
    ineligible_roles = [role for role in selected_roles if role not in eligible_roles]
    if ineligible_roles:
        raise BridgeError(f"Only roles with valid MIDI and lyric or note-skeleton content can be enabled: {', '.join(ineligible_roles)}")
    settings_path = song_dir / "settings.yaml"
    for role in configured_roles:
        _replace_role_setting(settings_path, role, "RENDER_ENABLED", role in selected_roles)
    return {"settings_path": str(settings_path), "enabled_roles": selected_roles}


def _visual_triplet(value: object, label: str, lower: float, upper: float) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise BridgeError(f"{label} must contain exactly three numeric values.")
    try:
        parsed = [float(item) for item in value]
    except (TypeError, ValueError) as error:
        raise BridgeError(f"{label} must contain exactly three numeric values.") from error
    if any(item < lower or item > upper for item in parsed):
        raise BridgeError(f"{label} values must be between {lower:g} and {upper:g}.")
    return parsed


def _replace_role_setting(path: Path, role: str, key: str, value: str) -> None:
    """Update one known per-role YAML setting without reformatting unrelated settings."""

    try:
        with path.open("r", encoding="utf-8", newline="") as settings_file:
            text = settings_file.read()
    except OSError as error:
        raise BridgeError(f"Could not read settings.yaml: {error}") from error
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    role_pattern = re.compile(rf"^  {re.escape(role)}:\s*(?:#.*)?(?:\r?\n)?$")
    role_index = next((index for index, line in enumerate(lines) if role_pattern.match(line)), None)
    if role_index is None:
        raise BridgeError(f"Could not locate role {role!r} in settings.yaml without rewriting it.")
    section_end = len(lines)
    for index in range(role_index + 1, len(lines)):
        if re.match(r"^  \S", lines[index]):
            section_end = index
            break
    setting_pattern = re.compile(rf"^    {re.escape(key)}:\s*")
    setting_index = next(
        (index for index in range(role_index + 1, section_end) if setting_pattern.match(lines[index])),
        None,
    )
    rendered = f"    {key}: {value}{newline}"
    if setting_index is None:
        lines.insert(section_end, rendered)
    else:
        lines[setting_index] = rendered
    updated = "".join(lines)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as settings_file:
            settings_file.write(updated)
        temporary.replace(path)
    except OSError as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise BridgeError(f"Could not save visualizer settings: {error}") from error


def _replace_role_mapping(
    path: Path,
    role: str,
    key: str,
    values: dict[str, object],
    remove_keys: set[str] | None = None,
) -> None:
    """Replace one nested role mapping while preserving unrelated YAML text."""

    try:
        with path.open("r", encoding="utf-8", newline="") as settings_file:
            text = settings_file.read()
    except OSError as error:
        raise BridgeError(f"Could not read settings.yaml: {error}") from error
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    role_pattern = re.compile(rf"^  {re.escape(role)}:\s*(?:#.*)?(?:\r?\n)?$")
    role_index = next((index for index, line in enumerate(lines) if role_pattern.match(line)), None)
    if role_index is None:
        raise BridgeError(f"Could not locate role {role!r} in settings.yaml without rewriting it.")
    section_end = next(
        (index for index in range(role_index + 1, len(lines)) if re.match(r"^  \S", lines[index])),
        len(lines),
    )
    if remove_keys:
        remove_pattern = re.compile(rf"^    (?:{'|'.join(re.escape(item) for item in sorted(remove_keys))}):\s*")
        lines = [line for index, line in enumerate(lines) if not (role_index < index < section_end and remove_pattern.match(line))]
        section_end = next(
            (index for index in range(role_index + 1, len(lines)) if re.match(r"^  \S", lines[index])),
            len(lines),
        )
    mapping_pattern = re.compile(rf"^    {re.escape(key)}:\s*(?:#.*)?(?:\r?\n)?$")
    mapping_index = next(
        (index for index in range(role_index + 1, section_end) if mapping_pattern.match(lines[index])),
        None,
    )
    rendered = [f"    {key}:{newline}"] + [
        f"      {child_key}: {_format_setting_value(value)}{newline}"
        for child_key, value in values.items()
    ] + [newline]
    if mapping_index is None:
        lines[section_end:section_end] = rendered
    else:
        mapping_end = next(
            (index for index in range(mapping_index + 1, section_end) if re.match(r"^    \S", lines[index])),
            section_end,
        )
        lines[mapping_index:mapping_end] = rendered
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as settings_file:
            settings_file.write("".join(lines))
        temporary.replace(path)
    except OSError as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise BridgeError(f"Could not save visualizer settings: {error}") from error


VISUAL_TEXT_POSITIONS = frozenset({
    "top-left", "top-center", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
})
VISUAL_FONTS = frozenset({"choir", "sans", "serif", "mono"})


def _visual_text_position(value: object, label: str, default: str) -> str:
    position = str(value or default).strip().lower()
    if position not in VISUAL_TEXT_POSITIONS:
        raise BridgeError(f"{label} must be a supported position anchor.")
    return position


def _visual_font(value: object, label: str) -> str:
    font = str(value or "choir").strip().lower()
    if font not in VISUAL_FONTS:
        raise BridgeError(f"{label} must be one of: {', '.join(sorted(VISUAL_FONTS))}.")
    return font


def _visual_font_size(value: object, label: str, default: float) -> float:
    try:
        size = float(default if value is None else value)
    except (TypeError, ValueError) as error:
        raise BridgeError(f"{label} must be a number from 2 to 25 percent.") from error
    if not 2 <= size <= 25:
        raise BridgeError(f"{label} must be from 2 to 25 percent.")
    return size


def _save_visual_layout(song: str, role: str, position: object, hsb: object, options: object = None) -> dict[str, Any]:
    size, left, top = _visual_triplet(position, "Visualizer position", 0.0, 1.0)
    if size <= 0.0:
        raise BridgeError("Visualizer size must be greater than zero.")
    if left + size > 1.0 or top + size > 1.0:
        raise BridgeError("Visualizer position plus size must stay within the video frame.")
    hue, saturation, brightness = _visual_triplet(hsb, "Visualizer color", 0.0, 360.0)
    if saturation > 100.0 or brightness > 100.0:
        raise BridgeError("Visualizer saturation and brightness must be between 0 and 100.")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise BridgeError("Visualizer text options must be an object.")
    label = str(options.get("label", role)).strip() or role
    if len(label) > 80 or "\n" in label or "\r" in label:
        raise BridgeError("Visualizer label must be one line and no more than 80 characters.")
    spectrogram = {
        "COLOR_HSB": [hue, saturation, brightness],
        "POSITION": [size, left, top],
        "LABEL": label,
        "LABEL_ENABLED": bool(options.get("label_enabled", False)),
        "LABEL_POSITION": _visual_text_position(options.get("label_position"), "Label position", "top-left"),
        "LABEL_SHOW_VOICE": bool(options.get("label_show_voice", False)),
        "LABEL_SHOW_HEAD_SIZE": bool(options.get("label_show_head_size", False)),
        "LABEL_FONT": _visual_font(options.get("label_font"), "Label font"),
        "LABEL_FONT_SIZE_PERCENT": _visual_font_size(options.get("label_font_size_percent"), "Label font size", 7),
        "CURRENT_WORD_ENABLED": bool(options.get("current_word_enabled", False)),
        "CURRENT_WORD_POSITION": _visual_text_position(options.get("current_word_position"), "Current-word position", "bottom-center"),
        "CURRENT_WORD_FONT": _visual_font(options.get("current_word_font"), "Current-word font"),
        "CURRENT_WORD_FONT_SIZE_PERCENT": _visual_font_size(options.get("current_word_font_size_percent"), "Current-word font size", 10),
        "CURRENT_WORD_USE_TRACK_COLOR": bool(options.get("current_word_use_track_color", False)),
    }
    song_dir, _ = load_settings(song)
    settings_path = song_dir / "settings.yaml"
    _replace_role_mapping(
        settings_path,
        role,
        "SPECTROGRAM",
        spectrogram,
        remove_keys={
            "VID_HSB", "VID_Position", "VID_Label", "VID_LabelEnabled",
            "VID_LabelPosition", "VID_LabelShowVoice", "VID_LabelShowHeadSize",
            "VID_CurrentWordEnabled", "VID_CurrentWordPosition",
            "VID_LabelTime", "VID_LabelDur", "VID_LabelFade",
        },
    )
    return {"settings_path": str(settings_path), "position": [size, left, top], "hsb": [hue, saturation, brightness], "options": spectrogram}


TRACK_TUNING_DEFAULTS = {
    "VOICE": None,
    "HEAD_SIZE": None,
    "PITCH_SHIFT": 0,
    "OCTAVE_BOOST": 0,
    "PITCH_WRAP_SHIFT": None,
    "VOLUME_ADJUST_DB": 0.0,
    "IGNORE_MIDI_VELOCITY": True,
    "VELOCITY_VOLUME_SCALE_DB": 0.0,
    "PITCH_VOLUME_BOOST_START": 0,
    "PITCH_VOLUME_BOOST_DB_PER_SEMITONE": 0.0,
    "PITCH_VOLUME_BOOST_MAX_DB": 6.0,
    "NOTE_NORMALIZE_TARGET_DBFS": "auto",
    "NOTE_NORMALIZE_MAX_BOOST_DB": 0.0,
    "NOTE_NORMALIZE_PEAK_CEILING_DBFS": -1.0,
    "STEM_PEAK_CEILING_DBFS": -1.0,
    "GAP_MEND_MS": 0.0,
}
DECTALK_VOICE_CODES = frozenset({"np", "nb", "nh", "nd", "nf", "nu", "nr", "nw", "nk"})
TRACK_TUNING_TOP_LEVEL_KEYS = {
    "PITCH_VOLUME_BOOST_START": "pitchVolumeBoostStart",
    "PITCH_VOLUME_BOOST_DB_PER_SEMITONE": "pitchVolumeBoostDbPerSemitone",
    "PITCH_VOLUME_BOOST_MAX_DB": "pitchVolumeBoostMaxDb",
    "NOTE_NORMALIZE_TARGET_DBFS": "noteNormalizeTargetDbfs",
    "NOTE_NORMALIZE_MAX_BOOST_DB": "noteNormalizeMaxBoostDb",
    "NOTE_NORMALIZE_PEAK_CEILING_DBFS": "noteNormalizePeakCeilingDbfs",
    "STEM_PEAK_CEILING_DBFS": "stemPeakCeilingDbfs",
    "IGNORE_MIDI_VELOCITY": "ignoreMidiVelocity",
    "VELOCITY_VOLUME_SCALE_DB": "velocityVolumeScaleDb",
    "GAP_MEND_MS": "gapMendMs",
}

# Measured from the raw [:np] head-size reference at pitches G3 through E4.
# Values align each head size to hs 110 before note-level correction is applied.
HEAD_SIZE_VOLUME_BASELINES = ((80, 4.5), (95, 2.9), (110, 0.0), (125, -4.5), (140, -11.4))
AUTO_NORMALIZE_TUNING = {
    "PITCH_VOLUME_BOOST_START": 15,
    "PITCH_VOLUME_BOOST_DB_PER_SEMITONE": 1.7,
    "PITCH_VOLUME_BOOST_MAX_DB": 12.5,
    "NOTE_NORMALIZE_TARGET_DBFS": "auto",
    "NOTE_NORMALIZE_MAX_BOOST_DB": 5.0,
}


def _interpolate_head_size_baseline(head_size: int) -> float:
    first_size, first_gain = HEAD_SIZE_VOLUME_BASELINES[0]
    last_size, last_gain = HEAD_SIZE_VOLUME_BASELINES[-1]
    if head_size <= first_size:
        return first_gain
    if head_size >= last_size:
        return last_gain
    for (lower_size, lower_gain), (upper_size, upper_gain) in zip(HEAD_SIZE_VOLUME_BASELINES, HEAD_SIZE_VOLUME_BASELINES[1:]):
        if lower_size <= head_size <= upper_size:
            fraction = (head_size - lower_size) / (upper_size - lower_size)
            return lower_gain + (upper_gain - lower_gain) * fraction
    return 0.0


def _head_size_from_setup(setup: object) -> int | None:
    match = re.search(r"\[:dv\s+hs\s+(\d+)\]", str(setup or "").lower())
    return int(match.group(1)) if match else None


def _voice_from_setup(setup: object) -> str | None:
    match = re.search(r"\[:n([a-z])\]", str(setup or "").lower())
    if not match:
        return None
    voice = f"n{match.group(1)}"
    return voice if voice in DECTALK_VOICE_CODES else None


def _voice_setting(value: object) -> str | None:
    if value is None or value == "":
        return None
    voice = str(value).strip().lower()
    if voice not in DECTALK_VOICE_CODES:
        choices = ", ".join(f"[:{item}]" for item in sorted(DECTALK_VOICE_CODES))
        raise BridgeError(f"VOICE must be one of: {choices}, or DECtalk default.")
    return voice


def _setup_with_voice(setup: object, voice: str | None) -> str:
    current = str(setup or "").strip()
    pattern = r"\[:n[a-z]\]"
    if voice is None:
        return re.sub(pattern, "", current, flags=re.IGNORECASE)
    replacement = f"[:{voice}]"
    if re.search(pattern, current, flags=re.IGNORECASE):
        return re.sub(pattern, replacement, current, count=1, flags=re.IGNORECASE)
    return f"{replacement}{current}"


def _setup_with_head_size(setup: object, head_size: int) -> str:
    current = str(setup or "").strip()
    replacement = f"[:dv hs {head_size}]"
    if re.search(r"\[:dv\s+hs\s+\d+\]", current, flags=re.IGNORECASE):
        return re.sub(r"\[:dv\s+hs\s+\d+\]", replacement, current, flags=re.IGNORECASE)
    return f"{current}{replacement}"


def _auto_normalize_tuning(
    song: str,
    role: str,
    requested_head_size: object = None,
    requested_voice: object = None,
) -> dict[str, Any]:
    _, settings = load_settings(song)
    track = settings["Tracks"].get(role) or {}
    setup = str(track.get("DEC_SETUP", "")).lower()
    voice = _voice_from_setup(setup) if requested_voice is None else _voice_setting(requested_voice)
    if voice != "np":
        return {
            "supported": False,
            "head_size": None,
            "message": "Auto-normalize is measured only for Perfect Paul ([:np]). Choose [:np] or tune this voice manually.",
            "values": None,
        }
    if requested_head_size is None:
        head_size = _head_size_from_setup(setup)
    else:
        head_size = _number_setting(requested_head_size, "HEAD_SIZE", 40, 200, integer=True)
    if head_size is None:
        return {
            "supported": False,
            "head_size": None,
            "message": "Auto-normalize needs a [:dv hs N] value in DEC_SETUP.",
            "values": None,
        }

    values = _track_tuning(song, role)
    values.update(AUTO_NORMALIZE_TUNING)
    values["VOICE"] = voice
    values["HEAD_SIZE"] = head_size
    values["VOLUME_ADJUST_DB"] = round(_interpolate_head_size_baseline(head_size), 1)
    return {
        "supported": True,
        "head_size": head_size,
        "message": (
            f"Measured [:np] baseline for head size {head_size}: "
            f"{values['VOLUME_ADJUST_DB']:+.1f} dB stem gain, then pitch and note normalization."
        ),
        "values": values,
    }


def _track_tuning(song: str, role: str) -> dict[str, Any]:
    _, settings = load_settings(song)
    track = settings["Tracks"].get(role) or {}
    values = dict(TRACK_TUNING_DEFAULTS)
    values.update({key: track[key] for key in values if key in track})
    values["VOICE"] = _voice_from_setup(track.get("DEC_SETUP"))
    values["HEAD_SIZE"] = _head_size_from_setup(track.get("DEC_SETUP"))
    for key, settings_key in TRACK_TUNING_TOP_LEVEL_KEYS.items():
        if key not in track and settings_key in settings:
            values[key] = settings[settings_key]
    return values


def _number_setting(value: object, key: str, lower: float, upper: float, integer: bool = False) -> int | float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise BridgeError(f"{key} must be a number.") from error
    if not math.isfinite(parsed) or parsed < lower or parsed > upper:
        raise BridgeError(f"{key} must be between {lower:g} and {upper:g}.")
    if integer:
        if not parsed.is_integer():
            raise BridgeError(f"{key} must be a whole number.")
        return int(parsed)
    return parsed


def _format_setting_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_setting_value(item) for item in value) + "]"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _update_track_tuning(song: str, role: str, requested: object) -> dict[str, Any]:
    if not isinstance(requested, dict):
        raise BridgeError("Track tuning values must be an object.")
    unknown = set(requested) - set(TRACK_TUNING_DEFAULTS)
    if unknown:
        raise BridgeError(f"Unknown track tuning setting(s): {', '.join(sorted(unknown))}")

    values = _track_tuning(song, role)
    for key, value in requested.items():
        if key == "VOICE":
            values[key] = _voice_setting(value)
        elif key == "HEAD_SIZE":
            values[key] = None if value is None or value == "" else _number_setting(value, key, 40, 200, integer=True)
        elif key == "PITCH_SHIFT":
            values[key] = _number_setting(value, key, -24, 24, integer=True)
        elif key == "OCTAVE_BOOST":
            values[key] = _number_setting(value, key, -48, 48, integer=True)
        elif key == "PITCH_WRAP_SHIFT":
            if value is None or value == "auto":
                values[key] = None
            else:
                parsed = _number_setting(value, key, -48, 48, integer=True)
                if parsed % 12:
                    raise BridgeError("PITCH_WRAP_SHIFT must be a whole octave or Auto.")
                values[key] = parsed
        elif key == "VOLUME_ADJUST_DB":
            values[key] = _number_setting(value, key, -24.0, 24.0)
        elif key == "IGNORE_MIDI_VELOCITY":
            if not isinstance(value, bool):
                raise BridgeError("IGNORE_MIDI_VELOCITY must be true or false.")
            values[key] = value
        elif key == "VELOCITY_VOLUME_SCALE_DB":
            values[key] = _number_setting(value, key, 0.0, 24.0)
        elif key == "PITCH_VOLUME_BOOST_START":
            values[key] = _number_setting(value, key, 0, 36, integer=True)
        elif key in {"PITCH_VOLUME_BOOST_DB_PER_SEMITONE", "PITCH_VOLUME_BOOST_MAX_DB", "NOTE_NORMALIZE_MAX_BOOST_DB"}:
            values[key] = _number_setting(value, key, 0.0, 24.0)
        elif key in {"NOTE_NORMALIZE_PEAK_CEILING_DBFS", "STEM_PEAK_CEILING_DBFS"}:
            values[key] = _number_setting(value, key, -60.0, 0.0)
        elif key == "NOTE_NORMALIZE_TARGET_DBFS":
            if value == "auto":
                values[key] = "auto"
            else:
                values[key] = _number_setting(value, key, -60.0, -1.0)
        elif key == "GAP_MEND_MS":
            values[key] = _number_setting(value, key, 0.0, 100.0)

    song_dir, _ = load_settings(song)
    settings_path = song_dir / "settings.yaml"
    _, settings = load_settings(song)
    setup = (settings.get("Tracks") or {}).get(role, {}).get("DEC_SETUP", "")
    update_setup = False
    if "VOICE" in requested:
        setup = _setup_with_voice(setup, values["VOICE"])
        update_setup = True
    if values["HEAD_SIZE"] is not None:
        setup = _setup_with_head_size(setup, values["HEAD_SIZE"])
        update_setup = True
    if update_setup:
        _replace_role_setting(settings_path, role, "DEC_SETUP", _format_setting_value(setup))
    for key, value in values.items():
        if key in {"VOICE", "HEAD_SIZE"}:
            continue
        _replace_role_setting(settings_path, role, key, _format_setting_value(value))
    return {"settings_path": str(settings_path), "values": values}


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


def _polyphonic_split_preview(song: str, role: str) -> dict[str, Any]:
    """Analyze one configured MIDI track without writing any song artifacts."""

    inspection = inspect_song(REPO_ROOT, song)
    inspected_role = next((item for item in inspection.roles if item.role == role), None)
    if not inspection.midi_path or not inspected_role or not inspected_role.midi_track:
        raise BridgeError("The selected role has no MIDI track to split.")
    try:
        source_midi = mido.MidiFile(inspection.midi_path)
        source_track = inspected_role.midi_track
        analysis = parse_track(source_midi.tracks[source_track.index], source_track.index)
        lanes = split_into_lanes(analysis.notes)
    except (OSError, EOFError, ValueError, MidiSplitError) as error:
        raise BridgeError(f"Could not analyze the selected MIDI track: {error}") from error

    lane_count = len(lanes)
    return {
        "source_path": str(inspection.midi_path),
        "source_name": analysis.source_name,
        "track_index": analysis.source_index,
        "note_count": len(analysis.notes),
        "max_polyphony": max_polyphony(analysis.notes),
        "default_filename": f"{inspection.midi_path.stem}_monophonic.mid",
        "lanes": [
            {
                "number": lane_number,
                "name": analysis.source_name if lane_number == 1 else f"{analysis.source_name} - Voice {lane_number}",
                "note_count": len(lane.notes),
                "minimum_pitch": min((note.note for note in lane.notes), default=None),
                "maximum_pitch": max((note.note for note in lane.notes), default=None),
            }
            for lane_number, lane in enumerate(lanes, start=1)
        ],
        "splittable": lane_count > 1,
    }


def _safe_midi_filename(value: object) -> str:
    filename = str(value or "").strip()
    if not filename or Path(filename).name != filename:
        raise BridgeError("Choose a MIDI filename, not a folder path.")
    if Path(filename).suffix.lower() not in {".mid", ".midi"}:
        raise BridgeError("The split output filename must end in .mid or .midi.")
    if not re.fullmatch(r"[A-Za-z0-9_. -]+", filename):
        raise BridgeError("The split output filename contains unsupported characters.")
    return filename


def _export_polyphonic_split(
    song: str,
    role: str,
    filename: object,
    replace_source: bool,
    confirm_overwrite: bool,
) -> dict[str, Any]:
    """Split one configured track, preserving every other source MIDI track."""

    preview = _polyphonic_split_preview(song, role)
    if not preview["splittable"]:
        raise BridgeError("This MIDI track is already monophonic and does not need splitting.")

    source = Path(preview["source_path"])
    if replace_source:
        output = source.with_name(f".{source.stem}.split-{uuid.uuid4().hex}.mid")
    else:
        output = source.parent / _safe_midi_filename(filename)
        if output.resolve() == source.resolve():
            raise BridgeError("Use Replace working MIDI to update the active source safely.")
        if output.exists() and not confirm_overwrite:
            raise BridgeError(f"{output.name} already exists. Confirm overwrite or choose another filename.")

    staging_output = output
    summary = output.with_name(f"{output.stem}_split_summary.txt")
    backup: Path | None = None
    replaced = False
    try:
        mappings = split_midi(source, output, target_track_indices=[int(preview["track_index"])])
        if replace_source:
            backup = source.with_name(f"{source.name}.bak")
            if not backup.exists():
                shutil.copy2(source, backup)
            os.replace(output, source)
            output = source
            replaced = True
            summary = source.with_name(f"{source.stem}_split_summary.txt")
            _, settings = load_settings(song)
            for affected_role in (settings.get("Tracks") or {}):
                if resolve_track(settings, str(affected_role))["track_filename"] != preview["source_name"]:
                    continue
                _, _, _, report_path = _role_paths(song, str(affected_role))
                report_path.unlink(missing_ok=True)
                source_sidecar = lyrics_directory(REPO_ROOT / "songs" / song) / ".alignment" / f"{affected_role}.json"
                source_sidecar.unlink(missing_ok=True)
    except (OSError, ValueError, MidiSplitError) as error:
        if not replaced:
            staging_output.unlink(missing_ok=True)
        raise BridgeError(f"MIDI split failed: {error}") from error

    summary_warning: str | None = None
    try:
        write_summary(source, output, mappings, summary)
    except OSError as error:
        summary_warning = f"The MIDI was split, but its text summary could not be written: {error}"

    return {
        "path": str(output),
        "summary_path": str(summary),
        "backup_path": str(backup) if backup else None,
        "replaced_source": replace_source,
        "lanes": preview["lanes"],
        "warning": summary_warning,
    }


def _write_candidate_alignment(song: str, role: str, report: dict[str, Any], text: str) -> dict[str, Any]:
    _, _, candidate_path, report_path = _role_paths(song, role)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_text = text.rstrip() + "\n"
    candidate_path.write_text(normalized_text, encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"text": normalized_text, "path": str(candidate_path), "report": report, "report_path": str(report_path)}


def _load_candidate(song: str, role: str) -> dict[str, Any]:
    """Return the durable working alignment, rebuilding only missing review metadata."""

    source_path, _, candidate_path, report_path = _role_paths(song, role)
    candidate_has_content = _has_lyric_content(candidate_path)
    if not candidate_has_content and not _has_lyric_content(source_path):
        return {"exists": False}
    alignment_path = candidate_path if candidate_has_content else source_path
    try:
        text = alignment_path.read_text(encoding="utf-8")
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if candidate_has_content and report_path.is_file()
            else None
        )
    except (OSError, json.JSONDecodeError) as error:
        raise BridgeError(f"Could not load lyric candidate: {error}") from error
    if not isinstance(report, dict):
        report, lines = build_alignment(song, role, alignment_path)
        text = "\n".join(lines).rstrip() + "\n"
        return {"exists": True, **_write_candidate_alignment(song, role, report, text)}
    return {"exists": True, "text": text, "path": str(candidate_path), "report": report, "report_path": str(report_path)}


def _alignment_template_sources(song: str, role: str) -> list[dict[str, str]]:
    """List saved aligned candidates that share the target's lyric source."""

    _, settings = load_settings(song)
    target = resolve_track(settings, role)
    sources: list[dict[str, str]] = []
    for candidate_role in (settings.get("Tracks") or {}):
        candidate_role = str(candidate_role)
        if candidate_role == role:
            continue
        candidate_track = resolve_track(settings, candidate_role)
        if candidate_track["lyrics_filename"] != target["lyrics_filename"]:
            continue
        _, _, candidate_path, report_path = _role_paths(song, candidate_role)
        if candidate_path.is_file() and report_path.is_file():
            sources.append({"role": candidate_role, "path": str(candidate_path)})
    return sources


def _load_alignment_workspace(song: str, role: str) -> dict[str, Any]:
    """Load the durable candidate and compatible templates in one bridge process."""

    return {
        "candidate": _load_candidate(song, role),
        "templates": _alignment_template_sources(song, role),
    }


def _copy_alignment_template(song: str, role: str, source_role: object) -> dict[str, Any]:
    source_role = _role(song, source_role)
    if source_role == role:
        raise BridgeError("Choose a different role as the alignment template.")
    _, settings = load_settings(song)
    target_track = resolve_track(settings, role)
    source_track = resolve_track(settings, source_role)
    if source_track["lyrics_filename"] != target_track["lyrics_filename"]:
        raise BridgeError("Alignment templates can only be shared by roles with the same lyric source.")
    _, _, source_candidate, source_report_path = _role_paths(song, source_role)
    if not source_candidate.is_file() or not source_report_path.is_file():
        raise BridgeError(f"{source_role} has no saved aligned candidate to copy.")
    try:
        source_text = source_candidate.read_text(encoding="utf-8")
        source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BridgeError(f"Could not read {source_role}'s alignment template: {error}") from error
    if not isinstance(source_report, dict):
        raise BridgeError(f"{source_role}'s alignment template is invalid.")

    _, _, target_candidate, target_report_path = _role_paths(song, role)
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as temporary:
            temporary.write(source_text)
            temporary_path = Path(temporary.name)
        target_report, _ = build_alignment(song, role, temporary_path)
    except (OSError, ValueError, SystemExit) as error:
        raise BridgeError(f"Could not prepare {role} for template timing: {error}") from error
    finally:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)
    target_report["draft_source"] = str(target_candidate)
    try:
        updated_report, updated_text = apply_timed_alignment_template(
            source_report,
            source_text,
            target_report,
            source_role,
        )
    except ValueError as error:
        raise BridgeError(str(error)) from error

    backup_path: Path | None = None
    if target_candidate.is_file() and not target_report_path.is_file():
        # A text-only candidate has no report, so retain it before creating one.
        backup_path = target_candidate.with_name(f"{target_candidate.stem}.before-template.txt")
    elif target_candidate.is_file():
        backup_path = target_candidate.with_name(f"{target_candidate.stem}.before-template.txt")
    if backup_path and not backup_path.exists():
        try:
            backup_path.write_text(target_candidate.read_text(encoding="utf-8"), encoding="utf-8")
            if target_report_path.is_file():
                target_report_path.with_name(f"{target_report_path.stem}.before-template.json").write_text(
                    target_report_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
        except OSError as error:
            raise BridgeError(f"Could not back up the existing alignment candidate: {error}") from error
    result = _write_candidate_alignment(song, role, updated_report, updated_text)
    result["source_role"] = source_role
    result["backup_path"] = str(backup_path) if backup_path else None
    return result


def _word_cues_from_report(report: dict[str, Any]) -> list[dict[str, int | str]]:
    token_words = {
        (item.get("line"), item.get("word_index")): str(item.get("word", "")).strip()
        for item in report.get("token_counts", [])
        if isinstance(item, dict)
    }
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for entry in report.get("notes", []):
        if not isinstance(entry, dict):
            continue
        line = entry.get("line")
        word_index = entry.get("word_index")
        if isinstance(line, int) and isinstance(word_index, int):
            grouped.setdefault((line, word_index), []).append(entry)
    cues: list[dict[str, int | str]] = []
    for key, entries in sorted(grouped.items(), key=lambda item: min(float(entry.get("start_ms", 0)) for entry in item[1])):
        word = token_words.get(key) or str(entries[0].get("lyric") or "").strip()
        if not word:
            continue
        cues.append({
            "word": word,
            "start_ms": round(min(float(entry.get("start_ms", 0)) for entry in entries)),
            "end_ms": round(max(float(entry.get("end_ms", 0)) for entry in entries)),
        })
    return cues


def _apply_alignment(song: str, role: str, text: object) -> dict[str, str | None]:
    """Validate and apply a saved alignment to the compiler's configured input."""

    _, _, candidate_path, report_path = _role_paths(song, role)
    if not candidate_path.is_file():
        raise BridgeError("No lyric candidate exists. Draft lyrics before applying them.")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BridgeError(f"Could not read alignment state: {error}") from error
    missing_note_words = int(report.get("summary", {}).get("zero_note_tokens", 0))
    if missing_note_words:
        raise BridgeError(
            f"Resolve {missing_note_words} lyric word(s) without a MIDI note before applying."
        )
    try:
        normalized_text = candidate_path.read_text(encoding="utf-8").rstrip()
    except OSError as error:
        raise BridgeError(f"Could not read lyric candidate: {error}") from error
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
    sidecar = source.parent / ".alignment" / f"{role}.json"
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({
                "virtual_splits": report.get("virtual_splits", []),
                "word_cues": _word_cues_from_report(report),
            }, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise BridgeError(f"Could not apply virtual note splits: {error}") from error
    return {"path": str(source), "backup_path": str(backup) if backup else None, "alignment_path": str(sidecar)}


def _alignment_request(song: str, role: str, request: dict[str, Any]) -> tuple[dict[str, Any], str, int, int]:
    report = request.get("report")
    _, _, candidate_path, _ = _role_paths(song, role)
    if not isinstance(report, dict) or not candidate_path.is_file():
        raise BridgeError("Alignment state is missing; rebuild alignment first.")
    try:
        text = candidate_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BridgeError(f"Could not read lyric candidate: {error}") from error
    try:
        line = int(request.get("line"))
        word_index = int(request.get("word_index"))
    except (TypeError, ValueError) as error:
        raise BridgeError("Choose one lyric word before adjusting it.") from error
    return report, text, line, word_index


def _candidate_alignment_request(song: str, role: str, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    report = request.get("report")
    _, _, candidate_path, _ = _role_paths(song, role)
    if not isinstance(report, dict) or not candidate_path.is_file():
        raise BridgeError("Alignment state is missing; rebuild alignment first.")
    try:
        return report, candidate_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BridgeError(f"Could not read lyric candidate: {error}") from error


def _phrase_alignment_request(song: str, role: str, request: dict[str, Any]) -> tuple[dict[str, Any], str, int]:
    report = request.get("report")
    _, _, candidate_path, _ = _role_paths(song, role)
    if not isinstance(report, dict) or not candidate_path.is_file():
        raise BridgeError("Alignment state is missing; rebuild alignment first.")
    try:
        text = candidate_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BridgeError(f"Could not read lyric candidate: {error}") from error
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
    if command == "save_visual_layout":
        return _save_visual_layout(song, role, request.get("position"), request.get("hsb"), request.get("options"))
    if command == "get_track_tuning":
        return _track_tuning(song, role)
    if command == "get_auto_normalize_tuning":
        return _auto_normalize_tuning(song, role, request.get("head_size"), request.get("voice"))
    if command == "update_track_tuning":
        return _update_track_tuning(song, role, request.get("values"))
    if command == "update_render_enabled_roles":
        return _update_render_enabled_roles(song, request.get("roles"))
    if command == "read_transcript":
        return _read_source(song, role)
    if command == "save_transcript":
        return _write_transcript(song, role, request.get("text"))
    if command == "validate_transcript":
        return _validate_transcript(request.get("text"))
    if command == "create_note_skeleton":
        return _create_note_skeleton(song, role, request.get("placeholder"))
    if command == "draft":
        return _draft(song, role, request.get("text"), request.get("auto_lines"))
    if command == "load_alignment_workspace":
        return _load_alignment_workspace(song, role)
    if command == "copy_alignment_template":
        return _copy_alignment_template(song, role, request.get("source_role"))
    if command == "align":
        return _align(song, role)
    if command == "prepare_midi_preview":
        return _prepare_midi_preview(song, role)
    if command == "preview_polyphonic_split":
        return _polyphonic_split_preview(song, role)
    if command == "export_polyphonic_split":
        return _export_polyphonic_split(
            song,
            role,
            request.get("filename"),
            request.get("replace_source") is True,
            request.get("confirm_overwrite") is True,
        )
    if command == "apply_alignment":
        return _apply_alignment(song, role, request.get("text"))
    if command == "resize_alignment":
        report, text, line, word_index = _alignment_request(song, role, request)
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
        return _write_candidate_alignment(song, role, updated_report, updated_text)
    if command == "adjust_word_note_count":
        report, text, line, word_index = _alignment_request(song, role, request)
        try:
            updated_report, updated_text = adjust_alignment_token_note_count(
                report,
                text,
                line,
                word_index,
                int(request.get("delta")),
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        return _write_candidate_alignment(song, role, updated_report, updated_text)
    if command == "toggle_word_mode":
        report, text, line, word_index = _alignment_request(song, role, request)
        try:
            updated_report, updated_text = toggle_alignment_token_mode(report, text, line, word_index)
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        return _write_candidate_alignment(song, role, updated_report, updated_text)
    if command == "resize_phrase":
        report, text, line = _phrase_alignment_request(song, role, request)
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
        return _write_candidate_alignment(song, role, updated_report, updated_text)
    if command == "insert_alignment":
        report, text, line, word_index = _alignment_request(song, role, request)
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
        result = _write_candidate_alignment(song, role, updated_report, updated_text)
        result["selected"] = {"line": selected[0], "word_index": selected[1]}
        return result
    if command == "delete_alignment":
        if request.get("confirm_delete") is not True:
            raise BridgeError("Hold Ctrl and click the delete control to remove a lyric word.")
        report, text, line, word_index = _alignment_request(song, role, request)
        try:
            updated_report, updated_text, selected = delete_alignment_token(
                report,
                text,
                line,
                word_index,
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        result = _write_candidate_alignment(song, role, updated_report, updated_text)
        result["selected"] = {"line": selected[0], "word_index": selected[1]}
        return result
    if command == "reorder_alignment":
        report, text, line, word_index = _alignment_request(song, role, request)
        try:
            updated_report, updated_text, selected = reorder_alignment_token(
                report,
                text,
                line,
                word_index,
                int(request.get("target_word_index")),
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        result = _write_candidate_alignment(song, role, updated_report, updated_text)
        result["selected"] = {"line": selected[0], "word_index": selected[1]}
        return result
    if command == "add_virtual_split":
        report, text = _candidate_alignment_request(song, role, request)
        try:
            updated_report, updated_text = add_virtual_note_split(
                report,
                text,
                int(request.get("note_index")),
                float(request.get("fraction")),
                target_line=request.get("target_line"),
                target_word_index=request.get("target_word_index"),
            )
        except (TypeError, ValueError) as error:
            raise BridgeError(str(error)) from error
        return _write_candidate_alignment(song, role, updated_report, updated_text)
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
