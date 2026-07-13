#!/usr/bin/env python3
"""Draft synchronized choir lyric files from plain text and a MIDI track.

The output uses the existing lyrics syntax consumed by choir.py:

- ``N*word`` when one word should span N notes.
- ``A|B|word`` when a multi-vowel word should assign note counts per vowel.

This is intentionally a drafting tool. It uses rests, tight-note gaps, and
phoneme vowel counts to make a useful first pass, then leaves the lyric file in
the same human-editable format choir.py already understands.
"""

import argparse
import contextlib
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

import pyFuncs.MidiProcessing as pymidi
import pyFuncs.PhonemeProcessing as pp


SHORT_WORDS = {
	'a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'i', 'if', "i'd",
	"i'll", "i'm", "i've", 'in', 'is', 'it', "it's", 'me', 'my', 'of',
	'on', 'or', 'the', 'to', 'uh', 'you', "you'd", "you'll", "you're",
	"you've", 'your',
}

EXAMPLE_SONGS = ['DaisyBell', 'AuldLangSyne', 'CarolOfTheBells_Short']


@dataclass
class NoteEvent:
	pitch: int
	velocity: int
	start_ms: float
	end_ms: float

	@property
	def duration_ms(self):
		return max(0.0, self.end_ms - self.start_ms)


@dataclass
class LyricToken:
	word: str
	note_count: int


def load_settings(song_title):
	song_dir = REPO_ROOT / 'songs' / song_title
	settings_path = song_dir / 'settings.yaml'
	if not song_dir.is_dir():
		raise SystemExit(f"Song folder not found: {song_dir}")
	if not settings_path.exists():
		raise SystemExit(f"settings.yaml not found: {settings_path}")

	with settings_path.open('r') as settings_file:
		settings = yaml.safe_load(settings_file) or {}

	if 'Tracks' not in settings:
		raise SystemExit(f"No Tracks section found in {settings_path}")

	return song_dir, settings


def resolve_track(settings, output_part_name):
	tracks = settings['Tracks']
	if output_part_name not in tracks:
		found = ', '.join(tracks.keys())
		raise SystemExit(f"Track '{output_part_name}' not found. Available tracks: {found}")

	track = tracks[output_part_name] or {}
	return {
		'output_name': output_part_name,
		'lyrics_filename': track.get('LYRICS_FILENAME', output_part_name),
		'track_filename': track.get('TRACK_FILENAME', output_part_name),
	}


def find_midi_file(song_dir):
	midi_files = sorted(path for path in song_dir.iterdir() if path.suffix.lower() == '.mid')
	if not midi_files:
		raise SystemExit(f"No .mid file found in {song_dir}")
	return midi_files[0]


def load_track_notes(song_dir, settings, midi_track_name):
	midi_file = find_midi_file(song_dir)
	midi_data = pymidi.loadMidiData(str(midi_file), printInfo=False)

	for midi_track in midi_data:
		if midi_track['title'] != midi_track_name:
			continue

		if len(midi_track['note']) == 0:
			raise SystemExit(f"MIDI track '{midi_track_name}' has no notes")

		if 'tempoEmergencyOverride' in settings:
			tempo_ms = float(settings['tempoEmergencyOverride'])
		else:
			tempo_ms = midi_track['tempo'] / 1000

		ticks_per_beat = midi_track['ticks_per_beat']
		notes = []

		ends = list(midi_track['end'])
		for ii in range(len(ends) - 1):
			if ends[ii] >= midi_track['start'][ii+1]:
				ends[ii] = midi_track['start'][ii+1]

		for ii in range(len(midi_track['note'])):
			start_ms = midi_track['start'][ii] * tempo_ms / ticks_per_beat
			end_ms = ends[ii] * tempo_ms / ticks_per_beat
			notes.append(NoteEvent(
				pitch=midi_track['note'][ii],
				velocity=midi_track['velocity'][ii],
				start_ms=start_ms,
				end_ms=end_ms,
			))

		beat_ms = tempo_ms
		bpm = 60000 / beat_ms if beat_ms > 0 else 120
		return notes, beat_ms, bpm, midi_file

	found = ', '.join(track['title'] for track in midi_data)
	raise SystemExit(f"MIDI track '{midi_track_name}' not found in {midi_file}. Found: {found}")


def default_threshold_ms(settings, name_ms, name_beats, fallback_beats, beat_ms):
	if name_ms in settings:
		return float(settings[name_ms])
	if name_beats in settings:
		return float(settings[name_beats]) * beat_ms
	return fallback_beats * beat_ms


def split_note_phrases(notes, phrase_gap_ms):
	if not notes:
		return []

	phrases = [[notes[0]]]
	for note in notes[1:]:
		gap_ms = note.start_ms - phrases[-1][-1].end_ms
		if gap_ms >= phrase_gap_ms:
			phrases.append([note])
		else:
			phrases[-1].append(note)
	return phrases


def parse_lyric_token(raw_token):
	token = raw_token.strip()
	if len(token) == 0:
		return None
	if token.startswith('['):
		try:
			if pp.parseLineTimingToken(token) is not None:
				return None
		except ValueError:
			return None

	note_count = 1
	word = token

	if '*' in word:
		prefix, rest = word.split('*', 1)
		if prefix.isdigit():
			note_count = int(prefix)
			word = rest

	if '|' in word:
		parts = word.split('|')
		if len(parts) > 1 and all(part.isdigit() for part in parts[:-1]):
			note_count = sum(int(part) for part in parts[:-1])
			word = parts[-1]

	if len(word) == 0:
		return None

	return LyricToken(word=word, note_count=max(1, note_count))


def read_lyric_token_lines(path):
	if not path.exists():
		raise SystemExit(f"Lyric source file not found: {path}")

	lines = []
	lyric_repetitions = 1
	for line in path.read_text().splitlines():
		stripped = line.strip()
		if len(stripped) == 0 or stripped.startswith('#'):
			continue
		tokens = []
		for raw_token in stripped.split():
			if raw_token.startswith('#'):
				break
			if raw_token.startswith('!'):
				try:
					lyric_repetitions = int(raw_token.split('!')[-1])
				except ValueError:
					raise SystemExit(f"Invalid repeat token '{raw_token}' in {path}")
				continue

			token = parse_lyric_token(raw_token)
			if token is not None:
				tokens.append(token)

		if tokens:
			for _ in range(lyric_repetitions):
				lines.append([LyricToken(token.word, token.note_count) for token in tokens])
		lyric_repetitions = 1

	return lines


def read_source_lines(path):
	return [[token.word for token in line] for line in read_lyric_token_lines(path)]


def strip_sync_to_source_lines(token_lines):
	return [[token.word for token in line] for line in token_lines]


def flatten_tokens(lines):
	return [token for line in lines for token in line]


def line_boundaries(lines):
	boundaries = set()
	word_count = 0
	total_words = len(flatten(lines))
	for line in lines:
		word_count += len(line)
		if word_count < total_words:
			boundaries.add(word_count)
	return boundaries


def token_line_boundaries(lines):
	boundaries = set()
	word_count = 0
	total_words = len(flatten_tokens(lines))
	for line in lines:
		word_count += len(line)
		if word_count < total_words:
			boundaries.add(word_count)
	return boundaries


def token_note_boundaries(lines):
	tokens = flatten_tokens(lines)
	boundaries = set()
	note_count = 0
	total_notes = sum(token.note_count for token in tokens)
	for token in tokens:
		note_count += token.note_count
		if note_count < total_notes:
			boundaries.add(note_count)
	return boundaries


def normalize_word_for_compare(word):
	return normalize_word_for_phonemes(word).lower()


def normalize_word_for_phonemes(word):
	word = word.strip()
	while '*' in word:
		prefix, rest = word.split('*', 1)
		if prefix.isdigit():
			word = rest
		else:
			break

	if '|' in word:
		parts = word.split('|')
		if len(parts) > 1 and all(part.isdigit() for part in parts[:-1]):
			word = parts[-1]

	return word.lower()


def phoneme_vowel_count(word):
	word = normalize_word_for_phonemes(word)
	if len(word) == 0:
		return 1

	try:
		if word.startswith('`'):
			phonemes = pp.convertDirectSyllableToPhonemes(word)
		else:
			with contextlib.redirect_stdout(io.StringIO()):
				phonemes = pp.convertWordToPhonemes(word, DECTALK_check=False)
	except Exception:
		phonemes = []

	vowels = 0
	for phoneme in phonemes:
		phoneme_text = str(phoneme).lower()
		if len(phoneme_text) > 0 and phoneme_text[-1].isdigit():
			phoneme_text = phoneme_text[:-1]
		if pp.isDirectVowelPhoneme(phoneme_text):
			vowels += 1

	return max(1, vowels)


def distribute_counts(total_notes, vowel_count):
	if total_notes <= 1:
		return [1]
	if vowel_count <= 1:
		return [total_notes]
	if total_notes < vowel_count:
		return []

	counts = [1 for _ in range(vowel_count)]
	extras = total_notes - vowel_count
	ii = vowel_count - 1
	while extras > 0:
		counts[ii] += 1
		extras -= 1
		ii -= 1
		if ii < 0:
			ii = vowel_count - 1
	return counts


def format_synced_word(word, note_count):
	if note_count <= 1:
		return word

	vowels = phoneme_vowel_count(word)
	if vowels <= 1:
		return f"{note_count}*{word}"

	counts = distribute_counts(note_count, vowels)
	if not counts:
		return f"{note_count}*{word}"

	return f"{'|'.join(str(count) for count in counts)}|{word}"


def boundary_cost(cumulative_notes, phrase_notes, tight_gap_ms, word_gap_ms):
	if cumulative_notes <= 0 or cumulative_notes >= len(phrase_notes):
		return 0.0

	gap_ms = phrase_notes[cumulative_notes].start_ms - phrase_notes[cumulative_notes-1].end_ms
	if gap_ms >= word_gap_ms:
		return -1.5
	if gap_ms <= tight_gap_ms:
		return 3.0
	return 0.25


def word_note_cost(word, note_count, syllable_count):
	if note_count < syllable_count:
		cost = (syllable_count - note_count) * 4.0
	else:
		cost = (note_count - syllable_count) * 1.1

	if normalize_word_for_phonemes(word) in SHORT_WORDS and note_count > 1:
		cost += (note_count - 1) * 2.0

	return cost


def allocate_note_counts(words, phrase_notes, tight_gap_ms, word_gap_ms):
	word_count = len(words)
	note_count = len(phrase_notes)
	if word_count == 0:
		return []
	if note_count <= word_count:
		return [1 for _ in words]

	syllables = [phoneme_vowel_count(word) for word in words]
	total_syllables = max(1, sum(syllables))

	dp = {(0, 0): (0.0, None)}
	for word_index, word in enumerate(words):
		next_dp = {}
		remaining_words = word_count - word_index - 1

		for (ii, notes_used), (cost, _) in dp.items():
			if ii != word_index:
				continue

			max_notes_for_word = note_count - notes_used - remaining_words
			for notes_for_word in range(1, max_notes_for_word + 1):
				new_notes_used = notes_used + notes_for_word
				new_key = (word_index + 1, new_notes_used)

				expected_boundary = round(
					sum(syllables[:word_index+1]) / total_syllables * note_count
				)
				step_cost = word_note_cost(word, notes_for_word, syllables[word_index])
				step_cost += abs(new_notes_used - expected_boundary) * 0.35
				if word_index < word_count - 1:
					step_cost += boundary_cost(
						new_notes_used,
						phrase_notes,
						tight_gap_ms,
						word_gap_ms,
					)

				new_cost = cost + step_cost
				if new_key not in next_dp or new_cost < next_dp[new_key][0]:
					next_dp[new_key] = (new_cost, (notes_used, notes_for_word))

		dp.update(next_dp)

	key = (word_count, note_count)
	if key not in dp:
		return [1 for _ in words]

	counts = []
	curr_word = word_count
	curr_notes = note_count
	while curr_word > 0:
		_, parent = dp[(curr_word, curr_notes)]
		prev_notes, notes_for_word = parent
		counts.append(notes_for_word)
		curr_word -= 1
		curr_notes = prev_notes

	return list(reversed(counts))


def flatten(lines):
	return [word for line in lines for word in line]


def split_tokens_by_note_groups(tokens, note_groups):
	lines = []
	token_index = 0
	notes_used = 0

	for group in note_groups:
		target_notes = notes_used + len(group)
		line = []
		while token_index < len(tokens):
			token = tokens[token_index]
			if line and notes_used + token.note_count > target_notes:
				break
			line.append(token)
			notes_used += token.note_count
			token_index += 1
			if notes_used >= target_notes:
				break
		lines.append(line)

	if token_index < len(tokens):
		if not lines:
			lines.append([])
		lines[-1].extend(tokens[token_index:])

	return lines


def allocate_line_note_counts(source_lines, notes, tight_gap_ms, word_gap_ms):
	line_count = len(source_lines)
	note_count = len(notes)
	if line_count == 0:
		return []

	minimum_notes = [max(1, len(line)) for line in source_lines]
	if sum(minimum_notes) > note_count:
		return minimum_notes

	line_weights = [sum(phoneme_vowel_count(word) for word in line) for line in source_lines]
	total_weight = max(1, sum(line_weights))
	dp = {(0, 0): (0.0, None)}

	for line_index in range(line_count):
		next_dp = {}
		remaining_minimum_notes = sum(minimum_notes[line_index+1:])
		min_notes_for_line = minimum_notes[line_index]

		for (ii, notes_used), (cost, _) in dp.items():
			if ii != line_index:
				continue

			max_notes_for_line = note_count - notes_used - remaining_minimum_notes
			for notes_for_line in range(min_notes_for_line, max_notes_for_line + 1):
				new_notes_used = notes_used + notes_for_line
				new_key = (line_index + 1, new_notes_used)

				expected_boundary = round(
					sum(line_weights[:line_index+1]) / total_weight * note_count
				)
				step_cost = abs(new_notes_used - expected_boundary) * 0.30
				step_cost += abs(notes_for_line - line_weights[line_index]) * 0.15

				if line_index < line_count - 1:
					step_cost += boundary_cost(
						new_notes_used,
						notes,
						tight_gap_ms,
						word_gap_ms,
					) * 1.5
					if 0 < new_notes_used <= len(notes):
						step_cost -= min(2.0, notes[new_notes_used-1].duration_ms / 1000.0)

				new_cost = cost + step_cost
				if new_key not in next_dp or new_cost < next_dp[new_key][0]:
					next_dp[new_key] = (new_cost, (notes_used, notes_for_line))

		dp.update(next_dp)

	key = (line_count, note_count)
	if key not in dp:
		return minimum_notes

	counts = []
	curr_line = line_count
	curr_notes = note_count
	while curr_line > 0:
		prev_notes, notes_for_line = dp[(curr_line, curr_notes)][1]
		counts.append(notes_for_line)
		curr_line -= 1
		curr_notes = prev_notes

	return list(reversed(counts))


def draft_auto_token_lines(source_lines, notes, phrases, tight_gap_ms, word_gap_ms):
	words = flatten(source_lines)
	counts = allocate_note_counts(words, notes, tight_gap_ms, word_gap_ms)
	tokens = [
		LyricToken(word, counts[ii] if ii < len(counts) else 1)
		for ii, word in enumerate(words)
	]
	return split_tokens_by_note_groups(tokens, phrases), []


def draft_line_aware_token_lines(source_lines, notes, tight_gap_ms, word_gap_ms):
	token_lines = []
	warnings = []
	line_note_counts = allocate_line_note_counts(source_lines, notes, tight_gap_ms, word_gap_ms)
	note_index = 0

	for line_index, words in enumerate(source_lines):
		notes_for_line = line_note_counts[line_index] if line_index < len(line_note_counts) else len(words)
		line_notes = notes[note_index:note_index+notes_for_line]
		note_index += notes_for_line

		if not words:
			token_lines.append([])
			continue

		if len(line_notes) < len(words):
			warnings.append(
				f"source line {line_index+1} has {len(line_notes)} notes but {len(words)} words"
			)

		counts = allocate_note_counts(words, line_notes, tight_gap_ms, word_gap_ms)
		token_lines.append([
			LyricToken(word, counts[ii] if ii < len(counts) else 1)
			for ii, word in enumerate(words)
		])

	if note_index < len(notes):
		warnings.append(f"{len(notes)-note_index} MIDI notes were not assigned to lyric lines")

	return token_lines, warnings


def draft_token_lines(source_lines, notes, phrases, tight_gap_ms, word_gap_ms, auto_lines=False):
	if auto_lines:
		return draft_auto_token_lines(source_lines, notes, phrases, tight_gap_ms, word_gap_ms)
	return draft_line_aware_token_lines(source_lines, notes, tight_gap_ms, word_gap_ms)

def render_draft(source_lines, notes, phrases, tight_gap_ms, word_gap_ms, include_comments, auto_lines=False):
	output_lines = []
	token_lines, warnings = draft_token_lines(
		source_lines,
		notes,
		phrases,
		tight_gap_ms,
		word_gap_ms,
		auto_lines=auto_lines,
	)

	for phrase_index, phrase_notes in enumerate(phrases):
		start_ms = round(phrase_notes[0].start_ms)
		end_ms = round(phrase_notes[-1].end_ms)

		if include_comments:
			words = token_lines[phrase_index] if phrase_index < len(token_lines) else []
			output_lines.append(
				f"# phrase {phrase_index+1}: start={start_ms}ms end={end_ms}ms "
				f"notes={len(phrase_notes)} words={len(words)}"
			)

		if phrase_index >= len(token_lines) or not token_lines[phrase_index]:
			output_lines.append('')
			continue

		output_lines.append(' '.join(
			format_synced_word(token.word, token.note_count)
			for token in token_lines[phrase_index]
		))

	return output_lines, warnings


def resolve_thresholds(args, settings, beat_ms):
	phrase_gap_ms = args.phrase_gap_ms
	if phrase_gap_ms is None:
		phrase_gap_ms = default_threshold_ms(
			settings, 'lyricSyncPhraseGapMs', 'lyricSyncPhraseGapBeats', 0.50, beat_ms
		)

	word_gap_ms = args.word_gap_ms
	if word_gap_ms is None:
		word_gap_ms = default_threshold_ms(
			settings, 'lyricSyncWordGapMs', 'lyricSyncWordGapBeats', 0.28, beat_ms
		)

	tight_gap_ms = args.tight_gap_ms
	if tight_gap_ms is None:
		tight_gap_ms = default_threshold_ms(
			settings, 'lyricSyncTightGapMs', 'lyricSyncTightGapBeats', 0.10, beat_ms
		)

	return phrase_gap_ms, word_gap_ms, tight_gap_ms


def compare_token_lines(gold_lines, draft_lines):
	gold_tokens = flatten_tokens(gold_lines)
	draft_tokens = flatten_tokens(draft_lines)
	total_gold_notes = sum(token.note_count for token in gold_tokens)
	total_draft_notes = sum(token.note_count for token in draft_tokens)
	aligned_words = min(len(gold_tokens), len(draft_tokens))
	max_words = max(len(gold_tokens), len(draft_tokens))

	word_mismatches = abs(len(gold_tokens) - len(draft_tokens))
	allocation_abs_error = 0
	exact_allocations = 0

	for ii in range(max_words):
		if ii >= len(gold_tokens):
			allocation_abs_error += draft_tokens[ii].note_count
			continue
		if ii >= len(draft_tokens):
			allocation_abs_error += gold_tokens[ii].note_count
			continue

		gold_token = gold_tokens[ii]
		draft_token = draft_tokens[ii]
		same_word = (
			normalize_word_for_compare(gold_token.word)
			== normalize_word_for_compare(draft_token.word)
		)
		if not same_word:
			word_mismatches += 1

		allocation_abs_error += abs(draft_token.note_count - gold_token.note_count)
		if same_word and draft_token.note_count == gold_token.note_count:
			exact_allocations += 1

	gold_boundaries = token_line_boundaries(gold_lines)
	draft_boundaries = token_line_boundaries(draft_lines)
	boundary_errors = len(gold_boundaries.symmetric_difference(draft_boundaries))
	gold_note_boundaries = token_note_boundaries(gold_lines)
	draft_note_boundaries = token_note_boundaries(draft_lines)
	note_boundary_errors = len(gold_note_boundaries.symmetric_difference(draft_note_boundaries))

	return {
		'gold_words': len(gold_tokens),
		'draft_words': len(draft_tokens),
		'aligned_words': aligned_words,
		'gold_notes': total_gold_notes,
		'draft_notes': total_draft_notes,
		'word_mismatches': word_mismatches,
		'allocation_abs_error': allocation_abs_error,
		'exact_allocations': exact_allocations,
		'gold_boundaries': len(gold_boundaries),
		'draft_boundaries': len(draft_boundaries),
		'boundary_errors': boundary_errors,
		'gold_note_boundaries': len(gold_note_boundaries),
		'draft_note_boundaries': len(draft_note_boundaries),
		'note_boundary_errors': note_boundary_errors,
		'gold_phrase_count': len(gold_lines),
		'draft_phrase_count': len(draft_lines),
		'word_error_pct': word_mismatches / max(1, len(gold_tokens)) * 100,
		'allocation_error_pct': allocation_abs_error / max(1, total_gold_notes) * 100,
		'exact_allocation_pct': exact_allocations / max(1, len(gold_tokens)) * 100,
		'note_total_error_pct': abs(total_draft_notes - total_gold_notes) / max(1, total_gold_notes) * 100,
		'boundary_error_pct': boundary_errors / max(1, len(gold_boundaries)) * 100,
		'note_boundary_error_pct': note_boundary_errors / max(1, len(gold_note_boundaries)) * 100,
		'phrase_count_error_pct': abs(len(draft_lines) - len(gold_lines)) / max(1, len(gold_lines)) * 100,
	}


def validate_part(song_title, part_name, args):
	song_dir, settings = load_settings(song_title)
	track = resolve_track(settings, part_name)
	notes, beat_ms, bpm, midi_file = load_track_notes(song_dir, settings, track['track_filename'])
	phrase_gap_ms, word_gap_ms, tight_gap_ms = resolve_thresholds(args, settings, beat_ms)

	gold_path = song_dir / 'lyrics' / f"{track['lyrics_filename']}.txt"
	gold_lines = read_lyric_token_lines(gold_path)
	source_lines = strip_sync_to_source_lines(gold_lines)
	phrases = split_note_phrases(notes, phrase_gap_ms)

	draft_lines, warnings = draft_token_lines(
		source_lines,
		notes,
		phrases,
		tight_gap_ms,
		word_gap_ms,
		auto_lines=args.auto_lines,
	)
	metrics = compare_token_lines(gold_lines, draft_lines)
	metrics.update({
		'song': song_title,
		'part': part_name,
		'mode': 'auto-lines' if args.auto_lines else 'preserve-lines',
		'bpm': bpm,
		'midi_notes': len(notes),
		'midi_phrases': len(phrases),
		'warnings': len(warnings),
		'gold_path': str(gold_path),
		'midi_file': str(midi_file),
	})
	return metrics


def iter_validation_parts(song_title, requested_part=None):
	song_dir, settings = load_settings(song_title)
	for part_name, track in settings['Tracks'].items():
		if requested_part is not None and part_name != requested_part:
			continue

		track = track or {}
		lyrics_filename = track.get('LYRICS_FILENAME', part_name)
		lyrics_path = song_dir / 'lyrics' / f"{lyrics_filename}.txt"
		if lyrics_path.exists():
			yield part_name


def print_validation_results(results):
	if not results:
		print("No validation samples found")
		return

	print(
		f"{'Song':<24} {'Part':<14} {'Mode':<14} {'Words':>5} {'Notes':>5} "
		f"{'AllocErr':>9} {'Exact':>8} {'NoteBnd':>8} {'LineBnd':>8} {'Phrases':>8} {'Warn':>5}"
	)
	for result in results:
		print(
			f"{result['song']:<24} {result['part']:<14} {result['mode']:<14} "
			f"{result['gold_words']:>5} {result['gold_notes']:>5} "
			f"{result['allocation_error_pct']:>8.1f}% "
			f"{result['exact_allocation_pct']:>7.1f}% "
			f"{result['note_boundary_error_pct']:>7.1f}% "
			f"{result['boundary_error_pct']:>7.1f}% "
			f"{result['phrase_count_error_pct']:>7.1f}% "
			f"{result['warnings']:>5}"
		)

	total_words = sum(result['gold_words'] for result in results)
	total_notes = sum(result['gold_notes'] for result in results)
	total_alloc_error = sum(result['allocation_abs_error'] for result in results)
	total_exact = sum(result['exact_allocations'] for result in results)
	total_boundary_errors = sum(result['boundary_errors'] for result in results)
	total_boundaries = sum(result['gold_boundaries'] for result in results)
	total_note_boundary_errors = sum(result['note_boundary_errors'] for result in results)
	total_note_boundaries = sum(result['gold_note_boundaries'] for result in results)
	total_phrase_error = sum(
		abs(result['draft_phrase_count'] - result['gold_phrase_count'])
		for result in results
	)
	total_phrases = sum(result['gold_phrase_count'] for result in results)
	total_warnings = sum(result['warnings'] for result in results)

	print("\nAggregate:")
	print(f"  samples: {len(results)}")
	print(f"  words: {total_words}")
	print(f"  notes: {total_notes}")
	print(f"  allocation error: {total_alloc_error / max(1, total_notes) * 100:.1f}%")
	print(f"  exact word allocations: {total_exact / max(1, total_words) * 100:.1f}%")
	print(f"  note boundary error: {total_note_boundary_errors / max(1, total_note_boundaries) * 100:.1f}%")
	print(f"  line boundary error: {total_boundary_errors / max(1, total_boundaries) * 100:.1f}%")
	print(f"  phrase count error: {total_phrase_error / max(1, total_phrases) * 100:.1f}%")
	print(f"  warnings: {total_warnings}")


def run_validation(args):
	if args.validate_examples:
		songs = EXAMPLE_SONGS
		requested_part = None
	else:
		if args.song is None:
			raise SystemExit("A song is required unless --validate-examples is used")
		songs = [args.song]
		requested_part = args.part

	results = []
	for song_title in songs:
		for part_name in iter_validation_parts(song_title, requested_part):
			try:
				results.append(validate_part(song_title, part_name, args))
			except SystemExit as exc:
				print(f"Skipping {song_title}/{part_name}: {exc}")

	print_validation_results(results)


def build_arg_parser():
	parser = argparse.ArgumentParser(
		description="Draft a synced lyrics file from plain text and a configured MIDI track."
	)
	parser.add_argument('song', nargs='?', help="Song folder under songs/")
	parser.add_argument('part', nargs='?', help="Output part name under Tracks:")
	parser.add_argument(
		'--text-file',
		help="Plain lyric source. Defaults to the configured lyrics file if no .raw.txt exists.",
	)
	parser.add_argument(
		'--output',
		help="Draft output path. Defaults to outputs/<song>/lyrics_drafts/<part>.txt",
	)
	parser.add_argument(
		'--apply',
		action='store_true',
		help="Write directly to songs/<song>/lyrics/<LYRICS_FILENAME>.txt instead of outputs/.",
	)
	parser.add_argument(
		'--overwrite',
		action='store_true',
		help="Allow replacing an existing output file.",
	)
	parser.add_argument(
		'--auto-lines',
		action='store_true',
		help="Split all source words across detected MIDI phrases instead of preserving source line breaks.",
	)
	parser.add_argument(
		'--validate',
		action='store_true',
		help="Benchmark the drafter against the configured perfected lyric file.",
	)
	parser.add_argument(
		'--validate-examples',
		action='store_true',
		help="Benchmark all configured tracks in DaisyBell, AuldLangSyne, and CarolOfTheBells_Short.",
	)
	parser.add_argument(
		'--phrase-gap-ms',
		type=float,
		help="Rest threshold that starts a new lyric phrase.",
	)
	parser.add_argument(
		'--word-gap-ms',
		type=float,
		help="Gap threshold where the assistant prefers a word boundary.",
	)
	parser.add_argument(
		'--tight-gap-ms',
		type=float,
		help="Gap threshold where the assistant avoids inserting a word boundary inside a tight cluster.",
	)
	parser.add_argument(
		'--no-comments',
		action='store_true',
		help="Do not include phrase timing comments in the drafted lyrics.",
	)
	return parser


def main():
	args = build_arg_parser().parse_args()
	if args.validate or args.validate_examples:
		run_validation(args)
		return

	if args.song is None or args.part is None:
		raise SystemExit("song and part are required unless --validate-examples is used")

	song_dir, settings = load_settings(args.song)
	track = resolve_track(settings, args.part)
	notes, beat_ms, bpm, midi_file = load_track_notes(song_dir, settings, track['track_filename'])
	phrase_gap_ms, word_gap_ms, tight_gap_ms = resolve_thresholds(args, settings, beat_ms)

	lyrics_dir = song_dir / 'lyrics'
	raw_source = lyrics_dir / f"{track['lyrics_filename']}.raw.txt"
	configured_source = lyrics_dir / f"{track['lyrics_filename']}.txt"
	if args.text_file:
		source_path = Path(args.text_file)
	elif raw_source.exists():
		source_path = raw_source
	else:
		source_path = configured_source

	source_lines = read_source_lines(source_path)
	phrases = split_note_phrases(notes, phrase_gap_ms)

	output_lines, warnings = render_draft(
		source_lines,
		notes,
		phrases,
		tight_gap_ms,
		word_gap_ms,
		include_comments=not args.no_comments,
		auto_lines=args.auto_lines,
	)

	if args.apply:
		output_path = configured_source
	elif args.output:
		output_path = Path(args.output)
	else:
		output_path = REPO_ROOT / 'outputs' / args.song / 'lyrics_drafts' / f"{args.part}.txt"

	if output_path.exists() and not args.overwrite:
		raise SystemExit(f"Refusing to overwrite existing file without --overwrite: {output_path}")

	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text('\n'.join(output_lines).rstrip() + '\n')

	print(f"Song: {args.song}")
	print(f"Output part: {args.part}")
	print(f"Lyric source: {source_path}")
	print(f"MIDI source: {midi_file} :: {track['track_filename']}")
	print(f"Tempo estimate: {bpm:.1f} BPM")
	print(f"Thresholds: phrase_gap={phrase_gap_ms:.1f}ms word_gap={word_gap_ms:.1f}ms tight_gap={tight_gap_ms:.1f}ms")
	print(f"Phrases: {len(phrases)}   Source lines: {len(source_lines)}")
	print(f"Wrote: {output_path}")

	if warnings:
		print("\nWarnings:")
		for warning in warnings:
			print(f"  - {warning}")


if __name__ == '__main__':
	main()
