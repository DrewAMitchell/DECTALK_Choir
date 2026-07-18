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
    program: int = 0,
) -> Path:
    """Write tempo metadata plus one source track using a fixed preview instrument."""

    source = mido.MidiFile(source_path)
    if track_index < 0 or track_index >= len(source.tracks):
        raise MidiPreviewError(f"MIDI track index {track_index} is not present in {source_path.name}.")
    if isinstance(program, bool) or not isinstance(program, int) or not 0 <= program <= 127:
        raise MidiPreviewError("MIDI preview instrument must be a General MIDI program from 0 through 127.")

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
    selected = source.tracks[track_index]
    used_channels = {
        message.channel
        for message in selected
        if not message.is_meta and hasattr(message, "channel") and message.type in {"note_on", "note_off"}
    }
    percussion_replacement = next(
        (channel for channel in range(16) if channel != 9 and channel not in used_channels),
        0,
    )
    preview_channels = {
        percussion_replacement if channel == 9 else channel
        for channel in used_channels
    }
    selected_events: list[tuple[int, int, int, mido.Message]] = [
        (0, 0, channel, mido.Message("program_change", channel=channel, program=program, time=0))
        for channel in sorted(preview_channels)
    ]
    absolute_tick = 0
    for event_index, message in enumerate(selected):
        absolute_tick += message.time
        if (
            message.type in {"program_change", "sysex"}
            or message.type == "control_change" and message.control in {0, 32}
        ):
            continue
        copied = message.copy(time=0)
        if not copied.is_meta and hasattr(copied, "channel") and copied.channel == 9:
            copied = copied.copy(channel=percussion_replacement)
        selected_events.append((absolute_tick, 1, event_index, copied))

    preview_track = mido.MidiTrack()
    previous_tick = 0
    for tick, _priority, _event_index, message in sorted(selected_events):
        preview_track.append(message.copy(time=max(0, tick - previous_tick)))
        previous_tick = tick
    preview.tracks.append(preview_track)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path)
    return output_path
