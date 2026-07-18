# DECTALK Choir Studio

`choir_studio/` is the supported desktop editor for the existing DECTALK Choir
compiler. It is not a second renderer.

The core renderer, song-file format, and command-line workflow are documented
in the [DECTALK Choir README](../README.md#choir-renderer).

## Boundaries

- React/CSS owns editing ergonomics: track navigation, MIDI visualization,
  transcript editing, and phrase-focused alignment review.
- Tauri owns the local desktop window and invokes a single JSON command.
- `tools/choir_studio_bridge.py` owns only safe workspace artifacts and calls
  the established inspector, lyric drafter, and alignment code.
- `choir.py` remains the only renderer. Studio keeps generated working state at
  `songs/<Song>/outputs/lyrics_drafts/<Role>.txt` and `.json`, but rendering
  requires the published `songs/<Song>/inputs/lyrics/<Part>.txt`. The original
  user input is captured once at `<Part>.transcript.txt` and is never replaced.

The Studio lifecycle is **MIDI -> Lyrics -> Align -> Review**. MIDI is a
read-only source preview; Align owns lyric-to-note editing. The track rail
replaces the source-track dropdown as the primary way to select a working role.
The header includes persistent dark/light preferences, output-folder access, and
a two-step song deletion control that removes the selected song workspace,
including its generated outputs.

## Bring-up

```powershell
cd choir_studio
npm install
npm run check
npx tauri dev
```

The app expects the repository’s Windows Python environment at
`..\.venv\Scripts\python.exe`. It does not change source MIDI. Applying an
alignment to the configured lyric input is an explicit, confirmed action.

## Windows Installer

Create the release installer with:

```powershell
cd choir_studio
npm run bundle
```

The release build bundles DECtalk, portable Python, Rubber Band, and Choir runtime
dependencies. First launch copies the runtime into a writable per-user workspace,
so song edits and renders do not write under the install directory.

FFmpeg is intentionally not bundled. The lowest-friction Windows install is:

```powershell
winget install --id Gyan.FFmpeg.Shared --exact
```

Restart Studio after installation. If Windows Package Manager is unavailable,
use [FFmpeg's official download page](https://ffmpeg.org/download.html), which
lists Gyan.dev among its Windows build providers. Ensure FFmpeg's `bin` folder
is on `PATH` before rendering audio or generating a spectrogram video; Studio
provides these same recovery actions before starting either job.

`external/rubberband.exe` must be supplied before packaging.
The build refuses to omit it because `OCTAVE_BOOST` depends on Rubber Band.
Confirm the DECtalk and Rubber Band licenses allow the intended distribution
before publishing an installer.

## Current Capability

- MIDI view: inspect a selected role, preview only its source MIDI track, seek,
  pause, stop, and play its existing stem or final mix.
- Lyrics view: load the published aligned lyric when present, otherwise load the
  original transcript. Paste or edit text, or generate a direct-phoneme note
  skeleton in place. Draft timing captures a missing transcript once, writes the
  working candidate, and returns that candidate to the same editor.
- Align view: reopen the existing note-backed candidate, select phrase and word
  spans in the piano roll, and use note-snapped start/end nudges or insertion.
  Every edit is saved directly to `songs/<Song>/outputs/lyrics_drafts/<Role>.txt`;
  **Apply to source** validates and publishes that candidate to the configured
  `LYRICS_FILENAME` consumed by `choir.py`. It creates the immutable transcript
  only when one has not already been captured.
- Render view: invokes the established `choir.py <Song>` contract and returns
  its compiler log and failure status.
- Render Audio view: enables renderable roles, opens per-role tuning, and exposes
  spectrogram layout directly beneath the render control once a finished mix exists.
  Per-track visual controls persist under `Tracks.<Role>.SPECTROGRAM`; track clips
  render concurrently before one ordered composition and audio mux.
