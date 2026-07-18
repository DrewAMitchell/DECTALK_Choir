"""Peak-ceiling helpers shared by note, stem, and final-mix processing."""

from __future__ import annotations

import math

from pydub import AudioSegment


DEFAULT_PEAK_CEILING_DBFS = -1.0
DEFAULT_NOTE_PEAK_TARGET_DBFS = -5.0
MAX_AUTOMATIC_NOTE_BOOST_DB = 30.0


def peak_ceiling_gain_db(audio: AudioSegment, ceiling_dbfs: float) -> float:
    """Return the attenuation needed to keep ``audio`` at or below a ceiling."""

    try:
        ceiling = min(0.0, float(ceiling_dbfs))
        peak = audio.max_dBFS
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(peak) or peak <= ceiling:
        return 0.0
    return ceiling - peak


def apply_peak_ceiling(audio: AudioSegment, ceiling_dbfs: float) -> tuple[AudioSegment, float]:
    """Attenuate only when needed and report the applied gain in decibels."""

    gain_db = peak_ceiling_gain_db(audio, ceiling_dbfs)
    return (audio + gain_db if gain_db < 0 else audio), gain_db


def note_peak_target_gain_db(
    audio: AudioSegment,
    target_dbfs: float = DEFAULT_NOTE_PEAK_TARGET_DBFS,
    max_boost_db: float = MAX_AUTOMATIC_NOTE_BOOST_DB,
) -> float:
    """Return bounded bidirectional gain that places a note peak at its target."""

    try:
        target = min(0.0, float(target_dbfs))
        max_boost = max(0.0, float(max_boost_db))
        peak = audio.max_dBFS
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(peak):
        return 0.0
    return min(max_boost, target - peak)


def apply_note_peak_target(
    audio: AudioSegment,
    target_dbfs: float = DEFAULT_NOTE_PEAK_TARGET_DBFS,
    max_boost_db: float = MAX_AUTOMATIC_NOTE_BOOST_DB,
) -> tuple[AudioSegment, float]:
    """Normalize a non-silent note peak and report the applied gain."""

    gain_db = note_peak_target_gain_db(audio, target_dbfs, max_boost_db)
    return (audio + gain_db if gain_db else audio), gain_db
