"""The whole job, from a URL to written score files, shared by the CLI and the API."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import librosa

from .download import SAMPLE_RATE, DownloadError, fetch_audio, to_wav
from .score import build_score, write_outputs
from .transcribe import transcribe

FORMATS = {"musicxml", "midi", "pdf"}
GRIDS = (4, 8, 16, 32)


class TranscriptionError(Exception):
    """The job could not be done: the audio couldn't be fetched, or holds no notes."""


@dataclass
class Options:
    """Everything about a transcription except the source and where it goes."""

    title: str | None = None  # defaults to the track's title
    formats: set[str] = field(default_factory=lambda: set(FORMATS))
    start: float | None = None  # seconds into the track
    duration: float | None = None  # seconds to transcribe
    tempo: float | None = None  # BPM; detected when None
    time_signature: str = "4/4"
    grid: int = 16  # shortest note value to quantise to
    min_note_ms: float = 80
    lowest: str = "C2"
    highest: str = "C7"
    split: str | None = None  # note from which a treble staff starts; None for one staff
    polyphony: int = 4  # most notes in a chord on one staff
    keep_audio: bool = False

    def validate(self) -> None:
        """Raise ValueError, with a message fit to show the user, if anything is off."""
        if unknown := self.formats - FORMATS:
            raise ValueError(f"unknown format(s): {', '.join(sorted(unknown))}")
        if not self.formats:
            raise ValueError("at least one output format is needed")
        if not re.fullmatch(r"\d+/\d+", self.time_signature):
            raise ValueError("time signature must look like 3/4 or 6/8")
        if self.grid not in GRIDS:
            raise ValueError(f"grid must be one of {', '.join(map(str, GRIDS))}")
        if self.polyphony < 1:
            raise ValueError("polyphony must be at least 1")
        if self.min_note_ms < 0:
            raise ValueError("min_note_ms cannot be negative")
        if self.tempo is not None and self.tempo <= 0:
            raise ValueError("tempo must be positive")
        try:
            lo, hi = librosa.note_to_midi(self.lowest), librosa.note_to_midi(self.highest)
            split = librosa.note_to_midi(self.split) if self.split else None
        except librosa.ParameterError:
            raise ValueError("pitches must be note names like C2, F#3 or Bb4") from None
        if not lo < hi:
            raise ValueError("lowest must be below highest")
        if split is not None and not lo < split < hi:
            raise ValueError("split must lie between lowest and highest")

    @property
    def ranges(self) -> list[tuple[str, str]]:
        """The (lowest, highest) range tracked for each staff, top staff first."""
        if not self.split:
            return [(self.lowest, self.highest)]
        # Treble staff from the split up; bass staff up to the semitone below it.
        below_split = librosa.midi_to_note(librosa.note_to_midi(self.split) - 1, unicode=False)
        return [(self.split, self.highest), (self.lowest, below_split)]


@dataclass
class Result:
    title: str
    files: list[Path]
    tempo: float  # BPM
    key: str  # as music21 names it, e.g. "E- major", or "?"
    key_name: str  # spelled out, e.g. "E-flat major", or "?"
    time_signature: str
    measures: int
    staff_notes: list[int]  # notes (chords counting once) per staff, top staff first
    chords: int
    split: bool


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", text).strip("_")
    return slug[:80] or "score"


def run(
    url: str,
    output_dir: Path,
    options: Options | None = None,
    log: Callable[[str], None] = lambda message: None,
) -> Result:
    """Fetch ``url``, transcribe it and write the score files into ``output_dir``.

    ``log`` receives progress messages. Raises TranscriptionError when the
    audio cannot be fetched or decoded, or no notes are found in it.
    """
    options = options or Options()
    options.validate()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="scorer-") as tmp:
        work = Path(tmp)
        try:
            log(f"Fetching {url} ...")
            src, track_title = fetch_audio(url, work)
            title = options.title or track_title
            base = output_dir / slugify(title)

            log("Decoding audio ...")
            wav = to_wav(src, work / "audio.wav", options.start, options.duration)
        except DownloadError as exc:
            raise TranscriptionError(str(exc)) from exc

        y, sr = librosa.load(wav, sr=SAMPLE_RATE, mono=True)
        if len(y) == 0:
            raise TranscriptionError("no audio found")
        log(f"Transcribing {len(y) / sr:.1f}s of audio (this can take a while) ...")
        transcription = transcribe(
            y, sr, options.ranges, min_note=options.min_note_ms / 1000, tempo=options.tempo,
            polyphony=options.polyphony,
        )
        if not transcription.notes:
            raise TranscriptionError("no pitched notes detected; try a track with a clearer melody")

        log("Engraving score ...")
        score, detected_key = build_score(
            transcription, title, options.time_signature, subdivision=options.grid // 4
        )
        key_name = "?"
        if detected_key:
            key_name = f"{detected_key.tonic.name} {detected_key.mode}"
            key_name = key_name.replace("-", "-flat").replace("#", "-sharp")
        kind = "Piano transcription" if options.split else "Melody transcription"
        subtitle = (
            f"{kind}  ·  ~{transcription.tempo:.0f} BPM  ·  {key_name}  ·  {options.time_signature}"
        )
        files = write_outputs(score, base, options.formats, title, subtitle)

        if options.keep_audio:
            kept = base.with_suffix(".wav")
            kept.write_bytes(wav.read_bytes())
            files.append(kept)

    return Result(
        title=title,
        files=files,
        tempo=transcription.tempo,
        key=str(detected_key) if detected_key else "?",
        key_name=key_name,
        time_signature=options.time_signature,
        measures=len(score.parts[0].getElementsByClass("Measure")),
        staff_notes=[len(staff) for staff in transcription.staves],
        chords=sum(1 for n in transcription.notes if n.extra),
        split=bool(options.split),
    )
