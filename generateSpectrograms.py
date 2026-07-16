import os
from pathlib import Path
import sys

import yaml

import pyFuncs.spectrogramAnimation as specAnimate


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_VIDEO_DIMENSIONS = (2560, 1440)
FRAMES_PER_SECOND = 30


def _even_dimension(value):
    value = max(2, int(round(float(value))))
    return value - value % 2


def _parse_video_dimensions(value):
    if value is None:
        return None
    if isinstance(value, str):
        for delimiter in ("x", "X", ",", " "):
            value = value.replace(delimiter, " ")
        parts = [part for part in value.split() if part]
    else:
        parts = list(value)
    if len(parts) != 2:
        return None
    try:
        return (_even_dimension(parts[0]), _even_dimension(parts[1]))
    except (TypeError, ValueError):
        return None


def _host_video_dimensions():
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        user32 = ctypes.windll.user32
        dims = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        if dims[0] > 0 and dims[1] > 0:
            return (_even_dimension(dims[0]), _even_dimension(dims[1]))
    except Exception:
        pass
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        dims = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        if dims[0] > 0 and dims[1] > 0:
            return (_even_dimension(dims[0]), _even_dimension(dims[1]))
    except Exception:
        pass
    return None


def _video_dimensions(settings):
    env_dims = _parse_video_dimensions(os.environ.get("DECTALK_VIDEO_SIZE"))
    if env_dims:
        print(f"Using DECTALK_VIDEO_SIZE: {env_dims}")
        return env_dims
    configured_dims = _parse_video_dimensions(settings.get("videoDimensions"))
    if configured_dims:
        print(f"Using settings videoDimensions: {configured_dims}")
        return configured_dims
    host_dims = _host_video_dimensions()
    if host_dims:
        print(f"Using host video dimensions: {host_dims}")
        return host_dims
    print(f"Using default video dimensions: {DEFAULT_VIDEO_DIMENSIONS}")
    return DEFAULT_VIDEO_DIMENSIONS


def _spectrogram_settings(track_name, track):
    visual = dict(track.get("SPECTROGRAM") or {})
    visual.setdefault("COLOR_HSB", track.get("VID_HSB", [0, 100, 100]))
    visual.setdefault("POSITION", track.get("VID_Position", [0.5, 0.25, 0.25]))
    visual.setdefault("LABEL", track.get("VID_Label", track_name))
    visual.setdefault("LABEL_ENABLED", track.get("VID_LabelEnabled", False))
    visual.setdefault("LABEL_POSITION", track.get("VID_LabelPosition", "top-left"))
    visual.setdefault("LABEL_SHOW_VOICE", track.get("VID_LabelShowVoice", False))
    visual.setdefault("LABEL_SHOW_HEAD_SIZE", track.get("VID_LabelShowHeadSize", False))
    visual.setdefault("CURRENT_WORD_ENABLED", track.get("VID_CurrentWordEnabled", False))
    visual.setdefault("CURRENT_WORD_POSITION", track.get("VID_CurrentWordPosition", "bottom-center"))
    return visual


def main(argv=None):
    os.chdir(BASE_DIR)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("No song specified")
        return 2
    song_title = argv[-1]
    song_dir = BASE_DIR / "songs" / song_title
    if not song_dir.is_dir():
        print("Song folders found:")
        for path in sorted((BASE_DIR / "songs").iterdir()):
            if path.is_dir():
                print(f"   {path.name}")
        print(f"Song {song_title} not found")
        return 2
    settings_path = song_dir / "settings.yaml"
    try:
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        print(f"Could not read {settings_path}: {error}")
        return 2

    tracks_dir = song_dir / "outputs" / "_tracks"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    rendered_stems = {path.stem for path in tracks_dir.glob("*.wav")}
    configured_tracks = list((settings.get("Tracks") or {}).keys())
    output_parts = [track for track in configured_tracks if track in rendered_stems]
    print(f"Only .wav:{sorted(rendered_stems - set(configured_tracks))}")
    print(f"Only settings:{[track for track in configured_tracks if track not in rendered_stems]}")

    requested = [part.strip() for part in os.environ.get("DECTALK_CHOIR_SPECTROGRAM_ROLES", "").split(",") if part.strip()]
    if requested:
        unavailable = [part for part in requested if part not in output_parts]
        output_parts = [part for part in output_parts if part in requested]
        if unavailable:
            print(f"Enabled tracks without rendered stems:{unavailable}")
    print(f"Spectrogram tracks:{output_parts}")
    if not output_parts:
        print("No enabled rendered stems are available for spectrogram generation")
        return 1

    for track_name in output_parts:
        settings["Tracks"][track_name]["SPECTROGRAM"] = _spectrogram_settings(
            track_name,
            settings["Tracks"][track_name],
        )
    specAnimate.generateAnimation(
        output_parts,
        song_title,
        settings,
        videoDims=_video_dimensions(settings),
        framesPerSecond=FRAMES_PER_SECOND,
        back_color=(100, 0, 0),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
