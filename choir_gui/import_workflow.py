"""Non-destructive MIDI-to-song scaffolding for the native GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

import yaml

from choir_gui.split_workflow import analyze_midi_source


class MidiImportError(ValueError):
    """The selected MIDI cannot become a valid choir song scaffold."""


@dataclass(frozen=True)
class ImportedSong:
    song_name: str
    song_dir: Path
    role_names: tuple[str, ...]


def normalize_song_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "", value).strip("._-")


def _role_name(source_name: str, source_index: int, used: set[str]) -> str:
    role = re.sub(r"[^A-Za-z0-9]+", "_", source_name).strip("_")
    role = role or f"Track_{source_index:02d}"
    candidate = role
    suffix = 2
    while candidate in used:
        candidate = f"{role}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def import_midi_song(source: Path, repo_root: Path, song_name: str) -> ImportedSong:
    """Copy a MIDI and create settings/lyric role artifacts atomically enough for a GUI action."""

    source = source.expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    if not source.is_file():
        raise MidiImportError(f"MIDI file not found: {source}")
    if source.suffix.lower() not in {".mid", ".midi"}:
        raise MidiImportError("The imported file must have a .mid or .midi extension.")

    song_name = normalize_song_name(song_name)
    if not song_name:
        raise MidiImportError("Song name must contain at least one letter or number.")
    song_dir = repo_root / "songs" / song_name
    if song_dir.exists():
        raise MidiImportError(f"Refusing to overwrite existing song folder: {song_dir}")

    try:
        _, analyses = analyze_midi_source(source)
    except Exception as error:
        raise MidiImportError(f"Could not read MIDI: {error}") from error
    note_tracks = [analysis for analysis in analyses if analysis.notes]
    if not note_tracks:
        raise MidiImportError("The selected MIDI contains no note-bearing tracks.")

    used_roles: set[str] = set()
    tracks_config: dict[str, dict[str, object]] = {}
    for analysis in note_tracks:
        role = _role_name(analysis.source_name, analysis.source_index, used_roles)
        tracks_config[role] = {
            "TRACK_FILENAME": analysis.source_name,
            "LYRICS_FILENAME": role,
            "DEC_SETUP": "[:np]",
            "VOLUME_ADJUST_DB": 0.0,
            "PITCH_SHIFT": 0,
            "OCTAVE_BOOST": 0,
        }

    settings = {"noteOffset": -48, "Tracks": tracks_config}
    try:
        song_dir.mkdir(parents=True)
        lyrics_dir = song_dir / "lyrics"
        lyrics_dir.mkdir()
        shutil.copy2(source, song_dir / f"{song_name}.mid")
        (song_dir / "settings.yaml").write_text(
            yaml.safe_dump(settings, sort_keys=False),
            encoding="utf-8",
        )
        for role in tracks_config:
            (lyrics_dir / f"{role}.txt").write_text(
                "# Imported MIDI role. Use Draft -> Note skeleton to create a timing scaffold.\n",
                encoding="utf-8",
            )
    except (OSError, yaml.YAMLError) as error:
        raise MidiImportError(f"Could not create song artifacts: {error}") from error

    return ImportedSong(song_name, song_dir, tuple(tracks_config))
