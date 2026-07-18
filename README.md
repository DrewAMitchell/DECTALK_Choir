# DECTALK Choir

Make the classic DECtalk speech synthesizer sing from MIDI, then arrange its voices into a synchronized choir and spectrogram video.

[![Watch DECTALK Choir perform with animated spectrograms](https://img.youtube.com/vi/MyFfZxVWiNA/maxresdefault.jpg)](https://www.youtube.com/watch?v=MyFfZxVWiNA)

**[Watch the full choir and animated spectrogram demonstration on YouTube](https://www.youtube.com/watch?v=MyFfZxVWiNA).**

DECTALK Choir runs on Windows. The supported desktop interface is **Choir Studio**, a Tauri application backed by the same Python renderer available from the command line.

## What It Does

- Imports a complete MIDI song and creates one Choir role per note-bearing track.
- Imports a timed DECtalk phoneme command as an editable MIDI track and applied alignment.
- Drafts lyric timing from plain text, then lets words and syllables claim notes visually.
- Previews one selected MIDI role, detects note overlap, and splits chords into monophonic voices.
- Tunes voice, head size, pitch handling, note duration, velocity behavior, and stem gain per role.
- Renders selected roles concurrently into synchronized stems and a final audio mix.
- Positions colored spectrogram regions, labels, and current-word overlays for final video.
- Exports a corrected aligned role back into a portable, timing-complete phoneme command.

## Workflow

```text
MIDI song or timed phoneme string
                |
                v
        Choir Studio: Align
  import -> draft -> claim notes -> tune
                |
                v
      Choir Studio: Render Audio
 select roles -> render stems -> review levels
                |
                v
     Optional spectrogram layout/video
```

Studio invokes the established Python services through a local JSON bridge. `choir.py` remains the only audio renderer, so Studio and command-line builds share the same song files, pitch rules, DECtalk commands, and output layout.

## Quick Start

### Install Choir Studio

1. Download the Windows installer from [GitHub Releases](https://github.com/DrewAMitchell/DECTALK_Choir/releases).
2. Install FFmpeg:
   ```powershell
   winget install --id Gyan.FFmpeg.Shared --exact
   ```
3. Launch **DECTALK Choir Studio** and use the inbox button to import a `.mid` or `.midi` song.

The installer contains Choir Studio, DECtalk, portable Python, Rubber Band, and the renderer. FFmpeg stays external to keep the package smaller.

See **[Installation and development](docs/installation.md)** for source setup, packaging, and GitHub Release guidance.

### Render From Source

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe choir.py AuldLangSyne
```

Add `-vis` to generate the configured spectrogram video and `-play` to open the completed mix.

## Import Paths

**MIDI song:** The header inbox creates `songs/<Song>/`, copies the MIDI, configures every note-bearing track, and opens Align. The original selected file is never modified.

**Timed phoneme string:** The adjacent phoneme-import action accepts a command such as:

```text
[:np][d<80,12>ao<500,12>ng<80,12>]
```

Studio can append it to the current song or create a new one-track song. It creates the MIDI proxy, direct-phoneme lyric source, and applied alignment together, then opens the role in Align. Timed rests and `[:tone frequency_hz,duration_ms]` events are preserved.

See **[Song authoring](docs/song-authoring.md)** for lyric syntax, note skeletons, alignment ownership, overlap splitting, and phoneme import/export constraints.

## Included Songs

Four complete example workspaces are included:

- `DaisyBell`
- `AuldLangSyne`
- `CarolOfTheBells_Short`
- `CantHelpFalling`

Generated audio and video stay under `songs/<Song>/outputs/` and are not committed.

## Documentation

- **[Installation and development](docs/installation.md)**: installer users, FFmpeg, source setup, Tauri bring-up, packaging, and releases.
- **[Song authoring](docs/song-authoring.md)**: MIDI import, lyrics, alignment, phoneme strings, splitting, and output files.
- **[Settings reference](docs/settings-reference.md)**: pitch, timing, loudness, voices, render participation, and spectrogram configuration.
- **[Choir Studio architecture](choir_studio/README.md)**: React, Tauri, bridge, and renderer ownership boundaries.

## Background

DECtalk is a text-to-speech synthesizer introduced in 1983. It was famously used by **Stephen Hawking** and later appeared in *Moonbase Alpha* to read chat aloud. Because DECtalk accepts explicit phonemes, pitches, and durations, its speech engine can also act as a distinctive synthetic singer.

This project turns that low-level control into a repeatable music workflow: MIDI supplies pitch and time, lyric alignment supplies pronunciation, and Choir Studio makes the result practical to edit and render.
