"""Async yt-dlp download manager: queue, workers, progress, cancel, metadata.

Each job is one video (a playlist "download all" is fanned out to one job per
entry by the handler layer). Downloads run as asyncio subprocesses. The manager
is fully injectable (spawner, http client, yt-dlp binary) so the whole lifecycle
can be exercised in tests with a fake process that emits canned progress lines
and a chosen exit code.

Lifecycle per job:
    queued -> downloading -> done | failed | cancelled

Partial-file safety: before a download starts we snapshot the target directory
and, on any failure/cancel, remove exactly the files that appeared during the
run. We also write a per-job ``.inflight-<jobid>.json`` journal in the target
directory; :func:`cleanup_leftovers` (run at startup) removes the files that a
previously interrupted run left behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from telegram.error import BadRequest

from . import plex
from .config import Config
from .i18n import t
from .probe import AudioTrack, UrlInfo
from .store import Archive, dedupe_key

VIDEO_EXTS = {"mkv", "mp4", "webm", "mov", "m4v", "flv", "avi", "ogv", "ts"}
#: extensions an audio-only download (``-f ba``) can land in
AUDIO_EXTS = {"m4a", "mp3", "opus", "ogg", "oga", "aac", "flac", "wma", "mka"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

JOURNAL_PREFIX = ".inflight-"

log = logging.getLogger("plexinbox")


# --- jobs ------------------------------------------------------------------


@dataclass
class Job:
    id: str
    url: str
    site: str
    channel: str
    title: str
    requestor: str
    base: str  # sanitized item id — the on-disk stem (no extension)
    target_dir: Path
    audio_mode: str  # "all" | "orig" | "best" | "audio" (audio-only, no video)
    quality: int | None = None  # height cap (e.g. 720); None = best available
    state: str = "queued"  # queued | downloading | done | failed | cancelled
    pct: float = 0.0
    speed: str = ""
    eta: str = ""
    position: int = 0
    user_id: int | None = None  # Telegram numeric id of the requester
    username: str | None = None  # Telegram username of the requester (if any)
    # Cumulative progress across *all* files of the job (video + N audio).
    # yt-dlp restarts its byte counter per file, so we fold per-file samples
    # into whole-job totals here; see :func:`update_progress`.
    _bytes_done: float = field(default=0.0, repr=False)
    _cur_bytes: float = field(default=0.0, repr=False)
    _cur_total: float | None = field(default=None, repr=False)
    _totals_known: float = field(default=0.0, repr=False)
    _shown_pct: float = field(default=0.0, repr=False)
    error: str = ""
    final_path: str = ""
    size_bytes: int = 0
    audio_count: int = 0
    info: UrlInfo | None = None
    proc: object | None = field(default=None, repr=False)
    # Set by the handler layer at enqueue time so callbacks can edit the message.
    bot: object | None = field(default=None, repr=False)
    chat_id: int | None = None
    msg_id: int | None = None
    lang: str = "en"
    # When set, this job archives an already-downloaded local file (a video the
    # user sent to the bot) instead of fetching a URL with yt-dlp. The worker
    # copies it into the target directory, then runs the same finalization.
    source_path: Path | None = field(default=None, repr=False)

    @property
    def dedupe_key(self) -> str:
        vid = self.info.id if self.info else ""
        return dedupe_key(self.site, vid, self.audio_mode == "audio")


# --- process interfaces ------------------------------------------------------


class FakeProc:
    """Test double: a pre-scripted process yielding lines then a return code.

    ``on_finish`` (optional sync callable) runs once on the first ``wait()``,
    letting tests simulate yt-dlp writing the output file before it exits.
    """

    def __init__(self, lines=None, returncode=0, stderr="", on_finish=None):
        self._lines = list(lines or [])
        self._rc = returncode
        self._stderr = stderr
        self._on_finish = on_finish
        self._finished = False
        self.returncode: int | None = None
        self.killed = False

    async def lines(self):
        for ln in self._lines:
            yield ln

    @property
    def stderr_tail(self) -> str:
        return self._stderr.strip()

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        if not self._finished:
            self._finished = True
            if self._on_finish:
                self._on_finish()
        self.returncode = self._rc
        return self._rc


class RealProc:
    """Wraps an asyncio subprocess: async stdout lines, stderr tail on wait."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self._proc = proc
        self.returncode: int | None = None
        self._stderr_buf = b""

    async def lines(self):
        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            yield raw.decode("utf-8", "replace").rstrip("\n")

    def kill(self) -> None:
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass

    async def wait(self) -> int:
        self.returncode = await self._proc.wait()
        if self._proc.stderr is not None:
            self._stderr_buf += await self._proc.stderr.read()
        return self.returncode

    @property
    def stderr_tail(self) -> str:
        return self._stderr_buf.decode("utf-8", "replace").strip()


class RealSpawner:
    """Spawn a real yt-dlp process; awaitable call returning a RealProc."""

    async def __call__(self, cmd: Sequence[str]) -> RealProc:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return RealProc(proc)


Spawner = Callable[[Sequence[str]], Awaitable[object]]


# --- pure helpers -----------------------------------------------------------


def _video_pref(quality: int | None) -> str:
    """The ``bv`` selection prefix for a height cap (``None`` = best available)."""
    if quality:
        return f"bv*[height<={quality}]"
    return "bv*"


def format_args(
    video_format: str | None,
    audio_mode: str,
    language: str | None = None,
    quality: int | None = None,
) -> list[str]:
    """Build the yt-dlp format-selection flags.

    * all   -> best video + *all* audio-only formats, merged (mkv carries them all)
    * orig  -> best video + the *original-language* audio track if the probe knows
      the video's language (``bv*+ba[language=xx]/bv*+ba/b``), else the single
      best audio. Every fallback keeps a video stream, so this mode never
      degrades to audio-only.
    * audio -> audio *only* (no video stream): the original-language track when
      the probe knows it (``ba[language=xx]/ba/b``), else the best audio track
      (the trailing ``b`` covers sites that ship no audio-only formats)
    * best  -> best video + single best audio (alias of ``orig`` without a language)
    * a user VIDEO_FORMAT override wins over all of the above.
    * ``quality`` (a height cap such as ``720``) constrains the *video* part to
      ``bv*[height<=720]`` (best video at or below that height). It has no effect
      in ``audio`` mode (there is no video stream to cap).

    Note: yt-dlp's format filters key on the field name ``language`` (``lang``
    is *not* a valid key and silently fails, dropping the selection to the
    audio-only fallback).
    """
    if video_format:
        return ["-f", video_format]
    v = _video_pref(quality)
    if audio_mode == "all":
        return ["-f", f"{v}+mergeall[vcodec=none]", "--audio-multistreams"]
    if audio_mode == "audio":
        # no video stream at all — original-language track when known, else any
        # audio-only track; only if a site has no audio-only formats at all does
        # the last ``b`` fallback hand back a combined file (better than failing)
        if language:
            return ["-f", f"ba[language={language}]/ba/b"]
        return ["-f", "ba/b"]
    if audio_mode == "orig" and language:
        return ["-f", f"{v}+ba[language={language}]/{v}+ba/b"]
    return ["-f", f"{v}+ba/b"]


# Progress line (one per --progress-delta tick), built from the template in
# :meth:`DownloadManager._cmd`. Tokens are pipe-separated so they never collide
# with free-text values; DL/TOT/EST are byte counters that make a *cumulative*,
# whole-job percent possible (yt-dlp otherwise restarts at 0% per file).
PROGRESS_MARK = "PROG|"

#: speed/ETA values yt-dlp emits when it does not know them yet
_NA_VALUES = {"", "NA", "None", "Unknown", "Unknown B/s", "-"}


def _clean_val(s: str) -> str:
    s = (s or "").strip()
    return "" if s in _NA_VALUES else s


def _to_number(s: str) -> float | None:
    s = (s or "").strip().rstrip("%")
    if s in _NA_VALUES:
        return None
    try:
        v = float(s)
        return v if v >= 0 else None
    except ValueError:
        return None


def _to_bytes(s: str) -> float | None:
    v = _to_number(s)
    return v if v and v > 0 else None


def parse_progress(
    line: str,
) -> tuple[float, str, str, float | None, float | None] | None:
    """Parse a progress line; ``None`` when the line is not one.

    Returns ``(pct, speed, eta, downloaded_bytes, total_bytes)``. The byte
    fields are ``None`` when yt-dlp did not report them (unknown size).
    """
    i = line.find(PROGRESS_MARK)
    if i < 0:
        return None
    toks = line[i + len(PROGRESS_MARK) :].split("|")
    if len(toks) < 6:
        return None
    pct = _to_number(toks[0])
    speed = _clean_val(toks[1])
    eta = _clean_val(toks[2])
    dl = tot = est = None
    for tok in toks[3:]:
        key, _, val = tok.partition("=")
        if key == "DL":
            dl = _to_bytes(val)
        elif key == "TOT":
            tot = _to_bytes(val)
        elif key == "EST":
            est = _to_bytes(val)
    if pct is None and dl is None:
        return None
    return pct or 0.0, speed, eta, dl, (tot if tot is not None else est)


def update_progress(
    job: Job, raw_pct: float, dl: float | None, total: float | None
) -> float:
    """Fold one per-file progress sample into the job's whole-job percent.

    A ``dl`` smaller than the previous sample means yt-dlp started the next
    file (its counter restarted); the finished file's bytes are banked into
    ``_bytes_done``. The result never moves backwards — revealing a new file's
    size must not drag a bar that was at 100% back down. When no size is ever
    known, the per-file ``raw_pct`` is used as a fallback.
    """
    if dl is not None:
        if job._cur_bytes > 0 and dl < job._cur_bytes:
            finished = job._cur_total if job._cur_total is not None else job._cur_bytes
            job._bytes_done += finished
            if job._cur_total is not None:
                job._totals_known += job._cur_total
            job._cur_bytes = 0.0
            job._cur_total = None
        job._cur_bytes = max(job._cur_bytes, dl)
    if total is not None:
        job._cur_total = max(job._cur_total or 0.0, total)
    total_all = job._totals_known + (job._cur_total or 0.0)
    if total_all > 0:
        pct = min(100.0, 100.0 * (job._bytes_done + job._cur_bytes) / total_all)
    else:
        pct = raw_pct
    if pct < job._shown_pct:
        pct = job._shown_pct
    job._shown_pct = pct
    return pct


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _remove(p: Path) -> bool:
    """Unlink a file if present; True when something was removed."""
    try:
        if p.is_file():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def _snapshot(d: Path) -> set[Path]:
    try:
        return {p for p in d.iterdir()}
    except OSError:
        return set()


def _find_media(d: Path, base: str) -> Path | None:
    """The final media file (video *or* audio-only) for this job's base name."""
    if not d.exists():
        return None
    for p in d.iterdir():
        if p.stem == base and p.suffix.lower().lstrip(".") in MEDIA_EXTS:
            return p
    return None


def write_journal(job: Job) -> Path:
    p = job.target_dir / f"{JOURNAL_PREFIX}{job.id}.json"
    payload = {
        "base": job.base,
        "info_id": job.info.id if job.info else "",
        "url": job.url,
        "started_at": _now_iso(),
    }
    try:
        p.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return p


def cleanup_leftovers(data_dir: str | Path) -> int:
    """Remove partial files left by a previously interrupted run.

    Walks every ``.inflight-*.json`` journal and removes the video, NFO and
    thumbnail for the recorded base name (plus the journal itself). Returns the
    number of files removed.
    """
    root = Path(data_dir)
    if not root.exists():
        return 0
    removed = 0
    for journal in root.glob(f"**/{JOURNAL_PREFIX}*.json"):
        try:
            meta = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        base = str(meta.get("base") or "")
        d = journal.parent
        if d.exists():
            for p in list(d.iterdir()):
                hit = False
                if (
                    base
                    and p.stem == base
                    and p.suffix.lower().lstrip(".") in MEDIA_EXTS
                ):
                    hit = True
                if base and p.name in (f"{base}.nfo", f"{base}.jpg"):
                    hit = True
                if hit:
                    _remove(p)
                    removed += 1
        if _remove(journal):
            removed += 1
    return removed


def human_size(n: float | int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


async def real_ffprobe_audio(
    ffprobe_bin: str, path: Path, timeout: float = 20.0
) -> int:
    """Count the audio streams in a local file via ``ffprobe``.

    Best-effort: any failure (missing binary, unreadable file, timeout) yields ``0``
    so a metadata probe can never fail an otherwise successful archive.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except Exception:
        return 0
    if proc.returncode != 0:
        return 0
    return sum(
        1 for line in out.decode("utf-8", "replace").splitlines() if line.strip()
    )


# --- manager ---------------------------------------------------------------


class DownloadManager:
    """Queue + N worker tasks driving one yt-dlp process per job.

    The handler layer stores (``bot``, ``chat_id``, ``msg_id``, ``lang``) on the
    job and :meth:`enqueue` returns the job; the manager then edits that single
    status message through the whole lifecycle (queued -> downloading ->
    done | failed | cancelled) with throttled progress updates.
    """

    def __init__(
        self,
        config: Config,
        archive: Archive,
        spawner: Spawner | None = None,
        ytdlp_bin: str = "yt-dlp",
        http=None,
        notify_interval: float = 2.0,
        ffprobe_bin: str = "ffprobe",
        ffprobe_audio: Callable[[Path], Awaitable[int]] | None = None,
    ):
        self.config = config
        self.archive = archive
        self.spawner = spawner or RealSpawner()
        self.ytdlp_bin = ytdlp_bin
        self.http = http
        self.notify_interval = max(0.0, notify_interval)
        self.ffprobe_bin = ffprobe_bin
        # Injectable audio-stream probe (``path -> int``). ``None`` -> real ffprobe.
        self.ffprobe_audio = ffprobe_audio
        self.data_dir = Path(config.data_dir)
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._workers: list[asyncio.Task] = []
        self._stopping = False
        self._notify_at: dict[str, float] = {}
        # Last text displayed per job: re-sending an identical message makes
        # Telegram reject the edit with "Message is not modified".
        self._last_text: dict[str, str] = {}

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        for i in range(max(1, self.config.max_concurrent)):
            self._workers.append(
                asyncio.create_task(self._worker(), name=f"ytdlp-worker-{i}")
            )

    async def stop(self, grace: float = 30.0) -> None:
        """Stop accepting work; give in-flight jobs ``grace`` seconds, then kill.

        Cancelled/interrupted jobs clean up their partial files; anything that
        slips through is still journaled and removed by :func:`cleanup_leftovers`
        on the next startup.
        """
        self._stopping = True
        while not self._queue.empty():
            job = self._queue.get_nowait()
            self._queue.task_done()
            if job.state == "queued":
                job.state = "cancelled"
                await self._safe_notify(job, force=True)
        if self._workers:
            done, pending = await asyncio.wait(list(self._workers), timeout=grace)
            for t in pending:
                t.cancel()
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def wait_idle(self, timeout: float | None = None) -> bool:
        async def _idle() -> None:
            busy = {"queued", "downloading"}
            while any(j.state in busy for j in self._jobs.values()):
                await asyncio.sleep(0.05)

        try:
            if timeout is None:
                await _idle()
            else:
                await asyncio.wait_for(_idle(), timeout)
            return True
        except (asyncio.TimeoutError, TimeoutError):
            return False

    # -- public API ----------------------------------------------------------

    def enqueue(
        self,
        info: UrlInfo,
        *,
        requestor: str,
        audio_mode: str,
        url: str | None = None,
        source_path: Path | str | None = None,
        quality: int | None = None,
    ) -> Job:
        site = plex.sanitize_filename(info.site or "unknown") or "unknown"
        channel = plex.sanitize_filename(info.channel or "unknown") or "unknown"
        # on-disk name is the item's own id (YouTube video id, Telegram
        # file_unique_id, …); the title stays in the NFO/sidecar for Plex
        base = plex.sanitize_filename(info.id or "")
        target_dir = self.data_dir / site / channel
        target_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=uuid.uuid4().hex[:12],
            url=url or info.url,
            site=info.site or "unknown",
            channel=info.channel or "unknown",
            title=info.title,
            requestor=requestor,
            base=base,
            target_dir=target_dir,
            audio_mode=audio_mode,
            quality=quality,
            info=info,
            source_path=Path(source_path) if source_path else None,
        )
        job.position = self._queue.qsize() + 1
        self._jobs[job.id] = job
        self._queue.put_nowait(job)
        return job

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state not in ("queued", "downloading"):
            return False
        job.state = "cancelled"
        if job.proc is not None:
            try:
                job.proc.kill()
            except Exception:
                pass
        await self._safe_notify(job, force=True)
        return True

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active_jobs(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.state in ("queued", "downloading")]

    def queue_length(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state == "queued")

    # -- internals -----------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job.state != "cancelled":
                    await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a bad job must never kill the worker
                if job.state not in ("done", "failed", "cancelled"):
                    job.state = "failed"
                job.error = job.error or str(exc)[:300]
                log.error("worker: job %s raised: %s", job.id, exc, exc_info=exc)
                await self._safe_notify(job, force=True)
            finally:
                self._queue.task_done()
                self._last_text.pop(job.id, None)

    async def _safe_notify(self, job: Job, force: bool = False) -> None:
        try:
            await self._notify(job, force=force)
        except Exception as exc:
            log.warning(
                "notify failed for job %s (state=%s): %s", job.id, job.state, exc
            )

    async def _notify(self, job: Job, force: bool = False) -> None:
        """Edit the job's single status message (throttled for progress)."""
        now = time.monotonic()
        if not force and now - self._notify_at.get(job.id, 0.0) < self.notify_interval:
            return
        self._notify_at[job.id] = now
        bot, chat_id, msg_id = job.bot, job.chat_id, job.msg_id
        if bot is None or chat_id is None or msg_id is None:
            return
        lang = job.lang
        if job.state == "downloading":
            head = t(lang, "downloading", title=job.title[:60])
            sub = t(
                lang,
                "progress",
                pct=int(job.pct),
                speed=job.speed or "-",
                eta=job.eta or "-",
            )
            text = f"{head}\n{sub}"
        elif job.state == "queued":
            text = t(lang, "queued", pos=job.position)
        elif job.state == "done":
            text = t(
                lang,
                "done",
                path=job.final_path,
                size=human_size(job.size_bytes),
                tracks=job.audio_count,
            )
        elif job.state == "failed":
            text = t(lang, "failed", reason=job.error or "unknown")
        elif job.state == "cancelled":
            text = t(lang, "cancelled")
        else:
            return
        if self._last_text.get(job.id) == text:
            return  # user already sees exactly this — skip the no-op edit
        self._last_text[job.id] = text
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text)
        except BadRequest:
            # "Message is not modified" (or a lost edit race): cosmetic — a
            # status edit must never be able to fail the download itself.
            log.debug("status edit skipped for job %s: message unchanged", job.id)

    def _cmd(self, job: Job) -> list[str]:
        lang = job.info.language if job.info is not None else None
        fmt = format_args(self.config.video_format, job.audio_mode, lang, job.quality)
        out = job.target_dir / (job.base + ".%(ext)s")
        return [
            self.ytdlp_bin,
            "--no-playlist",
            "--newline",
            "--no-colors",
            "--progress-delta",
            "1",
            "--progress-template",
            "download: PROG|%(progress._percent_str)s|%(progress._speed_str)s|"
            "%(progress._eta_str)s|DL=%(progress.downloaded_bytes)s|"
            "TOT=%(progress.total_bytes)s|EST=%(progress.total_bytes_estimate)s",
            "--output",
            str(out),
            *fmt,
            job.url,
        ]

    async def _run(self, job: Job) -> None:
        job.state = "downloading"
        job.error = ""
        await self._safe_notify(job, force=True)
        pre = _snapshot(job.target_dir)
        jpath = write_journal(job)
        log.info(
            "job %s started: %r (audio_mode=%s) -> %s",
            job.id,
            job.title[:80],
            job.audio_mode,
            job.url,
        )
        proc = None
        try:
            if job.source_path is not None:
                # A video the user sent to the bot: it is already on disk, so we
                # copy it into the library instead of fetching a URL.
                await self._run_local(job, pre)
                return
            proc = await self.spawner(self._cmd(job))
            job.proc = proc
            async for line in proc.lines():
                p = parse_progress(line)
                if p:
                    raw_pct, speed, eta, dl, total = p
                    job.pct = update_progress(job, raw_pct, dl, total)
                    job.speed, job.eta = speed, eta
                    await self._safe_notify(job)
                elif job.state == "cancelled":
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break
            rc = await proc.wait()
            if job.state == "cancelled":
                self._cleanup(job, pre)  # cancel() already notified the user
                log.info("job %s cancelled", job.id)
                return
            if rc != 0:
                job.state = "failed"
                tail = ""
                try:
                    tail = (proc.stderr_tail or "").strip()
                except Exception:
                    pass
                job.error = (tail or f"exit code {rc}")[:300]
                self._cleanup(job, pre)
                log.warning("job %s failed (rc=%s): %s", job.id, rc, job.error)
                await self._safe_notify(job, force=True)
                return
            await self._finalize(job)
        except asyncio.CancelledError:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            if job.state != "cancelled":
                job.state = "cancelled"
                # Worker task was cancelled (shutdown): cancel() hasn't spoken.
                await self._safe_notify(job, force=True)
            self._cleanup(job, pre)
            raise
        except Exception as exc:
            if job.state not in ("done", "failed", "cancelled"):
                job.state = "failed"
            job.error = str(exc)[:300]
            self._cleanup(job, pre)
            log.warning("job %s crashed: %s", job.id, exc, exc_info=exc)
            await self._safe_notify(job, force=True)
        finally:
            _remove(jpath)

    async def _audio_streams(self, path: Path) -> int:
        """Best-effort audio-stream count for a local file (0 on any failure)."""
        try:
            if self.ffprobe_audio is not None:
                return max(0, int(await self.ffprobe_audio(path)))
            return await real_ffprobe_audio(self.ffprobe_bin, path)
        except Exception:
            return 0

    async def _run_local(self, job: Job, pre: set[Path]) -> None:
        """Archive an already-downloaded local file (a video sent to the bot).

        Copies the file into the job's target directory under the final stem, then
        runs the same finalization as a URL download (sidecar, NFO, dedupe and
        notification are identical; the thumbnail/poster are skipped because a
        local upload has no remote artwork). The source temp file is removed once
        it has been safely copied, or on failure.

        Because a local upload has no probe metadata, the audio-track count is
        discovered from the copied file itself (best-effort) so the sidecar and the
        completion message report an honest number instead of a blank ``0``.
        """
        src = Path(job.source_path)
        if not src.is_file():
            raise FileNotFoundError(f"local source missing: {src}")
        ext = src.suffix.lower().lstrip(".")
        if ext not in VIDEO_EXTS:
            ext = "mp4"
        dest = job.target_dir / f"{job.base}.{ext}"
        shutil.copyfile(src, dest)
        # best-effort: fill in the (otherwise empty) track list from the real file
        if job.info is not None and not job.info.audio_tracks:
            n = await self._audio_streams(dest)
            if n:
                job.info.audio_tracks = [
                    AudioTrack(language="und", codec="und") for _ in range(n)
                ]
        job.pct = 100.0
        await self._safe_notify(job, force=True)
        try:
            await self._finalize(job)
        finally:
            _remove(src)
            # the source lived in a per-upload temp dir; drop it once empty
            parent = src.parent
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    def _cleanup(self, job: Job, pre: set[Path]) -> None:
        """Remove the files this run added, scoped to this job's base name.

        A concurrent job in the same channel folder is never touched because only
        files that start with ``<base>.`` (plus journals) are considered.
        """
        d = job.target_dir
        if not d.exists():
            return
        for p in _snapshot(d):
            if p in pre:
                continue
            name = p.name
            if name.startswith(JOURNAL_PREFIX):
                _remove(p)
                continue
            if name.startswith(job.base) and name[len(job.base) :].startswith("."):
                _remove(p)

    async def _finalize(self, job: Job) -> None:
        info = job.info
        d = job.target_dir
        video = _find_media(d, job.base)
        if video is None:
            # fall back to the newest media file the run produced
            vids = [
                p for p in d.iterdir() if p.suffix.lower().lstrip(".") in MEDIA_EXTS
            ]
            video = max(vids, key=lambda p: p.stat().st_mtime) if vids else None
        if video is None:
            job.state = "failed"
            job.error = "download finished but no output file was found"
            log.warning("job %s: no output file found in %s", job.id, d)
            await self._safe_notify(job, force=True)
            return
        size = video.stat().st_size

        tracks = list(info.audio_tracks) if info else []
        if job.audio_mode == "all":
            embedded = tracks
        else:
            # single track: the original-language one when the probe knows it
            lang = info.language if info is not None else None
            embedded = [a for a in tracks if a.language == lang] or tracks[:1]
        job.audio_count = len(embedded)

        # thumbnail (stem must match the video file)
        if info and info.thumbnail and self.http is not None:
            await plex.download_image(self.http, info.thumbnail, d / f"{job.base}.jpg")
        # channel poster (best-effort), best source first:
        #   1. avatar field from the probe (channel_favicon & co.)
        #   2. YouTube channel page (og:image) when we have a channel id
        #   3. the video thumbnail (keeps poster.jpg always populated)
        poster_url = plex.pick_avatar_url(info) if info else None
        if not poster_url and info is not None and self.http is not None:
            raw = info.raw or {}
            cid = info.channel_id or raw.get("channel_id") or raw.get("uploader_id")
            if cid and re.search(r"youtube\.", info.url or "", re.I):
                poster_url = await plex.resolve_youtube_avatar_url(self.http, cid)
        if not poster_url and info is not None:
            poster_url = plex.pick_poster_url(info)
        if poster_url and self.http is not None:
            await plex.download_image(self.http, poster_url, d / "poster.jpg")

        # NFO + sidecar
        added_at = _now_iso()
        rel = ""
        try:
            rel = video.relative_to(self.data_dir).as_posix()
        except ValueError:
            rel = video.as_posix()
        if self.config.write_nfo and info:
            tags = plex.build_tags(
                site=job.site,
                channel=job.channel,
                requestor=job.requestor,
                audio_langs=[a.language for a in embedded],
                year=info.year,
                duration=info.duration,
                genres=info.genres,
                keywords=info.tags,
            )
            plex.write_nfo(
                d / f"{job.base}.nfo",
                title=info.title,
                summary=info.description,
                year=info.year,
                genre=info.genres[0] if info.genres else None,
                tags=tags,
            )
        if info:
            plex.write_sidecar(
                d / f"{info.id}.info.json",
                plex.Sidecar(
                    id=info.id,
                    site=job.site,
                    channel=job.channel,
                    title=info.title,
                    url=job.url,
                    requestor=job.requestor,
                    user_id=job.user_id,
                    username=job.username,
                    added_at=added_at,
                    final_path=video.as_posix(),
                    size_bytes=size,
                    duration=info.duration,
                    year=info.year,
                    audio_tracks=[
                        {"language": a.language, "codec": a.codec} for a in embedded
                    ],
                    genres=list(info.genres),
                    tags=list(info.tags),
                ),
            )

        self.archive.add(
            dedupe_key(
                job.site, info.id if info else job.id, job.audio_mode == "audio"
            ),
            job.requestor,
            rel,
        )
        if self.config.plex_enabled and self.http is not None:
            await plex.refresh_plex(
                self.http, self.config.plex_url, self.config.plex_token
            )

        job.final_path = video.as_posix()
        job.size_bytes = size
        job.state = "done"
        log.info(
            "job %s done: %s (%s, %d audio track(s))",
            job.id,
            video.name,
            human_size(size),
            job.audio_count,
        )
        await self._safe_notify(job, force=True)
