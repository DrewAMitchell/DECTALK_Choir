from enum import IntEnum


SEMITONES_PER_OCTAVE = 12
DEFAULT_DECTALK_REFERENCE_MIDI = 48
DEFAULT_NOTE_OFFSET = -DEFAULT_DECTALK_REFERENCE_MIDI
DEFAULT_MIN_DECTALK_PITCH = 0
DEFAULT_MAX_DECTALK_PITCH = 36


class PitchClass(IntEnum):
	C = 0
	C_SHARP = 1
	D = 2
	D_SHARP = 3
	E = 4
	F = 5
	F_SHARP = 6
	G = 7
	G_SHARP = 8
	A = 9
	A_SHARP = 10
	B = 11


NOTE_NAMES_SHARP = (
	'C',
	'C#',
	'D',
	'D#',
	'E',
	'F',
	'F#',
	'G',
	'G#',
	'A',
	'A#',
	'B',
)


def midi_pitch_name(midiPitch):
	midiPitch = int(round(midiPitch))
	return f"{NOTE_NAMES_SHARP[midiPitch % SEMITONES_PER_OCTAVE]}{midiPitch // SEMITONES_PER_OCTAVE - 1}"


def dectalk_pitch_to_midi(dectalkPitch, referenceMidi=DEFAULT_DECTALK_REFERENCE_MIDI):
	return int(round(dectalkPitch)) + int(referenceMidi)


def dectalk_pitch_name(dectalkPitch, referenceMidi=DEFAULT_DECTALK_REFERENCE_MIDI):
	return midi_pitch_name(dectalk_pitch_to_midi(dectalkPitch, referenceMidi))


def format_dectalk_pitch(dectalkPitch, referenceMidi=DEFAULT_DECTALK_REFERENCE_MIDI):
	dectalkPitch = int(round(dectalkPitch))
	return f"{dectalkPitch}({dectalk_pitch_name(dectalkPitch, referenceMidi)})"


def validate_dectalk_pitch_bounds(minPitch, maxPitch):
	minPitch = int(minPitch)
	maxPitch = int(maxPitch)
	if maxPitch < minPitch:
		raise ValueError(f"maxDectalkPitch {maxPitch} must be >= minDectalkPitch {minPitch}")
	if maxPitch - minPitch < SEMITONES_PER_OCTAVE - 1:
		raise ValueError(
			f"DECTALK pitch bounds {minPitch} -> {maxPitch} must span at least one octave "
			"so every pitch class can be octave-wrapped"
		)


def wrap_dectalk_pitch(dectalkPitch, minPitch=DEFAULT_MIN_DECTALK_PITCH, maxPitch=DEFAULT_MAX_DECTALK_PITCH):
	validate_dectalk_pitch_bounds(minPitch, maxPitch)
	dectalkPitch = int(round(dectalkPitch))
	while dectalkPitch < minPitch:
		dectalkPitch += SEMITONES_PER_OCTAVE
	while dectalkPitch > maxPitch:
		dectalkPitch -= SEMITONES_PER_OCTAVE
	while dectalkPitch < minPitch:
		dectalkPitch += SEMITONES_PER_OCTAVE
	if dectalkPitch > maxPitch:
		raise ValueError(f"DECTALK pitch {dectalkPitch} cannot be wrapped into bounds {minPitch} -> {maxPitch}")
	return dectalkPitch
