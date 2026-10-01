"""Check a built scorer binary end to end.

    uv run python scripts/smoke_test.py dist/scorer.exe

Synthesises a C-major scale, runs the binary on it, and checks that the PDF,
MusicXML and MIDI files are written and that the transcribed notes are right.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from music21 import converter

EXPECTED = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "C5", "G4", "E4", "C4"]
SR = 22050
BEAT = 0.5  # seconds per quarter note at 120 BPM


def synth_scale(path: Path) -> None:
    tones = []
    for name in EXPECTED:
        t = np.arange(int(SR * BEAT)) / SR
        f = librosa.note_to_hz(name)
        tone = sum(np.sin(2 * np.pi * f * k * t) / k for k in (1, 2, 3))
        tones.append(0.3 * tone * np.minimum(1, t / 0.01) * np.exp(-2 * t))
    sf.write(path, np.concatenate([np.zeros(SR // 2), *tones]), SR)


def main(exe: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "scale.wav"
        out = Path(tmp) / "out"
        synth_scale(wav)

        subprocess.run([exe, str(wav), "-o", str(out)], check=True)

        for ext in (".pdf", ".musicxml", ".mid"):
            file = out / f"scale{ext}"
            assert file.is_file() and file.stat().st_size > 0, f"missing output: {file.name}"

        notes = [n.nameWithOctave for n in converter.parse(out / "scale.musicxml").flatten().notes]
        assert notes == EXPECTED, f"transcribed {notes}, expected {EXPECTED}"
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
