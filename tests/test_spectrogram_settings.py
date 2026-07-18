from pathlib import Path
import json

import yaml

from pyFuncs.AudioTiming import OUTPUT_LEAD_IN_MS
from pyFuncs.spectrogramAnimation import (
    FINAL_VIDEO_CRF,
    _cleanup_intermediate_animations,
    _should_delete_intermediate_animations,
    _load_word_cues,
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


def test_spectrogram_video_policy_defaults_to_cleanup_and_uses_distribution_crf():
    assert _should_delete_intermediate_animations({}) is True
    assert _should_delete_intermediate_animations({"spectrogramVideo": {"deleteIntermediateAnimations": False}}) is False
    assert FINAL_VIDEO_CRF == 23


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
        "  deleteIntermediateAnimations: false\n\n"
        "Tracks:\n"
        "  Soprano:\n"
        "    DEC_SETUP: \"[:np]\"\n",
        encoding="utf-8",
    )

    _replace_top_level_mapping(
        settings_path,
        "spectrogramVideo",
        {"deleteIntermediateAnimations": True},
    )

    saved = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert saved["noteOffset"] == -48
    assert saved["spectrogramVideo"]["deleteIntermediateAnimations"] is True
    assert saved["Tracks"]["Soprano"]["DEC_SETUP"] == "[:np]"
