#!/usr/bin/env python3
"""Migrate legacy lyric sources to immutable ``*.transcript.txt`` files."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

import yaml


def _has_content(path: Path) -> bool:
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def migrate_song(song_dir: Path, *, apply: bool, remove_raw: bool) -> list[str]:
    settings_path = song_dir / "settings.yaml"
    if not settings_path.is_file():
        return []
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    tracks = settings.get("Tracks") or {}
    roles_by_stem: dict[str, list[str]] = {}
    for role, raw_config in tracks.items():
        config = raw_config if isinstance(raw_config, dict) else {}
        stem = str(config.get("LYRICS_FILENAME", role))
        roles_by_stem.setdefault(stem, []).append(str(role))

    lyrics_dir = song_dir / "inputs" / "lyrics"
    legacy_drafts = song_dir / "outputs" / "lyrics_drafts"
    messages: list[str] = []
    for stem, roles in roles_by_stem.items():
        destination = lyrics_dir / f"{stem}.transcript.txt"
        raw_path = lyrics_dir / f"{stem}.raw.txt"
        candidates = [legacy_drafts / f"{role}.transcript.txt" for role in roles]
        candidates.extend([
            raw_path,
            lyrics_dir / f"{stem}.original.txt",
        ])
        source = next((path for path in candidates if _has_content(path)), None)
        if destination.is_file():
            messages.append(f"KEEP {destination}")
        elif source is None:
            messages.append(f"SKIP {destination}: no recoverable lyric source")
        elif apply:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if destination.read_bytes() != source.read_bytes():
                raise OSError(f"Transcript verification failed: {destination}")
            messages.append(f"CREATE {destination} from {source}")
        else:
            messages.append(f"WOULD CREATE {destination} from {source}")

        if remove_raw and raw_path.exists():
            raw_has_content = _has_content(raw_path)
            if raw_has_content and not destination.is_file() and not (not apply and source is not None):
                raise OSError(f"Refusing to remove {raw_path} without a transcript replacement")
            if apply:
                raw_path.unlink()
                messages.append(f"REMOVE {raw_path}")
            else:
                messages.append(f"WOULD REMOVE {raw_path}")
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs-root", type=Path, default=Path(__file__).resolve().parents[1] / "songs")
    parser.add_argument("--apply", action="store_true", help="Write verified transcript files instead of reporting a dry run.")
    parser.add_argument("--remove-raw", action="store_true", help="Remove each legacy .raw.txt only after its transcript replacement exists.")
    args = parser.parse_args()
    for song_dir in sorted((path for path in args.songs_root.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
        for message in migrate_song(song_dir, apply=args.apply, remove_raw=args.remove_raw):
            print(message)


if __name__ == "__main__":
    main()
