import re

from music21 import chord, note

from scorer.score import _quantise, build_score
from scorer.transcribe import NoteEvent, Transcription

BEAT = 0.5  # seconds per quarter at 120 BPM


def test_quantise_keeps_chord_tones_together_and_cuts_at_the_next_attack():
    notes = [
        NoteEvent(0.0, 1.2, 60, 80, extra=[67, 64]),  # rings past the next attack
        NoteEvent(1.0, 1.5, 62, 70),
    ]
    events = _quantise(notes, BEAT, 0.0, 4)
    assert events == [(0.0, 2.0, (60, 64, 67), 80), (2.0, 3.0, (62,), 70)]


def test_quantise_unions_notes_landing_on_the_same_grid_point():
    notes = [NoteEvent(0.0, 0.5, 60, 80), NoteEvent(0.03, 0.5, 64, 90, extra=[67])]
    events = _quantise(notes, BEAT, 0.0, 4)
    assert events == [(0.0, 1.0, (60, 64, 67), 90)]


def _transcription(staves):
    return Transcription(staves=staves, tempo=120.0, first_beat=0.0, duration=4.0)


def test_build_score_engraves_chords():
    t = _transcription([[NoteEvent(0.0, 1.0, 64, 80, extra=[60, 67]), NoteEvent(1.0, 2.0, 65, 80)]])
    score, _ = build_score(t, "test")
    (part,) = score.parts
    first, second = part.recurse().notes
    assert isinstance(first, chord.Chord)
    assert [p.midi for p in first.pitches] == [60, 64, 67]
    assert first.quarterLength == 2.0
    assert isinstance(second, note.Note) and second.pitch.midi == 65


def test_build_score_writes_chords_to_musicxml(tmp_path):
    t = _transcription([
        [NoteEvent(0.0, 1.0, 64, 80, extra=[60, 67])],
        [NoteEvent(0.0, 1.0, 48, 80)],
    ])
    score, _ = build_score(t, "test")
    path = tmp_path / "test.musicxml"
    score.write("musicxml", fp=path)
    xml = path.read_text()
    assert len(re.findall(r"<chord\s*/>", xml)) == 2  # the second and third notes of the triad
    assert len(score.parts) == 2


def test_render_pdf_works_from_a_worker_thread(tmp_path):
    # The API renders in background threads; verovio's default resource path
    # is only set for the importing thread, so the renderer must set its own.
    import threading

    from scorer.render import render_pdf

    t = _transcription([[NoteEvent(0.0, 1.0, 64, 80, extra=[60, 67]), NoteEvent(1.0, 2.0, 65, 80)]])
    score, _ = build_score(t, "test")
    outcome = {}

    def job():
        try:
            render_pdf(score, tmp_path / "thread.pdf", "title", "subtitle")
            outcome["size"] = (tmp_path / "thread.pdf").stat().st_size
        except Exception as exc:  # pragma: no cover - reported through the assertion
            outcome["error"] = repr(exc)

    worker = threading.Thread(target=job)
    worker.start()
    worker.join()
    assert "error" not in outcome, outcome
    assert outcome["size"] > 1000
