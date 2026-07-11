"""Read-only song, MIDI, lyric, and rendered-audio inspection for the Qt GUI."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, median
from typing import Iterable
import math

import mido
from pydub import AudioSegment
import yaml

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
    loudness: AudioLoudness | None
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


def inspect_midi(path: Path) -> MidiSummary:
    """Parse MIDI note spans without relying on the renderer's mutable state."""

    midi = mido.MidiFile(path)
    tracks: list[MidiTrackInfo] = []
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
        tracks.append(
            MidiTrackInfo(
                index=index,
                name=_track_name(track, index),
                notes=tuple(notes),
                max_polyphony=_max_polyphony(notes),
                warnings=tuple(track_warnings),
            )
        )
        warnings.extend(f"{_track_name(track, index)}: {warning}" for warning in track_warnings)

    return MidiSummary(
        path=path,
        ticks_per_beat=midi.ticks_per_beat,
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
    candidates = sorted(
        list(song_dir.glob("*.mid")) + list(song_dir.glob("*.midi")),
        key=lambda path: path.name.lower(),
    )
    if not candidates:
        return None, ["No .mid or .midi file is present in the song folder."]
    warnings = []
    if len(candidates) > 1:
        warnings.append(
            f"Multiple MIDI files found; inspection uses {candidates[0].name}."
        )
    return candidates[0], warnings


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
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


def inspect_song(repo_root: Path, song_name: str, include_audio: bool = True) -> SongInspection:
    """Build the GUI's song model. This never alters settings, lyrics, or output."""

    repo_root = repo_root.expanduser().resolve()
    song_dir = repo_root / "songs" / song_name
    settings_path = song_dir / "settings.yaml"
    output_dir = repo_root / "outputs" / song_name
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
        lyric_path = song_dir / "lyrics" / f"{lyric_stem}.txt"
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
        elif source_track.max_polyphony > 1:
            status = "Polyphonic source"
            details.append(
                f"Source overlaps up to {source_track.max_polyphony} notes; split it before rendering."
            )
        if not lyric_path.is_file():
            status = "Missing lyric input" if status == "Ready" else status
            details.append(f"Lyric file not found: lyrics/{lyric_stem}.txt")
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
                loudness=loudness,
                status=status,
                details=tuple(details),
            )
        )

    final_loudness = measure_audio(final_mix) if include_audio and final_mix.is_file() else None
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
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(errors),
    )
