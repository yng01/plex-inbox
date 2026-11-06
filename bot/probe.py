"""Probe a URL with ``yt-dlp -J`` and normalize the JSON into a :class:`UrlInfo`.

The parsing itself (:func:`parse_probe`, :func:`extract_audio_tracks`) is a pure
function of the JSON dict so it can be unit-tested with fixtures and no network.
The runner (:func:`run_probe`) is async and injects its subprocess spawner so tests
can feed a canned yt-dlp process.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, Sequence

DEFAULT_TIMEOUT = 60.0


class ProbeError(Exception):
    """A user-classifiable probe failure.

    ``code`` is one of: ``unsupported`` | ``private`` | ``region`` | ``unavailable``
    | ``timeout`` | ``generic``. Handlers map it to a short, user-safe i18n string.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class Spawner(Protocol):
    """Minimal async subprocess surface used by :func:`run_probe`."""

    async def run(self, cmd: Sequence[str]) -> "Proc": ...


class Proc(Protocol):
    returncode: int | None

    async def communicate(self) -> tuple[bytes, bytes]: ...
    def kill(self) -> None: ...


@dataclass(frozen=True)
class AudioTrack:
    language: str  # e.g. "en", "ar", "und"
    codec: str  # e.g. "opus", "aac"


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    url: str
    channel: str = ""
    duration: float | None = None


@dataclass
class UrlInfo:
    id: str
    title: str
    site: str
    url: str
    channel: str = "unknown"
    channel_id: str | None = None  # e.g. YouTube channel id (avatar fallback lookup)
    duration: float | None = None
    resolution: str | None = None
    language: str | None = (
        None  # video's own language, e.g. "ar" (for original-audio selection)
    )
    thumbnail: str | None = None
    description: str = ""
    year: int | None = None
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    audio_tracks: list[AudioTrack] = field(default_factory=list)
    is_playlist: bool = False
    entries: list[Entry] = field(default_factory=list)
    availability: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


class _Proc:
    """Asyncio subprocess wrapped to the :class:`Proc` protocol."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self._proc = proc

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return await self._proc.communicate()

    def kill(self) -> None:
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass


class RealProbeSpawner:
    """Default spawner: one ``yt-dlp`` subprocess per call."""

    async def run(self, cmd: Sequence[str]) -> _Proc:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return _Proc(proc)


def _clean(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def extract_audio_tracks(formats: Sequence[dict] | None) -> list[AudioTrack]:
    """Collect the distinct audio-only tracks from a yt-dlp format list.

    A track is any format that carries audio but no video (``vcodec`` == ``none``/empty).
    We de-duplicate on ``(language, codec)`` and preserve first-seen order. Missing
    language/codec become ``"und"`` so the track list is always total.
    """
    seen: set[tuple[str, str]] = set()
    out: list[AudioTrack] = []
    for f in formats or []:
        vcodec = str(f.get("vcodec") or "none").strip().lower()
        acodec = str(f.get("acodec") or "").strip().lower()
        # A track is an audio-only format that reports a codec.
        if vcodec not in ("none", "") or not acodec:
            continue
        lang = _clean(f.get("language")) or "und"
        key = (lang, acodec)
        if key in seen:
            continue
        seen.add(key)
        out.append(AudioTrack(language=lang, codec=acodec))
    return out


def _year_from(raw: dict) -> int | None:
    ry = raw.get("release_year")
    if isinstance(ry, (int, float)) and 1000 <= int(ry) <= 2999:
        return int(ry)
    for key in ("release_date", "upload_date"):
        v = str(raw.get(key) or "")
        if v[:4].isdigit() and 1000 <= int(v[:4]) <= 2999:
            return int(v[:4])
    return None


def _resolution_from(raw: dict) -> str | None:
    r = _clean(raw.get("resolution"))
    if r:
        return r
    best = (0, 0)
    for f in raw.get("formats") or []:
        w = f.get("width") or 0
        h = f.get("height") or 0
        if w and h and (w, h) > best:
            best = (w, h)
    if best != (0, 0):
        return f"{best[0]}x{best[1]}"
    return None


def _genres_from(raw: dict) -> list[str]:
    out: list[str] = []
    for g in raw.get("genres") or []:
        g = _clean(g)
        if g:
            out.append(g)
    if not out:
        g = _clean(raw.get("genre"))
        if g:
            out = [g]
    return out[:8]


def _tags_from(raw: dict) -> list[str]:
    out: list[str] = []
    for g in raw.get("tags") or []:
        g = _clean(g)
        if g:
            out.append(g)
    for c in raw.get("categories") or []:
        c = _clean(c)
        if c:
            out.append(c)
    return out[:12]


def parse_probe(raw: dict) -> UrlInfo:
    """Normalize a raw ``yt-dlp -J`` object into a :class:`UrlInfo`."""
    if not isinstance(raw, dict):
        raise ProbeError("generic", "probe JSON was not an object")

    entries_raw = raw.get("entries") or []
    is_playlist = isinstance(entries_raw, list) and len(entries_raw) > 0

    site = _clean(raw.get("extractor_key") or raw.get("extractor")) or "unknown"
    channel = (
        _clean(
            raw.get("channel")
            or raw.get("uploader")
            or raw.get("uploader_id")
            or raw.get("playlist_channel")
            or raw.get("playlist_uploader")
        )
        or "unknown"
    )

    title = (
        _clean(raw.get("title"))
        or _clean(raw.get("playlist_title"))
        or raw.get("id")
        or "untitled"
    )
    url = _clean(raw.get("webpage_url") or raw.get("original_url")) or ""
    vid = str(raw.get("id") or raw.get("playlist_id") or "")

    entries: list[Entry] = []
    if is_playlist:
        for e in entries_raw:
            if not isinstance(e, dict):
                continue
            e_title = _clean(e.get("title")) or e.get("id") or "untitled"
            e_url = _clean(e.get("webpage_url") or e.get("original_url")) or url
            e_channel = (
                _clean(e.get("channel") or e.get("uploader") or channel) or "unknown"
            )
            entries.append(
                Entry(
                    id=str(e.get("id") or e_url),
                    title=e_title,
                    url=e_url,
                    channel=e_channel,
                    duration=e.get("duration"),
                )
            )

    return UrlInfo(
        id=vid,
        title=title,
        site=site,
        url=url,
        channel=channel,
        channel_id=_clean(raw.get("channel_id") or raw.get("uploader_id")) or None,
        duration=raw.get("duration"),
        resolution=_resolution_from(raw),
        language=_clean(raw.get("language")) or None,
        thumbnail=_clean(raw.get("thumbnail")) or None,
        description=_clean(raw.get("description")),
        year=_year_from(raw),
        genres=_genres_from(raw),
        tags=_tags_from(raw),
        audio_tracks=extract_audio_tracks(raw.get("formats")),
        is_playlist=is_playlist,
        entries=entries,
        availability=_clean(raw.get("availability")) or None,
        raw=raw,
    )


# --- error classification ---------------------------------------------------

_PRIVATE_RE = re.compile(
    r"private|sign in|log ?in|login|authentication|age.?restrict", re.I
)
_REGION_RE = re.compile(
    r"region|geo.?restrict|not available in (your )?country|country", re.I
)
_UNAVAILABLE_RE = re.compile(r"unavailable|does not (exist|support)|no video|404", re.I)
_UNSUPPORTED_RE = re.compile(r"unsupported url|no extractor|unknown (url|type)", re.I)


def classify_probe_error(stderr: str, timed_out: bool) -> str:
    """Map yt-dlp stderr to a user-safe error code."""
    if timed_out:
        return "timeout"
    s = stderr or ""
    if _UNSUPPORTED_RE.search(s):
        return "unsupported"
    if _PRIVATE_RE.search(s):
        return "private"
    if _REGION_RE.search(s):
        return "region"
    if _UNAVAILABLE_RE.search(s):
        return "unavailable"
    return "generic"


async def run_probe(
    spawner: Spawner,
    url: str,
    ytdlp_bin: str = "yt-dlp",
    timeout: float = DEFAULT_TIMEOUT,
) -> UrlInfo:
    """Run ``yt-dlp -J <url>`` and parse the JSON into a :class:`UrlInfo`.

    Raises :class:`ProbeError` with a user-safe code on any failure.
    """
    cmd = [
        ytdlp_bin,
        "-J",
        "--no-warnings",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
        url,
    ]
    timed_out = False
    stdout = b""
    stderr = b""
    try:
        async with asyncio.timeout(timeout):
            proc = await spawner.run(cmd)
            try:
                stdout, stderr = await proc.communicate()
            except asyncio.CancelledError:
                proc.kill()
                raise
    except (TimeoutError, asyncio.TimeoutError):
        timed_out = True
        stderr = b"timed out"

    if timed_out:
        raise ProbeError("timeout", "timed out")

    text = stdout.decode("utf-8", "replace")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        msg = stderr.decode("utf-8", "replace")
        raise ProbeError(classify_probe_error(msg, False), msg or str(exc)) from None
    return parse_probe(raw)
