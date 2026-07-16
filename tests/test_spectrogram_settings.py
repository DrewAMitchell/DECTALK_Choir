from pathlib import Path

import yaml

from tools.choir_studio_bridge import _replace_role_mapping, _word_cues_from_report


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
