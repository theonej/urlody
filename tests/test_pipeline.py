import pytest

from scorer.pipeline import Options


def test_default_options_track_one_staff():
    options = Options()
    options.validate()
    assert options.ranges == [("C2", "C7")]


def test_split_gives_treble_and_bass_ranges():
    options = Options(split="C4")
    options.validate()
    assert options.ranges == [("C4", "C7"), ("C2", "B3")]


@pytest.mark.parametrize(
    "options, complaint",
    [
        (Options(formats={"pdf", "docx"}), "unknown format"),
        (Options(time_signature="4"), "time signature"),
        (Options(lowest="X9"), "note names"),
        (Options(lowest="C6", highest="C5"), "lowest must be below highest"),
        (Options(split="C7"), "split must lie between"),
        (Options(polyphony=0), "polyphony"),
        (Options(grid=3), "grid"),
    ],
)
def test_validate_explains_what_is_wrong(options, complaint):
    with pytest.raises(ValueError, match=complaint):
        options.validate()
