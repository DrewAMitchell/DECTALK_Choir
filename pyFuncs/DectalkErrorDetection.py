"""Detect DECTalk's spoken phoneme-command error in generated WAV audio."""

from __future__ import annotations

from pathlib import Path
import subprocess

import numpy as np
from pydub import AudioSegment, silence
from scipy.signal import correlate


DETECTION_SAMPLE_RATE = 8_000
DEFAULT_SIMILARITY_THRESHOLD = 0.80
_REFERENCE_COMMAND = "[:phoneme arpabet speak on]{setup}[duw<200,12> doo<100,12>]"


def _mono_samples(audio: AudioSegment) -> np.ndarray:
    prepared = audio.set_channels(1).set_frame_rate(DETECTION_SAMPLE_RATE).set_sample_width(2)
    return np.asarray(prepared.get_array_of_samples(), dtype=np.float64)


def _error_only_reference(audio: AudioSegment) -> AudioSegment:
    active_ranges = silence.detect_nonsilent(
        audio.set_channels(1),
        min_silence_len=30,
        silence_thresh=-45,
        seek_step=1,
    )
    error_ranges = [region for region in active_ranges if region[0] >= 600]
    if not error_ranges:
        raise RuntimeError("DECTalk did not produce a usable phoneme-error reference.")
    start_ms = max(0, error_ranges[0][0] - 60)
    end_ms = min(len(audio), error_ranges[-1][1] + 40)
    return audio[start_ms:end_ms]


def create_command_error_reference(
    say_path: str | Path,
    dectalk_setup: str,
    output_path: str | Path,
) -> AudioSegment:
    """Generate a temporary reference matching one track's voice configuration."""

    output = Path(output_path)
    command = _REFERENCE_COMMAND.format(setup=str(dectalk_setup or ""))
    result = subprocess.run(
        [str(say_path), "-w", str(output)],
        input=command.encode("ascii", errors="strict"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not output.is_file():
        details = result.stderr.decode(errors="replace").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Could not generate DECTalk phoneme-error reference: {details}")
    return _error_only_reference(AudioSegment.from_file(output))


def command_error_similarity(audio: AudioSegment, reference: AudioSegment) -> float:
    """Return the strongest zero-mean normalized correlation with ``reference``."""

    samples = _mono_samples(audio)
    reference_samples = _mono_samples(reference)
    if len(samples) < len(reference_samples) or len(reference_samples) == 0:
        return 0.0

    reference_samples -= reference_samples.mean()
    reference_energy = float(np.dot(reference_samples, reference_samples))
    if reference_energy <= 0:
        return 0.0

    correlation = correlate(samples, reference_samples, mode="valid", method="fft")
    window = np.ones(len(reference_samples), dtype=np.float64)
    window_sum = correlate(samples, window, mode="valid", method="fft")
    window_square_sum = correlate(samples * samples, window, mode="valid", method="fft")
    window_energy = window_square_sum - (window_sum * window_sum / len(reference_samples))
    denominator = np.sqrt(np.maximum(window_energy, 1e-9) * reference_energy)
    return float(np.max(np.abs(correlation) / denominator))


def contains_command_error(
    audio: AudioSegment,
    reference: AudioSegment,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> tuple[bool, float]:
    score = command_error_similarity(audio, reference)
    return score >= threshold, score

