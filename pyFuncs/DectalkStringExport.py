"""Build a reusable timed DECTalk command from compiled choir phrases."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

import pyFuncs.PhonemeProcessing as phonemes


class DectalkStringExportError(ValueError):
    pass


def build_dectalk_phoneme_string(
    compiled_lines: Iterable[Sequence[object]],
    setup: str,
    pitch_for: Callable[[float], int],
) -> str:
    """Serialize final aligned timing as one importable DECTalk command string."""

    output = ["[:phoneme arpabet speak on]", str(setup or "")]
    timed_tokens: list[str] = []
    cursor_ms = 0
    emitted_event = False

    def flush_tokens() -> None:
        nonlocal timed_tokens
        if timed_tokens:
            output.extend(("[", "".join(timed_tokens), "]"))
            timed_tokens = []

    def append_rest(duration_ms: int) -> None:
        if duration_ms > 0:
            timed_tokens.append(f"_<{duration_ms},0>")

    for line in sorted(compiled_lines, key=lambda item: int(item[0])):
        if len(line) < 2:
            continue
        start_ms = int(line[0])
        if start_ms < cursor_ms:
            raise DectalkStringExportError(
                f"Phrase at {start_ms} ms overlaps the preceding exported event ending at {cursor_ms} ms."
            )
        append_rest(start_ms - cursor_ms)
        cursor_ms = start_ms

        event = line[1]
        if (
            len(line) == 2
            and isinstance(event, tuple)
            and event
            and event[0] == phonemes.SPOKEN_WORD_MARKER
        ):
            raise DectalkStringExportError(
                f"Normal-speech phrase at {start_ms} ms cannot be represented as a timed phoneme string."
            )
        if (
            len(line) == 2
            and isinstance(event, tuple)
            and event
            and event[0] == phonemes.TONE_EVENT_MARKER
        ):
            flush_tokens()
            duration_ms = max(1, round(float(event[1])))
            output.append(f"[:tone {float(event[2]):g},{duration_ms}]")
            cursor_ms += duration_ms
            emitted_event = True
            continue

        for item in line[1:]:
            if item == " ":
                continue
            if not isinstance(item, tuple) or len(item) < 3:
                raise DectalkStringExportError(f"Phrase at {start_ms} ms contains an unsupported compiled event.")
            symbol = str(item[0])
            duration_ms = round(float(item[1]))
            if duration_ms <= 0:
                raise DectalkStringExportError(
                    f"Phoneme {symbol!r} at {cursor_ms} ms has no exportable duration."
                )
            pitch = 0 if symbol == "_" else int(pitch_for(float(item[2])))
            timed_tokens.append(f"{symbol}<{duration_ms},{pitch}>")
            cursor_ms += duration_ms
            emitted_event = True

    flush_tokens()
    if not emitted_event:
        raise DectalkStringExportError("The track contains no timed events to export.")
    return "".join(output) + "\n"
