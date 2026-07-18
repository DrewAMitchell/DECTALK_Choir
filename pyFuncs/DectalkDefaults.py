"""Built-in DECtalk voice defaults used when DEC_SETUP omits an override."""

DEFAULT_DECTALK_VOICE = "np"

DECTALK_VOICE_NAMES = {
    "np": "Perfect Paul",
    "nh": "Huge Harry",
    "nf": "Frail Frank",
    "nd": "Doctor Dennis",
    "nb": "Beautiful Betty",
    "nu": "Uppity Ursula",
    "nw": "Whispering Wendy",
    "nr": "Rough Rita",
    "nk": "Kit the Kid",
    "nv": "Val",
}

DEFAULT_HEAD_SIZE_BY_VOICE = {
    "np": 100,  # Perfect Paul
    "nh": 115,  # Huge Harry
    "nf": 90,   # Frail Frank
    "nd": 105,  # Doctor Dennis
    "nb": 100,  # Beautiful Betty
    "nu": 95,   # Uppity Ursula
    "nw": 100,  # Whispering Wendy
    "nr": 95,   # Rough Rita
    "nk": 80,   # Kit the Kid
    "nv": 100,  # Val initializes from Paul
}


def default_head_size(voice: str | None) -> int:
    return DEFAULT_HEAD_SIZE_BY_VOICE.get(
        str(voice or DEFAULT_DECTALK_VOICE).lower(),
        DEFAULT_HEAD_SIZE_BY_VOICE[DEFAULT_DECTALK_VOICE],
    )


def dectalk_voice_label(voice: str | None) -> str:
    code = str(voice or DEFAULT_DECTALK_VOICE).lower()
    name = DECTALK_VOICE_NAMES.get(code)
    return f"{name} [:{code}]" if name else f"[:{code}]"
