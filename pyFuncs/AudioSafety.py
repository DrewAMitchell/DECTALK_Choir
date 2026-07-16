"""Peak-ceiling helpers shared by note, stem, and final-mix processing."""

from __future__ import annotations

import math

from pydub import AudioSegment


DEFAULT_PEAK_CEILING_DBFS = -1.0


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
