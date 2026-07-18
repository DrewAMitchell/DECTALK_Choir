# Agent Guidelines for DECTALK_Choir

This repo is the DECTALK choir/song compiler.

## Bring-Up

- Work from the repo root: `<repo cwd>`.
- Use the checked local venv on Windows:
  ```powershell
  .\.venv\Scripts\python.exe choir.py DaisyBell
  .\.venv\Scripts\python.exe -m py_compile choir.py pyFuncs\PhonemeProcessing.py pyFuncs\MidiProcessing.py
  ```
- Runtime Python packages are pinned in `requirements.txt`; contributor-only packages such as `pytest` belong in `requirements-dev.txt`. Keep both synchronized with imports and the release runtime before adding dependencies.
- `choir.py` expects `say.exe` in the repo root and renders by launching many `say.exe` processes in parallel.
- `choir_studio/` is the Tauri/React application. It is the only supported GUI. Run its frontend checks with `npm.cmd run check` and `npm.cmd run build` from that folder; run the native check from `choir_studio/src-tauri/` with `cargo check`. Its native bridge owns long-running subprocesses so the web view must not call render scripts directly.
- Generated files go under `songs/<Song>/outputs/`. Do not edit generated outputs as source of truth.
- Rendering is partial by design: `choir.py` skips empty, comment-only, or unconvertible lyric roles and renders the remaining roles with valid MIDI. It exits with a clear no-renderable-parts error only when none remain.
- Studio Review persists its render set as `Tracks.<role>.RENDER_ENABLED` in `settings.yaml`. The compiler honors those values for direct CLI renders; Studio also passes the selected roles through `DECTALK_CHOIR_RENDER_ROLES` for its job. Roles need valid MIDI plus either configured lyrics or a valid Studio lyric candidate/note skeleton to be enabled.
- Useful render flags:
  - `-vis`: generate spectrogram visualization after audio.
  - `-plt`: generate phoneme plot images.
  - `-play`: play final mixed audio.
- The full render path depends on `pydub`, DECtalk `say.exe`, and FFmpeg being available on the host. Normal-speech lyric runs use FFmpeg's pitch-preserving `atempo` filter when they exceed their claimed MIDI window.

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
- `notePeakTargetDbfs`: automatic per-note peak target, defaulting to `-5.0 dBFS`. The renderer adjusts every non-silent sung MIDI-note group bidirectionally after pitch correction, so weak and hot registers converge without a hand-authored pitch curve.
- `ignoreMidiVelocity`: defaults to `true`, so MIDI velocity never changes rendered loudness unless explicitly enabled. `velocityVolumeScaleDb` controls the opt-in dynamic range.
- `consonantFractionTarget`, `consonantMinMs`, `consonantMaxMs`: control general consonant timing. `codaMaxMs` caps the complete ending consonant cluster of a one-vowel word spread over multiple notes; tracks can override it with `CODA_MAX_MS`.
- `gapMendMs`: folds tiny MIDI gaps into the previous note instead of emitting a rest. Tracks can override with `GAP_MEND_MS`.
- `minimumNoteDurationMs`: extends short notes only into available following rests, preserving every later onset and cross-track synchronization. Tracks can override with `MINIMUM_NOTE_DURATION_MS`; render logs distinguish extended notes from notes that remain constrained.
- `RENDER_ENABLED`: persisted render participation. Defaults to `true`; Studio disables it for roles excluded from rendering and uses the same set for spectrogram video generation.
- `SPECTROGRAM`: per-track visual ownership boundary. Its children contain `COLOR_HSB`, fractional `[size, left, top]` `POSITION`, optional label/voice/head-size fields, independent label/current-word font and size controls, and current-word color/display settings. Do not add new flat `VID_*` settings; legacy flat values are read only for migration.
- `spectrogramVideo.intermediateAnimationMode`: song-level output policy with `delete`, `compress`, and `keep` modes, defaulting to `delete`. Final videos use H.264 CRF 23 with AAC audio. Post-composition handling runs only after the final video succeeds; `compress` archives clips in parallel and retains any source whose conversion fails.

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
- Automatic note leveling groups all phonemes from one MIDI note before measuring and correcting its peak, preserving consonant/vowel balance. MIDI velocity is applied afterward when explicitly enabled, followed by `VOLUME_ADJUST_DB` on the complete stem.
- Studio Review's beta **Voice** selector replaces only the `[:n?]` directive in `DEC_SETUP`; it preserves head size and every other setup command. **Head size** writes or replaces only `[:dv hs N]`. This engine clamps values below `65` to the same effective `hs 65` voice.
- `IGNORE_MIDI_VELOCITY`: defaults to `true`; leave it enabled unless the MIDI intentionally encodes dynamics.
- `VELOCITY_VOLUME_SCALE_DB`: optional velocity dynamic range, used only when `IGNORE_MIDI_VELOCITY` is `false`.

## MIDI Rules

- MIDI matching uses track titles inside the `.mid`, not filenames.
- Unnamed MIDI tracks use the stable fallback title `Track NN` (zero-based MIDI track index) across inspection and lyric drafting.
- Each selected MIDI track should be monophonic. Split chords into separate MIDI tracks.
- The compiler uses note start, end, pitch, velocity, tempo, and ticks-per-beat.
- Overlapping notes are clamped so a note cannot extend beyond the next note's start.
- Tiny rests can be folded into the previous note with `gapMendMs` / `GAP_MEND_MS`; this is useful for transcription artifacts such as 20 ms gaps.
- `tools/split_polyphonic_midi.py` safely expands every overlapping-note source track into the minimum number of monophonic voice tracks. It preserves note spans/controllers, creates usable track names for unnamed inputs, verifies note preservation, and never overwrites its source MIDI.
- Studio exposes polyphonic splitting from affected items in both the Align track rail and the right side of the Align toolbar. Inspection polyphony and note counts are computed dataclass properties, so the bridge serializer must project them explicitly. The modal analyzes the selected configured track without writing files, previews the resulting lane names, ranges, and note counts, and invokes the established splitter only after explicit export. Other MIDI tracks pass through unchanged. Replacing the working MIDI is explicit, creates a one-time `.bak` backup, preserves the original track name on voice one, and invalidates stale alignment maps so they rebuild against the new notes. Do not make Align mutate MIDI implicitly. The renderer sequentializes overlaps and can tolerate short transitions, but simultaneous notes collapse to zero duration; it does not produce true within-role chord polyphony.
- Studio can scaffold a new song from a MIDI selected anywhere on disk, including `Downloads`: it copies `<Song>.mid`, generates `settings.yaml` roles from named note tracks, and creates comment-only lyric inputs. New scaffolds should use Draft -> Note skeleton before Align.

## Lyrics And Phonemes

Lyrics are line-sensitive. One lyric line becomes one compiled phrase, and line breaks are useful for fixing sync.

Supported lyric syntax:

- `# comment`: ignored.
- `!X words`: repeat the current lyric line X times.
- `X*word`: spread one word across X notes.
- `X|Y|word`: assign note counts to vowels in a word.
- `~word`: speak the word in the track's normal DECTALK voice while still claiming its assigned MIDI time. Counted forms such as `2*~word` are supported; consecutive spoken words render as one natural run and are time-fitted to their combined note window.
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
- `say.exe` vocalizes "command error in phoneme" into its WAV while still returning exit code `0` with no stdout/stderr. `choir.py` preflights emitted symbols, then compares raw partial WAVs against an intentional temporary error reference before mixing. The reference exists only in an OS temporary directory and is never surfaced in Studio or retained with song outputs.
- When a one-vowel word claims three or more notes, diphthongs sustain their nucleus and delay the glide and coda. For example, `3*time` allocates `t aa | ih | m` instead of repeating `ay` on every note.
- Timed lyric lines are padded with silence when shorter than the requested duration and trimmed from the end when longer.

## Lyric Sync Assistant

- Use `tools/lyric_sync_assistant/assistant.py` to draft lyrics from plain text and a MIDI track before hand-editing.
- Example raw lyric inputs live in `tools/lyric_sync_assistant/examples/`. They are reverse-engineered from one curated track per original example song and are useful for smoke tests, not expected-perfect outputs.
- The tool reads `TRACK_FILENAME` and `LYRICS_FILENAME` from `settings.yaml`.
- By default it writes safe drafts under `songs/<Song>/outputs/lyrics_drafts/<Part>.txt`.
- The drafter never replaces the configured render lyric. Publish only through Studio **Apply to source** or `alignment.py --apply --overwrite` after alignment.
- It prefers `songs/<Song>/inputs/lyrics/<LYRICS_FILENAME>.transcript.txt` as the plain lyric source when present; otherwise it reads the configured aligned `.txt`.
- Without `--auto-lines`, source line breaks are kept as lyric-phrase hints while notes are aligned globally to the MIDI track.
- `--auto-lines` flattens source words, aligns them globally to the MIDI track, then splits the output at detected MIDI rest phrases.
- Transcript drafts emit renderer-valid `[MM:SS] lyric` lines. Existing `[timestamp]` and `[timestamp|duration]` prefixes are preserved; plain input receives MIDI-derived starts. A single untimestamped bulk block is automatically split at MIDI phrase rests; use `--auto-lines` for multiline untimestamped input. Diagnostic `#` lines are opt-in with `--comments`.
- `--placeholder [phoneme]` creates a note-level direct-phoneme skeleton; it defaults to `` `duw`` and emits one placeholder per MIDI note, grouped by phrase gaps with a maximum of eight consecutive notes per generated line.
- Transcript source lines are normalized before dictionary lookup. Commas and unsupported punctuation are removed; apostrophes and existing lyric syntax such as timestamps, repeats, and count markers are retained.
- Phrase, word-boundary, and tight-cluster thresholds are BPM-relative by default; override with `--phrase-gap-ms`, `--word-gap-ms`, and `--tight-gap-ms` when a MIDI export needs different sync boundaries.
- The standalone `tools/lyric_sync_assistant/alignment.py` writes a note-by-note report and renderer-ready copy under `songs/<Song>/outputs/lyrics_aligned/`. Choir Studio keeps one durable working candidate at `songs/<Song>/outputs/lyrics_drafts/<Part>.txt` with its report beside it. **Note skeleton** only generates editable phrase-broken placeholder text in the editor; **Draft timing** turns that same editor text into the working candidate and captures a missing `songs/<Song>/inputs/lyrics/<LYRICS_FILENAME>.transcript.txt` once. Transcript files are immutable through Studio. Working candidates are never render sources. **Apply to source** validates and publishes the candidate to the configured `.txt`, which is the only lyric file `choir.py` renders.
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

Studio keeps lyric editing inside the application. Its primary stages flow from `Align` to `Render Audio`; it restores the last valid song, role, stage, and theme from local storage. Align owns MIDI playback and navigation, opens lyric drafting as a focused overlay, and presents words and durations directly above their notes. Clicking a lyric block selects it for direct edge dragging or phrase-local minus/plus note-count controls. Neighboring lyric units re-fit automatically. Inline Insert lyric controls add a word before or after the selected block. Ctrl-dragging a MIDI note creates a persisted virtual split: a selected zero-note word in the same phrase receives the new same-pitch segment, otherwise the current owner receives it. The candidate keeps normal count-based lyric syntax; its JSON is GUI working state, while the applied `.alignment/<Role>.json` supplies virtual MIDI splits to `choir.py` and word cues to spectrogram rendering. Removing the last word in a phrase removes that phrase and globally reflows later ownership; the final lyric unit remains protected. Roles with the same `LYRICS_FILENAME` can adopt another role's saved alignment candidate; this makes an independent target candidate and remaps word ownership by musical time when note counts differ. Apply validates the complete aligned lyric buffer through DECTALK phoneme conversion before publishing it. Align does not mutate source MIDI; use the separate splitter when the actual MIDI structure must change. Render Audio uses its statistics table as the role selector, opens per-role tuning as a modal, and selects spectrogram regions directly on the render canvas.

The header's timed-DECTalk importer is the deliberate exception to the normal draft workflow. It validates the entire pasted command first, converts phoneme pitches through the song's `noteOffset`, groups contiguous same-pitch phonemes into MIDI notes, preserves timed underscores as rests, appends a configured MIDI role, and immediately publishes both its direct-phoneme lyric source and applied alignment. `[:tone frequency_hz,duration_ms]` and `[:t ...]` remain exact render events while using the nearest MIDI pitch as their alignment proxy; never put those event commands into `DEC_SETUP`. Track-wide setup commands must precede the first timed event, while midstream setup changes and dial/conversational scripts are rejected because silently hoisting them would change semantics or produce no deterministic musical alignment. The original pasted command is preserved once as `<Role>.transcript.txt`. Import is atomic and rolls the MIDI, settings, and lyric artifacts back on failure; success selects the new role in Align.

## Consonant/Vowel Timing

- Keep consonants and vowels as separate emitted phonemes when timing matters.
- DECTALK may not honor the duration of combined-looking phonemes the way it honors separated phonemes. For example, `eh r` enforces timing better than a combined `ehr` shape.
- Direct phoneme syllables are split with longest-known-phoneme matching, then vowels are stressed for singing.
- Be careful with syllables that start with consonants, such as `` `daa`` or `` `llao``. The compiler must assign the current note pitch to consonants as well as vowels, or the start of the syllable can produce unstable pitch artifacts.
- On short notes, the consonant minimum must yield enough time to the vowel. Otherwise a valid consonant-plus-vowel placeholder can calculate a negative vowel duration and be silently skipped. The compiler reserves up to 40 ms per vowel before clamping consonants.

## Audio Output Invariants

- Each output track stem should be padded or trimmed to the same target length before the final mix.
- Sung notes target `-5.0 dBFS` peaks automatically. `STEM_PEAK_CEILING_DBFS` guards the complete role stem and song-level `finalMixPeakCeilingDbfs` guards the 32-bit final mix; both safety ceilings default to `-1.0 dBFS` and remain separate from the musical note target.
- Pitch values passed to DECTALK must be inside `minDectalkPitch` / `maxDectalkPitch`. The compiler octave-wraps every emitted pitch into those bounds. Sharps are represented by integer pitch classes, not note-name syntax; for example pitch `% 12 == 1` is `C#`.
- `VOLUME_ADJUST_DB` is the primary manual loudness lever after automatic note correction. High soprano and octave-boosted notes should not require a separate per-semitone gain curve.
- Use `tools/create_head_size_pitch_reference.py` to generate `HeadSizePitchReference`. It renders the same C3-C6 chromatic line at effective head sizes 65, 80, 95, 110, 125, and 140 with automatic loudness correction disabled; after rendering, run it with `--analyze` to write `outputs/analysis/head_size_pitch_levels.csv`.
- Use `tools/create_octave_boost_reference_song.py` to regenerate `OctaveBoostReference`. It creates synchronized 13-note chromatic previews for `OCTAVE_BOOST` values `-12`, `0`, `12`, and `24`, covering audible C2-C6 while holding every temporary DECTALK render to C3-C4. Do not restore the unstable `36` (`+3 octave`) profile.
- When changing duration, note matching, or phoneme splitting logic, compare output lengths and inspect generated partial `.txt` files before assuming the audio backend is the cause.
- `generateSpectrograms.py` renders independent, lossless region-sized track clips with up to four CPU workers, then serially overlays those clips in configured order and muxes one final video. Choir Studio starts that generator as a native background job and polls its status, keeping the UI responsive.
- Spectrogram jobs stream stdout/stderr through the Tauri bridge while running. Preserve the machine-readable `TIMING stage=... seconds=...` and per-track timing lines from `pyFuncs/spectrogramAnimation.py`; Studio uses the stage records for its live duration summary and retains them when verbose logs are trimmed.
- Per-track clips under `outputs/_animation/` are removed only after ffmpeg successfully creates a non-empty final `<Song>.mp4`; retain them when composition fails. Current-word overlays read word cues from the applied alignment sidecar, with the active draft report as fallback.
- Raw MIDI/alignment timestamps do not include the renderer's lead-in. Keep `pyFuncs.AudioTiming.OUTPUT_LEAD_IN_MS` as the single source for both stem placement and synchronized visual word cues.

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

For audio-affecting changes, rerender at least one small example and inspect:

- `songs/<Song>/outputs/_tracks/*.wav` lengths match.
- `songs/<Song>/outputs/_finished/<Song>.wav` exists and has the expected mix.
- Generated partial text files under `songs/<Song>/outputs/<Part>/*.txt` have sane phoneme durations and non-negative pitch values.
