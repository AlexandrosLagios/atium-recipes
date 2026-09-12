import logging
import subprocess
from pathlib import Path

import yt_dlp
from pydantic import BaseModel

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class SocialBlocked(Exception):
    """yt-dlp could not reach the post. Ask the user for a screenshot instead."""


class SocialResult(BaseModel):
    caption: str = ""
    thumbnail_url: str = ""
    frames: list[bytes] = []


def keyframes(video: Path, workdir: Path, max_frames: int) -> list[bytes]:
    pattern = workdir / "frame%02d.jpg"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-i", str(video),
            "-vf", "fps=1/5,scale=768:-1",
            "-frames:v", str(max_frames),
            str(pattern),
        ],
        check=True,
        timeout=120,
    )
    return [path.read_bytes() for path in sorted(workdir.glob("frame*.jpg"))]


def fetch_social(url: str, workdir: Path, *, max_frames: int = 4) -> SocialResult:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": str(workdir / "post.%(ext)s"),
        "format": "mp4/bestvideo*+bestaudio/best",
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise SocialBlocked(str(exc)) from exc

    caption = (info.get("description") or info.get("title") or "").strip()
    result = SocialResult(caption=caption, thumbnail_url=info.get("thumbnail") or "")

    videos = [p for p in workdir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES]
    if videos:
        try:
            result.frames = keyframes(videos[0], workdir, max_frames)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("ffmpeg failed on %s: %s", url, exc)
        return result

    images = sorted(p for p in workdir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    result.frames = [p.read_bytes() for p in images[:max_frames]]
    return result
