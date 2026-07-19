"""Parse timed DECTalk phoneme commands into MIDI notes and aligned lyric tokens."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

import mido

import pyFuncs.PhonemeProcessing as phonemes


TOKEN_PATTERN = re.compile(
    r"([A-Za-z_]+)\s*<\s*(\d+(?:\.\d+)?)(?:\s*,\s*(-?\d+))?\s*>"
)
COMMAND_PATTERN = re.compile(r"\[:([^\]]+)\]")
TONE_PATTERN = re.compile(r"\[:(?:t|tone)\s+(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
BRACKET_PATTERN = re.compile(r"\[([^\]]*)\]")
MAX_PHRASE_NOTES = 8


class DectalkTrackImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedNote:
    start_ms: float
    end_ms: float
    dectalk_pitch: int
    midi_pitch: int
    phonemes: tuple[str, ...]
    phrase_break_before: bool = False
    event_kind: str = "phoneme"
    tone_frequency_hz: float | None = None

    @property
    def lyric_token(self) -> str:
        if self.event_kind == "tone" and self.tone_frequency_hz is not None:
            frequency = f"{self.tone_frequency_hz:g}"
            duration = f"{self.end_ms - self.start_ms:g}"
            return f"@tone({frequency},{duration})"
        return "`" + "".join(self.phonemes)


@dataclass(frozen=True)
class DectalkTrackImport:
    notes: tuple[ImportedNote, ...]
    setup: str
    lyric_text: str
    duration_ms: float


def _setup_from_commands(text: str) -> str:
    commands = []
    for command in COMMAND_PATTERN.findall(text):
        normalized = command.strip()
        if normalized.lower().startswith("phoneme"):
            continue
        command_name = normalized.lower().split(None, 1)[0]
        if command_name in {"t", "tone"} or command_name.startswith(("dial", "dia")):
            continue
        commands.append(f"[:{normalized}]")
    return "".join(commands)


def _merge_untimed_phonemes(text: str) -> str:
    """Fold natural-duration phonemes into the neighboring timed event."""

    def bare_symbols(fragment: str) -> str | None:
        if re.sub(r"[A-Za-z\s]", "", fragment):
            return None
        return "".join(re.findall(r"[A-Za-z]+", fragment))

    def require_symbols(symbols: str) -> None:
        if symbols and not phonemes.splitDirectPhonemeSyllable(symbols, strict=True):
            raise DectalkTrackImportError(f"Unsupported untimed DECTalk phoneme: {symbols}")

    def normalize_group(group_match: re.Match[str]) -> str:
        body = group_match.group(1)
        if body.lstrip().startswith(":"):
            return group_match.group(0)
        timed = list(TOKEN_PATTERN.finditer(body))
        if not timed:
            return group_match.group(0)
        symbols = [match.group(1) for match in timed]
        prefix = bare_symbols(body[:timed[0].start()])
        if prefix is None:
            return group_match.group(0)
        require_symbols(prefix)
        symbols[0] = prefix + symbols[0]
        for index in range(1, len(timed)):
            gap = body[timed[index - 1].end():timed[index].start()]
            adjacent_coda = re.match(r"[A-Za-z]+", gap)
            coda = adjacent_coda.group(0) if adjacent_coda else ""
            remainder = gap[len(coda):]
            onset = bare_symbols(remainder)
            if onset is None:
                return group_match.group(0)
            require_symbols(coda)
            require_symbols(onset)
            symbols[index - 1] += coda
            symbols[index] = onset + symbols[index]
        suffix = bare_symbols(body[timed[-1].end():])
        if suffix is None:
            return group_match.group(0)
        require_symbols(suffix)
        symbols[-1] += suffix
        normalized = "".join(
            symbol + body[match.end(1):match.end()]
            for symbol, match in zip(symbols, timed)
        )
        return f"[{normalized}]"

    return BRACKET_PATTERN.sub(normalize_group, text)


def parse_dectalk_track(
    text: str,
    note_offset: int,
    phrase_rest_ms: float = 250.0,
) -> DectalkTrackImport:
    """Parse a DECTalk timed-phoneme string without writing any song artifacts."""

    source = str(text or "").strip()
    if not source:
        raise DectalkTrackImportError("Paste a timed DECTalk phoneme string to import.")
    unsupported_events = []
    for command_match in COMMAND_PATTERN.finditer(source):
        command_name = command_match.group(1).strip().lower().split(None, 1)[0]
        if command_name.startswith(("dial", "dia")):
            unsupported_events.append(command_name)
    if unsupported_events:
        raise DectalkTrackImportError(
            "Dial and conversational event commands cannot be mapped to a musical alignment: "
            + ", ".join(sorted(set(unsupported_events)))
        )

    parse_source = _merge_untimed_phonemes(source)
    phoneme_matches = list(TOKEN_PATTERN.finditer(parse_source))
    tone_matches = list(TONE_PATTERN.finditer(parse_source))
    events = sorted(
        [(match.start(), "phoneme", match) for match in phoneme_matches]
        + [(match.start(), "tone", match) for match in tone_matches],
        key=lambda item: item[0],
    )
    if not events:
        raise DectalkTrackImportError("No timed phonemes or tone events were found.")

    first_event_offset = events[0][0]
    for command_match in COMMAND_PATTERN.finditer(parse_source):
        command_name = command_match.group(1).strip().lower().split(None, 1)[0]
        if command_match.start() > first_event_offset and command_name not in {"t", "tone"}:
            raise DectalkTrackImportError(
                f"Midstream DECTalk command ':{command_name}' is not supported; "
                "place track-wide voice and rate commands before the first timed event."
            )

    remainder = TOKEN_PATTERN.sub("", COMMAND_PATTERN.sub("", parse_source))
    remainder = remainder.replace("[", "").replace("]", "")
    if remainder.strip():
        preview = " ".join(remainder.split())[:80]
        raise DectalkTrackImportError(f"Unsupported text remains between timed phonemes: {preview}")

    cursor_ms = 0.0
    pending_break = False
    notes: list[ImportedNote] = []
    current: dict[str, object] | None = None
    previous_match_end = 0
    previous_pitch: int | None = None

    def finish_note() -> None:
        nonlocal current
        if current is None:
            return
        notes.append(ImportedNote(**current))
        current = None

    for _, event_kind, match in events:
        if event_kind == "tone":
            finish_note()
            frequency_hz = float(match.group(1))
            duration_ms = float(match.group(2))
            if frequency_hz <= 0 or duration_ms <= 0:
                raise DectalkTrackImportError("Tone frequency and duration must both be positive.")
            midi_pitch = round(69 + 12 * math.log2(frequency_hz / 440.0))
            if not 0 <= midi_pitch <= 127:
                raise DectalkTrackImportError(
                    f"Tone frequency {frequency_hz:g} Hz maps outside the MIDI range."
                )
            end_ms = cursor_ms + duration_ms
            notes.append(ImportedNote(
                start_ms=cursor_ms,
                end_ms=end_ms,
                dectalk_pitch=midi_pitch + int(note_offset),
                midi_pitch=midi_pitch,
                phonemes=(),
                phrase_break_before=pending_break,
                event_kind="tone",
                tone_frequency_hz=frequency_hz,
            ))
            pending_break = False
            cursor_ms = end_ms
            previous_match_end = match.end()
            continue
        symbol = match.group(1).lower()
        duration_ms = float(match.group(2))
        pitch_text = match.group(3)
        separator = parse_source[previous_match_end:match.start()]
        if current is not None and "]" in separator and "[" in separator:
            finish_note()
        previous_match_end = match.end()
        if not math.isfinite(duration_ms) or duration_ms <= 0:
            raise DectalkTrackImportError(f"{symbol} must have a positive finite duration.")
        end_ms = cursor_ms + duration_ms
        if symbol == "_":
            finish_note()
            pending_break = pending_break or duration_ms >= phrase_rest_ms
            cursor_ms = end_ms
            continue
        split_symbols = phonemes.splitDirectPhonemeSyllable(symbol, strict=True)
        if not split_symbols:
            raise DectalkTrackImportError(f"Unsupported DECTalk phoneme: {symbol}")
        if pitch_text is None and previous_pitch is None:
            raise DectalkTrackImportError(f"{symbol} omits pitch before any pitched phoneme.")
        dectalk_pitch = int(pitch_text) if pitch_text is not None else int(previous_pitch)
        previous_pitch = dectalk_pitch
        midi_pitch = dectalk_pitch - int(note_offset)
        if not 0 <= midi_pitch <= 127:
            raise DectalkTrackImportError(
                f"DECTalk pitch {dectalk_pitch} maps to MIDI {midi_pitch}; valid MIDI pitches are 0 through 127."
            )
        if current is not None and current["dectalk_pitch"] == dectalk_pitch:
            current["end_ms"] = end_ms
            current["phonemes"] = tuple(current["phonemes"]) + tuple(split_symbols)
        else:
            finish_note()
            current = {
                "start_ms": cursor_ms,
                "end_ms": end_ms,
                "dectalk_pitch": dectalk_pitch,
                "midi_pitch": midi_pitch,
                "phonemes": tuple(split_symbols),
                "phrase_break_before": pending_break,
            }
            pending_break = False
        cursor_ms = end_ms
    finish_note()
    if not notes:
        raise DectalkTrackImportError("The string contains rests but no voiced phonemes.")

    lines: list[list[str]] = []
    for note in notes:
        if note.phrase_break_before or not lines or len(lines[-1]) >= MAX_PHRASE_NOTES:
            lines.append([])
        lines[-1].append(note.lyric_token)
    lyric_text = "\n".join(" ".join(line) for line in lines) + "\n"
    return DectalkTrackImport(tuple(notes), _setup_from_commands(source), lyric_text, cursor_ms)


def milliseconds_to_tick(midi: mido.MidiFile, milliseconds: float) -> int:
    """Convert absolute milliseconds through the source MIDI's tempo map."""

    tempo_events: list[tuple[int, int]] = []
    for track in midi.tracks:
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if message.type == "set_tempo":
                tempo_events.append((absolute_tick, message.tempo))
    tempo_events.sort(key=lambda item: item[0])
    target_seconds = max(0.0, float(milliseconds) / 1000.0)
    current_tick = 0
    current_seconds = 0.0
    tempo = 500_000
    for event_tick, event_tempo in tempo_events:
        if event_tick < current_tick:
            continue
        segment_seconds = mido.tick2second(event_tick - current_tick, midi.ticks_per_beat, tempo)
        if target_seconds <= current_seconds + segment_seconds:
            return round(current_tick + mido.second2tick(target_seconds - current_seconds, midi.ticks_per_beat, tempo))
        current_seconds += segment_seconds
        current_tick = event_tick
        tempo = event_tempo
    return round(current_tick + mido.second2tick(target_seconds - current_seconds, midi.ticks_per_beat, tempo))


def append_imported_track(midi: mido.MidiFile, role: str, imported: DectalkTrackImport) -> None:
    """Append one monophonic track while preserving every existing MIDI track."""

    events: list[tuple[int, int, mido.Message]] = []
    for note in imported.notes:
        start_tick = milliseconds_to_tick(midi, note.start_ms)
        end_tick = max(start_tick + 1, milliseconds_to_tick(midi, note.end_ms))
        events.append((start_tick, 1, mido.Message("note_on", note=note.midi_pitch, velocity=100, time=0)))
        events.append((end_tick, 0, mido.Message("note_off", note=note.midi_pitch, velocity=0, time=0)))
    events.sort(key=lambda item: (item[0], item[1]))
    track = mido.MidiTrack([mido.MetaMessage("track_name", name=role, time=0)])
    previous_tick = 0
    for absolute_tick, _, message in events:
        track.append(message.copy(time=max(0, absolute_tick - previous_tick)))
        previous_tick = absolute_tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(track)
