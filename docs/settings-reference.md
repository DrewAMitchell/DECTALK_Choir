# Settings Reference

[Back to the DECTALK Choir README](../README.md) | [Installation](installation.md) | [Song authoring](song-authoring.md)

Each song owns `songs/<Song>/settings.yaml`. Choir Studio edits the same settings consumed by `choir.py`. Unsupported keys are reported before rendering instead of being silently accepted.

## Structure

```yaml
noteOffset: -48
notePeakTargetDbfs: -5
ignoreMidiVelocity: true
minimumNoteDurationMs: 0

Tracks:
  Soprano:
    TRACK_FILENAME: Soprano
    LYRICS_FILENAME: Soprano
    DEC_SETUP: "[:nf][:dv hs 90]"
    VOLUME_ADJUST_DB: 0
    RENDER_ENABLED: true
```

The key under `Tracks:` is the output role name. It names generated partial folders, stems, and labels unless a visual label overrides it.

## Song-Level Audio

| Setting | Meaning |
| --- | --- |
| `noteOffset` | MIDI-to-DECtalk pitch offset. The normal `-48` maps MIDI C3 (48) to DECtalk pitch 0. |
| `minDectalkPitch`, `maxDectalkPitch` | Inclusive generated pitch bounds, normally 0 through 36 (C3 through C6). Pitches octave-wrap into this range. |
| `notePeakTargetDbfs` | Automatic bidirectional per-note peak target. Default: `-5.0` dBFS. |
| `stemPeakCeilingDbfs` | Safety ceiling for completed role stems. |
| `finalMixPeakCeilingDbfs` | Safety ceiling for the final 32-bit mix. |
| `ignoreMidiVelocity` | Ignore MIDI velocity for loudness. Defaults to `true`. |
| `velocityVolumeScaleDb` | Opt-in velocity dynamic range when velocity is enabled. |
| `minimumNoteDurationMs` | Extend short notes only into available following silence. `0` disables it. |
| `gapMendMs` | Fold rests at or below this duration into the preceding note. |
| `consonantFractionTarget` | Target fraction of a word reserved for consonants. |
| `consonantMinMs`, `consonantMaxMs` | Per-consonant timing bounds. |
| `codaMaxMs` | Maximum complete ending-consonant cluster duration for a held word. |
| `spectrogramVideo.intermediateAnimationMode` | `delete`, `compress`, or `keep` working clips after final composition. |

DECtalk pitch values are integer semitones. Sharps are supported even though commands do not spell note names: pitch classes 1, 3, 6, 8, and 10 correspond to C#, D#, F#, G#, and A#.

## Per-Track Audio

| Setting | Meaning |
| --- | --- |
| `TRACK_FILENAME` | Internal MIDI track name. Defaults to the output role name. |
| `LYRICS_FILENAME` | Render lyric filename stem. Defaults to the output role name. |
| `DEC_SETUP` | Track-wide DECtalk setup prefix, including voice and head size. |
| `VOLUME_ADJUST_DB` | Manual completed-stem gain after automatic note leveling. |
| `PITCH_SHIFT` | Musical transposition in semitones after `noteOffset`. |
| `OCTAVE_BOOST` | Temporary DECtalk render shift in semitones, corrected in audio afterward for stability. |
| `PITCH_WRAP_SHIFT` | Optional manual octave-wrap override; normally leave unset. |
| `IGNORE_MIDI_VELOCITY` | Per-role override for song-level velocity handling. |
| `VELOCITY_VOLUME_SCALE_DB` | Per-role velocity range when enabled. |
| `MINIMUM_NOTE_DURATION_MS` | Per-role short-note pronunciation floor. |
| `GAP_MEND_MS` | Per-role tiny-rest threshold. |
| `CODA_MAX_MS` | Per-role ending consonant-cluster ceiling. |
| `STEM_PEAK_CEILING_DBFS` | Per-role completed-stem clipping guard. |
| `RENDER_ENABLED` | Persisted Render Audio participation. |
| `EXPORT_PHONEME_STRING` | Generate `outputs/_phonemes/<Role>.txt` on the next render. |

### Voice And Head Size

Common `DEC_SETUP` prefixes:

```text
[:np][:dv hs 100]
[:nf][:dv hs 90]
[:nh][:dv hs 115]
```

Built-in voice codes:

| Code | Voice |
| --- | --- |
| `np` | Perfect Paul |
| `nh` | Huge Harry |
| `nf` | Frail Frank |
| `nd` | Doctor Dennis |
| `nb` | Beautiful Betty |
| `nu` | Uppity Ursula |
| `nw` | Whispering Wendy |
| `nr` | Rough Rita |
| `nk` | Kit the Kid |
| `nv` | Val |

Choir Studio's beta Voice selector replaces only the `[:n?]` directive. Head size replaces only `[:dv hs N]`. Head size changes timbre and register response, not merely volume; this engine clamps values below 65 to the same effective lower boundary.

### Pitch And Gain

`PITCH_SHIFT` changes the intended musical output. `OCTAVE_BOOST` changes the temporary pitch presented to DECtalk and then compensates the rendered audio. If MIDI already contains the intended octave, leave `PITCH_SHIFT` at zero and use `OCTAVE_BOOST` only when the DECtalk register needs stabilization.

Large negative octave boosts can create strong low-frequency pulse or formant artifacts. The checked-in `OctaveBoostReference` demonstrates `-12`, `0`, `12`, and `24`; the unstable `36` profile is intentionally excluded.

Automatic note leveling targets each non-silent sung note before optional velocity and `VOLUME_ADJUST_DB`. Use stem gain as the primary manual loudness control rather than rebuilding a per-semitone curve.

## Spectrogram Layout

Visual settings live under each role's nested `SPECTROGRAM` mapping:

```yaml
Tracks:
  Soprano:
    SPECTROGRAM:
      COLOR_HSB: [328, 70, 97]
      POSITION: [0.5, 0, 0]
      LABEL: Soprano
      LABEL_ENABLED: true
      LABEL_POSITION: top-left
      LABEL_SHOW_VOICE: true
      LABEL_SHOW_HEAD_SIZE: true
      LABEL_FONT: choir
      LABEL_FONT_SIZE_PERCENT: 7
      CURRENT_WORD_ENABLED: true
      CURRENT_WORD_POSITION: bottom-center
      CURRENT_WORD_FONT: choir
      CURRENT_WORD_FONT_SIZE_PERCENT: 10
      CURRENT_WORD_USE_TRACK_COLOR: false
```

`POSITION` is `[size, left, top]`, expressed as fractions of the final frame. The Studio layout canvas provides drag/resize controls and saves each region automatically.

Text positions combine `top`, `center`, or `bottom` with `left`, `center`, or `right`. Fonts are `choir`, `sans`, `serif`, or `mono`; sizes are percentages of the track region height.

Current-word overlays require applied alignment timing. Enabling them without valid word cues is a render failure rather than a silent omission.

Independent track clips render concurrently. Final composition overlays them in configured order and muxes the completed audio once. Working clips are only deleted or compressed after a successful final MP4.

[Back to the DECTALK Choir README](../README.md)

