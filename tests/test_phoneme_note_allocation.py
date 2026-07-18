import pyFuncs.PhonemeProcessing as phonemes


def test_time_uses_held_diphthong_and_reserves_final_consonant() -> None:
    symbols = ["T", "AY", "M"]

    groups = phonemes.allocateSingleVowelWordToNotes(symbols, [0, 1, 0], 3)

    assert groups == [["T", "AA"], ["IH"], ["M"]]


def test_mine_can_hold_its_vowel_across_additional_middle_notes() -> None:
    symbols = ["M", "AY", "N"]

    groups = phonemes.allocateSingleVowelWordToNotes(symbols, [0, 1, 0], 4)

    assert groups == [["M", "AA"], ["AA"], ["IH"], ["N"]]


def test_open_vowel_word_continues_through_final_note() -> None:
    symbols = ["AY"]

    groups = phonemes.allocateSingleVowelWordToNotes(symbols, [1], 3)

    assert groups == [["AA"], ["AA"], ["IH"]]


def test_two_note_word_keeps_complete_diphthong_before_coda() -> None:
    symbols = ["T", "AY", "M"]

    groups = phonemes.allocateSingleVowelWordToNotes(symbols, [0, 1, 0], 2)

    assert groups == [["T", "AY"], ["M"]]


def test_multi_vowel_word_keeps_existing_explicit_distribution_path() -> None:
    assert phonemes.allocateSingleVowelWordToNotes(
        ["F", "IY", "L", "IY", "NG"],
        [0, 1, 0, 1, 0],
        3,
    ) is None
