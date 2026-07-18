from pyFuncs.MidiProcessing import enforceMinimumNoteDuration


def test_minimum_duration_consumes_only_available_following_rest() -> None:
    ends, extended, constrained = enforceMinimumNoteDuration(
        starts=[0, 200, 400],
        ends=[80, 300, 480],
        minimumDurationTicks=150,
    )

    assert ends == [150, 350, 480]
    assert extended == 2
    assert constrained == 1


def test_minimum_duration_does_not_move_or_overlap_next_onset() -> None:
    ends, extended, constrained = enforceMinimumNoteDuration(
        starts=[0, 100],
        ends=[80, 180],
        minimumDurationTicks=150,
    )

    assert ends == [100, 180]
    assert extended == 1
    assert constrained == 2


def test_zero_minimum_preserves_note_ends() -> None:
    ends, extended, constrained = enforceMinimumNoteDuration(
        starts=[0, 100],
        ends=[40, 140],
        minimumDurationTicks=0,
    )

    assert ends == [40, 140]
    assert extended == 0
    assert constrained == 0
