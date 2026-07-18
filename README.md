# DECTALK Choir
Making a retro speech synth sing! Able to work with polyphonic choral music. All written in Python3 and runs on Windows. Also generate spectrogram animation for the voices.

## Start Here

DECTALK Choir has two connected workflows:

- **[Choir Studio](choir_studio/README.md)**: the desktop editor for importing and inspecting MIDI, drafting and aligning lyrics, tuning roles, and starting renders.
- **[Choir Renderer](#choir-renderer)**: the underlying Python compiler that turns configured MIDI and lyric files into DECtalk stems, final audio, and optional spectrogram video.

## Process Map

```text
MIDI + lyric transcript
         |
         v
  Choir Studio (Tauri desktop app)
  inspect -> draft -> align -> tune -> choose roles
         |
         | JSON bridge invokes established Python services
         v
  choir.py (renderer)
  MIDI + settings.yaml + lyric candidates -> DECtalk stems -> final mix/video
```

Studio owns the editing experience. `choir.py` remains the single rendering
engine, so command-line renders and Studio renders use the same settings,
lyrics, pitch rules, DECtalk commands, audio processing, and output layout.

## Choir Studio

[![DECTALK Choir Studio MIDI and review workflow](choir_studio/assets/studio-review.png)](choir_studio/assets/studio-overview.mp4)

**[Watch the short Choir Studio overview (MP4)](choir_studio/assets/studio-overview.mp4)** to see the MIDI source view and populated Review workspace with render readiness, pitch ranges, and loudness statistics.

[![Watch the DECTALK Choir demonstration on YouTube](https://img.youtube.com/vi/oPg8LVGdd4I/maxresdefault.jpg)](https://www.youtube.com/watch?v=oPg8LVGdd4I)

**[Watch the DECTALK Choir demonstration on YouTube](https://www.youtube.com/watch?v=oPg8LVGdd4I).**

To start from an existing MIDI, use the **inbox** button beside the song selector.
Choose a `.mid` or `.midi` file and name the song. Studio copies the MIDI into a
new `songs/<Song>/inputs/` workspace, creates one configured role and lyric
placeholder for every note-bearing track, then opens the first role in Align.
Duplicate or filesystem-unsafe MIDI track names are normalized only in the copy;
the selected source file is never modified. Add lyrics or a note skeleton before
enabling each role for rendering.

## Background
Dectalk is a text to speech synthesizer released in 1983. It was famously used by Steven Hawking, and was included in the game Moonbase Alpha to read chat messages aloud. The system allows pronunciation phonemes, even inputting specific pitches and durations. Players of Moonbase Alpha quickly realized that these could be used to sing songs. I think this is absolutely delightful, and really wanted to play with this myself. Rather than copy-paste lines of text into the game, I tracked down a standard version of DECTalk and compiled it line by line.

## Choir Renderer
For a source checkout, create a Python virtual environment and install the runtime dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Contributors can install `requirements-dev.txt` instead to include the test runner. FFmpeg and Rubber Band are external executables and are not installed by pip.

Each song is saved in a folder under /songs. Before compilation, specify source MIDI in `inputs/`, lyrics in `inputs/lyrics/*.txt`, and settings in `settings.yaml`. Run choir.py to compile.
choir.py Usage: python3 choir.py \[options\] \[songFolder\]
Example:
> python3 choir.py -vis AuldLangSyne

Outputs are saved to `songs/<Song>/outputs/`. One folder for each track is generated to save partial outputs. **_tracks** contains each individual track's compiled output, **_animation** contains generated spectrogram animations, and **_finished** contains the final compiled audio and video.

Align can also enable **Phoneme output** for an individual track. Its next audio render writes a reusable, timing-complete DECTalk command to `outputs/_phonemes/<Track>.txt`. Phrase gaps become timed rests, making an imported phoneme track editable in Studio and exportable again after its alignment is corrected. Normal-speech words (`~word`) and overlapping phrase timelines cannot be represented faithfully in one serial command, so those tracks fail export with a clear render error instead of producing misleading data.

Four complete example songs are included: `DaisyBell`, `AuldLangSyne`, `CarolOfTheBells_Short`, and `CantHelpFalling`. Choir Studio can create a complete starting workspace directly from another MIDI file.

## MIDI
In `songs/<Song>/inputs/`, choir.py checks for a single .mid file. I use LMMS to work with MIDI, but other software *should* be able to export compatible files. For each output track in settings.yaml, `TRACK_FILENAME` selects the MIDI track to read. If `TRACK_FILENAME` is omitted, the settings key is used as the MIDI track name. Each track should be monophonic, only playing one note at a time. Split chords into separate tracks. The only data used are note positions, timings, and velocity.

Choir Studio marks configured tracks with simultaneous notes in both the Align rail and Align toolbar. Open either yellow branch warning to preview the minimum monophonic voice lanes and their note counts. The splitter can export a separate MIDI or replace the working MIDI with a one-time `.bak` backup; every untargeted track and every source note is preserved.

The renderer accepts overlapping notes, but one output role is still a monophonic DECTALK voice. Brief transition overlaps are sequentialized by ending the earlier note at the next onset. Simultaneous chord notes therefore collapse to zero-duration events instead of producing multiple voices; split those tracks when each chord voice must remain audible.

### Lyrics
Lyrics should be saved as a .txt file in `songs/<Song>/inputs/lyrics/`. Lyrics are run one line at a time, so desync issues in playback can frequently be fixed by separating words into individual lines. Internally, words are split up into phonemes by **pyFuncs/PhonemeProcessing.py**. If a word can't be converted, try replacing it with a homophone.
Lines starting with **\#** are comments and will be ignored.
> \# Start Repeat

Words starting with **`** are not split into phonemes, allowing very specific input if you're familiar with how DECtalk works.
> `kahl

If a word needs to be played across multiple notes, add X* before it to play X notes across it. The code will attempt to match syllables to notes.
> 2*christmas is here

To specify a number of notes for each vowel, add numbers separated by |. Each vowel will pronounce that many syllables.
> 1|1|christmas is here

Begin a line with !X to repeat X times.
> !2 ding dong

Begin a line with `[timestamp]` to force the partial's start time. Add `|duration` to also force the partial length. Timestamps without units are seconds; durations without units are milliseconds. Durations can also use `s`, `ms`, or clock syntax.
> [0:40|5000] built for two

If the compiled line is shorter than the requested duration, silence is appended. If it is longer, the end of the line is trimmed.

### Lyric Sync Assistant

For a faster first pass, write plain lyrics in `songs/<Song>/inputs/lyrics/<Part>.transcript.txt` or point the tool at any text file. A transcript is immutable once captured by Studio; delete it manually before intentionally replacing the original input. The assistant reads the configured MIDI source track, detects lyric phrases from rests, and writes a working draft using the existing `X*word` and `X|Y|word` syntax.

Prefix an aligned word with `~` when it should use normal DECTALK speech instead of pitched singing while retaining its claimed MIDI time, for example `2*~hello`. Choir Studio exposes this as a compact toggle on the selected word.

Example raw inputs live under `tools/lyric_sync_assistant/examples/`; they are seed inputs reverse-engineered from one curated track per original example song.

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --auto-lines --overwrite
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --text-file songs\DaisyBell\inputs\lyrics\Vocals.transcript.txt --output songs\DaisyBell\outputs\lyrics_drafts\Vocals.txt --overwrite
```

Drafts are written to `songs/<Song>/outputs/lyrics_drafts/<Part>.txt` by default and are never render inputs. Transcript drafts use renderer-valid timestamped lyric lines, preserving pasted `[timestamp]` or `[timestamp|duration]` prefixes and deriving starts from MIDI for plain input. A single untimestamped bulk block is automatically split at MIDI phrase rests. Diagnostic comments are opt-in with `--comments`. Only the aligned `songs/<Song>/inputs/lyrics/<Part>.txt` configured by `LYRICS_FILENAME` is rendered. Publish it through Studio **Apply to source** or `alignment.py --apply --overwrite` after review.

The phrase and word-boundary thresholds are BPM-relative by default, and can be overridden with `--phrase-gap-ms`, `--word-gap-ms`, and `--tight-gap-ms`. Without `--auto-lines`, source line breaks are preserved as lyric-phrase hints while note counts are aligned globally to the MIDI track. With `--auto-lines`, source words are flattened and aligned globally, then the output is split at detected MIDI rest phrases.

Legacy workspaces can migrate their earlier transcript artifacts safely with `python tools/migrate_lyric_transcripts.py --apply --remove-raw`. The command creates each missing immutable transcript from the best recoverable pre-alignment source, verifies the copy, and only then removes the obsolete artifact.

The assistant can benchmark itself against the perfected example lyric files:

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py --validate-examples --auto-lines
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --validate --auto-lines
```

Validation reports note-allocation error, exact word allocation percentage, word-to-note boundary error, line-boundary error, and warning counts. The word-to-note boundary metric is the most useful signal for whether held words and syllables landed on the same note spans as the curated lyrics.

## Settings
Settings.yaml holds both general settings and per track settings. All settings are optional, and a default will be added by choir.py if none is specified.

### General Settings

**noteOffset**: DECtalk uses a different pitch encoding than MIDI. With the default `noteOffset: -48`, the raw emitted pitch is `MIDI pitch - 48`, so MIDI `48` (`C3`) becomes DECTALK pitch `0`, MIDI `69` (`A4`) becomes DECTALK pitch `21`, and MIDI `84` (`C6`) becomes DECTALK pitch `36`. Change this only when you intentionally want to transpose the song before DECTALK rendering.

**minDectalkPitch / maxDectalkPitch**: Inclusive DECTALK pitch bounds. Defaults are `0` through `36`, which maps to `C3` through `C6` in the project pitch model. The compiler octave-wraps every emitted pitch into this range before writing DECTALK text, so bad MIDI or a manual track shift should not leak an ugly out-of-range pitch. The range must span at least one octave so all 12 pitch classes can still be represented.

Pitch classes are preserved as integers. There is no `#` spelling in the DECTALK output, but sharps are supported: pitch `% 12 == 1` is `C#`, `3` is `D#`, `6` is `F#`, `8` is `G#`, and `10` is `A#`.

**notePeakTargetDbfs**: Automatic per-note peak target, defaulting to `-5.0 dBFS`. Choir groups every sung MIDI note's consonants and vowels, then applies bidirectional correction after pitch processing. This replaces manual high-note gain curves while preserving each note's internal pronunciation balance.

**ignoreMidiVelocity / velocityVolumeScaleDb**: MIDI velocity is ignored by default (`ignoreMidiVelocity: true`), independent of the configured scale. Set `ignoreMidiVelocity: false` and choose a positive `velocityVolumeScaleDb` only when a MIDI performance intentionally encodes dynamics that should survive normalization.

**minimumNoteDurationMs**: Optional song-wide pronunciation floor for short MIDI notes. The renderer extends a short note only into silence before the next note; it never moves later note onsets or changes the MIDI file. A track can override this with `MINIMUM_NOTE_DURATION_MS`. `0` disables the floor. The render log reports how many notes were extended and how many remained short because no rest was available.

Consonants are played as separate phonemes. How long each consonant is played for can be tweaked with the following.
**consonantFractionTarget**: The maximum time taken up by consonants across the whole word.
**consonantMinMs**: Minimum time per consonant (mS)
**consonantMaxMs**: Maximum time per consonant (mS)
**codaMaxMs**: Maximum total time for the ending consonant cluster of a one-vowel word spread across multiple notes. Defaults to `200 ms`; short final notes naturally use less. A track can override this with `CODA_MAX_MS`.

### Per Track Audio Settings
The key under `Tracks:` is the output name used for folders, text chunks, WAV stems, and the final mix. `LYRICS_FILENAME` and `TRACK_FILENAME` can point that output to different lyric and MIDI sources.

**LYRICS_FILENAME**: Name of file to read lyrics from. Defaults to the output name. Allows different parts to read from the same lyrics file for simplicity.

**TRACK_FILENAME**: Name of the MIDI track to read. Defaults to the output name. Allows an output stem to use a differently named MIDI track, or multiple output stems to share the same MIDI source.

**MINIMUM_NOTE_DURATION_MS**: Per-track override for `minimumNoteDurationMs`. It consumes only available following silence, so tightly adjacent notes remain unchanged rather than shifting the song out of sync.

**CODA_MAX_MS**: Per-track ceiling for the complete ending consonant cluster on multi-note words such as `time` or `mine`. Remaining final-note time sustains the vowel/glide instead of holding `m` or `n` for the full note.

**PITCH_SHIFT**: Per-track musical transposition in semitones after the song-level `noteOffset`. Use this when two tracks share the same MIDI notes but should sing at different octaves or intervals.

**OCTAVE_BOOST**: Render-cleanup shift in semitones. DECtalk is asked to sing this many semitones lower for stability, with note durations stretched to match; after rendering, the WAV is sped back up. Pair with `PITCH_SHIFT` for octave duplicate tracks, e.g. `PITCH_SHIFT: 12` and `OCTAVE_BOOST: 12`.

Negative `OCTAVE_BOOST` is valid for very low final notes: DECtalk is asked to sing higher/shorter, then the WAV is slowed back down. If the MIDI already contains the desired final octave for each part, `PITCH_SHIFT` is usually unnecessary; use `OCTAVE_BOOST` only to move the temporary DECTALK-rendered pitch into a stable register.

Avoid large negative boosts as a default. `OCTAVE_BOOST: -12` can be useful for C2-range bass references, but `-24` has produced strong low-frequency pulse/formant artifacts in practice.

Run `python tools/create_octave_boost_reference_song.py` to regenerate the checked-in octave reference. It previews complete chromatic octaves from C2 through C6 using boosts `-12`, `0`, `12`, and `24`. The `+36` three-octave boost is intentionally excluded because it does not render reliably.

**VOLUME_ADJUST_DB**: Will adjust volume level of each track in decibels. Positive is louder, negative is quieter, and 0 is the same. I usually make higher tracks louder to be audible.

Automatic note leveling targets `-5.0 dBFS` for every non-silent sung MIDI note. Optional MIDI velocity is applied afterward, then `VOLUME_ADJUST_DB` adjusts the assembled stem. `STEM_PEAK_CEILING_DBFS` and `finalMixPeakCeilingDbfs` remain independent `-1.0 dBFS` safety guards.

**IGNORE_MIDI_VELOCITY / VELOCITY_VOLUME_SCALE_DB**: Per-track velocity controls. `IGNORE_MIDI_VELOCITY` defaults to `true`, keeping velocity out of gain calculations. When set to `false`, `VELOCITY_VOLUME_SCALE_DB` defines the opt-in dynamic range.

**DEC_SETUP**: Add a bit of scripting to the beginning of each text file read by DECtalk to change settings.
\[:np\] sets the voice to perfect paul, the most popular voice. Other voices include \[:np\] \[:nb\] \[:nh\] \[:nd\] \[:nf\] \[:nu\] \[:nr\] \[:nw\] & \[:nk\]
\[:dv hs 95\] changes the head size to be 95% standard. I usually increase head size for lower voices as I think it sounds better.
Choir Studio Review exposes these voice commands as a beta per-track selector. Saving replaces only the `[:n?]` command and preserves the rest of `DEC_SETUP`. Head size is a timbre control rather than a gain control; this engine clamps values below `65` to the same effective `hs 65` voice.

Choir Studio can also import an existing timed DECTalk command into the selected song. Use the file-import button beside the song folder control, name the new role, and paste a string such as `[:np][d<80,12>ao<500,12>ng<80,12>]`. Studio creates the MIDI track, direct-phoneme lyric source, and applied alignment together, then opens the new role in Align. Contiguous phonemes at one pitch become one note; `_` tokens retain rests. `[:tone frequency_hz,duration_ms]` and its `[:t ...]` alias are retained exactly for audio while their frequencies map to the nearest MIDI notes for visualization. Dialing and conversational event scripts are rejected because they have no deterministic musical alignment.
There are a ton of other settings to play with that I haven't taken the time to learn, I've been mostly focused on the synchronization and playback.




### Per-track spectrogram settings

Final spectrogram videos are encoded as H.264 at CRF 23 with AAC audio. The
song-level `spectrogramVideo.intermediateAnimationMode` setting defaults to
`delete`. Choir Studio exposes three choices in the spectrogram layout view:
delete the much larger lossless working clips, compress them to H.264 archive
copies in parallel, or keep them lossless for later compositing. Compression
also replaces a legacy `animation.mp4` with its H.264 equivalent. Failed final
composition never triggers any cleanup or compression, and a failed archive
conversion retains its source clip.

Spectrogram layout and text overlays belong to a nested `SPECTROGRAM` mapping under each track. Choir Studio edits this mapping directly:

```yaml
Tracks:
  Soprano:
    DEC_SETUP: "[:nf][:dv hs 90]"
    SPECTROGRAM:
      COLOR_HSB: [328, 70, 97]
      POSITION: [0.5, 0, 0]
      LABEL: "Soprano"
      LABEL_ENABLED: true
      LABEL_POSITION: "top-left"
      LABEL_SHOW_VOICE: true
      LABEL_SHOW_HEAD_SIZE: true
      LABEL_FONT: "choir"
      LABEL_FONT_SIZE_PERCENT: 7
      CURRENT_WORD_ENABLED: true
      CURRENT_WORD_POSITION: "bottom-center"
      CURRENT_WORD_FONT: "choir"
      CURRENT_WORD_FONT_SIZE_PERCENT: 10
      CURRENT_WORD_USE_TRACK_COLOR: false
```

`POSITION` is `[size, left, top]`, expressed as fractions of the final video frame. Text positions support the nine combinations of `top`, `center`, or `bottom` with `left`, `center`, or `right`. Font choices are `choir`, `sans`, `serif`, and `mono`; sizes are percentages of the track region height. Current-word text is white unless `CURRENT_WORD_USE_TRACK_COLOR` is enabled. Its saved alignment timing automatically includes the renderer's one-second output lead-in.

The generator renders enabled track clips concurrently, then composites them in configured order and muxes the final audio once. Lossless intermediate clips are deleted only after the final video succeeds.
