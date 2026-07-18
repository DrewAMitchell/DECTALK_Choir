import pytest

from pyFuncs.DectalkStringExport import DectalkStringExportError, build_dectalk_phoneme_string
from pyFuncs.DectalkTrackImport import parse_dectalk_track
from pyFuncs.PhonemeProcessing import SPOKEN_WORD_MARKER, TONE_EVENT_MARKER


def test_exported_string_round_trips_aligned_phonemes_rests_and_tones():
    compiled = [
        [0, ("d", 100, 12, 100, 0), ("uw", 400, 12, 100, 0)],
        [800, (TONE_EVENT_MARKER, 200, 440.0, 100, 1)],
        [1200, ("m", 80, 14, 100, 2), ("ay", 420, 14, 100, 2)],
    ]

    output = build_dectalk_phoneme_string(compiled, "[:np][:dv hs 90]", lambda pitch: round(pitch))
    imported = parse_dectalk_track(output, note_offset=-48)

    assert output.startswith("[:phoneme arpabet speak on][:np][:dv hs 90]")
    assert "_<300,0>" in output
    assert "[:tone 440,200]" in output
    assert [(note.midi_pitch, note.start_ms, note.end_ms, note.event_kind) for note in imported.notes] == [
        (60, 0, 500, "phoneme"),
        (69, 800, 1000, "tone"),
        (62, 1200, 1700, "phoneme"),
    ]


def test_export_rejects_overlapping_phrase_timeline():
    compiled = [
        [0, ("aa", 500, 12, 100, 0)],
        [400, ("iy", 500, 14, 100, 1)],
    ]

    with pytest.raises(DectalkStringExportError, match="overlaps"):
        build_dectalk_phoneme_string(compiled, "[:np]", round)


def test_export_rejects_normal_speech_words():
    compiled = [[0, (SPOKEN_WORD_MARKER, 500, "hello", 100, None)]]

    with pytest.raises(DectalkStringExportError, match="Normal-speech"):
        build_dectalk_phoneme_string(compiled, "[:np]", round)
