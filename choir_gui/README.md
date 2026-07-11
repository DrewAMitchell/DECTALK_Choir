# DECTALK Choir GUI

Launch the native operator interface from the repo root:

```powershell
.\.venv\Scripts\python.exe choir_gui.py
```

The app is deliberately a front end for the existing command-line contract. A
render runs the equivalent of `python choir.py <Song>` from the selected
project root and streams its stdout/stderr into the UI. It does not duplicate
or replace the renderer.

The project folder, selected song, dialog folder, and window geometry are
stored with Qt's per-user application settings, outside this repository.

The table reports, for every configured output role:

- MIDI source track, lyric input, note count, MIDI range, and final DECTALK
  render/audible ranges.
- MIDI polyphony, so a non-monophonic source is visible before rendering.
- Existing stem loudness measured in active 100 ms RMS windows as
  `min / median / average / max (peak)` dBFS. Windows below `-70 dBFS` are
  omitted from the four window statistics so leading silence does not dominate.

Use **Split MIDI** to run `tools/split_polyphonic_midi.py` through native file
pickers. The splitter still owns the MIDI transform and verification.

For a non-GUI smoke test of the same song inspector:

```powershell
.\.venv\Scripts\python.exe choir_gui.py --inspect DaisyBell
```
