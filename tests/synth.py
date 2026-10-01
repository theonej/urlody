"""A small additive piano for the tests: decaying, slightly inharmonic partials
with a sharp attack, so chord tones and their partials overlap as on a piano."""

import librosa
import numpy as np

SR = 22050


def piano_note(midi: int, seconds: float, amp: float = 0.25) -> np.ndarray:
    f = librosa.midi_to_hz(midi)
    t = np.arange(int(seconds * SR)) / SR
    inharmonicity = 0.0002 * (midi / 60) ** 2
    out = np.zeros_like(t)
    for h in range(1, 12):
        fh = f * h * np.sqrt(1 + inharmonicity * h * h)
        if fh > 0.45 * SR:
            break
        a = amp / h ** (1.1 if midi > 55 else 0.7)
        if h == 1 and midi < 52:
            a *= 0.7
        rate = (0.8 + 0.5 * h) * (0.5 + midi / 70)
        out += a * np.exp(-t * rate) * (1 + 1.5 * np.exp(-t / 0.04)) * np.sin(2 * np.pi * fh * t)
    return out * np.minimum(1, t / 0.004)


def render(events: list[tuple[float, float, list[int], float]], seconds: float) -> np.ndarray:
    """Mix ``(onset, duration, midi pitches, amplitude)`` events into one signal."""
    y = np.zeros(int(seconds * SR))
    for onset, duration, pitches, amp in events:
        for midi in pitches:
            tone = piano_note(midi, duration, amp)
            i = int(onset * SR)
            n = min(len(tone), len(y) - i)
            y[i:i + n] += tone[:n]
    return (0.6 * y / max(1e-9, np.abs(y).max())).astype(np.float32)
