from pydub import AudioSegment

from pyFuncs.DectalkErrorDetection import command_error_similarity, contains_command_error


def _tone(frequency: int, duration_ms: int, frame_rate: int = 8000) -> AudioSegment:
    from pydub.generators import Sine

    return Sine(frequency, sample_rate=frame_rate).to_audio_segment(duration=duration_ms)


def test_command_error_detector_finds_reference_inside_longer_audio() -> None:
    reference = _tone(431, 240) + _tone(683, 260)
    rendered = _tone(220, 300) + reference + _tone(330, 300)

    detected, score = contains_command_error(rendered, reference)

    assert detected is True
    assert score > 0.99


def test_command_error_detector_rejects_unrelated_audio() -> None:
    reference = _tone(431, 240) + _tone(683, 260)
    rendered = _tone(220, 800)

    assert command_error_similarity(rendered, reference) < 0.3
