"""Plex-ready metadata on disk.

Layout produced for each video (under ``<DATA_DIR>/<site>/<channel>/``)::

    <id>.mkv                the merged video (mkv carries multiple audio streams);
                            title/date embedded in the file, channel as genre
    <id>.jpg                thumbnail, stem matches the video (Plex hover/grid art)
    <id>.info.json          sidecar: requestor, timestamp, path, size, audio tracks
    <id>.nfo                Kodi/Plex NFO with <title>/<summary>/<year>/<genre>/<tags>
    poster.jpg              channel artwork (once per channel folder)

``<id>`` is the item's own id from the probe (e.g. the YouTube video id; for an
uploaded video file, its Telegram ``file_unique_id``). The human-readable title is
embedded in the media file itself (via ``--embed-metadata``) and also written to
the NFO and sidecar, so Plex shows the proper title regardless of the id-based
file name.

Tags are the collection hook: lowercase, single-line, ``key:value`` so they are trivial
to filter in Plex (``tag contains requested:ali123``). Because they are written to disk,
Plex collections keep working even if the bot is reinstalled.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Characters that are unsafe or awkward in filenames across common filesystems.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_filename(name: str, maxlen: int = 150) -> str:
    """Make a string safe to use as a single filename/directory component.

    Replaces path separators and unsafe characters, collapses whitespace, trims
    trailing dots/spaces, caps the length (on a character boundary, so Arabic titles
    are not split mid-codepoint), and guarantees a non-empty result.
    """
    s = _UNSAFE.sub(" ", str(name or ""))
    s = _WHITESPACE.sub(" ", s).strip()
    s = s.strip(".").strip()
    if not s:
        return "untitled"
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .")
    return s or "untitled"


def format_duration(seconds: float | None) -> str:
    """Human duration like ``1h2m3s`` / ``3m33s`` / ``45s`` / ``0s``."""
    if not seconds or seconds <= 0:
        return "0s"
    seconds = round(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return "".join(parts)


def _tag_value(v: str) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def build_tags(
    site: str,
    channel: str,
    requestor: str,
    audio_langs: Sequence[str],
    year: int | None,
    duration: float | None,
    genres: Sequence[str] = (),
    keywords: Sequence[str] = (),
) -> list[str]:
    """Assemble the NFO ``<tag>`` list (lowercase, single-line, ``key:value``).

    Audio tags are one per language and are omitted entirely when there is only a
    single (default) track — matching the spec that a lone default track adds no
    ``audio:`` tag.
    """
    tags = [f"site:{_tag_value(site) or 'unknown'}"]
    ch = _tag_value(channel)
    if ch and ch != "unknown":
        tags.append(f"channel:{ch}")
    tags.append(f"requested:{_tag_value(requestor) or 'user:unknown'}")

    langs = []
    seen = set()
    for lang in audio_langs:
        tag = _tag_value(lang)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        langs.append(tag)
    if len(langs) > 1:  # omit for a single default track
        for tag in langs:
            tags.append(f"audio:{tag}")

    if year and 1000 <= int(year) <= 2999:
        tags.append(f"year:{int(year)}")
    tags.append(f"duration:{format_duration(duration)}")

    for g in genres[:8]:
        g = _tag_value(g)
        if g:
            tags.append(f"genre:{g}")
    for k in keywords[:8]:
        k = _tag_value(k)
        if k:
            tags.append(f"keyword:{k}")
    return tags


def build_nfo_xml(
    title: str,
    summary: str,
    year: int | None,
    genre: str | None,
    tags: Sequence[str],
) -> str:
    """Render a Kodi-style ``<video>`` NFO as an XML string."""
    root = ET.Element("video")
    ET.SubElement(root, "title").text = title or ""
    ET.SubElement(root, "summary").text = (summary or "")[:500]
    if year and 1000 <= int(year) <= 2999:
        ET.SubElement(root, "year").text = str(int(year))
    if genre:
        ET.SubElement(root, "genre").text = genre
    if tags:
        tags_el = ET.SubElement(root, "tags")
        for tag in tags:
            ET.SubElement(tags_el, "tag").text = tag
    ET.indent(root)  # py>=3.9
    return (
        '<?xml version="1.0" encoding="utf-8" ?>\n'
        + ET.tostring(root, encoding="unicode")
        + "\n"
    )


def write_nfo(
    path: Path,
    title: str,
    summary: str,
    year: int | None,
    genre: str | None,
    tags: Sequence[str],
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_nfo_xml(title, summary, year, genre, tags), encoding="utf-8")
    return p


@dataclass
class Sidecar:
    id: str
    site: str
    channel: str
    title: str
    url: str
    requestor: str
    added_at: str
    final_path: str
    size_bytes: int
    duration: float | None
    year: int | None
    audio_tracks: list[dict]  # [{"language":..,"codec":..}]
    genres: list[str]
    tags: list[str]
    user_id: int | None = None  # Telegram numeric id of the requester
    username: str | None = None  # Telegram username of the requester (if any)


def write_sidecar(path: Path, sc: Sidecar) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": sc.id,
        "site": sc.site,
        "channel": sc.channel,
        "title": sc.title,
        "url": sc.url,
        "requestor": sc.requestor,
        "user_id": sc.user_id,
        "username": sc.username,
        "added_at": sc.added_at,
        "final_path": sc.final_path,
        "size_bytes": sc.size_bytes,
        "duration": sc.duration,
        "year": sc.year,
        "audio_tracks": sc.audio_tracks,
        "genres": sc.genres,
        "tags": sc.tags,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


#: probe fields that carry a channel/uploader *avatar* (best first)
_AVATAR_KEYS = (
    "channel_favicon",
    "uploader_avatar",
    "uploader_image",
    "channel_image",
    "channel_logo",
    "uploader_logo",
    "avatar",
)


def pick_avatar_url(info) -> str | None:
    """Avatar URL from probe fields, or None (no video-thumbnail fallback)."""
    raw = getattr(info, "raw", {}) or {}
    for key in _AVATAR_KEYS:
        v = (raw.get(key) or "").strip()
        if v.startswith(("http://", "https://")):
            return v
    return None


def pick_poster_url(info) -> str | None:
    """Best-effort channel/uploader avatar URL; falls back to the video thumbnail."""
    raw = getattr(info, "raw", {}) or {}
    return (
        pick_avatar_url(info)
        or _first_http_url(raw.get("thumbnail"))
        or _first_http_url(getattr(info, "thumbnail", None))
    )


def _first_http_url(v) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else ""
    return v if v.startswith(("http://", "https://")) else None


_OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')
_ITEMPROP_LOGO_RE = re.compile(r'itemprop="logo"[^>]*content="([^"]+)"')


async def resolve_youtube_avatar_url(
    http, channel_id: str | None, timeout: float = 15.0
) -> str | None:
    """Fetch a YouTube channel page and pull its avatar (``og:image``).

    Best-effort: any failure (no id, network, consent wall, no match) -> None.
    """
    if not channel_id:
        return None
    try:
        resp = await http.get(
            f"https://www.youtube.com/channel/{channel_id}",
            headers={"Accept-Language": "en", "Cookie": "CONSENT=YES+1"},
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception:
        return None
    if getattr(resp, "status_code", None) != 200:
        return None
    text = getattr(resp, "text", "") or ""
    m = _ITEMPROP_LOGO_RE.search(text) or _OG_IMAGE_RE.search(text)
    if not m:
        return None
    url = m.group(1).strip()
    return url if url.startswith(("http://", "https://")) else None


async def download_image(http, url: str, dest: Path, timeout: float = 30.0) -> bool:
    """Download an image to ``dest`` (best-effort; False on any failure)."""
    try:
        resp = await http.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200 or not resp.content:
            return False
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception:
        return False


async def refresh_plex(http, plex_url: str, token: str, timeout: float = 20.0) -> bool:
    """Trigger a Plex library refresh. Fails silently (returns False) on any error."""
    url = plex_url.rstrip("/") + "/library/sections/refresh"
    try:
        resp = await http.post(url, headers={"X-Plex-Token": token}, timeout=timeout)
        return resp.status_code in (200, 204)
    except Exception:
        return False
