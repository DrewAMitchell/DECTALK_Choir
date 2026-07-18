# Song Authoring

[Back to the DECTALK Choir README](../README.md) | [Installation](installation.md) | [Settings reference](settings-reference.md)

## Song Workspace

Each song lives under `songs/<Song>/`:

```text
songs/<Song>/
|-- settings.yaml
|-- inputs/
|   |-- <Song>.mid
|   `-- lyrics/
|       |-- <Part>.transcript.txt
|       |-- <Part>.txt
|       `-- .alignment/<Part>.json
`-- outputs/
    |-- lyrics_drafts/
    |-- _tracks/
    |-- _phonemes/
    |-- _animation/
    `-- _finished/
```

- `<Part>.transcript.txt` preserves the original lyric input and is created once.
- `outputs/lyrics_drafts/<Part>.txt` and its JSON report are Studio's editable candidate.
- `<Part>.txt` is the only lyric source rendered by `choir.py`.
- `.alignment/<Part>.json` stores applied timing details, including virtual note splits and word cues.
- Generated output folders are ignored by Git.

## Start From MIDI

Use the header inbox to choose a `.mid` or `.midi` file anywhere on disk. Studio copies it into a new song folder, creates a disabled role and lyric placeholder for every note-bearing track, and opens the first role in Align.

The Align rail shows how many note-bearing MIDI tracks are imported. **Add MIDI track** exposes tracks added to the working MIDI later. Removing a rail role removes only its `settings.yaml` entry; MIDI and authored lyric/alignment files remain available for reimport.

MIDI roles are matched by their internal track names, not by filename. A role should represent one monophonic DECtalk voice.

### Note Overlap And Splitting

Brief transition overlaps can render sequentially, but simultaneous chord notes cannot become a true chord within one DECtalk role. Studio marks overlap in the rail and Align toolbar.

The split workflow previews the minimum monophonic lanes and can:

- Export a separate DAW-compatible MIDI without changing the active song.
- Replace the working MIDI after explicit confirmation and create a restorable backup.
- Preserve every source note, including identical duplicate notes.
- Assign stable track names and distinct non-percussion channels for reliable DAW import.

Structural MIDI corrections such as moving, extending, or separating source notes still belong in a MIDI editor.

## Draft And Align Lyrics

Open **Edit track lyrics** from Align.

1. Paste plain lyrics or generate a direct-phoneme note skeleton.
2. Choose **Draft alignment** once to map the text against MIDI rests and note timing.
3. Adjust phrase and word ownership directly over the piano roll.
4. Choose **Apply to source**, then confirm **Apply alignment**.
5. Enable the role in Render Audio.

Once a candidate exists, the editor updates that candidate positionally instead of rerunning the timing algorithm. Bulk rewording preserves phrase timing and note ownership when the word count is unchanged. Insert or delete words in Align when structure changes.

A transcript is immutable through Studio. Deleting it manually is the explicit reset when a completely new first-pass draft is required.

### Lyric Syntax

```text
# comment
!2 repeated line
2*word
1|2|word
~spoken
2*~spoken phrase
[0:40] forced start
[0:40|5000] forced start and duration
`duw
```

- `X*word` spreads one word across X notes.
- `X|Y|word` assigns note counts to successive vowel groups.
- `~word` uses normal speech while retaining claimed MIDI time.
- Timestamps use seconds or clock syntax; bare durations use milliseconds.
- A leading backtick supplies direct DECtalk phonemes.

Near-homonyms and split spellings can improve pronunciation. For example, `uh` and `a` produce different vowels, while uncommon words may work better as several phonetic fragments. Extremely short notes often cannot carry clear pronunciation; use Track tuning's minimum note duration where a following rest is available.

Ctrl-dragging a note boundary can create a virtual same-pitch split as a last resort. Virtual segments remain alignment metadata rather than changing the source MIDI.

## Import A Timed Phoneme String

Use the phoneme-import button beside the song selector. Paste a timing-complete command and either:

- Add it as a new role in the current song.
- Enable **Create as a new song** to make a one-track workspace.

Example:

```text
[:np][:dv hs 100][d<80,12>ao<500,12>ng<80,12>_<250,0>][:tone 9000,999]
```

Studio validates the complete string before writing anything. Contiguous phonemes at one pitch become one MIDI note, timed underscores remain rests, and long rests divide phrases. Tone events remain exact render commands while their frequency maps to the nearest MIDI pitch for visualization.

Track-wide setup commands must precede timed events. Midstream setup changes, dialing scripts, and conversational command streams are rejected when they cannot produce deterministic musical alignment.

A successful import creates and applies the lyric/alignment source immediately, then selects the new role in Align.

## Export A Timed Phoneme String

Enable **Phoneme output** in the Align toolbar for a role. Its next audio render writes:

```text
songs/<Song>/outputs/_phonemes/<Role>.txt
```

The export includes aligned timing and phrase rests. It fails clearly rather than flattening normal-speech segments or overlapping phrase timelines that cannot be represented faithfully in one serial command.

## Lyric Sync Assistant CLI

The Studio workflow is preferred, but the assistant remains available:

```powershell
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py DaisyBell Vocals --auto-lines --overwrite
.\.venv\Scripts\python.exe tools\lyric_sync_assistant\assistant.py --validate-examples --auto-lines
```

Drafts default to `songs/<Song>/outputs/lyrics_drafts/` and are never renderer inputs until explicitly applied.

## Included Examples

- `DaisyBell`: lead and harmony role mapping.
- `AuldLangSyne`: complete curated multi-role lyric alignment.
- `CarolOfTheBells_Short`: dense short-note and octave behavior.
- `CantHelpFalling`: overlap detection and split Bass Chords voices.

[Back to the DECTALK Choir README](../README.md)
