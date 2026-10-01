"""Audio -> note events.

Tracks one melodic line per requested pitch range with pYIN and segments it
into notes at pitch changes and detected onsets. At each onset it also reads
the tones struck together off a constant-Q spectrum, so that chords come out
as chords, with pYIN's line supplying what happens between attacks. Tempo and
beats are estimated once for the whole recording.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import librosa
import numpy as np
import scipy.ndimage
import scipy.signal

HOP = 256

# Chord detection works on a constant-Q spectrum with one bin per semitone.
# A tone's partials 1-10 lie these many semitones above its fundamental and
# are weighted 1/h when summed into the tone's salience.
PARTIALS = np.array([0, 12, 19, 24, 28, 31, 34, 36, 38, 40])
PARTIAL_WEIGHTS = 1.0 / np.arange(1, len(PARTIALS) + 1)
# Fraction of a partial's energy that leaks into the neighbouring semitone bins.
LEAK = 0.5
# A tone's partials are expected to fall off from its fundamental roughly as
# h ** -ROLLOFF; what rises well above that line is taken to be another tone
# sharing the partial. Piano fundamentals weaken toward the bottom of the
# keyboard, so the line is lifted for tones below C3 (up to 2.5x at C1).
ROLLOFF = 0.7
# A tone's own fundamental must carry this share of its salience (a pitch
# whose evidence is all in other tones' partials is a ghost), and stand this
# far above the bins a whole tone away (broadband noise has no peaks).
FUNDAMENTAL_SHARE = 0.25
PEAK_RATIO = 2.0
# Attacks quieter than this fraction of the loudest in the recording are
# ignored: there is nothing to read in the spectrum of a click in silence.
LEVEL_FLOOR = 0.03
# Tones are pulled out of an attack's spectrum, strongest first, until the
# next would be weaker than this fraction of the strongest.
EXPLAIN_RATIO = 0.05
MAX_TONES = 8
# A tone was struck at an attack (rather than ringing on from before) when
# at least this fraction of its energy is new, and it is at least this
# fraction of the newest tone in the staff's range.
NEW_FLOOR = 0.1
NEW_RATIO = 0.4
# Between attacks, pYIN's pitch is a fresh (legato) note only if this much
# of its energy is new; less is a chord still ringing, with some wobble.
LEGATO_NEW = 0.25
# ... and nothing within this many frames of an attack is fresh: the attack's
# own rise is still in the comparison window.
SETTLE = 12
# Struck tones join the chord down to this fraction of the strongest one ...
CHORD_RATIO = 0.2
# ... but a tone sitting on a partial of a stronger one (its octave, fifth
# or double octave) needs this much of the strongest struck tone in the
# staff's range, and this much of the strongest tone of the attack
# overall, unless pYIN also heard it during the chord.
PARTIAL_RATIO = 0.5
PARTIAL_FLOOR = 0.06
# A chord nobody is tracking ends when its tones' energy falls to this
# fraction of the attack.
DECAY_END = 0.15
# A note resumes an earlier sound only if it begins within this long of it.
RESUME_GAP = 0.3


@dataclass
class NoteEvent:
    start: float  # seconds
    end: float  # seconds
    midi: int  # the tracked (or, for a chord, the strongest) pitch
    velocity: int
    extra: list[int] = field(default_factory=list)  # other tones struck with it (a chord)

    @property
    def pitches(self) -> list[int]:
        return sorted({self.midi, *self.extra})


@dataclass
class Transcription:
    staves: list[list[NoteEvent]]  # one line per requested range, in the order given
    tempo: float  # BPM
    first_beat: float  # seconds
    duration: float  # seconds

    @property
    def notes(self) -> list[NoteEvent]:
        return [n for staff in self.staves for n in staff]


def transcribe(
    y: np.ndarray,
    sr: int,
    ranges: list[tuple[str, str]],
    min_note: float = 0.08,
    tempo: float | None = None,
    polyphony: int = 4,
) -> Transcription:
    """Track one line per (lowest, highest) note-name range in ``ranges``.

    ``polyphony`` is the most tones a note on one staff may carry, counting
    itself: tones struck together become a chord. 1 keeps each staff a
    single line, read from pYIN alone.
    """
    duration = len(y) / sr

    # Tempo from the full mix (drums help); pitch from the harmonic part only.
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    est_tempo, beats = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, bpm=tempo, units="time"
    )
    bpm = float(tempo or np.atleast_1d(est_tempo)[0]) or 120.0
    first_beat = float(beats[0]) if len(beats) else 0.0
    # Each onset as (where the attack begins, where it is loudest).
    peaks = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    starts = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, backtrack=True
    )
    onsets = sorted({(int(s), int(p)) for s, p in zip(starts, peaks)})
    onset_frames = {s for s, _ in onsets}

    y_harm = librosa.effects.harmonic(y)
    attacks = _Attacks(y, sr, ranges, onsets) if polyphony > 1 else None
    staves = []
    for lo, hi in ranges:
        track, rms = _track(y_harm, sr, lo, hi, _has_range_above(ranges, hi))
        notes = _fix_octave_slips(_segment(track, rms, onset_frames, sr, min_note))
        if attacks is not None:
            notes = _chords(notes, track, rms, attacks, lo, hi, polyphony, min_note)
        staves.append(notes)
    return Transcription(staves=staves, tempo=bpm, first_beat=first_beat, duration=duration)


def _has_range_above(ranges: list[tuple[str, str]], hi: str) -> bool:
    """True if another range starts right above ``hi`` (a split point)."""
    above = librosa.note_to_midi(hi) + 1
    return any(librosa.note_to_midi(lo) == above for lo, _ in ranges)


def _track(
    y_harm: np.ndarray, sr: int, fmin: str, fmax: str, lowpass: bool
) -> tuple[np.ndarray, np.ndarray]:
    """pYIN's pitch per frame (MIDI, -1 where unvoiced) and the RMS per frame."""
    fmin_hz, fmax_hz = librosa.note_to_hz(fmin), librosa.note_to_hz(fmax)
    # Below a split, remove the upper staff's line before tracking: pYIN on a
    # two-voice mix otherwise reports sub-harmonics of the upper voice as bass
    # notes. The upper staff stays unfiltered: the lower voice's fundamental is
    # already outside its candidate range, and hearing it helps pYIN reject
    # sub-octaves of the melody.
    y_band = _lowpass(y_harm, sr, fmax_hz) if lowpass else y_harm
    rms = librosa.feature.rms(y=y_band, frame_length=2048, hop_length=HOP)[0]

    f0, voiced, _ = librosa.pyin(
        y_band, fmin=fmin_hz, fmax=fmax_hz, sr=sr, frame_length=2048, hop_length=HOP
    )
    midi = np.full(len(f0), -1, dtype=int)
    midi[voiced] = np.round(librosa.hz_to_midi(f0[voiced])).astype(int)
    # Median filter removes single-frame octave slips and vibrato flicker.
    midi = scipy.ndimage.median_filter(midi, size=5, mode="nearest")
    return midi, rms[: len(f0)]


def _lowpass(y: np.ndarray, sr: int, edge_hz: float) -> np.ndarray:
    """Elliptic low-pass passing ``edge_hz`` and stopping the semitone above it.

    Applied causally: a zero-phase filter would ring backwards into the
    silence before each note, and pYIN happily pitches such faint pre-echo.
    """
    stop_hz = edge_hz * 2 ** (1 / 12)
    order, wn = scipy.signal.ellipord(edge_hz, stop_hz, gpass=1, gstop=40, fs=sr)
    sos = scipy.signal.ellip(order, 1, 40, wn, btype="low", output="sos", fs=sr)
    return scipy.signal.sosfilt(sos, y)


def _fix_octave_slips(notes: list[NoteEvent], window: int = 4) -> list[NoteEvent]:
    """Pull notes that leap an octave or more away from their neighbours back in.

    pYIN's typical failure is briefly locking onto a harmonic (or sub-harmonic)
    of the real pitch. A genuine melody rarely leaps 12+ semitones out of its
    current register and straight back, so such outliers are octave-shifted
    toward the median of the surrounding notes. Register changes survive:
    the median straddles them, so no single note is 12 away from it.
    """
    pitches = np.array([n.midi for n in notes])
    for i, n in enumerate(notes):
        neighbours = np.concatenate([pitches[max(0, i - window):i], pitches[i + 1:i + 1 + window]])
        if len(neighbours) < 2:
            continue
        centre = float(np.median(neighbours))
        while n.midi - centre >= 12:
            n.midi -= 12
        while centre - n.midi >= 12:
            n.midi += 12
    return notes


def _velocity(rms: np.ndarray, start: int, end: int) -> int:
    peak = float(rms.max()) or 1.0
    loudness = float(rms[start:max(end, start + 1)].mean()) / peak
    return int(np.clip(40 + loudness * 87, 1, 127))


def _segment(
    midi: np.ndarray,
    rms: np.ndarray,
    onset_frames: set[int],
    sr: int,
    min_note: float,
    merge_gap: float = 0.05,
    pre_echo: float = 0.15,
) -> list[NoteEvent]:
    frame_sec = HOP / sr
    notes: list[NoteEvent] = []
    prev_at_onset = False

    start = 0
    for i in range(1, len(midi) + 1):
        boundary = (
            i == len(midi)
            or midi[i] != midi[start]
            or (i in onset_frames and midi[i] >= 0)
        )
        if not boundary:
            continue
        pitch = int(midi[start])
        length = (i - start) * frame_sec
        if pitch >= 0 and length >= min_note:
            t0, t1 = start * frame_sec, i * frame_sec
            at_onset = start in onset_frames
            prev = notes[-1] if notes else None
            if prev and prev.midi == pitch and t0 - prev.end <= merge_gap:
                if not at_onset:
                    # Same pitch resumed after a tracking dropout, not a new attack.
                    prev.end = t1
                    start = i
                    continue
                if not prev_at_onset and prev.end - prev.start < pre_echo:
                    # Pitch tracking smears slightly ahead of the real attack;
                    # the onset marks where the note truly begins.
                    notes.pop()
            notes.append(NoteEvent(t0, t1, pitch, _velocity(rms, start, i)))
            prev_at_onset = at_onset
        start = i

    return notes


# ---------------------------------------------------------------------------
# Chords
# ---------------------------------------------------------------------------


@dataclass
class _Tone:
    midi: int
    salience: float  # relative to the strongest tone at this attack
    newness: float  # fraction of its energy that arrived at this attack
    on_partial: bool  # its fundamental coincides with a partial of a stronger tone


@dataclass
class _Attack:
    frame: int  # where the attack begins
    peak: int  # where it is loudest
    tones: list[_Tone]


class _Attacks:
    """The tones sounding, and newly struck, at each detected onset.

    Works on a constant-Q magnitude spectrum of the recording with one bin
    per semitone, centred on the recording's tuning. Bins run from the
    lowest tracked pitch (but no lower than A0) up to the tenth partial of
    the highest, capped below Nyquist, so every candidate fundamental in
    any staff's range has its partials available.
    """

    def __init__(
        self, y: np.ndarray, sr: int, ranges: list[tuple[str, str]], onsets: list[tuple[int, int]]
    ) -> None:
        pitches = [librosa.note_to_midi(name) for pair in ranges for name in pair]
        self.base = max(min(pitches), 21)
        self.n = max(pitches) - self.base + 1  # candidate fundamentals
        top = min(max(pitches) + int(PARTIALS[-1]), 120)
        tuning = librosa.estimate_tuning(y=y, sr=sr)
        cqt = librosa.cqt(
            y, sr=sr, hop_length=HOP, fmin=librosa.midi_to_hz(self.base),
            n_bins=top - self.base + 1, bins_per_octave=12, tuning=tuning,
        )
        # Constant-Q magnitudes scale as amplitude / sqrt(frequency); undo that
        # so partials of one tone can be compared as amplitudes.
        freqs = librosa.cqt_frequencies(cqt.shape[0], fmin=librosa.midi_to_hz(self.base), tuning=tuning)
        self.mag = (np.abs(cqt) * np.sqrt(freqs / freqs[0])[:, None]).astype(np.float32)
        self.frame_sec = HOP / sr
        # The attack is read a frame after the onset peaks, once the constant-Q
        # windows have settled on the new sound.
        spectra = [(self.mean(peak + 1, 6), self.mean(start - 8, 8)) for start, peak in onsets]
        loudest = max((float(_salience(after, self.n).max()) for after, _ in spectra), default=0.0)
        self.attacks = [
            _Attack(start, peak, self._tones(after, before, LEVEL_FLOOR * loudest))
            for (start, peak), (after, before) in zip(onsets, spectra)
        ]

    def mean(self, start: int, length: int) -> np.ndarray:
        """Mean magnitude per bin over frames [start, start + length)."""
        a, b = max(0, start), max(0, min(start + length, self.mag.shape[1]))
        if b <= a:
            return np.zeros(self.mag.shape[0], dtype=np.float32)
        return self.mag[:, a:b].mean(axis=1)

    def sounding(self, frame: int) -> dict[int, _Tone]:
        """The tones sounding at ``frame`` (newness relative to just before it), by pitch."""
        return {t.midi: t for t in self._tones(self.mean(frame, 6), self.mean(frame - 10, 8))}

    def decay_end(self, frame: int, pitches: list[int]) -> int:
        """First frame after ``frame`` where the pitches' energy has died away."""
        rows = [p - self.base for p in pitches if 0 <= p - self.base < self.mag.shape[0]]
        env = self.mag[rows, frame:].sum(axis=0) if rows else np.zeros(0)
        if len(env) == 0:
            return frame + 1
        peak_at = int(env[:12].argmax())
        quiet = np.nonzero(env[peak_at:] < DECAY_END * env[peak_at])[0]
        return frame + peak_at + (int(quiet[0]) if len(quiet) else len(env) - peak_at)

    def _tones(self, after: np.ndarray, before: np.ndarray, floor: float = 0.0) -> list[_Tone]:
        """Pull tones out of an attack's spectrum, strongest first.

        Each tone's partials are removed before the next is sought, so the
        harmonics of one tone do not pass for further tones. Newness is then
        judged from the partials each tone shares with no other, since a
        shared one may be new only because the other tone is.
        """
        salience = _salience(after, self.n)
        top = float(salience.max())
        if top <= 0 or top < floor:
            return []
        # Only peaks of the spectrum can be tones; the rest is leakage or noise.
        padded = np.concatenate([[-np.inf], salience, [-np.inf]])
        candidates = (salience > 0) & (salience >= padded[:-2]) & (salience >= padded[2:])
        wide = np.concatenate([[0, 0], after[: self.n + 2], [0, 0]])
        candidates &= after[: self.n] >= PEAK_RATIO * np.minimum(wide[:-4][: self.n], wide[4:][: self.n])

        found: list[tuple[int, float, bool]] = []
        residual = after.copy()
        while len(found) < MAX_TONES and candidates.any():
            current = np.where(candidates, _salience(residual, self.n), -1.0)
            tone = int(current.argmax())
            if current[tone] < EXPLAIN_RATIO * top:
                break
            candidates[tone] = False
            if residual[tone] < FUNDAMENTAL_SHARE * current[tone]:
                continue  # its evidence is other tones' partials: a ghost
            on_partial = any(tone - f in PARTIALS[1:] for f, _, _ in found)
            found.append((tone, float(current[tone]) / top, on_partial))
            _subtract(residual, tone, self.base + tone, after)

        flux = np.maximum(after - before, 0.0)
        tones = []
        for tone, strength, on_partial in found:
            others = {f + p for f, _, _ in found if f != tone for p in PARTIALS}
            own = [tone] + [
                tone + p for p in PARTIALS[1:] if tone + p < len(after) and tone + p not in others
            ]
            weights = PARTIAL_WEIGHTS[[int(np.nonzero(PARTIALS == b - tone)[0][0]) for b in own]]
            total = float((weights * after[own]).sum())
            newness = float((weights * flux[own]).sum() / total) if total > 0 else 0.0
            tones.append(_Tone(self.base + tone, strength, newness, on_partial))
        return tones


def _snap(midi: int, heard) -> int | None:
    """``midi`` if the spectrum heard it, else the heard pitch a semitone
    away (pYIN's usual error on chords), else None."""
    if midi in heard:
        return midi
    return next((m for m in (midi + 1, midi - 1) if m in heard), None)


def _salience(spec: np.ndarray, n: int) -> np.ndarray:
    """Weighted sum of each of the first ``n`` fundamentals' partials in ``spec``."""
    salience = np.zeros(n)
    for offset, weight in zip(PARTIALS, PARTIAL_WEIGHTS):
        count = min(n, len(spec) - offset)
        if count > 0:
            salience[:count] += weight * spec[offset:offset + count]
    return salience


def _subtract(spec: np.ndarray, tone: int, midi: int, original: np.ndarray) -> None:
    """Remove a tone's partials from ``spec`` in place.

    Each partial comes out only as far as expected: the lesser of what the
    envelope of its neighbouring partials in the ``original`` mixture
    predicts (Klapuri's spectral smoothness) and what the fundamental's
    roll-off predicts. A partial standing well above both leaves a residue:
    the evidence that a second tone, an octave, a fifth or two octaves up,
    shares it.
    """
    bins = tone + PARTIALS
    bins = bins[bins < len(spec)]
    amps = spec[bins]
    mixture = original[bins]
    smooth = np.array([mixture[max(0, h - 1):h + 2].mean() for h in range(len(bins))])
    lift = 1.0 + max(0.0, 48 - midi) / 16
    rolloff = lift * amps[0] * np.arange(1, len(bins) + 1) ** -ROLLOFF
    removed = np.minimum(amps, np.minimum(smooth, rolloff))
    removed[0] = amps[0]  # the fundamental is this tone's own
    spec[bins] -= removed
    for side in (bins - 1, bins + 1):
        ok = (side >= 0) & (side < len(spec))
        spec[side[ok]] -= np.minimum(spec[side[ok]], LEAK * removed[ok])


@dataclass
class _Event:
    start: int  # frame
    end: int | None  # frame; None until something tells us when the sound stopped
    tones: dict[int, float]  # midi -> salience
    heard: set[int]  # every tone the spectrum showed at the attack, struck or not
    weak: dict[int, float] = field(default_factory=dict)  # struck, but too doubtful on their own
    last: int = 0  # frame of the latest attack folded into this event
    anchor: int | None = None  # pYIN's pitch, when it joined in

    def add(self, midi: int, salience: float, polyphony: int) -> None:
        self.tones[midi] = max(salience, self.tones.get(midi, 0.0))
        while len(self.tones) > polyphony:
            weakest = min((m for m in self.tones if m != self.anchor), key=self.tones.__getitem__)
            del self.tones[weakest]


def _chords(
    notes: list[NoteEvent],
    track: np.ndarray,
    rms: np.ndarray,
    attacks: _Attacks,
    lo_name: str,
    hi_name: str,
    polyphony: int,
    min_note: float,
) -> list[NoteEvent]:
    """Rebuild a staff's notes around its attacks.

    Every onset at which tones in the staff's range were struck becomes an
    event carrying those tones. pYIN's notes then join in: one starting at
    an attack lends the event its pitch (if the spectrum heard it there) and
    its end; one starting between attacks is either the previous sound
    continuing, if its pitch was already ringing and nothing new arrived, or
    a legato change, which stays a note of its own.
    """
    lo, hi = librosa.note_to_midi(lo_name), librosa.note_to_midi(hi_name)
    fs = attacks.frame_sec
    near = round(min_note / fs)

    events: list[_Event] = []
    for k, attack in enumerate(attacks.attacks):
        in_range = [t for t in attack.tones if lo <= t.midi <= hi]
        if not in_range:
            continue
        newest = max(t.newness for t in in_range)
        struck = [t for t in in_range if t.newness >= max(NEW_FLOOR, NEW_RATIO * newest)]
        if not struck:
            continue
        strongest = max(t.salience for t in struck)
        until = attacks.attacks[k + 1].frame if k + 1 < len(attacks.attacks) else len(track)
        heard_by_pyin = set(track[attack.frame:min(until, attack.frame + 40)].tolist())
        # The attack's peak marks the note's start; the onset's backtracked
        # frame, where the previous sound bottomed out, can be well before it.
        event = _Event(attack.peak, None, {}, {t.midi for t in attack.tones}, last=attack.peak)
        for t in sorted(struck, key=lambda t: -t.salience):
            doubtful = (
                t.on_partial
                and (t.salience < PARTIAL_RATIO * strongest or t.salience < PARTIAL_FLOOR)
                and t.midi not in heard_by_pyin
            )
            if t.salience < CHORD_RATIO * strongest or doubtful or len(event.tones) >= polyphony:
                event.weak[t.midi] = t.salience  # a tone pYIN may yet vouch for
            else:
                event.tones[t.midi] = t.salience
        if not event.tones:
            continue
        if events and event.start - events[-1].last <= near:
            # One attack detected twice, or a chord rolled: the same event.
            prev = events[-1]
            prev.heard |= event.heard
            prev.weak.update(event.weak)
            prev.last = event.start
            for midi, salience in event.tones.items():
                prev.add(midi, salience, polyphony)
        else:
            events.append(event)

    for n in notes:
        start, end = round(n.start / fs), round(n.end / fs)
        prev = max((e for e in events if e.start < start), key=lambda e: e.start, default=None)
        event = min(
            (e for e in events if e.start - 4 <= start <= e.last + 6),
            key=lambda e: abs(e.start - start), default=None,
        )
        if event is not None:
            # pYIN's pitch joins the chord if the spectrum heard it struck here;
            # heard but not struck, it is the earlier sound still ringing, and
            # unheard it is a tracking error (pYIN is unreliable on chords).
            midi = _snap(n.midi, event.tones.keys() | event.weak.keys() | event.heard)
            if midi in event.tones or midi in event.weak:
                event.add(midi, 1.0, polyphony)
                event.anchor = midi
                event.end = max(event.end or 0, end)
            elif midi is not None and prev is not None and midi in prev.tones:
                prev.end = max(prev.end or 0, end)
            continue
        # Between attacks: a pitch already ringing is the previous sound
        # continuing (pYIN wanders between the tones of a chord); one the
        # spectrum does not hear is the tracker lost; anything new is a
        # legato change and stays a note of its own.
        sounding = attacks.sounding(start)
        midi = _snap(n.midi, sounding)
        settling = prev is not None and start - prev.start <= SETTLE
        if midi is None or settling or sounding[midi].newness < LEGATO_NEW:
            if prev is not None:
                prev_end = prev.end if prev.end is not None else attacks.decay_end(prev.start, list(prev.tones))
                if start - prev_end <= RESUME_GAP / fs and (midi is None or midi in prev.tones or midi in prev.weak):
                    if midi in prev.weak:
                        prev.add(midi, prev.weak.pop(midi), polyphony)
                    prev.end = max(prev_end, end)
            continue
        events.append(_Event(start, end, {midi: 1.0}, set(sounding), last=start, anchor=midi))
        events.sort(key=lambda e: e.start)

    result: list[NoteEvent] = []
    for i, event in enumerate(events):
        end = event.end if event.end is not None else attacks.decay_end(event.start, list(event.tones))
        if i + 1 < len(events):
            end = min(end, events[i + 1].start)
        end = max(end, event.start + round(min_note / fs))
        pitches = sorted(event.tones, key=lambda m: -event.tones[m])
        main = event.anchor if event.anchor in event.tones else pitches[0]
        extra = sorted(m for m in event.tones if m != main)
        result.append(NoteEvent(event.start * fs, end * fs, main, _velocity(rms, event.start, event.start + 8), extra))
    return result
