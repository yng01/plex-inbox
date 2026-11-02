import json
import xml.etree.ElementTree as ET

from bot import plex
from fakes import FakeHttp


def test_sanitize_filename():
    assert plex.sanitize_filename('a/b:c*d?e"f<g|h\\i') == "a b c d e f g h i"
    assert plex.sanitize_filename("  lots   of   spaces  ") == "lots of spaces"
    assert plex.sanitize_filename("") == "untitled"
    assert plex.sanitize_filename("...") == "untitled"
    assert len(plex.sanitize_filename("a" * 300)) <= 150
    # Arabic (non-ASCII) titles survive, character-boundary safe
    assert plex.sanitize_filename("محا / اختبار") == "محا اختبار"


def test_format_duration():
    assert plex.format_duration(0) == "0s"
    assert plex.format_duration(None) == "0s"
    assert plex.format_duration(45) == "45s"
    assert plex.format_duration(213) == "3m33s"
    assert plex.format_duration(3723) == "1h2m3s"


def test_build_tags_multi_audio():
    tags = plex.build_tags(
        site="YouTube",
        channel="Cool Channel",
        requestor="alice",
        audio_langs=["en", "ar"],
        year=2024,
        duration=372.5,
        genres=["Documentary"],
        keywords=["nature"],
    )
    assert "site:youtube" in tags
    assert "channel:cool channel" in tags
    assert "requested:alice" in tags
    assert "audio:en" in tags and "audio:ar" in tags
    assert "year:2024" in tags
    assert "duration:6m12s" in tags
    assert "genre:documentary" in tags
    assert "keyword:nature" in tags
    # every tag: lowercase, single line
    for tag in tags:
        assert tag == tag.lower().strip()
        assert "\n" not in tag


def test_build_tags_single_audio_omitted():
    tags = plex.build_tags("YouTube", "unknown", "user:123", ["en"], None, 45)
    assert not any(t.startswith("audio:") for t in tags)
    assert "channel:unknown" not in tags
    assert "requested:user:123" in tags
    assert "duration:45s" in tags


def test_build_tags_dedupe_audio_langs():
    # case-insensitive duplicates collapse to one tag per language
    tags = plex.build_tags("YouTube", "c", "alice", ["en", "EN", "ar", "AR", "ar"], 2020, 10)
    assert tags.count("audio:en") == 1
    assert tags.count("audio:ar") == 1


def test_nfo_xml_wellformed(tmp_path):
    p = plex.write_nfo(
        tmp_path / "v.nfo", "Title X", "summary", 2021, "Drama",
        ["site:youtube", "requested:alice"],
    )
    root = ET.parse(p).getroot()
    assert root.tag == "video"
    assert root.findtext("title") == "Title X"
    assert root.findtext("summary") == "summary"
    assert root.findtext("year") == "2021"
    assert root.findtext("genre") == "Drama"
    assert [e.text for e in root.find("tags")] == ["site:youtube", "requested:alice"]


def test_nfo_summary_truncated(tmp_path):
    p = plex.write_nfo(tmp_path / "v.nfo", "T", "x" * 900, None, None, [])
    root = ET.parse(p).getroot()
    assert len(root.findtext("summary")) == 500


def test_sidecar_roundtrip(tmp_path):
    sc = plex.Sidecar(
        id="v1",
        site="YouTube",
        channel="Ch",
        title="T",
        url="https://x",
        requestor="alice",
        added_at="2026-01-01T00:00:00Z",
        final_path="/data/YouTube/Ch/T.mkv",
        size_bytes=123,
        duration=60.0,
        year=2024,
        audio_tracks=[{"language": "en", "codec": "opus"}],
        genres=["Doc"],
        tags=["a"],
        user_id=123456789,
        username="alice",
    )
    p = plex.write_sidecar(tmp_path / "v1.info.json", sc)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["requestor"] == "alice"
    assert data["user_id"] == 123456789
    assert data["username"] == "alice"
    assert data["audio_tracks"] == [{"language": "en", "codec": "opus"}]
    assert data["size_bytes"] == 123


def test_pick_poster_url_prefers_avatar():
    class I:
        raw = {"channel_favicon": "https://i/av.png", "thumbnail": "https://i/thumb.jpg"}

    assert plex.pick_poster_url(I()) == "https://i/av.png"

    class J:
        raw = {"thumbnail": "https://i/thumb.jpg"}

    assert plex.pick_poster_url(J()) == "https://i/thumb.jpg"

    class K:
        raw = {}

    assert plex.pick_poster_url(K()) is None


def test_pick_avatar_url_no_thumbnail_fallback():
    class J:
        raw = {"thumbnail": "https://i/thumb.jpg"}

    assert plex.pick_avatar_url(J()) is None

    class I:
        raw = {"uploader_image": "https://i/av.png", "thumbnail": "https://i/t.jpg"}

    assert plex.pick_avatar_url(I()) == "https://i/av.png"


async def test_resolve_youtube_avatar_url_og_image():
    html = (
        b"<html><head>"
        b'<meta property="og:image" content="https://yt3.example/av.jpg">'
        b"</head></html>"
    )
    http = FakeHttp(get_status=200, get_content=html)
    url = await plex.resolve_youtube_avatar_url(http, "UC123")
    assert url == "https://yt3.example/av.jpg"
    assert http.gets[0]["url"] == "https://www.youtube.com/channel/UC123"
    assert http.gets[0]["headers"].get("Cookie") == "CONSENT=YES+1"


async def test_resolve_youtube_avatar_url_no_id_skips_get():
    http = FakeHttp()
    assert await plex.resolve_youtube_avatar_url(http, None) is None
    assert http.gets == []


async def test_resolve_youtube_avatar_url_non200_is_none():
    http = FakeHttp(get_status=404, get_content=b"<html></html>")
    assert await plex.resolve_youtube_avatar_url(http, "UC123") is None


async def test_resolve_youtube_avatar_url_no_match_is_none():
    http = FakeHttp(get_status=200, get_content=b"<html><head></head></html>")
    assert await plex.resolve_youtube_avatar_url(http, "UC123") is None


async def test_download_image_ok(tmp_path):
    http = FakeHttp(get_status=200, get_content=b"jpgbytes")
    dest = tmp_path / "sub" / "thumb.jpg"
    assert await plex.download_image(http, "https://i/t.jpg", dest) is True
    assert dest.read_bytes() == b"jpgbytes"
    assert http.gets[0]["url"] == "https://i/t.jpg"


async def test_download_image_fail(tmp_path):
    http = FakeHttp(get_status=404)
    assert await plex.download_image(http, "https://i/t.jpg", tmp_path / "x.jpg") is False


async def test_refresh_plex_ok_and_fail():
    ok = FakeHttp(post_status=204)
    assert await plex.refresh_plex(ok, "https://plex", "tok") is True
    assert ok.posts[0]["headers"]["X-Plex-Token"] == "tok"
    assert ok.posts[0]["url"].endswith("/library/sections/refresh")

    bad = FakeHttp(post_status=500)
    assert await plex.refresh_plex(bad, "https://plex", "tok") is False
