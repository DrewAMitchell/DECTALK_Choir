from pydub.generators import Sine

from pyFuncs.AudioSafety import apply_note_peak_target


def tone_at_peak(peak_dbfs: float):
    tone = Sine(440).to_audio_segment(duration=250)
    return tone + (peak_dbfs - tone.max_dBFS)


def test_note_peak_target_boosts_weak_notes():
    normalized, gain = apply_note_peak_target(tone_at_peak(-20), -5)

    assert abs(gain - 15) < 0.01
    assert abs(normalized.max_dBFS - -5) < 0.05


def test_note_peak_target_attenuates_hot_notes():
    normalized, gain = apply_note_peak_target(tone_at_peak(-1), -5)

    assert abs(gain - -4) < 0.01
    assert abs(normalized.max_dBFS - -5) < 0.05


def test_note_peak_target_caps_extreme_boost():
    normalized, gain = apply_note_peak_target(tone_at_peak(-50), -5, max_boost_db=30)

    assert gain == 30
    assert abs(normalized.max_dBFS - -20) < 0.05
