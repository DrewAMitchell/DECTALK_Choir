"""Read-only splitter analysis helpers used by the native GUI."""

from __future__ import annotations

from pathlib import Path

import mido

from choir_gui.inspector import MidiNote, MidiTrackInfo
from tools.split_polyphonic_midi import (
    MidiSplitError,
    TrackAnalysis,
    VoiceLane,
    max_polyphony,
    parse_track,
    split_into_lanes,
)


def analyze_midi_source(source: Path) -> tuple[mido.MidiFile, tuple[TrackAnalysis, ...]]:
    """Parse a MIDI source without writing or mutating it."""

    midi = mido.MidiFile(source)
    analyses = tuple(parse_track(track, index) for index, track in enumerate(midi.tracks))
    return midi, analyses


def split_track_preview(
    source: Path,
    track_index: int,
) -> tuple[mido.MidiFile, TrackAnalysis, list[VoiceLane]]:
    """Return one selected source track and its tentative monophonic lanes."""

    midi, analyses = analyze_midi_source(source)
    analysis = next(
        (item for item in analyses if item.source_index == track_index),
        None,
    )
    if analysis is None:
        raise MidiSplitError(f"MIDI track index {track_index} is not present in {source.name}.")
    if not analysis.notes:
        return midi, analysis, []
    lanes = split_into_lanes(analysis.notes)
    if len(lanes) != max_polyphony(analysis.notes):
        raise MidiSplitError(
            f"Track {track_index} requires {max_polyphony(analysis.notes)} lanes "
            f"but the preview created {len(lanes)}."
        )
    return midi, analysis, lanes


def _track_info(index: int, name: str, notes: list[MidiNote]) -> MidiTrackInfo:
    return MidiTrackInfo(
        index=index,
        name=name,
        notes=tuple(notes),
        max_polyphony=max_polyphony(notes),
        overlap_regions=0,
        total_overlap_ms=0.0,
        longest_overlap_ms=0.0,
        duplicate_note_spans=0,
        warnings=(),
    )


def split_view_tracks(
    analysis: TrackAnalysis,
    lanes: list[VoiceLane],
) -> tuple[MidiTrackInfo, ...]:
    """Build source-plus-lane tracks for the existing piano-roll widget."""

    source_notes = [
        MidiNote(
            start_tick=note.start_tick,
            end_tick=note.end_tick,
            pitch=note.note,
            velocity=note.velocity,
            channel=note.channel,
        )
        for note in analysis.notes
    ]
    tracks = [_track_info(0, f"{analysis.source_name} (source)", source_notes)]
    for lane_index, lane in enumerate(lanes, start=1):
        lane_notes = [
            MidiNote(
                start_tick=note.start_tick,
                end_tick=note.end_tick,
                pitch=note.note,
                velocity=note.velocity,
                channel=note.channel,
            )
            for note in lane.notes
        ]
        tracks.append(
            _track_info(
                lane_index,
                f"{analysis.source_name} - Voice {lane_index}",
                lane_notes,
            )
        )
    return tuple(tracks)
