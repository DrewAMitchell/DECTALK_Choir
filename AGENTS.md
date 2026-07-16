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
- `choir_studio/` is the Tauri/React application. It is the only supported GUI. Run its frontend checks with `npm.cmd run check` and `npm.cmd run build` from that folder; run the native check from `choir_studio/src-tauri/` with `cargo check`. Its native bridge owns long-running subprocesses so the web view must not call render scripts directly.
- Generated files go under `songs/<Song>/outputs/`. Do not edit generated outputs as source of truth.
- Rendering is partial by design: `choir.py` skips empty, comment-only, or unconvertible lyric roles and renders the remaining roles with valid MIDI. It exits with a clear no-renderable-parts error only when none remain.
- Studio Review persists its render set as `Tracks.<role>.RENDER_ENABLED` in `settings.yaml`. The compiler honors those values for direct CLI renders; Studio also passes the selected roles through `DECTALK_CHOIR_RENDER_ROLES` for its job. Roles need valid MIDI plus either configured lyrics or a valid Studio lyric candidate/note skeleton to be enabled.
- Useful render flags:
  - `-vis`: generate spectrogram visualization after audio.
  - `-plt`: generate phoneme plot images.
  - `-play`: play final mixed audio.
- The full render path depends on `pydub`, `pyrubberband`, DECtalk `say.exe`, and audio tooling such as ffmpeg/rubberband being available on the host.

## Git And Files

- The worktree may contain in-progress user changes. Do not revert or overwrite unrelated edits.
- For non-trivial commits, use a concise imperative subject followed by a blank line and bullet points that name the user-visible features, important safeguards, and packaging or workflow changes. Keep the body high-signal rather than repeating file-level churn.
- Root `outputs/` is legacy-only and ignored. Generated song outputs live in ignored `songs/<Song>/outputs/` folders.
- `.gitignore` ignores `songs/*` except the included example songs. New song folders or local experiment songs may not appear in `git status`.
- If a generated or ignored song asset matters, mention its path explicitly in the handoff.

## Song Layout

Each song lives under `songs/<SongName>/`:

- One `.mid` file is selected from `songs/<SongName>/inputs/`.
- Lyrics live in `songs/<SongName>/inputs/lyrics/*.txt`.
- Per-song settings live in `songs/<SongName>/settings.yaml`.
- Final stems are exported to `songs/<SongName>/outputs/_tracks/*.wav`.
- Final mix is exported to `songs/<SongName>/outputs/_finished/<SongName>.wav`.
- A completed visual render is exported to `songs/<SongName>/outputs/_finished/<SongName>.mp4`.

## Settings Model

Pitch constants and conversion helpers live in `pyFuncs/PitchMapping.py`.

Top-level settings include:

- `noteOffset`: MIDI-to-DECTALK pitch offset. `-48` is the normal starting point; with that default, MIDI `48` (`C3`) emits DECTALK pitch `0`, MIDI `69` (`A4`) emits pitch `21`, and MIDI `84` (`C6`) emits pitch `36`.
- `minDectalkPitch` / `maxDectalkPitch`: inclusive generated DECTALK pitch bounds. Defaults are `0` through `36`, mapped as `C3` through `C6` in this project. The bounds must span at least one octave so every pitch class can be octave-wrapped.
- `pitchVolumeBoostStart`, `pitchVolumeBoostDbPerSemitone`, `pitchVolumeBoostMaxDb`: song-level defaults for the per-track `PITCH_VOLUME_BOOST_*` settings. `24` is `C5`, a useful starting point for high-note loudness compensation. Boost thresholds use the final audible pitch after `OCTAVE_BOOST`.
- `noteNormalizeReferenceMin`, `noteNormalizeReferenceMax`, `noteNormalizeTargetDbfs`, `noteNormalizeMaxBoostDb`, `noteNormalizePeakCeilingDbfs`: song-level defaults for note-level voice leveling. Auto target mode uses the voice's own notes in the reference range when available; the default reference range is `7` through `16` (`G3` through `E4`).
- `ignoreMidiVelocity`: defaults to `true`, so MIDI velocity never changes rendered loudness unless explicitly enabled. `velocityVolumeScaleDb` controls the opt-in dynamic range.
- `consonantFractionTarget`, `consonantMinMs`, `consonantMaxMs`: control consonant timing.
- `gapMendMs`: folds tiny MIDI gaps into the previous note instead of emitting a rest. Tracks can override with `GAP_MEND_MS`.
- `RENDER_ENABLED`: persisted render participation. Defaults to `true`; Studio disables it for roles excluded from rendering and uses the same set for spectrogram video generation.

Under `Tracks:`, the YAML key is the output part name. It controls output folders, partial text files, stems, and final mix labels.

- `LYRICS_FILENAME`: lyric file stem in `inputs/lyrics/`. Defaults to the output part name.
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
- `OCTAVE_BOOST`: render cleanup shift. Positive values make DECTALK sing lower/slower, then the WAV is sped back up. Negative values make DECTALK sing higher/shorter, then the WAV is slowed down. If MIDI contains the desired final octave, use `OCTAVE_BOOST` without `PITCH_SHIFT`. Treat `-24` as too aggressive for this voice path unless a test proves otherwise; it can create low-frequency rumble artifacts.
- `PITCH_WRAP_SHIFT`: optional manual pitch wrap override. Leave unset for automatic low-note wrapping.
- `PITCH_VOLUME_BOOST_*`: pitch-dependent gain for high notes that get too quiet.
- `NOTE_NORMALIZE_*`: preferred per-note voice leveling. It groups all phonemes from one MIDI note before measuring/boosting, preserving consonant/vowel balance inside the note.
- Studio Review's beta **Voice** selector replaces only the `[:n?]` directive in `DEC_SETUP`; it preserves head size and every other setup command. **Head size** writes or replaces only `[:dv hs N]`. Its **Auto-normalize** action can use staged voice/head-size fields before saving, but is measured only for `[:np]` (Perfect Paul); other voices require their own reference measurement.
- `IGNORE_MIDI_VELOCITY`: defaults to `true`; leave it enabled unless the MIDI intentionally encodes dynamics.
- `VELOCITY_VOLUME_SCALE_DB`: optional velocity dynamic range, used only when `IGNORE_MIDI_VELOCITY` is `false`.
- `SEGMENT_NORMALIZE_*`: legacy/surgical measured per-segment boost for weak generated phonemes, with a peak guard. Prefer `NOTE_NORMALIZE_*` for normal singing.

## MIDI Rules

- MIDI matching uses track titles inside the `.mid`, not filenames.
- Unnamed MIDI tracks use the stable fallback title `Track NN` (zero-based MIDI track index) across inspection and lyric drafting.
- Each selected MIDI track should be monophonic. Split chords into separate MIDI tracks.
- The compiler uses note start, end, pitch, velocity, tempo, and ticks-per-beat.
- Overlapping notes are clamped so a note cannot extend beyond the next note's start.
- Tiny rests can be folded into the previous note with `gapMendMs` / `GAP_MEND_MS`; this is useful for transcription artifacts such as 20 ms gaps.
- `tools/split_polyphonic_midi.py` safely expands every overlapping-note source track into the minimum number of monophonic voice tracks. It preserves note spans/controllers, creates usable track names for unnamed inputs, verifies note preservation, and never overwrites its source MIDI.
- Studio's MIDI workflow targets one track from the current working song MIDI for a read-only split dry run, overlays the source track translucently under tentative lanes, and invokes `tools/split_polyphonic_midi.py --track-index N` only for explicit export. Other tracks pass through unchanged; replacing the working MIDI is explicit and creates a `.mid.bak` backup. Do not make Align mutate MIDI.
- Studio can scaffold a new song from a MIDI selected anywhere on disk, including `Downloads`: it copies `<Song>.mid`, generates `settings.yaml` roles from named note tracks, and creates comment-only lyric inputs. New scaffolds should use Draft -> Note skeleton before Align.

## Lyrics And Phonemes

Lyrics are line-sensitive. One lyric line becomes one compiled phrase, and line breaks are useful for fixing sync.

Supported lyric syntax:

- `# comment`: ignored.
- `!X words`: repeat the current lyric line X times.
- `X*word`: spread one word across X notes.
- `X|Y|word`: assign note counts to vowels in a word.
- `[timestamp] words` or `[timestamp|duration] words`: override the compiled partial start time and optional line duration. Example: `[0:40|5000] built for two`. Bare timestamps are seconds; bare durations are milliseconds.
- `` `phonemes``: direct DECTALK/ARPAbet-style phoneme syllable input.

Phoneme conversion details:

- Normal words use `cmudict`.
- If a word is missing from `cmudict`, the converter tries to parse it as direct DECTALK phonemes before failing. This is intentional so advanced users can type phoneme-like words such as custom `aa n s` style spellings.
- Known pronunciation overrides live in `pyFuncs/PhonemeProcessing.py` in `Pronunciation_Overrides`.
- Current important overrides include `to -> T UW1`, `the -> TH IY0`, `feeling -> F IY1 L IY0 NG`, and `dong -> D AO1 NG`.
- `IH` followed by `NG` is normalized to `IY NG`. `IH N` is left alone.
- CMU `hh` is converted to DECTALK `hx`; `y` is converted to `yx`.
- Direct syllables are validated before rendering. `doo` and `dxoo` are not valid direct phoneme syllables; use `duw` and `dxuw`. Invalid direct input fails during lyric conversion rather than producing DECTALK command errors.
- Timed lyric lines are padded with silence when shorter than the requested duration and trimmed from the end when longer.

## Lyric Sync Assistant

- Use `tools/lyric_sync_assistant/assistant.py` to draft lyrics from plain text and a MIDI track before hand-editing.
- Example raw lyric inputs live in `tools/lyric_sync_assistant/examples/`. They are reverse-engineered from one curated track per original example song and are useful for smoke tests, not expected-perfect outputs.
- The tool reads `TRACK_FILENAME` and `LYRICS_FILENAME` from `settings.yaml`.
- By default it writes safe drafts under `songs/<Song>/outputs/lyrics_drafts/<Part>.txt`.
- It only replaces the real lyric file when called with `--apply --overwrite`.
- It prefers `songs/<Song>/inputs/lyrics/<LYRICS_FILENAME>.raw.txt` as the plain lyric source when present; otherwise it reads the configured `.txt`.
- Without `--auto-lines`, source line breaks are kept as lyric-phrase hints while notes are aligned globally to the MIDI track.
- `--auto-lines` flattens source words, aligns them globally to the MIDI track, then splits the output at detected MIDI rest phrases.
- Transcript drafts emit renderer-valid `[MM:SS] lyric` lines. Existing `[timestamp]` and `[timestamp|duration]` prefixes are preserved; plain input receives MIDI-derived starts. A single untimestamped bulk block is automatically split at MIDI phrase rests; use `--auto-lines` for multiline untimestamped input. Diagnostic `#` lines are opt-in with `--comments`.
- `--placeholder [phoneme]` creates a note-level direct-phoneme skeleton; it defaults to `` `duw`` and emits one placeholder per MIDI note, grouped by phrase gaps.
- Transcript source lines are normalized before dictionary lookup. Commas and unsupported punctuation are removed; apostrophes and existing lyric syntax such as timestamps, repeats, and count markers are retained.
- Phrase, word-boundary, and tight-cluster thresholds are BPM-relative by default; override with `--phrase-gap-ms`, `--word-gap-ms`, and `--tight-gap-ms` when a MIDI export needs different sync boundaries.
- The standalone `tools/lyric_sync_assistant/alignment.py` writes a note-by-note report and renderer-ready copy under `songs/<Song>/outputs/lyrics_aligned/`. Choir Studio keeps one durable working candidate at `songs/<Song>/outputs/lyrics_drafts/<Part>.txt` with its report beside it. The Lyrics editor always shows that active candidate when present. **Note skeleton** only generates editable phrase-broken placeholder text in the editor; **Draft timing** turns that same editor text into the active candidate and preserves the pre-draft text under `songs/<Song>/inputs/lyrics/<LYRICS_FILENAME>.raw.txt`. A non-empty candidate is the active lyric source for Review and rendering; its Align edits are immediately renderable. **Apply to source** is only needed to replace the configured lyric input.
- Example:
  ```powershell
  .\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --auto-lines --overwrite
  ```
- Benchmark against perfected example lyrics with:
  ```powershell
  .\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py --validate-examples --auto-lines
  ```
- Validation covers all configured tracks with lyric files in `DaisyBell`, `AuldLangSyne`, and `CarolOfTheBells_Short`.
- Treat note-allocation error, exact word allocation percentage, and note-boundary error as the main quality signals. Line-boundary error is useful, but the curated files also use editorial/subphrase line breaks that are not always equivalent to MIDI rest phrases.
- Current validation baseline is roughly 4.3% note-allocation error in line-aware mode and 4.5% in `--auto-lines` mode across the included perfected examples.

Studio keeps lyric editing inside the application. Its header flows left to right from song selection through `MIDI -> Lyrics -> Align -> Render -> Review`; it restores the last valid song, role, workspace, and theme from local storage. The MIDI tab owns source playback, pitch navigation, duration readout, and horizontal time navigation; `Ctrl+wheel` zooms the time axis around the pointer, the normal wheel or blank-canvas drag pans time, and the compact range display reports the visible window and total duration. The Draft tab edits and saves the safe draft buffer, and the Align tab is a visual canvas: words and durations sit above their notes, the same time controls expose dense passages, and clicking a lyric block selects it for direct edge dragging or phrase-local minus/plus note-count controls. Neighboring lyric units re-fit automatically. Inline Insert lyric controls add a word before or after the selected block. Roles with the same `LYRICS_FILENAME` can adopt another role's saved alignment candidate; this makes an independent target candidate and remaps word ownership by musical time when note counts differ. Save and Apply validate the complete aligned lyric buffer through DECTALK phoneme conversion before writing it. Advanced raw lyric text is disclosure-only. Applying either buffer to `songs/<Song>/inputs/lyrics/` is explicit and confirmed; external editors are optional rather than required. Align does not mutate MIDI; use the separate splitter when note structure must change. Review hides the duplicated role rail and selects roles from its statistics table.

## Consonant/Vowel Timing

- Keep consonants and vowels as separate emitted phonemes when timing matters.
- DECTALK may not honor the duration of combined-looking phonemes the way it honors separated phonemes. For example, `eh r` enforces timing better than a combined `ehr` shape.
- Direct phoneme syllables are split with longest-known-phoneme matching, then vowels are stressed for singing.
- Be careful with syllables that start with consonants, such as `` `daa`` or `` `llao``. The compiler must assign the current note pitch to consonants as well as vowels, or the start of the syllable can produce unstable pitch artifacts.
- On short notes, the consonant minimum must yield enough time to the vowel. Otherwise a valid consonant-plus-vowel placeholder can calculate a negative vowel duration and be silently skipped. The compiler reserves up to 40 ms per vowel before clamping consonants.

## Audio Output Invariants

- Each output track stem should be padded or trimmed to the same target length before the final mix.
- Peak ceilings are safety guards, not only boost limits: `NOTE_NORMALIZE_PEAK_CEILING_DBFS` attenuates already-hot per-note audio after pitch/segment/note boosts, `STEM_PEAK_CEILING_DBFS` guards the complete role stem, and song-level `finalMixPeakCeilingDbfs` guards the 32-bit final mix. All default to `-1.0 dBFS`.
- Pitch values passed to DECTALK must be inside `minDectalkPitch` / `maxDectalkPitch`. The compiler octave-wraps every emitted pitch into those bounds. Sharps are represented by integer pitch classes, not note-name syntax; for example pitch `% 12 == 1` is `C#`.
- High soprano or octave-boosted parts may need both fixed gain and segment normalization because DECTALK can get quiet at peak notes.
- Before tuning high-note gain by pitch alone, use `tools/create_head_size_pitch_reference.py` to generate `HeadSizePitchReference`. It renders the same C3-C6 chromatic line at head sizes 80, 95, 110, 125, and 140 with all loudness correction disabled; after rendering, run it with `--analyze` to write `outputs/analysis/head_size_pitch_levels.csv` for an empirical head-size/pitch comparison.
- When changing duration, note matching, or phoneme splitting logic, compare output lengths and inspect generated partial `.txt` files before assuming the audio backend is the cause.
- `generateSpectrograms.py` prepares independent stem FFT data with up to four CPU workers, then composes and writes one ordered final video. Choir Studio starts that generator as a native background job and polls its status, keeping the UI responsive.
- `outputs/_finished/animation.mp4` is an intermediate video. It is removed only after ffmpeg successfully muxes a non-empty final `<Song>.mp4`; retain it when muxing fails or no final audio is available.

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

- `songs/<Song>/outputs/_tracks/*.wav` lengths match.
- `songs/<Song>/outputs/_finished/<Song>.wav` exists and has the expected mix.
- Generated partial text files under `songs/<Song>/outputs/<Part>/*.txt` have sane phoneme durations and non-negative pitch values.
