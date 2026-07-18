# DECTALK Choir Studio

[Back to DECTALK Choir](../README.md) | [Installation and releases](../docs/installation.md) | [Song authoring](../docs/song-authoring.md)

`choir_studio/` is the supported desktop editor for the existing DECTALK Choir compiler. It is not a second renderer.

## Ownership Boundaries

- React and CSS own track navigation, MIDI visualization, transcript editing, alignment, tuning, render review, and spectrogram layout.
- Tauri owns the native window, filesystem dialogs, media launch, and long-running background jobs.
- `tools/choir_studio_bridge.py` owns safe workspace operations and calls the established inspector, lyric drafter, alignment, and MIDI tools.
- `choir.py` remains the only audio renderer.

Studio and command-line rendering therefore consume the same `settings.yaml`, applied lyric sources, MIDI tracks, pitch rules, and output folders.

## Workflow

The Studio lifecycle is **Align -> Render Audio**.

**Align** owns selected-role MIDI preview, lyric drafting, direct word-to-note editing, overlap inspection and splitting, portable phoneme import/export, and explicit publishing to the render source.

**Render Audio** owns render participation, track statistics, per-role tuning, background render progress, completed audio review, and spectrogram layout/video generation.

Working candidates live under `songs/<Song>/outputs/lyrics_drafts/`. Rendering requires the published `songs/<Song>/inputs/lyrics/<Part>.txt`. The original transcript is captured once at `<Part>.transcript.txt` and is never replaced by Studio.

## Development Bring-Up

```powershell
cd choir_studio
npm install
npm run check
npx tauri dev
```

The development app uses the repository's Windows Python environment at `..\.venv\Scripts\python.exe`. Imported MIDI is copied before Studio normalizes track names or performs an explicit split.

Use the VS Code **DECTALK Choir Studio (Tauri + CDP)** launch configuration when WebView diagnostics are needed on port `9356`.

## Installer And Releases

See **[Installation and development](../docs/installation.md)** for end-user setup, FFmpeg, contributor prerequisites, bundle output paths, versioning, and GitHub Release procedure.
