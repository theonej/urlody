"""Note events -> engraved score (PDF / MusicXML / MIDI) via music21."""

from __future__ import annotations

import math
from pathlib import Path

from music21 import chord, clef, instrument, key, layout, meter, metadata, note, stream, tempo
from music21.stream import makeNotation

from .render import render_pdf
from .transcribe import NoteEvent, Transcription

Event = tuple[float, float, tuple[int, ...], int]  # start, end (quarter lengths), pitches, velocity


def build_score(
    t: Transcription,
    title: str,
    time_signature: str = "4/4",
    subdivision: int = 4,
) -> tuple[stream.Score, key.Key | None]:
    """Quantise note events onto a beat grid and lay them out as notation.

    One staff per tracked line: a single line gets the best-fitting clef; two
    lines become a piano grand staff (treble over bass). A note carrying
    extra tones is engraved as a chord. ``subdivision`` is grid steps per
    quarter note (4 = sixteenth notes).
    """
    beat_sec = 60.0 / t.tempo
    # Shift so the first detected beat lands on a beat boundary.
    origin = t.first_beat % beat_sec
    staves = [_quantise(line, beat_sec, origin, subdivision) for line in t.staves]

    # Every staff runs to the same final barline.
    bar = meter.TimeSignature(time_signature).barDuration.quarterLength
    last = max((end for events in staves for _, end, _, _ in events), default=0.0)
    total = max(bar, math.ceil(last / bar) * bar)

    grand = len(staves) == 2
    parts: list[stream.Part] = []
    for i, events in enumerate(staves):
        part = stream.PartStaff() if grand else stream.Part()
        if i == 0:
            part.insert(0, instrument.Piano())
            part.insert(0, tempo.MetronomeMark(number=round(t.tempo)))
        part.insert(0, meter.TimeSignature(time_signature))
        for start, end, pitches, vel in events:
            length = end - start
            if len(pitches) > 1:
                m21 = chord.Chord(list(pitches), quarterLength=length)
            else:
                m21 = note.Note(pitches[0], quarterLength=length)
            m21.volume.velocity = vel
            part.insert(start, m21)
        parts.append(part)

    score = stream.Score()
    # An empty composer: left unset, music21 would credit itself ("Music21").
    score.metadata = metadata.Metadata(title=title, composer="")
    for part in parts:
        score.insert(0, part)
    if grand:
        score.insert(0, layout.StaffGroup(parts, name="Piano", abbreviation="Pno.", symbol="brace"))

    detected_key = score.analyze("key") if t.notes else None
    for i, part in enumerate(parts):
        if detected_key:
            part.insert(0, key.KeySignature(detected_key.sharps))
        _spell(part, detected_key)
        if grand:
            part.insert(0, clef.TrebleClef() if i == 0 else clef.BassClef())
        elif detected_key:
            part.insert(0, clef.bestClef(part, recurse=True))
        part.makeRests(refStreamOrTimeRange=[0, total], fillGaps=True, inPlace=True)
        part.makeMeasures(inPlace=True)
        part.makeTies(inPlace=True)
        # Print only the accidentals the key signature and the bar so far don't imply.
        makeNotation.makeAccidentalsInMeasureStream(part)
        part.makeBeams(inPlace=True)
    return score, detected_key


def _quantise(
    notes: list[NoteEvent], beat_sec: float, origin: float, subdivision: int
) -> list[Event]:
    """Snap notes to the grid as one voice: notes landing on the same grid
    point sound together as a chord, and a new attack cuts off whatever was
    sounding before it."""
    step = 1.0 / subdivision

    def q(seconds: float) -> float:
        beats = (seconds - origin) / beat_sec
        return max(0.0, round(beats * subdivision) / subdivision)

    events: list[Event] = []
    for n in notes:
        start, end = q(n.start), q(n.end)
        if end - start < step:
            end = start + step
        if events and start < events[-1][1]:
            prev_start, prev_end, prev_pitches, prev_vel = events[-1]
            if start <= prev_start:
                events.pop()
                pitches = tuple(sorted({*prev_pitches, *n.pitches}))
                events.append((prev_start, max(prev_end, end), pitches, max(prev_vel, n.velocity)))
                continue
            events[-1] = (prev_start, start, prev_pitches, prev_vel)
        events.append((start, end, tuple(n.pitches), n.velocity))
    return events


def _spell(part: stream.Part, k: key.Key | None) -> None:
    """Spell MIDI-derived pitches for the key.

    music21 spells them with sharps (use flats in flat keys) and gives the
    rest an explicit natural, which the exporter would print on every note.
    """
    flats = k is not None and k.sharps < 0
    for n in part.recurse().notes:
        for p in n.pitches:
            if p.accidental is None:
                continue
            if flats and p.accidental.name == "sharp":
                p.getEnharmonic(inPlace=True)
            if p.accidental is not None and p.accidental.alter == 0:
                p.accidental = None


def write_outputs(
    score: stream.Score, base: Path, formats: set[str], title: str, subtitle: str
) -> list[Path]:
    written: list[Path] = []
    if "pdf" in formats:
        pdf_path = base.with_suffix(".pdf")
        render_pdf(score, pdf_path, title, subtitle)
        written.append(pdf_path)
    if "musicxml" in formats:
        xml_path = base.with_suffix(".musicxml")
        score.write("musicxml", fp=xml_path)
        written.append(xml_path)
    if "midi" in formats:
        midi_path = base.with_suffix(".mid")
        score.write("midi", fp=midi_path)
        written.append(midi_path)
    return written
