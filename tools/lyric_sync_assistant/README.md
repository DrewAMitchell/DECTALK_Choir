# Lyric Sync Assistant

Draft synchronized `lyrics/*.txt` files from plain lyric text plus an existing MIDI track.

This tool is intentionally a first-pass drafter. The current validation baseline is roughly 95% exact word-to-note allocation on the curated examples, so generated drafts still need human review before replacing perfected lyric files.

Transcript drafts are written as renderer-valid timestamped lyric lines. Existing
`[timestamp]` and `[timestamp|duration]` prefixes are preserved; plain lyric
inputs receive timestamps from the MIDI note starts. Diagnostic `#` lines are
off by default and can be enabled with `--comments`. A single untimestamped bulk
lyric block is automatically split at MIDI phrase rests; use `--auto-lines` for
the same treatment of multiline untimestamped input.

## Run

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --auto-lines --overwrite
```

By default, working drafts are written under `songs/<Song>/outputs/lyrics_drafts/<Part>.txt`. They are not render sources; rendering requires the configured aligned file under `inputs/lyrics/`.

The drafter cannot replace `songs/<Song>/inputs/lyrics/<LYRICS_FILENAME>.txt`. Publish only through Studio **Apply to source** or the aligner's `--apply --overwrite` after review.

For a track with no lyrics yet, create an editable note-level scaffold:

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --placeholder duw --overwrite
```

This writes one direct-phoneme placeholder per MIDI note, split at detected
phrase gaps and additionally capped at eight consecutive notes per line. Transcript inputs are normalized before lookup: commas and other
unsupported punctuation are removed while apostrophes and choir lyric control
syntax are retained.

Placeholders use direct DECTALK phonemes. `doo` is not recognized; use `duw`.

## Align

Map a generated or manually edited draft to the source MIDI notes:

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\alignment.py DaisyBell Vocals --draft songs\DaisyBell\outputs\lyrics_drafts\Vocals.txt --overwrite
```

The aligner writes a renderer-ready lyric file under
`songs/<Song>/outputs/lyrics_aligned/<Part>.txt` and a note-by-note JSON report beside
it. The report includes absolute note timing, MIDI pitch, lyric assignment,
gaps, and boundary status. `--apply --overwrite` writes the aligned text to
the configured lyric input after intentional review.

## Examples

The `examples/` files are transcript inputs reverse-engineered from one curated track in each original example song by stripping note-count syntax such as `2*word` and `1|2|word`.

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --text-file tools\lyric_sync_assistant\examples\DaisyBell_Vocals.transcript.txt --output songs\DaisyBell\outputs\lyrics_drafts\Vocals.example.txt --overwrite

.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py AuldLangSyne Soprano --text-file tools\lyric_sync_assistant\examples\AuldLangSyne_Soprano.transcript.txt --output songs\AuldLangSyne\outputs\lyrics_drafts\Soprano.example.txt --overwrite

.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py CarolOfTheBells_Short Soprano --text-file tools\lyric_sync_assistant\examples\CarolOfTheBells_Short_Soprano.transcript.txt --output songs\CarolOfTheBells_Short\outputs\lyrics_drafts\Soprano.example.txt --overwrite
```

## Validate

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py --validate-examples
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py --validate-examples --auto-lines
```
