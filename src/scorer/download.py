"""Fetch audio from a URL (or local path) and normalise it to a mono WAV file."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import yt_dlp

SAMPLE_RATE = 22050


class DownloadError(RuntimeError):
    pass


def fetch_audio(source: str, workdir: Path) -> tuple[Path, str]:
    """Download ``source`` into ``workdir``. Returns (file path, title).

    ``source`` may be anything yt-dlp understands (YouTube, SoundCloud,
    Bandcamp, a direct link to an .mp3/.wav, ...) or a local file path.
    """
    local = Path(source).expanduser()
    if local.is_file():
        return local, local.stem

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=True)
            if info.get("entries"):
                # Search results / playlists: use the first (only) downloaded item.
                info = next(e for e in info["entries"] if e)
            path = Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(str(exc)) from exc

    if not path.exists():
        # Some extractors change the extension after download.
        candidates = sorted(workdir.glob("source.*"))
        if not candidates:
            raise DownloadError(f"yt-dlp reported success but no file was written for {source}")
        path = candidates[0]
    return path, info.get("title") or path.stem


def to_wav(
    src: Path,
    dest: Path,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """Convert any audio/video file to mono 22.05 kHz WAV using a bundled ffmpeg."""
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", str(dest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise DownloadError(f"ffmpeg failed to decode {src}: {result.stderr.strip()}")
    return dest
