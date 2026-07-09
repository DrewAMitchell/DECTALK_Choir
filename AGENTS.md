# Agent Guidelines for DECTALK_Choir

This repo is the DECTALK choir/song compiler.

## Bring-Up

- Work from the repo root: `<repo cwd>`.
- Use the checked local venv on Windows:
  ```powershell
  .\.venv\Scripts\python.exe choir.py DaisyBell
  .\.venv\Scripts\python.exe -m py_compile choir.py pyFuncs\PhonemeProcessing.py pyFuncs\MidiProcessing.py
  ```
- `choir.py` expects `say.exe` in the repo root and renders by launching many `say.exe` processes in parallel.
- Generated files go under `outputs/<Song>/`. Do not edit generated outputs as source of truth.
- Useful render flags:
  - `-vis`: generate spectrogram visualization after audio.
  - `-plt`: generate phoneme plot images.
  - `-play`: play final mixed audio.
- The full render path depends on `pydub`, `pyrubberband`, DECtalk `say.exe`, and audio tooling such as ffmpeg/rubberband being available on the host.

## Git And Files

- The worktree often contains Drew's in-progress changes. Do not revert or overwrite unrelated edits.
- `outputs/*` is ignored.
- `.gitignore` ignores `songs/*` except the included example songs. New song folders or local experiment songs may not appear in `git status`.
- If a generated or ignored song asset matters, mention its path explicitly in the handoff.

## Song Layout

Each song lives under `songs/<SongName>/`:

- One `.mid` file is selected from the song folder.
- Lyrics live in `songs/<SongName>/lyrics/*.txt`.
- Per-song settings live in `songs/<SongName>/settings.yaml`.
- Final stems are exported to `outputs/<SongName>/_tracks/*.wav`.
- Final mix is exported to `outputs/<SongName>/_finished/<SongName>.wav`.

## Settings Model

Top-level settings include:

- `noteOffset`: MIDI-to-DECTALK pitch offset. `-48` is the normal starting point; some songs use nearby values like `-52`.
- `minDectalkPitch`: lower bound for generated DECTALK pitch values. The script wraps low notes upward by octaves to avoid negative pitch values.
- `consonantFractionTarget`, `consonantMinMs`, `consonantMaxMs`: control consonant timing.
- `gapMendMs`: folds tiny MIDI gaps into the previous note instead of emitting a rest. Tracks can override with `GAP_MEND_MS`.

Under `Tracks:`, the YAML key is the output part name. It controls output folders, partial text files, stems, and final mix labels.

- `LYRICS_FILENAME`: lyric file stem in `lyrics/`. Defaults to the output part name.
- `TRACK_FILENAME`: MIDI track title to read. Defaults to the output part name.
- This allows separate output identities while sharing sources:
  ```yaml
  Tracks:
    Tenor1:
      LYRICS_FILENAME: Tenor
      TRACK_FILENAME: Tenor1

    Tenor2:
      LYRICS_FILENAME: Tenor
      TRACK_FILENAME: Tenor2
  ```
- If multiple outputs share one MIDI source, note conversion is still source-level. Gap mending uses the largest configured threshold for that MIDI source.

Per-track audio controls:

- `DEC_SETUP`: DECTALK setup prefix, commonly voice/head-size commands like `[:np][:dv hs 120]`.
- `VOLUME_ADJUST_DB`: fixed gain for the rendered track.
- `PITCH_SHIFT`: musical transposition in semitones after `noteOffset`.
- `OCTAVE_BOOST`: render cleanup shift. DECTALK sings lower/slower, then the WAV is sped back up.
- `PITCH_WRAP_SHIFT`: optional manual pitch wrap override. Leave unset for automatic low-note wrapping.
- `PITCH_VOLUME_BOOST_*`: pitch-dependent gain for high notes that get too quiet.
- `SEGMENT_NORMALIZE_*`: measured per-segment boost for weak generated phonemes, with a peak guard.

## MIDI Rules

- MIDI matching uses track titles inside the `.mid`, not filenames.
- Each selected MIDI track should be monophonic. Split chords into separate MIDI tracks.
- The compiler uses note start, end, pitch, velocity, tempo, and ticks-per-beat.
- Overlapping notes are clamped so a note cannot extend beyond the next note's start.
- Tiny rests can be folded into the previous note with `gapMendMs` / `GAP_MEND_MS`; this is useful for transcription artifacts such as 20 ms gaps.

## Lyrics And Phonemes

Lyrics are line-sensitive. One lyric line becomes one compiled phrase, and line breaks are useful for fixing sync.

Supported lyric syntax:

- `# comment`: ignored.
- `!X words`: repeat the current lyric line X times.
- `X*word`: spread one word across X notes.
- `X|Y|word`: assign note counts to vowels in a word.
- `` `phonemes``: direct DECTALK/ARPAbet-style phoneme syllable input.

Phoneme conversion details:

- Normal words use `cmudict`.
- If a word is missing from `cmudict`, the converter tries to parse it as direct DECTALK phonemes before failing. This is intentional so advanced users can type phoneme-like words such as custom `aa n s` style spellings.
- Known pronunciation overrides live in `pyFuncs/PhonemeProcessing.py` in `Pronunciation_Overrides`.
- Current important overrides include `to -> T UW1`, `the -> TH IY0`, `feeling -> F IY1 L IY0 NG`, and `dong -> D AO1 NG`.
- `IH` followed by `NG` is normalized to `IY NG`. `IH N` is left alone.
- CMU `hh` is converted to DECTALK `hx`; `y` is converted to `yx`.

## Consonant/Vowel Timing

- Keep consonants and vowels as separate emitted phonemes when timing matters.
- DECTALK may not honor the duration of combined-looking phonemes the way it honors separated phonemes. For example, `eh r` enforces timing better than a combined `ehr` shape.
- Direct phoneme syllables are split with longest-known-phoneme matching, then vowels are stressed for singing.
- Be careful with syllables that start with consonants, such as `` `daa`` or `` `llao``. The compiler must assign the current note pitch to consonants as well as vowels, or the start of the syllable can produce unstable pitch artifacts.

## Audio Output Invariants

- Each output track stem should be padded or trimmed to the same target length before the final mix.
- Pitch values passed to DECTALK should not be negative. Use `minDectalkPitch`, automatic wrapping, or `PITCH_WRAP_SHIFT` instead of allowing negative pitch output.
- High soprano or octave-boosted parts may need both fixed gain and segment normalization because DECTALK can get quiet at peak notes.
- When changing duration, note matching, or phoneme splitting logic, compare output lengths and inspect generated partial `.txt` files before assuming the audio backend is the cause.

## Validation Checklist

For code changes:

```powershell
.\.venv\Scripts\python.exe -m py_compile choir.py pyFuncs\PhonemeProcessing.py pyFuncs\MidiProcessing.py
```

For settings/source matching, confirm each configured output has both a lyric source and MIDI source. Example songs that should remain valid:

- `DaisyBell`
- `AuldLangSyne`
- `CarolOfTheBells_Short`
- `CantHelpFalling` if present locally
- `idk` if present locally

For audio-affecting changes, rerender at least one small example and inspect:

- `outputs/<Song>/_tracks/*.wav` lengths match.
- `outputs/<Song>/_finished/<Song>.wav` exists and has the expected mix.
- Generated partial text files under `outputs/<Song>/<Part>/*.txt` have sane phoneme durations and non-negative pitch values.
