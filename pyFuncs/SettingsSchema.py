"""Supported settings.yaml keys and typo-oriented validation diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from difflib import get_close_matches


TOP_LEVEL_KEYS = frozenset({
    "Tracks",
    "autoNoteLevelEnabled",
    "codaMaxMs",
    "consonantFractionTarget",
    "consonantMaxMs",
    "consonantMinMs",
    "finalMixPeakCeilingDbfs",
    "gapMendMs",
    "ignoreMidiVelocity",
    "maxDectalkPitch",
    "minDectalkPitch",
    "minimumNoteDurationMs",
    "monophonicOverlapToleranceMs",
    "noteOffset",
    "notePeakTargetDbfs",
    "spectrogramVideo",
    "stemPeakCeilingDbfs",
    "velocityVolumeScaleDb",
    "videoDimensions",
})

TRACK_KEYS = frozenset({
    "AUTO_NOTE_LEVEL_ENABLED",
    "CODA_MAX_MS",
    "DEC_SETUP",
    "EXPORT_PHONEME_STRING",
    "GAP_MEND_MS",
    "IGNORE_MIDI_VELOCITY",
    "LYRICS_FILENAME",
    "MINIMUM_NOTE_DURATION_MS",
    "NOTE_PEAK_TARGET_DBFS",
    "OCTAVE_BOOST",
    "PITCH_SHIFT",
    "PITCH_WRAP_SHIFT",
    "RENDER_ENABLED",
    "SPECTROGRAM",
    "SPLIT_SOURCE_ROLE",
    "STEM_PEAK_CEILING_DBFS",
    "TRACK_FILENAME",
    "VELOCITY_VOLUME_SCALE_DB",
    "VID_CurrentWordEnabled",
    "VID_CurrentWordPosition",
    "VID_HSB",
    "VID_Label",
    "VID_LabelEnabled",
    "VID_LabelPosition",
    "VID_LabelShowHeadSize",
    "VID_LabelShowVoice",
    "VID_Position",
    "VOLUME_ADJUST_DB",
})

SPECTROGRAM_KEYS = frozenset({
    "COLOR_HSB",
    "CURRENT_WORD_ENABLED",
    "CURRENT_WORD_FONT",
    "CURRENT_WORD_FONT_SIZE_PERCENT",
    "CURRENT_WORD_POSITION",
    "CURRENT_WORD_USE_TRACK_COLOR",
    "LABEL",
    "LABEL_ENABLED",
    "LABEL_FONT",
    "LABEL_FONT_SIZE_PERCENT",
    "LABEL_POSITION",
    "LABEL_SHOW_HEAD_SIZE",
    "LABEL_SHOW_VOICE",
    "POSITION",
})

SPECTROGRAM_VIDEO_KEYS = frozenset({"intermediateAnimationMode"})


def _unknown_key_message(path: str, key: object, supported: frozenset[str]) -> str:
    name = str(key)
    matches = get_close_matches(name, supported, n=1, cutoff=0.72)
    suggestion = f" Did you mean '{matches[0]}'?" if matches else ""
    return f"Unsupported settings key '{path}'. It is ignored.{suggestion}"


def settings_key_warnings(settings: object) -> list[str]:
    """Return warnings for settings keys that no runtime currently consumes."""

    if not isinstance(settings, Mapping):
        return ["settings.yaml must contain a mapping."]

    warnings = [
        _unknown_key_message(str(key), key, TOP_LEVEL_KEYS)
        for key in settings
        if key not in TOP_LEVEL_KEYS
    ]

    video = settings.get("spectrogramVideo")
    if isinstance(video, Mapping):
        warnings.extend(
            _unknown_key_message(f"spectrogramVideo.{key}", key, SPECTROGRAM_VIDEO_KEYS)
            for key in video
            if key not in SPECTROGRAM_VIDEO_KEYS
        )

    tracks = settings.get("Tracks")
    if not isinstance(tracks, Mapping):
        return warnings
    for role, track in tracks.items():
        if not isinstance(track, Mapping):
            continue
        for key in track:
            if key not in TRACK_KEYS:
                warnings.append(
                    _unknown_key_message(f"Tracks.{role}.{key}", key, TRACK_KEYS)
                )
        spectrogram = track.get("SPECTROGRAM")
        if isinstance(spectrogram, Mapping):
            warnings.extend(
                _unknown_key_message(
                    f"Tracks.{role}.SPECTROGRAM.{key}", key, SPECTROGRAM_KEYS
                )
                for key in spectrogram
                if key not in SPECTROGRAM_KEYS
            )
    return warnings
