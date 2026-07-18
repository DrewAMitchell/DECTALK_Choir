from tools import choir_studio_bridge as bridge


def test_render_role_update_does_not_require_a_single_role(monkeypatch) -> None:
    requested = ["Voice 2", "Voice 3"]
    monkeypatch.setattr(bridge, "_song_name", lambda value: "Example")
    monkeypatch.setattr(
        bridge,
        "_update_render_enabled_roles",
        lambda song, roles: {"song": song, "roles": roles},
    )

    def reject_role_lookup(song: str, value: object) -> str:
        raise AssertionError("song-level render selection must not validate one role")

    monkeypatch.setattr(bridge, "_role", reject_role_lookup)

    result = bridge.handle(
        {
            "command": "update_render_enabled_roles",
            "song": "Example",
            "roles": requested,
        }
    )

    assert result == {"song": "Example", "roles": requested}
