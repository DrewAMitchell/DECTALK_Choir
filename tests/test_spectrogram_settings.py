from pathlib import Path
import json

import yaml

from pyFuncs.AudioTiming import OUTPUT_LEAD_IN_MS
from pyFuncs.spectrogramAnimation import (
    FINAL_VIDEO_CRF,
    _cleanup_intermediate_animations,
    _compress_intermediate_animation,
    _intermediate_animation_mode,
    _load_word_cues,
    _track_label,
    _validate_current_word_cues,
)
from tools.choir_studio_bridge import _replace_role_mapping, _replace_top_level_mapping, _word_cues_from_report


def test_nested_spectrogram_save_preserves_role_settings_and_removes_legacy_keys(tmp_path: Path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "Tracks:\n"
        "  Soprano:\n"
        "    DEC_SETUP: \"[:np][:dv hs 90]\"\n"
        "    VID_HSB: [1, 2, 3]\n"
        "    VID_Position: [0.5, 0, 0]\n"
        "  Alto:\n"
        "    DEC_SETUP: \"[:nk]\"\n",
        encoding="utf-8",
    )

    _replace_role_mapping(
        settings_path,
        "Soprano",
        "SPECTROGRAM",
        {
            "COLOR_HSB": [328, 70, 97],
            "POSITION": [0.5, 0, 0],
            "LABEL": "Soprano",
            "LABEL_ENABLED": True,
        },
        remove_keys={"VID_HSB", "VID_Position"},
    )

    saved = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    soprano = saved["Tracks"]["Soprano"]
    assert soprano["DEC_SETUP"] == "[:np][:dv hs 90]"
    assert soprano["SPECTROGRAM"]["COLOR_HSB"] == [328, 70, 97]
    assert soprano["SPECTROGRAM"]["LABEL_ENABLED"] is True
    assert "VID_HSB" not in soprano
    assert "VID_Position" not in soprano
    assert saved["Tracks"]["Alto"]["DEC_SETUP"] == "[:nk]"


def test_word_cues_merge_all_notes_owned_by_one_word():
    report = {
        "token_counts": [{"line": 0, "word_index": 0, "word": "forever"}],
        "notes": [
            {"line": 0, "word_index": 0, "lyric": "for", "start_ms": 1250, "end_ms": 1500},
            {"line": 0, "word_index": 0, "lyric": "ever", "start_ms": 1500, "end_ms": 1900},
        ],
    }

    assert _word_cues_from_report(report) == [
        {"word": "forever", "start_ms": 1250, "end_ms": 1900}
    ]


def test_spectrogram_word_cues_include_renderer_lead_in(tmp_path: Path, monkeypatch):
    report_path = tmp_path / "songs" / "TestSong" / "inputs" / "lyrics" / ".alignment" / "Lead.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps({"word_cues": [{"word": "hello", "start_ms": 250, "end_ms": 700}]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    cues, source = _load_word_cues("TestSong", "Lead")

    assert source == str(report_path.relative_to(tmp_path))
    assert cues == [{
        "word": "hello",
        "start_ms": 250 + OUTPUT_LEAD_IN_MS,
        "end_ms": 700 + OUTPUT_LEAD_IN_MS,
    }]


def test_spectrogram_word_cues_fall_back_to_working_report_when_applied_sidecar_is_legacy(tmp_path: Path, monkeypatch):
    song_dir = tmp_path / "songs" / "TestSong"
    applied = song_dir / "inputs" / "lyrics" / ".alignment" / "Lead.json"
    draft = song_dir / "outputs" / "lyrics_drafts" / "Lead.json"
    applied.parent.mkdir(parents=True)
    draft.parent.mkdir(parents=True)
    applied.write_text(json.dumps({"virtual_splits": []}), encoding="utf-8")
    draft.write_text(json.dumps({
        "token_counts": [{"line": 0, "word_index": 0, "word": "fallback"}],
        "notes": [{"line": 0, "word_index": 0, "start_ms": 100, "end_ms": 500}],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cues, source = _load_word_cues("TestSong", "Lead")

    assert source == str(draft.relative_to(tmp_path))
    assert cues == [{
        "word": "fallback",
        "start_ms": 100 + OUTPUT_LEAD_IN_MS,
        "end_ms": 500 + OUTPUT_LEAD_IN_MS,
    }]


def test_current_word_overlay_requires_alignment_timing():
    payloads = [
        {"track_name": "Lead", "spectrogram": {"CURRENT_WORD_ENABLED": True}, "word_cues": []},
        {"track_name": "Bass", "spectrogram": {"CURRENT_WORD_ENABLED": False}, "word_cues": []},
    ]

    try:
        _validate_current_word_cues(payloads)
    except RuntimeError as error:
        assert "Lead" in str(error)
        assert "Bass" not in str(error)
    else:
        raise AssertionError("Missing current-word timing must fail spectrogram generation")


def test_spectrogram_video_policy_defaults_to_cleanup_and_uses_distribution_crf():
    assert _intermediate_animation_mode({}) == "delete"
    assert _intermediate_animation_mode({"spectrogramVideo": {"intermediateAnimationMode": "compress"}}) == "compress"
    assert _intermediate_animation_mode({"spectrogramVideo": {"intermediateAnimationMode": "keep"}}) == "keep"
    assert FINAL_VIDEO_CRF == 23


def test_spectrogram_label_shows_builtin_head_size_when_not_overridden():
    visual = {
        "LABEL": "Bass",
        "LABEL_SHOW_HEAD_SIZE": True,
    }

    assert _track_label("Bass", {"DEC_SETUP": "[:np]"}, visual) == "Bass | hs 100 (default)"
    assert _track_label("Bass", {"DEC_SETUP": "[:nh]"}, visual) == "Bass | hs 115 (default)"
    assert _track_label("Bass", {"DEC_SETUP": "[:nh][:dv hs 130]"}, visual) == "Bass | hs 130"


def test_intermediate_cleanup_is_opt_in_to_success_policy(tmp_path: Path):
    clip = tmp_path / "track.mkv"
    legacy = tmp_path / "animation.mp4"
    clip.write_bytes(b"clip")
    legacy.write_bytes(b"legacy")

    assert _cleanup_intermediate_animations([{"path": str(clip)}], tmp_path, False) == []
    assert clip.is_file()
    assert legacy.is_file()

    removed = _cleanup_intermediate_animations([{"path": str(clip)}], tmp_path, True)
    assert removed == [clip, legacy]
    assert not clip.exists()
    assert not legacy.exists()


def test_top_level_spectrogram_video_save_preserves_song_settings(tmp_path: Path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "noteOffset: -48\n\n"
        "spectrogramVideo:\n"
        "  intermediateAnimationMode: keep\n\n"
        "Tracks:\n"
        "  Soprano:\n"
        "    DEC_SETUP: \"[:np]\"\n",
        encoding="utf-8",
    )

    _replace_top_level_mapping(
        settings_path,
        "spectrogramVideo",
        {"intermediateAnimationMode": "compress"},
    )

    saved = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert saved["noteOffset"] == -48
    assert saved["spectrogramVideo"]["intermediateAnimationMode"] == "compress"
    assert saved["Tracks"]["Soprano"]["DEC_SETUP"] == "[:np]"


def test_intermediate_compression_replaces_source_only_after_ffmpeg_output(tmp_path: Path, monkeypatch):
    source = tmp_path / "track.mkv"
    source.write_bytes(b"lossless-animation")

    def fake_run(arguments, check, capture_output, text):
        assert check is True
        assert capture_output is True
        assert text is True
        assert arguments[arguments.index("-crf") + 1] == "23"
        Path(arguments[-1]).write_bytes(b"compressed")

    monkeypatch.setattr("pyFuncs.spectrogramAnimation.sp.run", fake_run)
    result = _compress_intermediate_animation(source)

    target = tmp_path / "track.mp4"
    assert not source.exists()
    assert target.read_bytes() == b"compressed"
    assert result["original_size"] == len(b"lossless-animation")
    assert result["compressed_size"] == len(b"compressed")


def test_failed_intermediate_compression_retains_original(tmp_path: Path, monkeypatch):
    source = tmp_path / "animation.mp4"
    source.write_bytes(b"original-video")

    def fail_run(arguments, check, capture_output, text):
        Path(arguments[-1]).write_bytes(b"partial")
        raise RuntimeError("encoder failed")

    monkeypatch.setattr("pyFuncs.spectrogramAnimation.sp.run", fail_run)

    try:
        _compress_intermediate_animation(source)
    except RuntimeError as error:
        assert str(error) == "encoder failed"
    else:
        raise AssertionError("Compression failure should be surfaced")

    assert source.read_bytes() == b"original-video"
    assert not (tmp_path / "animation.compressing.mp4").exists()
