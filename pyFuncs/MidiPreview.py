"""Headless MIDI preview helpers shared by the desktop GUIs."""

from __future__ import annotations

from pathlib import Path

import mido


class MidiPreviewError(RuntimeError):
    """The selected MIDI source cannot be prepared or played locally."""


def write_single_track_preview(
    source_path: Path,
    track_index: int,
    output_path: Path,
) -> Path:
    """Write tempo metadata plus exactly one source track for local preview playback."""

    source = mido.MidiFile(source_path)
    if track_index < 0 or track_index >= len(source.tracks):
        raise MidiPreviewError(f"MIDI track index {track_index} is not present in {source_path.name}.")

    preview = mido.MidiFile(type=1, ticks_per_beat=source.ticks_per_beat)
    # Keep timing metadata from every source track, but never copy conductor
    # note events. Some MIDI files put drums on track zero; copying that track
    # made the Windows sequencer play every source layer in a single-track
    # preview and caused percussion to be interpreted as tonal notes.
    metadata_events: list[tuple[int, int, int, mido.Message]] = []
    source_end_tick = 0
    for source_track_index, track in enumerate(source.tracks):
        absolute_tick = 0
        for event_index, message in enumerate(track):
            absolute_tick += message.time
            if message.is_meta and message.type not in {"track_name", "end_of_track"}:
                metadata_events.append(
                    (absolute_tick, source_track_index, event_index, message.copy(time=0))
                )
        source_end_tick = max(source_end_tick, absolute_tick)

    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Preview timing", time=0))
    previous_tick = 0
    for tick, _source_track_index, _event_index, message in sorted(metadata_events):
        conductor.append(message.copy(time=max(0, tick - previous_tick)))
        previous_tick = tick
    conductor.append(mido.MetaMessage("end_of_track", time=max(0, source_end_tick - previous_tick)))
    preview.tracks.append(conductor)
    preview.tracks.append(
        mido.MidiTrack(message.copy() for message in source.tracks[track_index])
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path)
    return output_path
