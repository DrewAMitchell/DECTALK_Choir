#!/usr/bin/env python3
"""Create a choir-ready MIDI whose note tracks are all monophonic.

The source file is never modified.  A polyphonic source track is expanded into
the smallest number of voice tracks required to prevent note overlap.  Notes
are assigned to available voices by nearest previous pitch so each voice tends
to retain a musically coherent contour.

Examples:
    .\.venv\Scripts\python.exe tools\split_polyphonic_midi.py ^
        C:/Users/Drew/Downloads/earth-angel.mid

    .\.venv\Scripts\python.exe tools\split_polyphonic_midi.py input.mid ^
        --output output_monophonic.mid
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import mido


class MidiSplitError(ValueError):
    """A MIDI event sequence cannot be split without guessing its meaning."""


@dataclass(frozen=True)
class NoteSpan:
    """A paired note event expressed in absolute MIDI ticks."""

    start_tick: int
    end_tick: int
    note: int
    velocity: int
    channel: int
    release_velocity: int
    source_order: int


@dataclass
class VoiceLane:
    """One output lane and the musical state used to extend it."""

    notes: list[NoteSpan]
    available_at: int = 0
    last_pitch: int | None = None


@dataclass(frozen=True)
class TrackAnalysis:
    """Parsed source-track state required for reconstruction and reporting."""

    source_index: int
    source_name: str
    end_tick: int
    notes: tuple[NoteSpan, ...]
    passthrough_events: tuple[tuple[int, int, mido.Message], ...]


def is_note_on(message: mido.Message) -> bool:
    return message.type == "note_on" and message.velocity > 0


def is_note_off(message: mido.Message) -> bool:
    return message.type == "note_off" or (
        message.type == "note_on" and message.velocity == 0
    )


def track_label(track: mido.MidiTrack, source_index: int) -> str:
    """Return a stable title even for MIDI files whose tracks are unnamed."""

    return track.name.strip() or f"Track {source_index:02d}"


def parse_track(track: mido.MidiTrack, source_index: int) -> TrackAnalysis:
    """Pair note events while retaining all non-note events at absolute ticks."""

    active: dict[tuple[int, int], deque[tuple[int, int, int]]] = defaultdict(deque)
    notes: list[NoteSpan] = []
    passthrough_events: list[tuple[int, int, mido.Message]] = []
    absolute_tick = 0

    for event_order, message in enumerate(track):
        absolute_tick += message.time

        if message.type == "track_name" or message.type == "end_of_track":
            continue

        if is_note_on(message):
            active[(message.channel, message.note)].append(
                (absolute_tick, message.velocity, event_order)
            )
            continue

        if is_note_off(message):
            key = (message.channel, message.note)
            if not active[key]:
                raise MidiSplitError(
                    f"Track {source_index} ({track_label(track, source_index)!r}) has an "
                    f"unmatched note-off for MIDI note {message.note} at tick {absolute_tick}."
                )

            start_tick, velocity, source_order = active[key].popleft()
            notes.append(
                NoteSpan(
                    start_tick=start_tick,
                    end_tick=absolute_tick,
                    note=message.note,
                    velocity=velocity,
                    channel=message.channel,
                    release_velocity=message.velocity,
                    source_order=source_order,
                )
            )
            continue

        passthrough_events.append((absolute_tick, event_order, message.copy(time=0)))

    dangling = [
        (channel, note, values[0][0])
        for (channel, note), values in active.items()
        if values
    ]
    if dangling:
        channel, note, tick = dangling[0]
        raise MidiSplitError(
            f"Track {source_index} ({track_label(track, source_index)!r}) has an unmatched "
            f"note-on for MIDI note {note} on channel {channel} at tick {tick}."
        )

    return TrackAnalysis(
        source_index=source_index,
        source_name=track_label(track, source_index),
        end_tick=absolute_tick,
        notes=tuple(notes),
        passthrough_events=tuple(passthrough_events),
    )


def max_polyphony(notes: Iterable[NoteSpan]) -> int:
    """Return the largest number of notes sounding simultaneously."""

    events: list[tuple[int, int]] = []
    for note in notes:
        if note.end_tick <= note.start_tick:
            continue
        events.append((note.start_tick, 1))
        events.append((note.end_tick, -1))

    events.sort(key=lambda event: (event[0], event[1]))
    active = 0
    maximum = 0
    for _, change in events:
        active += change
        maximum = max(maximum, active)
    return maximum


def split_into_lanes(notes: Sequence[NoteSpan]) -> list[VoiceLane]:
    """Partition note spans into the minimum count of non-overlapping lanes."""

    lanes: list[VoiceLane] = []
    grouped: dict[int, list[NoteSpan]] = defaultdict(list)
    for note in notes:
        grouped[note.start_tick].append(note)

    for start_tick in sorted(grouped):
        # Simultaneous chord tones must be placed in separate lanes.  Sorting by
        # pitch, then preferring the nearest historical lane, reduces voice jumps.
        available = [
            lane_index
            for lane_index, lane in enumerate(lanes)
            if lane.available_at <= start_tick
        ]
        for note in sorted(grouped[start_tick], key=lambda item: (item.note, item.source_order)):
            if available:
                lane_index = min(
                    available,
                    key=lambda index: (
                        abs((lanes[index].last_pitch or note.note) - note.note),
                        index,
                    ),
                )
                available.remove(lane_index)
            else:
                lane_index = len(lanes)
                lanes.append(VoiceLane(notes=[]))

            lane = lanes[lane_index]
            lane.notes.append(note)
            lane.available_at = max(note.end_tick, start_tick)
            lane.last_pitch = note.note

    return lanes


def event_priority(message: mido.Message) -> int:
    """Preserve controller changes before notes and release before retrigger."""

    if message.type == "note_off":
        return 1
    if message.type == "note_on":
        return 2
    return 0


def build_lane_track(
    analysis: TrackAnalysis,
    lane: VoiceLane,
    lane_number: int,
    lane_count: int,
) -> mido.MidiTrack:
    """Build one output MIDI track with copied controls and one note voice."""

    output = mido.MidiTrack()
    lane_name = (
        analysis.source_name
        if lane_count == 1
        else f"{analysis.source_name} - Voice {lane_number}"
    )
    events: list[tuple[int, int, int, mido.Message]] = [
        (0, -1, -1, mido.MetaMessage("track_name", name=lane_name, time=0))
    ]
    events.extend(
        (tick, event_priority(message), order, message)
        for tick, order, message in analysis.passthrough_events
    )
    for note in lane.notes:
        events.append(
            (
                note.start_tick,
                2,
                note.source_order,
                mido.Message(
                    "note_on",
                    note=note.note,
                    velocity=note.velocity,
                    channel=note.channel,
                    time=0,
                ),
            )
        )
        events.append(
            (
                note.end_tick,
                1,
                note.source_order,
                mido.Message(
                    "note_off",
                    note=note.note,
                    velocity=note.release_velocity,
                    channel=note.channel,
                    time=0,
                ),
            )
        )

    previous_tick = 0
    for tick, _, _, message in sorted(events, key=lambda event: event[:3]):
        if tick < previous_tick:
            raise MidiSplitError("Internal error: output events were not time ordered.")
        output.append(message.copy(time=tick - previous_tick))
        previous_tick = tick

    output.append(
        mido.MetaMessage("end_of_track", time=max(0, analysis.end_tick - previous_tick))
    )
    return output


def clone_non_note_track(track: mido.MidiTrack) -> mido.MidiTrack:
    """Keep conductor and other no-note tracks byte-for-byte event equivalent."""

    return mido.MidiTrack(message.copy() for message in track)


def note_signature(analysis: TrackAnalysis) -> Counter[tuple[int, int, int, int, int]]:
    return Counter(
        (note.start_tick, note.end_tick, note.note, note.velocity, note.channel)
        for note in analysis.notes
    )


def split_midi(source: Path, output: Path) -> list[tuple[TrackAnalysis, list[VoiceLane]]]:
    """Write a monophonic-track MIDI and return the source-to-lane mapping."""

    source_midi = mido.MidiFile(source)
    analyses = [parse_track(track, index) for index, track in enumerate(source_midi.tracks)]
    result_type = source_midi.type
    output_midi = mido.MidiFile(type=result_type, ticks_per_beat=source_midi.ticks_per_beat)
    mappings: list[tuple[TrackAnalysis, list[VoiceLane]]] = []

    for source_track, analysis in zip(source_midi.tracks, analyses):
        if not analysis.notes:
            output_midi.tracks.append(clone_non_note_track(source_track))
            continue

        lanes = split_into_lanes(analysis.notes)
        if len(lanes) != max_polyphony(analysis.notes):
            raise MidiSplitError(
                f"Internal error: track {analysis.source_index} required {max_polyphony(analysis.notes)} "
                f"lanes but received {len(lanes)}."
            )
        mappings.append((analysis, lanes))
        for lane_number, lane in enumerate(lanes, start=1):
            output_midi.tracks.append(
                build_lane_track(analysis, lane, lane_number, len(lanes))
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    output_midi.save(output)
    verify_split(source, output)
    return mappings


def verify_split(source: Path, output: Path) -> None:
    """Ensure no note was lost and every output note track is monophonic."""

    source_midi = mido.MidiFile(source)
    output_midi = mido.MidiFile(output)
    source_analyses = [parse_track(track, index) for index, track in enumerate(source_midi.tracks)]
    output_analyses = [parse_track(track, index) for index, track in enumerate(output_midi.tracks)]

    source_notes = Counter()
    for analysis in source_analyses:
        source_notes.update(note_signature(analysis))
    output_notes = Counter()
    for analysis in output_analyses:
        output_notes.update(note_signature(analysis))

    if source_notes != output_notes:
        raise MidiSplitError("Verification failed: output MIDI does not preserve the source notes.")

    polyphonic_tracks = [
        analysis.source_index
        for analysis in output_analyses
        if max_polyphony(analysis.notes) > 1
    ]
    if polyphonic_tracks:
        raise MidiSplitError(
            f"Verification failed: output tracks still overlap: {polyphonic_tracks}."
        )


def write_summary(
    source: Path,
    output: Path,
    mappings: Sequence[tuple[TrackAnalysis, list[VoiceLane]]],
    summary_path: Path,
) -> None:
    """Write a compact inspection report beside the generated MIDI file."""

    lines = [
        "DECTALK Choir MIDI split summary",
        f"Source: {source}",
        f"Output: {output}",
        "Verification: all note-bearing output tracks are monophonic; note spans preserved.",
        "",
    ]
    for analysis, lanes in mappings:
        source_polyphony = max_polyphony(analysis.notes)
        lane_note_counts = ", ".join(str(len(lane.notes)) for lane in lanes)
        lines.append(
            f"Track {analysis.source_index:02d} {analysis.source_name!r}: "
            f"{len(analysis.notes)} notes, max polyphony {source_polyphony} -> "
            f"{len(lanes)} lane(s) ({lane_note_counts} notes)."
        )

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def default_output_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}_monophonic.mid")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split every polyphonic MIDI track into monophonic voice tracks."
    )
    parser.add_argument("source", type=Path, help="Input MIDI file; it is never modified.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output MIDI path (default: <source>_monophonic.mid).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="Text report path (default: beside the output MIDI).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Input MIDI was not found: {source}")
    if source.suffix.lower() not in {".mid", ".midi"}:
        raise SystemExit(f"Input must have a .mid or .midi extension: {source}")

    output = (args.output or default_output_path(source)).expanduser().resolve()
    if output == source:
        raise SystemExit("Refusing to overwrite the source MIDI; choose a distinct --output path.")
    summary_path = (
        args.summary
        or output.with_name(f"{output.stem}_split_summary.txt")
    ).expanduser().resolve()

    try:
        mappings = split_midi(source, output)
    except (OSError, ValueError, MidiSplitError) as error:
        raise SystemExit(f"MIDI split failed: {error}") from error

    write_summary(source, output, mappings, summary_path)
    split_count = sum(1 for _, lanes in mappings if len(lanes) > 1)
    print(f"Wrote {output}")
    print(f"Wrote {summary_path}")
    print(
        f"Verified {sum(len(analysis.notes) for analysis, _ in mappings)} notes across "
        f"{len(mappings)} note tracks; {split_count} source tracks were expanded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
