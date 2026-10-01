from scorer.transcribe import transcribe

from .synth import SR, render

QUARTER = 0.5  # seconds, at 120 BPM
PIANO = [("C4", "C7"), ("C2", "B3")]  # treble staff, bass staff


def _events(staff, start, end):
    return [n for n in staff if start - 0.12 <= n.start <= end]


def test_block_chords_come_out_as_chords():
    # Two bars of I-IV-V-I triads in the right hand over root notes in the left.
    treble = [[60, 64, 67], [65, 69, 72], [67, 71, 74], [60, 64, 67]]
    bass = [48, 53, 55, 48]
    score, t = [], 0.5
    for _ in range(2):
        for chord, root in zip(treble, bass):
            score.append((t, QUARTER * 0.95, chord, 0.25))
            score.append((t, QUARTER * 0.95, [root], 0.3))
            t += QUARTER
    y = render(score, t + 1)

    result = transcribe(y, SR, PIANO, tempo=120.0)
    upper, lower = result.staves

    found = 0
    for k, chord in enumerate(treble * 2):
        onset = 0.5 + k * QUARTER
        hits = [n for n in upper if abs(n.start - onset) <= 0.1]
        assert hits, f"no treble event at {onset}"
        if set(hits[0].pitches) == set(chord):
            found += 1
    assert found >= 6, f"only {found}/8 chords transcribed exactly"
    # The bass line is single notes, and a bass attack never shows up as a chord.
    assert all(not n.extra for n in lower)
    assert [n.midi for n in lower][:4] == bass


def test_sustained_chord_is_struck_once_under_a_melody():
    score = [(0.5, 2.0, [60, 64, 67], 0.22), (0.5, 2.0, [48], 0.3)]
    for k, midi in enumerate([76, 74, 72]):
        score.append((0.5 + QUARTER * (k + 1), QUARTER * 0.9, [midi], 0.3))
    y = render(score, 3.5)

    upper, lower = transcribe(y, SR, PIANO, tempo=120.0).staves
    first = upper[0]
    assert abs(first.start - 0.5) < 0.1 and set(first.pitches) == {60, 64, 67}
    melody = [n for n in upper[1:] if n.start < 2.4]
    assert [n.pitches for n in melody] == [[76], [74], [72]]
    # The held bass note is not re-attacked at each melody note.
    assert len(_events(lower, 0.4, 2.4)) == 1


def test_single_line_stays_single():
    scale = [60, 62, 64, 65, 67, 69, 71, 72, 72, 67, 64, 60]
    y = render([(0.5 + k * QUARTER, QUARTER, [m], 0.3) for k, m in enumerate(scale)], 7.0)
    (staff,) = transcribe(y, SR, [("C2", "C7")], tempo=120.0).staves
    assert [n.midi for n in staff] == scale
    assert all(not n.extra for n in staff)


def test_polyphony_one_disables_chords():
    y = render([(0.5, 0.9, [60, 64, 67], 0.25), (1.5, 0.9, [65, 69, 72], 0.25)], 3.0)
    (staff,) = transcribe(y, SR, [("C2", "C7")], tempo=120.0, polyphony=1).staves
    assert staff and all(not n.extra for n in staff)
