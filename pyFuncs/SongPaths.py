"""Canonical filesystem layout for a DECTALK Choir song workspace."""

from __future__ import annotations

from pathlib import Path


def song_directory(repo_root: Path, song_name: str) -> Path:
    return repo_root / "songs" / song_name


def inputs_directory(song_dir: Path) -> Path:
    return song_dir / "inputs"


def lyrics_directory(song_dir: Path) -> Path:
    return inputs_directory(song_dir) / "lyrics"


def outputs_directory(song_dir: Path) -> Path:
    return song_dir / "outputs"


def has_lyric_content(path: Path) -> bool:
    """Return whether a lyric file has at least one renderable line."""

    try:
        return any(
            line.strip() and not line.lstrip().startswith("#")
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    except OSError:
        return False


def render_lyrics_path(song_dir: Path, role: str, lyric_stem: str) -> Path:
    """Return the Studio candidate when present, otherwise the configured lyric input."""

    candidate = outputs_directory(song_dir) / "lyrics_drafts" / f"{role}.txt"
    if has_lyric_content(candidate):
        return candidate
    return lyrics_directory(song_dir) / f"{lyric_stem}.txt"


def find_midi_file(song_dir: Path) -> Path | None:
    """Return the deterministic first MIDI input, or None when it is absent."""

    candidates = sorted(
        [*inputs_directory(song_dir).glob("*.mid"), *inputs_directory(song_dir).glob("*.midi")],
        key=lambda path: path.name.lower(),
    )
    return candidates[0] if candidates else None
