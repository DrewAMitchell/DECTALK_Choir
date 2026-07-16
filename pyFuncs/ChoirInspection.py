"""Read-only song, MIDI, lyric, and rendered-audio inspection services."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
import contextlib
from dataclasses import dataclass, field
import io
from pathlib import Path
import re
from statistics import fmean, median
from typing import Iterable
import math

import mido
from pydub import AudioSegment
import yaml

import pyFuncs.PhonemeProcessing as phoneme_processing

from pyFuncs.PitchMapping import (
    DEFAULT_MAX_DECTALK_PITCH,
    DEFAULT_MIN_DECTALK_PITCH,
    DEFAULT_NOTE_OFFSET,
    SEMITONES_PER_OCTAVE,
    format_dectalk_pitch,
    midi_pitch_name,
    validate_dectalk_pitch_bounds,
    wrap_dectalk_pitch,
)
from pyFuncs.SongPaths import (
    find_midi_file,
    has_lyric_content,
    lyrics_directory,
    outputs_directory,
    render_lyrics_path,
)


@dataclass(frozen=True)
class MidiNote:
    """One paired MIDI note, represented in absolute ticks."""

    start_tick: int
    end_tick: int
    pitch: int
    velocity: int
    channel: int


@dataclass(frozen=True)
class MidiTrackInfo:
    """The note-bearing information needed to inspect a MIDI source track."""

    index: int
    name: str
    notes: tuple[MidiNote, ...]
    max_polyphony: int
    overlap_regions: int
    total_overlap_ms: float
    longest_overlap_ms: float
    duplicate_note_spans: int
    warnings: tuple[str, ...]

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def min_pitch(self) -> int | None:
        return min((note.pitch for note in self.notes), default=None)

    @property
    def max_pitch(self) -> int | None:
        return max((note.pitch for note in self.notes), default=None)


@dataclass(frozen=True)
class MidiSummary:
    path: Path
    ticks_per_beat: int
    duration_ticks: int
    duration_seconds: float
    tracks: tuple[MidiTrackInfo, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AudioLoudness:
    """Active-window RMS loudness. Silence is excluded from the four statistics."""

    path: Path
    minimum_dbfs: float | None
    median_dbfs: float | None
    average_dbfs: float | None
    maximum_dbfs: float | None
    peak_dbfs: float | None
    active_windows: int
    total_windows: int
    error: str | None = None

    @property
    def display(self) -> str:
        if self.error:
            return f"Unavailable: {self.error}"
        if self.minimum_dbfs is None:
            return "No audible windows"
        return (
            f"{self.minimum_dbfs:.1f} / {self.median_dbfs:.1f} / "
            f"{self.average_dbfs:.1f} / {self.maximum_dbfs:.1f} "
            f"(peak {self.peak_dbfs:.1f})"
        )


@dataclass(frozen=True)
class RoleInspection:
    """The configured output part and the sources/audio associated with it."""

    role: str
    midi_source_name: str
    lyric_stem: str
    lyric_path: Path
    midi_track: MidiTrackInfo | None
    midi_range: str
    render_range: str
    audible_range: str
    pitch_wrap_shift: int | None
    stem_path: Path
    stem_exists: bool
    loudness: AudioLoudness | None
    visual_hsb: tuple[float, float, float]
    visual_position: tuple[float, float, float]
    visual_configured: bool
    visual_label: str
    visual_label_enabled: bool
    visual_label_position: str
    visual_label_show_voice: bool
    visual_label_show_head_size: bool
    visual_label_font: str
    visual_label_font_size_percent: float
    visual_current_word_enabled: bool
    visual_current_word_position: str
    visual_current_word_font: str
    visual_current_word_font_size_percent: float
    visual_current_word_use_track_color: bool
    dectalk_voice: str | None
    head_size: int | None
    render_enabled: bool
    render_eligible: bool
    status: str
    details: tuple[str, ...] = ()

    @property
    def note_count(self) -> int:
        return self.midi_track.note_count if self.midi_track else 0

    @property
    def polyphony(self) -> int | None:
        return self.midi_track.max_polyphony if self.midi_track else None


@dataclass(frozen=True)
class SongInspection:
    repo_root: Path
    song_name: str
    song_dir: Path
    settings_path: Path
    midi_path: Path | None
    midi: MidiSummary | None
    roles: tuple[RoleInspection, ...]
    output_dir: Path
    final_mix: Path
    final_loudness: AudioLoudness | None
    animation_path: Path | None = None
    animation_exists: bool = False
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _is_note_on(message: mido.Message) -> bool:
    return message.type == "note_on" and message.velocity > 0


def _is_note_off(message: mido.Message) -> bool:
    return message.type == "note_off" or (
        message.type == "note_on" and message.velocity == 0
    )


def _track_name(track: mido.MidiTrack, index: int) -> str:
    return track.name.strip() or f"Track {index:02d}"


def _max_polyphony(notes: Iterable[MidiNote]) -> int:
    events: list[tuple[int, int]] = []
    for note in notes:
        if note.end_tick > note.start_tick:
            events.extend(((note.start_tick, 1), (note.end_tick, -1)))
    events.sort(key=lambda event: (event[0], event[1]))
    active = maximum = 0
    for _, change in events:
        active += change
        maximum = max(maximum, active)
    return maximum


def _tick_to_milliseconds(midi: mido.MidiFile):
    """Build a tempo-aware converter for overlap diagnostics and timeline labels."""

    tempo_events: list[tuple[int, int, int]] = [(0, 0, 500000)]
    for track_index, track in enumerate(midi.tracks):
        absolute_tick = 0
        for event_index, message in enumerate(track):
            absolute_tick += message.time
            if message.type == "set_tempo":
                tempo_events.append((absolute_tick, track_index * 100000 + event_index, message.tempo))
    tempo_events.sort()

    segments: list[tuple[int, int]] = []
    for tick, _, tempo in tempo_events:
        if segments and segments[-1][0] == tick:
            segments[-1] = (tick, tempo)
        else:
            segments.append((tick, tempo))

    def convert(tick: int) -> float:
        elapsed_ms = 0.0
        previous_tick = 0
        tempo = 500000
        for segment_tick, segment_tempo in segments:
            if segment_tick > tick:
                break
            elapsed_ms += (segment_tick - previous_tick) * tempo / midi.ticks_per_beat / 1000
            previous_tick = segment_tick
            tempo = segment_tempo
        elapsed_ms += (tick - previous_tick) * tempo / midi.ticks_per_beat / 1000
        return elapsed_ms

    return convert


def _overlap_metrics(notes: Iterable[MidiNote], tick_to_ms) -> tuple[int, float, float]:
    """Report actual simultaneous-note time, ignoring zero-length event boundaries."""

    events: list[tuple[int, int]] = []
    for note in notes:
        if note.end_tick > note.start_tick:
            events.extend(((note.start_tick, 1), (note.end_tick, -1)))
    events.sort(key=lambda event: (event[0], event[1]))

    active = 0
    overlap_start_tick: int | None = None
    durations_ms: list[float] = []
    for tick, change in events:
        before = active
        active += change
        if before <= 1 and active > 1:
            overlap_start_tick = tick
        elif before > 1 and active <= 1 and overlap_start_tick is not None:
            duration_ms = tick_to_ms(tick) - tick_to_ms(overlap_start_tick)
            if duration_ms > 0:
                durations_ms.append(duration_ms)
            overlap_start_tick = None

    return len(durations_ms), sum(durations_ms), max(durations_ms, default=0.0)


def _unique_note_spans(notes: Iterable[MidiNote]) -> tuple[tuple[MidiNote, ...], int]:
    """Ignore exact duplicate spans when deciding whether a track has a second voice."""

    unique: list[MidiNote] = []
    seen: set[tuple[int, int, int, int]] = set()
    duplicates = 0
    for note in notes:
        identity = (note.start_tick, note.end_tick, note.pitch, note.channel)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        unique.append(note)
    return tuple(unique), duplicates


def inspect_midi(path: Path) -> MidiSummary:
    """Parse MIDI note spans without relying on the renderer's mutable state."""

    midi = mido.MidiFile(path)
    parsed_tracks: list[tuple[int, str, tuple[MidiNote, ...], tuple[str, ...]]] = []
    duration_ticks = 0
    warnings: list[str] = []

    for index, track in enumerate(midi.tracks):
        absolute_tick = 0
        active: dict[tuple[int, int], deque[tuple[int, int]]] = defaultdict(deque)
        notes: list[MidiNote] = []
        track_warnings: list[str] = []
        for message in track:
            absolute_tick += message.time
            if _is_note_on(message):
                active[(message.channel, message.note)].append(
                    (absolute_tick, message.velocity)
                )
            elif _is_note_off(message):
                key = (message.channel, message.note)
                if not active[key]:
                    track_warnings.append(
                        f"unmatched note-off {message.note} at tick {absolute_tick}"
                    )
                    continue
                start_tick, velocity = active[key].popleft()
                if absolute_tick > start_tick:
                    notes.append(
                        MidiNote(
                            start_tick=start_tick,
                            end_tick=absolute_tick,
                            pitch=message.note,
                            velocity=velocity,
                            channel=message.channel,
                        )
                    )
                else:
                    track_warnings.append(
                        f"zero-duration note {message.note} at tick {absolute_tick}"
                    )

        dangling = sum(len(values) for values in active.values())
        if dangling:
            track_warnings.append(f"{dangling} unmatched note-on event(s)")
        name = _track_name(track, index)
        duration_ticks = max(duration_ticks, absolute_tick)
        parsed_tracks.append((index, name, tuple(notes), tuple(track_warnings)))
        warnings.extend(f"{name}: {warning}" for warning in track_warnings)

    tick_to_ms = _tick_to_milliseconds(midi)
    tracks = []
    for index, name, notes, track_warnings in parsed_tracks:
        overlap_notes, duplicate_note_spans = _unique_note_spans(notes)
        overlap_regions, total_overlap_ms, longest_overlap_ms = _overlap_metrics(
            overlap_notes, tick_to_ms
        )
        tracks.append(
            MidiTrackInfo(
                index=index,
                name=name,
                notes=notes,
                max_polyphony=_max_polyphony(overlap_notes),
                overlap_regions=overlap_regions,
                total_overlap_ms=total_overlap_ms,
                longest_overlap_ms=longest_overlap_ms,
                duplicate_note_spans=duplicate_note_spans,
                warnings=track_warnings,
            )
        )

    return MidiSummary(
        path=path,
        ticks_per_beat=midi.ticks_per_beat,
        duration_ticks=duration_ticks,
        duration_seconds=midi.length,
        tracks=tuple(tracks),
        warnings=tuple(warnings),
    )


def measure_audio(path: Path, window_ms: int = 100, floor_dbfs: float = -70.0) -> AudioLoudness:
    """Measure active 100 ms RMS windows so initial silence does not dominate."""

    try:
        audio = AudioSegment.from_file(path)
    except Exception as error:  # pydub exposes backend-specific exception types.
        return AudioLoudness(path, None, None, None, None, None, 0, 0, str(error))

    values: list[float] = []
    total_windows = math.ceil(len(audio) / window_ms) if len(audio) else 0
    for start_ms in range(0, len(audio), window_ms):
        value = audio[start_ms : start_ms + window_ms].dBFS
        if math.isfinite(value) and value >= floor_dbfs:
            values.append(value)

    peak = audio.max_dBFS
    if not math.isfinite(peak):
        peak = None
    if not values:
        return AudioLoudness(path, None, None, None, None, peak, 0, total_windows)
    return AudioLoudness(
        path=path,
        minimum_dbfs=min(values),
        median_dbfs=median(values),
        average_dbfs=fmean(values),
        maximum_dbfs=max(values),
        peak_dbfs=peak,
        active_windows=len(values),
        total_windows=total_windows,
    )


def _find_midi(song_dir: Path) -> tuple[Path | None, list[str]]:
    midi_path = find_midi_file(song_dir)
    if midi_path is None:
        return None, ["No .mid or .midi file is present in inputs/."]
    return midi_path, []


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_wrap_shift(pitches: list[int], minimum: int, maximum: int) -> int:
    candidates = range(-10 * SEMITONES_PER_OCTAVE, 10 * SEMITONES_PER_OCTAVE + 1, SEMITONES_PER_OCTAVE)
    fitting = [
        shift
        for shift in candidates
        if min(pitches) + shift >= minimum and max(pitches) + shift <= maximum
    ]
    if fitting:
        return min(fitting, key=lambda shift: (abs(shift), shift))

    def out_of_bounds(shift: int) -> int:
        return max(minimum - (min(pitches) + shift), 0) + max(
            max(pitches) + shift - maximum, 0
        )

    return min(candidates, key=lambda shift: (out_of_bounds(shift), abs(shift), shift))


def _midi_range(notes: Iterable[MidiNote]) -> str:
    pitches = [note.pitch for note in notes]
    if not pitches:
        return "--"
    return f"{midi_pitch_name(min(pitches))} to {midi_pitch_name(max(pitches))}"


def _dectalk_range(pitches: list[int]) -> str:
    if not pitches:
        return "--"
    return f"{format_dectalk_pitch(min(pitches))} to {format_dectalk_pitch(max(pitches))}"


def _audio_path(output_dir: Path, role: str) -> Path:
    return output_dir / "_tracks" / f"{role}.wav"


def _has_lyric_content(path: Path) -> bool:
    """Return whether a lyric file has at least one non-comment line."""
    return has_lyric_content(path)


def _lyric_conversion_issue(path: Path) -> str | None:
    """Return a concise conversion error without allowing choir.py's exit() to escape."""
    if not _has_lyric_content(path):
        return "lyric input is empty or comment-only"
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            phonemes = phoneme_processing.lyricsToPhonemes(
                str(path),
                printInfo=False,
                DECTALK_check=False,
            )
    except SystemExit:
        message = next(
            (
                line.strip()
                for line in reversed(output.getvalue().splitlines())
                if line.strip()
            ),
            "lyric input could not be converted to phonemes",
        )
        return message
    except (OSError, ValueError, KeyError) as error:
        return f"lyric input could not be converted: {error}"
    if not any(item != ["\n"] for item in phonemes):
        return "lyric input produced no phonemes"
    return None


def inspect_song(repo_root: Path, song_name: str, include_audio: bool = True) -> SongInspection:
    """Build the GUI's song model. This never alters settings, lyrics, or output."""

    repo_root = repo_root.expanduser().resolve()
    song_dir = repo_root / "songs" / song_name
    settings_path = song_dir / "settings.yaml"
    output_dir = outputs_directory(song_dir)
    final_mix = output_dir / "_finished" / f"{song_name}.wav"
    errors: list[str] = []
    warnings: list[str] = []

    if not song_dir.is_dir():
        return SongInspection(
            repo_root, song_name, song_dir, settings_path, None, None, (), output_dir,
            final_mix, None, errors=(f"Song folder does not exist: {song_dir}",),
        )
    if not settings_path.is_file():
        return SongInspection(
            repo_root, song_name, song_dir, settings_path, None, None, (), output_dir,
            final_mix, None, errors=(f"Missing settings file: {settings_path}",),
        )

    try:
        with settings_path.open("r", encoding="utf-8") as settings_file:
            settings = yaml.safe_load(settings_file) or {}
    except (OSError, yaml.YAMLError) as error:
        return SongInspection(
            repo_root, song_name, song_dir, settings_path, None, None, (), output_dir,
            final_mix, None, errors=(f"Could not read settings.yaml: {error}",),
        )

    if not isinstance(settings, dict):
        return SongInspection(
            repo_root, song_name, song_dir, settings_path, None, None, (), output_dir,
            final_mix, None, errors=("settings.yaml must contain a mapping.",),
        )

    midi_path, midi_warnings = _find_midi(song_dir)
    warnings.extend(midi_warnings)
    midi: MidiSummary | None = None
    if midi_path:
        try:
            midi = inspect_midi(midi_path)
            warnings.extend(midi.warnings)
        except (OSError, ValueError, EOFError) as error:
            errors.append(f"Could not read MIDI {midi_path.name}: {error}")

    tracks_config = settings.get("Tracks")
    if not isinstance(tracks_config, dict):
        errors.append("settings.yaml must define a Tracks mapping.")
        tracks_config = {}

    note_offset = _as_int(settings.get("noteOffset"), DEFAULT_NOTE_OFFSET)
    minimum = _as_int(settings.get("minDectalkPitch"), DEFAULT_MIN_DECTALK_PITCH)
    maximum = _as_int(settings.get("maxDectalkPitch"), DEFAULT_MAX_DECTALK_PITCH)
    try:
        validate_dectalk_pitch_bounds(minimum, maximum)
    except ValueError as error:
        errors.append(str(error))
        minimum, maximum = DEFAULT_MIN_DECTALK_PITCH, DEFAULT_MAX_DECTALK_PITCH
    overlap_tolerance_ms = max(
        0.0,
        _as_float(settings.get("monophonicOverlapToleranceMs"), 120.0),
    )

    tracks_by_name: dict[str, list[MidiTrackInfo]] = defaultdict(list)
    if midi:
        for source_track in midi.tracks:
            tracks_by_name[source_track.name].append(source_track)

    roles: list[RoleInspection] = []
    for role, raw_config in tracks_config.items():
        config = raw_config if isinstance(raw_config, dict) else {}
        role_name = str(role)
        source_name = str(config.get("TRACK_FILENAME", role_name))
        lyric_stem = str(config.get("LYRICS_FILENAME", role_name))
        configured_lyric_path = lyrics_directory(song_dir) / f"{lyric_stem}.txt"
        lyric_path = render_lyrics_path(song_dir, role_name, lyric_stem)
        matching_tracks = tracks_by_name.get(source_name, [])
        source_track = matching_tracks[0] if len(matching_tracks) == 1 else None
        details: list[str] = []
        status = "Ready"
        if len(matching_tracks) > 1:
            status = "Ambiguous MIDI source"
            details.append(f"{len(matching_tracks)} MIDI tracks are named {source_name!r}.")
        elif source_track is None:
            status = "Missing MIDI source"
            details.append(f"No MIDI track is named {source_name!r}.")
        elif source_track.max_polyphony > 2 or source_track.longest_overlap_ms > overlap_tolerance_ms:
            status = "Polyphonic source"
            details.append(
                f"Source overlaps up to {source_track.max_polyphony} notes; longest overlap is "
                f"{source_track.longest_overlap_ms:.1f} ms (tolerance {overlap_tolerance_ms:.1f} ms). "
                "The renderer will sequentialize these overlaps; split it to preserve independent chord voices."
            )
        elif source_track.max_polyphony > 1:
            details.append(
                f"Transition overlap accepted: {source_track.overlap_regions} handoff region(s), "
                f"longest {source_track.longest_overlap_ms:.1f} ms, total "
                f"{source_track.total_overlap_ms:.1f} ms (tolerance {overlap_tolerance_ms:.1f} ms)."
            )
        if source_track and source_track.duplicate_note_spans:
            details.append(
                f"Ignored {source_track.duplicate_note_spans} exact duplicate MIDI note span(s) for polyphony assessment."
            )
        if lyric_path != configured_lyric_path:
            details.append(f"Using Studio lyric candidate: outputs/lyrics_drafts/{role_name}.txt")
        if not lyric_path.is_file():
            status = "Missing lyric input" if status == "Ready" else status
            details.append(f"Lyric file not found: inputs/lyrics/{lyric_stem}.txt")
        elif not _has_lyric_content(lyric_path):
            if status in {"Ready", "Polyphonic source"}:
                status = "Missing lyric content"
            details.append(
                f"Lyric file is empty or comment-only: inputs/lyrics/{lyric_stem}.txt"
            )
        else:
            lyric_issue = _lyric_conversion_issue(lyric_path)
            if lyric_issue:
                if status in {"Ready", "Polyphonic source"}:
                    status = "Invalid lyric content"
                details.append(lyric_issue)
        if source_track and source_track.warnings:
            details.extend(source_track.warnings)

        render_range = audible_range = "--"
        wrap_shift: int | None = None
        if source_track and source_track.notes:
            pitch_shift = _as_int(config.get("PITCH_SHIFT"), 0)
            octave_boost = _as_int(config.get("OCTAVE_BOOST"), 0)
            raw_pitches = [note.pitch + note_offset + pitch_shift - octave_boost for note in source_track.notes]
            configured_shift = config.get("PITCH_WRAP_SHIFT")
            wrap_shift = (
                _as_int(configured_shift, 0)
                if configured_shift is not None
                else _get_wrap_shift(raw_pitches, minimum, maximum)
            )
            render_pitches = [
                wrap_dectalk_pitch(pitch + wrap_shift, minimum, maximum)
                for pitch in raw_pitches
            ]
            audible_pitches = [pitch + octave_boost for pitch in render_pitches]
            render_range = _dectalk_range(render_pitches)
            audible_range = _dectalk_range(audible_pitches)

        stem_path = _audio_path(output_dir, role_name)
        loudness = measure_audio(stem_path) if include_audio and stem_path.is_file() else None
        spectrogram = config.get("SPECTROGRAM") if isinstance(config.get("SPECTROGRAM"), dict) else {}
        raw_hsb = spectrogram.get("COLOR_HSB", config.get("VID_HSB", [0, 100, 100]))
        raw_position = spectrogram.get("POSITION", config.get("VID_Position", [0.5, 0.25, 0.25]))
        visual_hsb = tuple(
            _as_float(value, default)
            for value, default in zip(
                raw_hsb if isinstance(raw_hsb, (list, tuple)) and len(raw_hsb) == 3 else [0, 100, 100],
                [0.0, 100.0, 100.0],
            )
        )
        visual_position = tuple(
            _as_float(value, default)
            for value, default in zip(
                raw_position if isinstance(raw_position, (list, tuple)) and len(raw_position) == 3 else [0.5, 0.25, 0.25],
                [0.5, 0.25, 0.25],
            )
        )
        visual_configured = (
            "COLOR_HSB" in spectrogram and "POSITION" in spectrogram
        ) or ("VID_HSB" in config and "VID_Position" in config)
        setup = str(config.get("DEC_SETUP", ""))
        voice_match = re.search(r"\[:n([a-z])\]", setup, flags=re.IGNORECASE)
        head_size_match = re.search(r"\[:dv\s+hs\s+(\d+)\]", setup, flags=re.IGNORECASE)
        label_position = str(spectrogram.get("LABEL_POSITION", config.get("VID_LabelPosition", "top-left")))
        word_position = str(spectrogram.get("CURRENT_WORD_POSITION", config.get("VID_CurrentWordPosition", "bottom-center")))
        render_enabled = bool(config.get("RENDER_ENABLED", True))
        # choir.py deterministically truncates a note at the next note start. A
        # polyphonic source is therefore a fidelity warning, not a hard render block.
        render_eligible = status in {"Ready", "Polyphonic source"}
        roles.append(
            RoleInspection(
                role=role_name,
                midi_source_name=source_name,
                lyric_stem=lyric_stem,
                lyric_path=lyric_path,
                midi_track=source_track,
                midi_range=_midi_range(source_track.notes) if source_track else "--",
                render_range=render_range,
                audible_range=audible_range,
                pitch_wrap_shift=wrap_shift,
                stem_path=stem_path,
                stem_exists=stem_path.is_file(),
                loudness=loudness,
                visual_hsb=visual_hsb,
                visual_position=visual_position,
                visual_configured=visual_configured,
                visual_label=str(spectrogram.get("LABEL", config.get("VID_Label", role_name))),
                visual_label_enabled=bool(spectrogram.get("LABEL_ENABLED", config.get("VID_LabelEnabled", False))),
                visual_label_position=label_position,
                visual_label_show_voice=bool(spectrogram.get("LABEL_SHOW_VOICE", config.get("VID_LabelShowVoice", False))),
                visual_label_show_head_size=bool(spectrogram.get("LABEL_SHOW_HEAD_SIZE", config.get("VID_LabelShowHeadSize", False))),
                visual_label_font=str(spectrogram.get("LABEL_FONT", "choir")),
                visual_label_font_size_percent=_as_float(spectrogram.get("LABEL_FONT_SIZE_PERCENT"), 7.0),
                visual_current_word_enabled=bool(spectrogram.get("CURRENT_WORD_ENABLED", config.get("VID_CurrentWordEnabled", False))),
                visual_current_word_position=word_position,
                visual_current_word_font=str(spectrogram.get("CURRENT_WORD_FONT", "choir")),
                visual_current_word_font_size_percent=_as_float(spectrogram.get("CURRENT_WORD_FONT_SIZE_PERCENT"), 10.0),
                visual_current_word_use_track_color=bool(spectrogram.get("CURRENT_WORD_USE_TRACK_COLOR", False)),
                dectalk_voice=f"n{voice_match.group(1).lower()}" if voice_match else None,
                head_size=int(head_size_match.group(1)) if head_size_match else None,
                render_enabled=render_enabled,
                render_eligible=render_eligible,
                status=status,
                details=tuple(details),
            )
        )

    final_loudness = measure_audio(final_mix) if include_audio and final_mix.is_file() else None
    animation_path = output_dir / "_finished" / f"{song_name}.mp4"
    return SongInspection(
        repo_root=repo_root,
        song_name=song_name,
        song_dir=song_dir,
        settings_path=settings_path,
        midi_path=midi_path,
        midi=midi,
        roles=tuple(roles),
        output_dir=output_dir,
        final_mix=final_mix,
        final_loudness=final_loudness,
        animation_path=animation_path,
        animation_exists=animation_path.is_file(),
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(errors),
    )
