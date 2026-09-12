from pathlib import Path

import pytest
import yt_dlp

from recipebot import social
from recipebot.social import SocialBlocked, fetch_social


class FakeYDL:
    def __init__(self, info, workdir, error=None):
        self.info = info
        self.workdir = workdir
        self.error = error

    def __call__(self, opts):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if self.error:
            raise self.error
        (self.workdir / "video.mp4").write_bytes(b"fake video")
        return self.info


def test_fetch_social_returns_the_caption_and_the_frames(tmp_path, monkeypatch):
    info = {"description": "Best noodles ever. 200g noodles.", "thumbnail": "https://cdn/t.jpg"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL(info, tmp_path))
    monkeypatch.setattr(social, "keyframes", lambda video, workdir, n: [b"frame1", b"frame2"])

    result = fetch_social("https://instagram.com/p/abc/", tmp_path)

    assert result.caption == "Best noodles ever. 200g noodles."
    assert result.thumbnail_url == "https://cdn/t.jpg"
    assert result.frames == [b"frame1", b"frame2"]


def test_fetch_social_falls_back_to_the_title_when_there_is_no_description(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL({"title": "Noodles"}, tmp_path))
    monkeypatch.setattr(social, "keyframes", lambda video, workdir, n: [])

    assert fetch_social("https://tiktok.com/@a/video/1", tmp_path).caption == "Noodles"


def test_a_download_error_becomes_social_blocked(tmp_path, monkeypatch):
    error = yt_dlp.utils.DownloadError("login required")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL({}, tmp_path, error=error))

    with pytest.raises(SocialBlocked):
        fetch_social("https://instagram.com/p/abc/", tmp_path)


def test_keyframes_returns_the_bytes_ffmpeg_wrote(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    def fake_run(cmd, **kwargs):
        (tmp_path / "frame01.jpg").write_bytes(b"jpeg-one")
        (tmp_path / "frame02.jpg").write_bytes(b"jpeg-two")
        return None

    monkeypatch.setattr(social.subprocess, "run", fake_run)

    assert social.keyframes(video, tmp_path, 4) == [b"jpeg-one", b"jpeg-two"]
