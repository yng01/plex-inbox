import json

import pytest

from bot.probe import (
    ProbeError,
    classify_probe_error,
    extract_audio_tracks,
    parse_probe,
    run_probe,
)
from fakes import FakeProbeSpawner

FORMATS = [
    {"format_id": "1", "vcodec": "avc1", "acodec": "mp4a.40.2", "width": 1920, "height": 1080},
    {"format_id": "2", "vcodec": "none", "acodec": "opus", "language": "en"},
    {"format_id": "3", "vcodec": "none", "acodec": "opus", "language": "ar"},
    {"format_id": "4", "vcodec": "none", "acodec": "opus", "language": "en"},  # dup
    {"format_id": "5", "vcodec": "none", "acodec": None},  # no codec -> skipped
    {"format_id": "6", "vcodec": "none", "acodec": ""},  # empty codec -> skipped
]

VIDEO_JSON = {
    "id": "abc123",
    "title": "Great Video",
    "extractor_key": "YouTube",
    "channel": "Cool Channel",
    "uploader": "Cool Channel",
    "webpage_url": "https://youtube.com/watch?v=abc123",
    "duration": 372.5,
    "resolution": "1920x1080",
    "thumbnail": "https://i.example/thumb.jpg",
    "description": "A very long description " * 30,
    "release_year": 2024,
    "genres": ["Documentary"],
    "tags": ["nature"],
    "formats": FORMATS,
}


def test_parse_single_video():
    info = parse_probe(VIDEO_JSON)
    assert info.id == "abc123"
    assert info.title == "Great Video"
    assert info.site == "YouTube"
    assert info.channel == "Cool Channel"
    assert info.is_playlist is False
    assert info.year == 2024
    assert info.resolution == "1920x1080"
    assert info.genres == ["Documentary"]
    assert info.tags == ["nature"]
    assert [(a.language, a.codec) for a in info.audio_tracks] == [
        ("en", "opus"),
        ("ar", "opus"),
    ]


def test_parse_playlist():
    raw = {
        "id": "pl1",
        "playlist_title": "Show",
        "extractor_key": "YouTube",
        "channel": "The Show",
        "webpage_url": "https://youtube.com/playlist?list=pl1",
        "entries": [
            {"id": "e1", "title": "Ep 1", "webpage_url": "https://youtube.com/watch?v=e1",
             "uploader": "The Show", "duration": 600},
            {"id": "e2", "title": "Ep 2", "webpage_url": "https://youtube.com/watch?v=e2"},
        ],
    }
    info = parse_probe(raw)
    assert info.is_playlist is True
    assert len(info.entries) == 2
    assert info.entries[0].id == "e1"
    assert info.entries[1].channel == "The Show"


def test_extract_audio_tracks_dedupes_and_filters():
    tracks = extract_audio_tracks(FORMATS)
    assert [(t.language, t.codec) for t in tracks] == [("en", "opus"), ("ar", "opus")]
    assert extract_audio_tracks(None) == []
    assert extract_audio_tracks([{"vcodec": "avc1", "acodec": "mp3"}]) == []


def test_extract_missing_language_becomes_und():
    tracks = extract_audio_tracks([{"vcodec": "none", "acodec": "aac"}])
    assert tracks[0].language == "und"


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("ERROR: Unsupported URL: 'http://x'", "unsupported"),
        ("ERROR: This video is private", "private"),
        ("ERROR: video is not available in your country", "region"),
        ("ERROR: Video unavailable", "unavailable"),
        ("ERROR: something totally unknown", "generic"),
    ],
)
def test_classify_probe_error(stderr, expected):
    assert classify_probe_error(stderr, timed_out=False) == expected


def test_classify_timeout_wins():
    assert classify_probe_error("ERROR: something totally unknown", timed_out=True) == "timeout"


async def test_run_probe_success():
    sp = FakeProbeSpawner(stdout=json.dumps(VIDEO_JSON))
    info = await run_probe(sp, "https://youtube.com/watch?v=abc123")
    assert info.id == "abc123"
    assert sp.cmds and "-J" in sp.cmds[0]


async def test_run_probe_error_classification():
    sp = FakeProbeSpawner(stdout="", stderr="ERROR: This video is private", returncode=1)
    with pytest.raises(ProbeError) as exc:
        await run_probe(sp, "https://x/v")
    assert exc.value.code == "private"


async def test_run_probe_timeout():
    sp = FakeProbeSpawner(stdout=json.dumps(VIDEO_JSON), delay=0.2)
    with pytest.raises(ProbeError) as exc:
        await run_probe(sp, "https://x/v", timeout=0.05)
    assert exc.value.code == "timeout"
