from pyFuncs.SettingsSchema import settings_key_warnings


def test_settings_schema_accepts_current_and_legacy_migration_keys() -> None:
    settings = {
        "noteOffset": -48,
        "spectrogramVideo": {"intermediateAnimationMode": "compress"},
        "Tracks": {
            "Lead": {
                "DEC_SETUP": "[:np]",
                "NOTE_PEAK_TARGET_DBFS": -5,
                "VID_HSB": [0, 100, 100],
                "SPECTROGRAM": {
                    "COLOR_HSB": [0, 100, 100],
                    "LABEL_SHOW_VOICE": True,
                },
            }
        },
    }

    assert settings_key_warnings(settings) == []


def test_settings_schema_reports_full_paths_and_typo_suggestions() -> None:
    settings = {
        "noteOffest": -48,
        "spectrogramVideo": {"intermediateMode": "delete"},
        "Tracks": {
            "Lead": {
                "VOLUME_ADJUST_DN": 2,
                "SPECTROGRAM": {"LABEL_SHOW_VOISE": True},
            }
        },
    }

    warnings = settings_key_warnings(settings)

    assert any("noteOffest" in warning and "noteOffset" in warning for warning in warnings)
    assert any("spectrogramVideo.intermediateMode" in warning for warning in warnings)
    assert any("Tracks.Lead.VOLUME_ADJUST_DN" in warning and "VOLUME_ADJUST_DB" in warning for warning in warnings)
    assert any("Tracks.Lead.SPECTROGRAM.LABEL_SHOW_VOISE" in warning and "LABEL_SHOW_VOICE" in warning for warning in warnings)
