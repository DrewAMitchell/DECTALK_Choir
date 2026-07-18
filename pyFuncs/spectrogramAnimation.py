import colorsys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import json
import math as m
import os
from pathlib import Path
import re
import subprocess as sp
from time import perf_counter

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import signal
import scipy.io.wavfile as wavfile

from pyFuncs.AudioTiming import OUTPUT_LEAD_IN_MS
from pyFuncs.DectalkDefaults import default_head_size


FONT_PATH = Path(__file__).resolve().parent / "fonts" / "NexaText-Trial-Light.ttf"
FONT_CHOICES = {
    "choir": FONT_PATH,
    "sans": "segoeui.ttf",
    "serif": "georgia.ttf",
    "mono": "consola.ttf",
}
TEXT_ANCHORS = {
    "top-left", "top-center", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
}
FINAL_VIDEO_CRF = 23
INTERMEDIATE_VIDEO_CRF = 23
INTERMEDIATE_ANIMATION_MODES = frozenset({"delete", "compress", "keep"})


def _output_song_dir(song_title):
    return Path("songs") / song_title / "outputs"


def _newest_file(paths):
    existing = [path for path in paths if path.exists()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _find_output_audio(song_title, song_output_dir):
    finished_dir = song_output_dir / "_finished"
    audio_file = _newest_file([
        finished_dir / f"{song_title}.mp3",
        song_output_dir / f"{song_title}.mp3",
    ])
    if audio_file:
        return audio_file
    audio_file = _newest_file(
        path
        for search_dir in (finished_dir, song_output_dir)
        if search_dir.exists()
        for path in search_dir.glob("*.mp3")
    )
    if audio_file:
        return audio_file
    audio_file = _newest_file([
        finished_dir / f"{song_title}.wav",
        song_output_dir / f"{song_title}.wav",
    ])
    if audio_file:
        return audio_file
    return _newest_file(
        path
        for search_dir in (finished_dir, song_output_dir)
        if search_dir.exists()
        for path in search_dir.glob("*.wav")
    )


def _media_duration_seconds(media_file):
    try:
        result = sp.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(media_file),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        if media_file.suffix.lower() == ".wav":
            try:
                sampling_rate, data = wavfile.read(str(media_file), mmap=True)
                return len(data) / sampling_rate
            except Exception:
                pass
    return None


def _even_dimension(value):
    return max(2, int(value) // 2 * 2)


def _region_geometry(video_dims, position):
    size = max(0.01, min(1.0, float(position[0])))
    left = max(0.0, min(1.0 - size, float(position[1])))
    top = max(0.0, min(1.0 - size, float(position[2])))
    width = _even_dimension(video_dims[0] * size)
    height = _even_dimension(video_dims[1] * size)
    return width, height, round(video_dims[0] * left), round(video_dims[1] * top)


def _prepare_track_spectrogram(track_name, wav_path, frames_per_second, freq_range, bar_count):
    sampling_rate, wave_data = wavfile.read(str(wav_path))
    if np.ndim(wave_data) > 1:
        wave_data = np.mean(wave_data.astype(np.float32), axis=1)
    proc_spec = signal.spectrogram(
        wave_data,
        sampling_rate,
        window="hamming",
        nperseg=max(8, int(sampling_rate / frames_per_second)),
    )
    time_range = len(wave_data) / sampling_rate
    frame_count = max(1, m.ceil(time_range * frames_per_second))
    spec_data = proc_spec[2]

    fps_adjusted = np.zeros((len(spec_data), frame_count), dtype=np.float32)
    target_frames = np.arange(frame_count)
    source_frames = np.linspace(0, frame_count - 1, len(spec_data[0]))
    for index in range(len(spec_data)):
        fps_adjusted[index] = np.interp(target_frames, source_frames, spec_data[index])
    fps_adjusted = np.transpose(fps_adjusted)

    freq_domain = proc_spec[0]
    freq_min_index = int((np.abs(freq_domain - freq_range[0])).argmin())
    freq_max_index = int((np.abs(freq_domain - freq_range[1])).argmin())
    if freq_max_index <= freq_min_index:
        freq_max_index = min(len(freq_domain) - 1, freq_min_index + 1)
    selected = fps_adjusted[:, freq_min_index:freq_max_index + 1]
    source_bins = np.linspace(0, bar_count - 1, selected.shape[1])
    target_bins = np.arange(bar_count)
    bar_adjusted = np.zeros((len(selected), bar_count), dtype=np.float32)
    for index, frame in enumerate(selected):
        bar_adjusted[index] = np.interp(target_bins, source_bins, frame)

    np.sqrt(np.abs(bar_adjusted), out=bar_adjusted)
    spec_max = np.sort(np.max(bar_adjusted, axis=1)[np.max(bar_adjusted, axis=1) > 0.0])
    normalization = spec_max[m.floor(len(spec_max) * 0.9)] if len(spec_max) else 1.0
    if normalization <= 0:
        normalization = 1.0
    bar_adjusted /= normalization
    np.minimum(bar_adjusted, 1.0, out=bar_adjusted)
    return {
        "track": track_name,
        "data": bar_adjusted,
        "frame_count": frame_count,
        "time_range": time_range,
        "normalization": float(normalization),
    }


def _word_cues_from_payload(payload):
    direct = payload.get("word_cues") if isinstance(payload, dict) else None
    if isinstance(direct, list):
        return [
            {
                "word": str(item.get("word", "")).strip(),
                "start_ms": int(item.get("start_ms", 0)),
                "end_ms": int(item.get("end_ms", 0)),
            }
            for item in direct
            if isinstance(item, dict) and str(item.get("word", "")).strip()
        ]
    token_words = {
        (item.get("line"), item.get("word_index")): str(item.get("word", "")).strip()
        for item in payload.get("token_counts", [])
        if isinstance(item, dict)
    }
    grouped = {}
    for entry in payload.get("notes", []):
        if not isinstance(entry, dict):
            continue
        key = (entry.get("line"), entry.get("word_index"))
        if all(isinstance(value, int) for value in key):
            grouped.setdefault(key, []).append(entry)
    cues = []
    for key, entries in grouped.items():
        word = token_words.get(key) or str(entries[0].get("lyric") or "").strip()
        if word:
            cues.append({
                "word": word,
                "start_ms": round(min(float(item.get("start_ms", 0)) for item in entries)),
                "end_ms": round(max(float(item.get("end_ms", 0)) for item in entries)),
            })
    return sorted(cues, key=lambda item: (item["start_ms"], item["end_ms"]))


def _load_word_cues(song_title, track_name):
    song_dir = Path("songs") / song_title
    candidates = [
        song_dir / "inputs" / "lyrics" / ".alignment" / f"{track_name}.json",
        song_dir / "outputs" / "lyrics_drafts" / f"{track_name}.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            cues = _word_cues_from_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
        if cues:
            return [
                {
                    **cue,
                    "start_ms": cue["start_ms"] + OUTPUT_LEAD_IN_MS,
                    "end_ms": cue["end_ms"] + OUTPUT_LEAD_IN_MS,
                }
                for cue in cues
            ], str(path)
    return [], None


def _validate_current_word_cues(payloads):
    missing = [
        payload["track_name"]
        for payload in payloads
        if payload["spectrogram"].get("CURRENT_WORD_ENABLED") and not payload["word_cues"]
    ]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            f"Current-word display is enabled but no alignment timing is available for: {names}. "
            "Apply or draft an alignment for each track, or disable Current word before generating the video."
        )


def _setup_metadata(track_settings):
    setup = str(track_settings.get("DEC_SETUP", ""))
    voice_match = re.search(r"\[:n([a-z])\]", setup, flags=re.IGNORECASE)
    head_match = re.search(r"\[:dv\s+hs\s+(\d+)\]", setup, flags=re.IGNORECASE)
    voice = f"n{voice_match.group(1).lower()}" if voice_match else None
    head_size = int(head_match.group(1)) if head_match else None
    return voice, head_size


def _track_label(track_name, track_settings, spectrogram):
    pieces = [str(spectrogram.get("LABEL") or track_name)]
    voice, head_size = _setup_metadata(track_settings)
    if spectrogram.get("LABEL_SHOW_VOICE"):
        pieces.append("Perfect Paul [:np]" if voice == "np" else f"[:{voice}]" if voice else "default voice")
    if spectrogram.get("LABEL_SHOW_HEAD_SIZE"):
        effective_head_size = head_size if head_size is not None else default_head_size(voice)
        pieces.append(f"hs {effective_head_size}" if head_size is not None else f"hs {effective_head_size} (default)")
    return " | ".join(pieces)


def _load_font(choice, size):
    candidate = FONT_CHOICES.get(str(choice).lower(), FONT_PATH)
    try:
        return ImageFont.truetype(str(candidate), size)
    except OSError:
        return ImageFont.truetype(str(FONT_PATH), size)


def _draw_anchored_text(draw, text, anchor, dimensions, font, fill, stroke_width):
    if not text:
        return
    anchor = anchor if anchor in TEXT_ANCHORS else "top-left"
    margin = max(6, round(min(dimensions) * 0.035))
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    available_width = max(1, dimensions[0] - margin * 2)
    if width > available_width and getattr(font, "size", 0) > 8:
        font = font.font_variant(size=max(8, round(font.size * available_width / width)))
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
    vertical, horizontal = anchor.split("-") if "-" in anchor else ("center", "center")
    box_x = margin if horizontal == "left" else dimensions[0] - margin - width if horizontal == "right" else (dimensions[0] - width) / 2
    box_y = margin if vertical == "top" else dimensions[1] - margin - height if vertical == "bottom" else (dimensions[1] - height) / 2
    draw.text((box_x - bounds[0], box_y - bounds[1]), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=(0, 0, 0))


def _render_track_clip(payload):
    total_started = perf_counter()
    track_name = payload["track_name"]
    analysis_started = perf_counter()
    prepared = _prepare_track_spectrogram(
        track_name,
        Path(payload["wav_path"]),
        payload["frames_per_second"],
        tuple(payload["freq_range"]),
        payload["bar_count"],
    )
    analysis_seconds = perf_counter() - analysis_started
    width, height, left, top = _region_geometry(tuple(payload["video_dims"]), payload["position"])
    spectrogram = payload["spectrogram"]
    rgb = colorsys.hsv_to_rgb(
        float(spectrogram["COLOR_HSB"][0]) / 360,
        float(spectrogram["COLOR_HSB"][1]) / 100,
        float(spectrogram["COLOR_HSB"][2]) / 100,
    )
    bar_color = tuple(round(255 * value) for value in rgb)
    output_path = Path(payload["output_path"])
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"FFV1"),
        payload["frames_per_second"],
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not create lossless spectrogram clip for {track_name}.")

    label_font = _load_font(
        spectrogram.get("LABEL_FONT", "choir"),
        max(10, round(height * float(spectrogram.get("LABEL_FONT_SIZE_PERCENT", 7)) / 100)),
    )
    word_font = _load_font(
        spectrogram.get("CURRENT_WORD_FONT", "choir"),
        max(10, round(height * float(spectrogram.get("CURRENT_WORD_FONT_SIZE_PERCENT", 10)) / 100)),
    )
    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    bar_spacing = width / (payload["bar_count"] + 1)
    bar_width = max(1.0, bar_spacing * payload["bar_gap_fraction"])
    max_height = height / 2
    current_fft = np.array(prepared["data"][0], copy=True)
    cues = payload["word_cues"]
    cue_index = 0
    label = _track_label(track_name, payload["track_settings"], spectrogram)
    frame_render_started = perf_counter()
    try:
        for frame_index in range(payload["animation_frame_count"]):
            draw.rectangle((0, 0, width, height), fill=(0, 0, 0))
            if frame_index < len(prepared["data"]):
                frame_fft = prepared["data"][frame_index]
                for bar_index in range(payload["bar_count"]):
                    current_fft[bar_index] = max(current_fft[bar_index] / 1.4, frame_fft[bar_index])
                    value = min(1.0, max(0.001, float(current_fft[bar_index])))
                    x = bar_index * bar_spacing
                    draw.ellipse(
                        (x, height / 2 - value * max_height, x + bar_width, height / 2 + value * max_height),
                        fill=bar_color,
                    )
            if spectrogram.get("LABEL_ENABLED"):
                _draw_anchored_text(draw, label, spectrogram.get("LABEL_POSITION"), (width, height), label_font, bar_color, 2)
            if spectrogram.get("CURRENT_WORD_ENABLED") and cues:
                current_ms = frame_index * 1000 / payload["frames_per_second"]
                while cue_index < len(cues) and current_ms >= cues[cue_index]["end_ms"]:
                    cue_index += 1
                if cue_index < len(cues) and cues[cue_index]["start_ms"] <= current_ms < cues[cue_index]["end_ms"]:
                    _draw_anchored_text(
                        draw,
                        cues[cue_index]["word"],
                        spectrogram.get("CURRENT_WORD_POSITION"),
                        (width, height),
                        word_font,
                        bar_color if spectrogram.get("CURRENT_WORD_USE_TRACK_COLOR") else (245, 248, 247),
                        2,
                    )
            writer.write(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))
    finally:
        writer.release()
    frame_render_seconds = perf_counter() - frame_render_started
    return {
        "track": track_name,
        "path": str(output_path),
        "left": left,
        "top": top,
        "frames": prepared["frame_count"],
        "duration": prepared["time_range"],
        "normalization": prepared["normalization"],
        "word_cues": len(cues),
        "word_source": payload.get("word_source"),
        "analysis_seconds": analysis_seconds,
        "frame_render_seconds": frame_render_seconds,
        "total_seconds": perf_counter() - total_started,
    }


def _compose_clips(clips, output_path, audio_path, video_dims, frames_per_second, duration, background):
    background_hex = "0x{:02x}{:02x}{:02x}".format(*background)
    args = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"color=c={background_hex}:s={video_dims[0]}x{video_dims[1]}:r={frames_per_second}:d={duration:.6f}",
    ]
    for clip in clips:
        args.extend(["-i", clip["path"]])
    audio_index = None
    if audio_path:
        audio_index = len(clips) + 1
        args.extend(["-i", str(audio_path)])

    filters = []
    prior = "[0:v]"
    for index, clip in enumerate(clips):
        keyed = f"keyed{index}"
        composed = f"composed{index}"
        filters.append(f"[{index + 1}:v]colorkey=0x000000:0.01:0.0[{keyed}]")
        filters.append(
            f"{prior}[{keyed}]overlay=x={clip['left']}:y={clip['top']}:eof_action=pass:shortest=0[{composed}]"
        )
        prior = f"[{composed}]"

    temporary = output_path.with_name(f"{output_path.stem}.building{output_path.suffix}")
    temporary.unlink(missing_ok=True)
    args.extend([
        "-filter_complex", ";".join(filters),
        "-map", prior,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(FINAL_VIDEO_CRF), "-pix_fmt", "yuv420p",
    ])
    if audio_index is not None:
        args.extend(["-map", f"{audio_index}:a:0", "-c:a", "aac", "-b:a", "192k"])
    args.extend(["-t", f"{duration:.6f}", "-movflags", "+faststart", str(temporary)])
    sp.run(args, check=True)
    if not temporary.is_file() or temporary.stat().st_size <= 0:
        raise RuntimeError("FFmpeg did not produce the composite spectrogram video.")
    temporary.replace(output_path)


def _intermediate_animation_mode(settings_yaml):
    video_settings = settings_yaml.get("spectrogramVideo") or {}
    if not isinstance(video_settings, dict):
        return "delete"
    mode = str(video_settings.get("intermediateAnimationMode", "")).strip().lower()
    if mode in INTERMEDIATE_ANIMATION_MODES:
        return mode
    return "delete" if video_settings.get("deleteIntermediateAnimations", True) is not False else "keep"


def _cleanup_intermediate_animations(clips, finished_dir, enabled):
    if not enabled:
        return []
    removed = []
    for clip in clips:
        path = Path(clip["path"])
        if path.is_file():
            path.unlink()
            removed.append(path)
    legacy_animation = Path(finished_dir) / "animation.mp4"
    if legacy_animation.is_file():
        legacy_animation.unlink()
        removed.append(legacy_animation)
    return removed


def _compress_intermediate_animation(source_path):
    source = Path(source_path)
    target = source if source.suffix.lower() == ".mp4" else source.with_suffix(".mp4")
    temporary = target.with_name(f"{target.stem}.compressing{target.suffix}")
    temporary.unlink(missing_ok=True)
    original_size = source.stat().st_size
    try:
        try:
            sp.run(
                [
                    "ffmpeg", "-y", "-i", str(source), "-map", "0:v:0", "-an",
                    "-c:v", "libx264", "-preset", "medium", "-crf", str(INTERMEDIATE_VIDEO_CRF),
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except sp.CalledProcessError as error:
            detail_lines = (error.stderr or error.stdout or "").strip().splitlines()
            detail = detail_lines[-1] if detail_lines else f"FFmpeg exited with {error.returncode}"
            raise RuntimeError(detail) from error
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg did not produce a compressed animation for {source.name}.")
        temporary.replace(target)
        if source != target:
            source.unlink()
        return {
            "source": str(source),
            "target": str(target),
            "original_size": original_size,
            "compressed_size": target.stat().st_size,
        }
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _compress_intermediate_animations(clips, finished_dir):
    sources = [Path(clip["path"]) for clip in clips if Path(clip["path"]).is_file()]
    legacy_animation = Path(finished_dir) / "animation.mp4"
    if legacy_animation.is_file():
        sources.append(legacy_animation)
    if not sources:
        return []
    worker_count = min(len(sources), max(1, min(4, os.cpu_count() or 1)))
    print(f"Compressing {len(sources)} intermediate animation videos across {worker_count} workers", flush=True)
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_compress_intermediate_animation, path): path for path in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
                results.append(result)
                reduction = (1 - result["compressed_size"] / result["original_size"]) * 100 if result["original_size"] else 0
                print(f"Compressed {source.name}: {reduction:.1f}% smaller", flush=True)
            except Exception as error:
                errors.append(f"{source.name}: {error}")
    if errors:
        raise RuntimeError("Could not compress every intermediate animation; original failed clips were kept. " + "; ".join(errors))
    return results


def generateAnimation(trackNames, songTitle, settings_yaml, videoDims=(2560, 1440), freqRange=(100, 5000), divisionFactor=500, framesPerSecond=30, barCount=100, back_color=(0, 0, 0), barGapFrac=0.5):
    total_started = perf_counter()
    print("PROGRESS stage=setup state=started", flush=True)
    del divisionFactor
    song_output_dir = _output_song_dir(songTitle)
    tracks_dir = song_output_dir / "_tracks"
    animation_dir = song_output_dir / "_animation"
    finished_dir = song_output_dir / "_finished"
    animation_dir.mkdir(parents=True, exist_ok=True)
    finished_dir.mkdir(parents=True, exist_ok=True)

    background_rgb = colorsys.hsv_to_rgb(back_color[0] / 360, back_color[1] / 100, back_color[2] / 100)
    background = tuple(round(255 * value) for value in background_rgb)
    audio_path = _find_output_audio(songTitle, song_output_dir)
    durations = [
        duration
        for duration in (_media_duration_seconds(tracks_dir / f"{track}.wav") for track in trackNames)
        if duration
    ]
    audio_duration = _media_duration_seconds(audio_path) if audio_path else None
    if audio_duration:
        durations.append(audio_duration)
    if not durations:
        raise RuntimeError("No usable audio duration was found for spectrogram generation.")
    duration = max(durations)
    animation_frame_count = max(1, m.ceil(duration * framesPerSecond))

    payloads = []
    for index, track_name in enumerate(trackNames):
        track_settings = settings_yaml["Tracks"][track_name]
        spectrogram = dict(track_settings.get("SPECTROGRAM") or {})
        cues, cue_source = _load_word_cues(songTitle, track_name)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", track_name).strip("_") or f"track_{index + 1}"
        payloads.append({
            "track_name": track_name,
            "track_settings": track_settings,
            "spectrogram": spectrogram,
            "wav_path": str(tracks_dir / f"{track_name}.wav"),
            "output_path": str(animation_dir / f"{index:02d}_{safe_name}.mkv"),
            "video_dims": list(videoDims),
            "position": spectrogram["POSITION"],
            "frames_per_second": framesPerSecond,
            "animation_frame_count": animation_frame_count,
            "freq_range": list(freqRange),
            "bar_count": barCount,
            "bar_gap_fraction": barGapFrac,
            "word_cues": cues,
            "word_source": cue_source,
        })

    _validate_current_word_cues(payloads)
    setup_seconds = perf_counter() - total_started
    print(f"TIMING stage=setup seconds={setup_seconds:.3f}", flush=True)

    worker_count = min(len(payloads), max(1, min(4, os.cpu_count() or 1)))
    print("PROGRESS stage=parallel_tracks state=started", flush=True)
    print(f"Rendering {len(payloads)} lossless track clips across {worker_count} workers", flush=True)
    parallel_started = perf_counter()
    clips_by_track = {}
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_render_track_clip, payload): payload["track_name"] for payload in payloads}
        for future in as_completed(futures):
            track_name = futures[future]
            clip = future.result()
            clips_by_track[track_name] = clip
            print(
                f"Rendered {track_name}: {clip['frames']} source frames, "
                f"{clip['word_cues']} word cues, normalization {clip['normalization']:.4f}",
                flush=True,
            )
            print(
                f"TIMING track={json.dumps(track_name)} analysis={clip['analysis_seconds']:.3f} "
                f"frames={clip['frame_render_seconds']:.3f} total={clip['total_seconds']:.3f}",
                flush=True,
            )

    parallel_seconds = perf_counter() - parallel_started
    worker_seconds = sum(clip["total_seconds"] for clip in clips_by_track.values())
    effective_parallelism = worker_seconds / parallel_seconds if parallel_seconds else 0
    print(
        f"TIMING stage=parallel_tracks seconds={parallel_seconds:.3f} workers={worker_count} "
        f"worker_seconds={worker_seconds:.3f} effective_parallelism={effective_parallelism:.2f}",
        flush=True,
    )

    clips = [clips_by_track[track_name] for track_name in trackNames]
    output_path = finished_dir / f"{songTitle}.mp4"
    print("PROGRESS stage=composition state=started", flush=True)
    print(f"Compositing {len(clips)} track clips and final audio", flush=True)
    composition_started = perf_counter()
    _compose_clips(clips, output_path, audio_path, videoDims, framesPerSecond, duration, background)
    print(
        f"TIMING stage=composition seconds={perf_counter() - composition_started:.3f} "
        f"encoder=libx264 crf={FINAL_VIDEO_CRF}",
        flush=True,
    )
    print("PROGRESS stage=cleanup state=started", flush=True)
    cleanup_started = perf_counter()
    intermediate_mode = _intermediate_animation_mode(settings_yaml)
    if intermediate_mode == "delete":
        removed = _cleanup_intermediate_animations(clips, finished_dir, True)
        print(f"Removed {len(removed)} intermediate animation videos", flush=True)
    elif intermediate_mode == "compress":
        compressed = _compress_intermediate_animations(clips, finished_dir)
        original_bytes = sum(item["original_size"] for item in compressed)
        compressed_bytes = sum(item["compressed_size"] for item in compressed)
        reduction = (1 - compressed_bytes / original_bytes) * 100 if original_bytes else 0
        print(
            f"Compressed {len(compressed)} intermediate animation videos with libx264 "
            f"CRF {INTERMEDIATE_VIDEO_CRF}; size reduced {reduction:.1f}%",
            flush=True,
        )
    else:
        print(f"Kept {len(clips)} intermediate animation videos by song setting", flush=True)
    print(f"TIMING stage=cleanup seconds={perf_counter() - cleanup_started:.3f}", flush=True)
    print(f"TIMING stage=total seconds={perf_counter() - total_started:.3f}", flush=True)
    print(f"DONE: {output_path}", flush=True)
