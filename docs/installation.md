# Installation And Development

[Back to the DECTALK Choir README](../README.md) | [Song authoring](song-authoring.md) | [Settings reference](settings-reference.md)

## Installer Setup

Choir Studio is the supported interface for non-developers on Windows.

1. Download the current `.exe` installer from [GitHub Releases](https://github.com/DrewAMitchell/DECTALK_Choir/releases).
2. Run the installer and launch **DECTALK Choir Studio**.
3. Install FFmpeg with Windows Package Manager:
   ```powershell
   winget install --id Gyan.FFmpeg.Shared --exact
   ```
4. Restart Studio after FFmpeg is installed.

The installer bundles Choir Studio, DECtalk, portable Python, Rubber Band, the renderer, and the included example songs. On first launch, Studio initializes a writable per-user workspace instead of modifying files under the installation directory.

FFmpeg is intentionally external. If `winget` is unavailable, use [FFmpeg's official download page](https://ffmpeg.org/download.html) and ensure the selected Windows build's `bin` directory is on `PATH`. Studio exposes the same FFmpeg recovery guidance when a render cannot find it.

## Contributor Setup

### Prerequisites

- Windows and Python 3.11
- Node.js and npm
- Rust through `rustup`
- The Windows prerequisites required by Tauri 2
- FFmpeg on `PATH`
- The repository's DECtalk runtime files
- `external/rubberband.exe` for octave processing and release bundles

### Python

```powershell
git clone https://github.com/DrewAMitchell/DECTALK_Choir.git
cd DECTALK_Choir
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Use `requirements.txt` instead when only the renderer runtime is needed.

### Choir Studio

```powershell
cd choir_studio
npm install
npm run check
npx tauri dev
```

The development app uses the repository root directly and expects `..\.venv\Scripts\python.exe`. The VS Code launch configuration **DECTALK Choir Studio (Tauri + CDP)** starts the same development path with WebView diagnostics on port `9356`.

### Renderer CLI

From the repository root:

```powershell
.\.venv\Scripts\python.exe choir.py AuldLangSyne
.\.venv\Scripts\python.exe choir.py -vis AuldLangSyne
.\.venv\Scripts\python.exe choir.py -play DaisyBell
```

Common flags:

- `-vis`: generate the configured spectrogram video after audio.
- `-plt`: generate phoneme plot images.
- `-play`: open the finished mix.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile choir.py pyFuncs\PhonemeProcessing.py pyFuncs\MidiProcessing.py
cd choir_studio
npm run check
npm run build
```

Run `cargo check` from `choir_studio/src-tauri/` when native code or Tauri configuration changes.

## Build The Windows Installer

Keep the version synchronized in:

- `choir_studio/package.json`
- `choir_studio/package-lock.json`
- `choir_studio/src-tauri/tauri.conf.json`
- `choir_studio/src-tauri/Cargo.toml` and `Cargo.lock` when the Rust package version changes

Then run:

```powershell
cd choir_studio
npm run bundle
```

Tauri runs `npm run build:release`, which prepares the self-contained runtime before compiling the frontend and native bundle. Packaging fails when the required DECtalk files or redistributable Rubber Band executable are missing.

Generated installers are written under:

```text
choir_studio/src-tauri/target/release/bundle/nsis/
choir_studio/src-tauri/target/release/bundle/msi/
```

## Publish A GitHub Release

**Do not commit the generated installer.** The installer is a reproducible build artifact, while Git tracks the source and configuration that define it.

Recommended release sequence:

1. Commit and validate the intended source state.
2. Update the application version files.
3. Create and push a version tag such as `v0.2.0`.
4. Build `npm run bundle` from that exact tag.
5. Create a GitHub Release for the tag.
6. Upload the generated NSIS `.exe` as the primary download and optionally the `.msi`.
7. Add release notes and, ideally, SHA-256 checksums.

This versions the installer through its Git tag and Release attachment without adding large binaries to normal Git history. A future GitHub Actions workflow can automate the same build-and-attach boundary.

[Back to the DECTALK Choir README](../README.md)

