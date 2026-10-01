"""Command-line entry point: ``scorer <url>``."""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

from . import __version__
from .pipeline import GRIDS, Options, TranscriptionError, run


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scorer",
        description="Download music from a URL and transcribe it into a score.",
    )
    p.add_argument("url", help="YouTube/SoundCloud/Bandcamp/direct audio URL, or a local file")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("scores"))
    p.add_argument("--title", help="score title (defaults to the track title)")
    p.add_argument(
        "-f", "--formats", default="pdf,musicxml,midi",
        help="comma-separated: pdf, musicxml, midi. Default: all three",
    )
    p.add_argument("--start", type=float, help="start offset in seconds")
    p.add_argument("--duration", type=float, help="length to transcribe in seconds")
    p.add_argument("--tempo", type=float, help="force tempo in BPM instead of detecting it")
    p.add_argument("--time-signature", default="4/4", help="default: 4/4")
    p.add_argument(
        "--grid", type=int, default=16, choices=GRIDS,
        help="shortest note value to quantise to (16 = sixteenth notes). Default: 16",
    )
    p.add_argument("--min-note-ms", type=float, default=80, help="drop notes shorter than this")
    p.add_argument(
        "--polyphony", type=int, default=4, metavar="N",
        help="most notes to stack in a chord on one staff (default 4); 1 writes each staff "
             "as a single line with no chords",
    )
    p.add_argument("--lowest", default="C2", help="lowest pitch to track (default C2)")
    p.add_argument("--highest", default="C7", help="highest pitch to track (default C7)")
    p.add_argument(
        "--split", nargs="?", const="C4", metavar="NOTE",
        help="write a two-staff piano score: pitches from NOTE up go on a treble staff, "
             "those below it on a bass staff (NOTE defaults to C4, middle C)",
    )
    p.add_argument("--keep-audio", action="store_true", help="also save the decoded WAV")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    args.options = Options(
        title=args.title,
        formats={f.strip().lower() for f in args.formats.split(",") if f.strip()},
        start=args.start,
        duration=args.duration,
        tempo=args.tempo,
        time_signature=args.time_signature,
        grid=args.grid,
        min_note_ms=args.min_note_ms,
        lowest=args.lowest,
        highest=args.highest,
        split=args.split,
        polyphony=args.polyphony,
        keep_audio=args.keep_audio,
    )
    try:
        args.options.validate()
    except ValueError as exc:
        p.error(str(exc))
    return args


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="numba")
    # Track titles can contain characters a redirected Windows console can't encode.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    args = parse_args(argv)

    try:
        result = run(args.url, args.output_dir, args.options, log=log)
    except TranscriptionError as exc:
        log(f"error: {exc}")
        return 1

    print(f"\n{result.title}")
    print(f"  tempo  ~{result.tempo:.0f} BPM   key  {result.key}   time  {result.time_signature}")
    if result.split:
        upper, lower = result.staff_notes
        print(f"  {upper} treble + {lower} bass notes ({result.chords} chords) "
              f"across {result.measures} measures")
    else:
        print(f"  {sum(result.staff_notes)} notes ({result.chords} chords) "
              f"across {result.measures} measures")
    for path in result.files:
        print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
