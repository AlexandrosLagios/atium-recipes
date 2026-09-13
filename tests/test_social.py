import subprocess
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


class FakeYDLPhotoPost:
    def __init__(self, info, workdir, image_names):
        self.info = info
        self.workdir = workdir
        self.image_names = image_names

    def __call__(self, opts):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        for name in self.image_names:
            (self.workdir / name).write_bytes(name.encode())
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


def test_fetch_social_falls_back_to_the_title_when_the_description_is_blank(tmp_path, monkeypatch):
    info = {"description": "   ", "title": "Real Title"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL(info, tmp_path))
    monkeypatch.setattr(social, "keyframes", lambda video, workdir, n: [])

    assert fetch_social("https://instagram.com/p/abc/", tmp_path).caption == "Real Title"


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


def test_fetch_social_survives_a_calledprocesserror_from_keyframes(tmp_path, monkeypatch):
    info = {"description": "Noodles caption", "thumbnail": "https://cdn/t.jpg"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL(info, tmp_path))

    def raise_called_process_error(video, workdir, n):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(social, "keyframes", raise_called_process_error)

    result = fetch_social("https://instagram.com/p/abc/", tmp_path)

    assert result.caption == "Noodles caption"
    assert result.thumbnail_url == "https://cdn/t.jpg"
    assert result.frames == []


def test_fetch_social_survives_a_timeoutexpired_from_keyframes(tmp_path, monkeypatch):
    info = {"description": "Noodles caption", "thumbnail": "https://cdn/t.jpg"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL(info, tmp_path))

    def raise_timeout_expired(video, workdir, n):
        raise subprocess.TimeoutExpired("ffmpeg", 120)

    monkeypatch.setattr(social, "keyframes", raise_timeout_expired)

    result = fetch_social("https://instagram.com/p/abc/", tmp_path)

    assert result.caption == "Noodles caption"
    assert result.thumbnail_url == "https://cdn/t.jpg"
    assert result.frames == []


def test_fetch_social_reads_images_capped_at_max_frames_when_there_is_no_video(
    tmp_path, monkeypatch
):
    image_names = ["photo1.jpg", "photo2.jpg", "photo3.jpg"]
    info = {"description": "Photo carousel recipe"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDLPhotoPost(info, tmp_path, image_names))

    result = fetch_social("https://instagram.com/p/photos/", tmp_path, max_frames=2)

    assert result.caption == "Photo carousel recipe"
    assert result.frames == [b"photo1.jpg", b"photo2.jpg"]


def test_a_missing_ffmpeg_binary_is_not_blocking(tmp_path, monkeypatch):
    info = {"description": "Best noodles", "thumbnail": "https://cdn/t.jpg"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL(info, tmp_path))

    def no_ffmpeg(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'ffmpeg'")

    monkeypatch.setattr(social.subprocess, "run", no_ffmpeg)

    result = fetch_social("https://www.instagram.com/p/abc/", tmp_path)

    assert result.frames == []
    assert result.caption == "Best noodles"
    assert result.thumbnail_url == "https://cdn/t.jpg"
