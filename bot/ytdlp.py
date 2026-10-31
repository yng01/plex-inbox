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
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from . import plex
from .config import Config
from .i18n import t
from .probe import UrlInfo
from .store import Archive

VIDEO_EXTS = {"mkv", "mp4", "webm", "mov", "m4v", "flv", "avi", "ogv", "ts"}

JOURNAL_PREFIX = ".inflight-"


# --- jobs ------------------------------------------------------------------


@dataclass
class Job:
    id: str
    url: str
    site: str
    channel: str
    title: str
    requestor: str
    base: str  # sanitized video stem (no extension)
    target_dir: Path
    audio_mode: str  # "all" | "best"
    state: str = "queued"  # queued | downloading | done | failed | cancelled
    pct: float = 0.0
    speed: str = ""
    eta: str = ""
    position: int = 0
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

    @property
    def dedupe_key(self) -> str:
        vid = self.info.id if self.info else ""
        return f"{self.site}:{vid}"


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


def format_args(video_format: str | None, audio_mode: str) -> list[str]:
    """Build the yt-dlp format-selection flags.

    * all  -> best video + *all* audio-only formats, merged (mkv carries them all)
    * best -> best video + single best audio (or the best combined format)
    * a user VIDEO_FORMAT override wins over both.
    """
    if video_format:
        return ["-f", video_format]
    if audio_mode == "all":
        return ["-f", "bv*+mergeall[vcodec=none]", "--audio-multistreams"]
    return ["-f", "bv*+ba/b"]


PROGRESS_RE = re.compile(r"PROG\s+(?P<pct>\S+)\s+(?P<speed>\S+)\s+(?P<eta>.+)$")


def parse_progress(line: str) -> tuple[float, str, str] | None:
    """Parse a ``download: PROG 45.0% 1.2MiB/s 00:10`` line; None otherwise."""
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    try:
        pct = float(m.group("pct").rstrip("%").strip())
    except ValueError:
        pct = 0.0
    return pct, m.group("speed").strip(), m.group("eta").strip()


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


def _find_video(d: Path, base: str) -> Path | None:
    if not d.exists():
        return None
    for p in d.iterdir():
        if p.stem == base and p.suffix.lower().lstrip(".") in VIDEO_EXTS:
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
                if base and p.stem == base and p.suffix.lower().lstrip(".") in VIDEO_EXTS:
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
    ):
        self.config = config
        self.archive = archive
        self.spawner = spawner or RealSpawner()
        self.ytdlp_bin = ytdlp_bin
        self.http = http
        self.notify_interval = max(0.0, notify_interval)
        self.data_dir = Path(config.data_dir)
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._workers: list[asyncio.Task] = []
        self._stopping = False
        self._notify_at: dict[str, float] = {}

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
    ) -> Job:
        site = plex.sanitize_filename(info.site or "unknown") or "unknown"
        channel = plex.sanitize_filename(info.channel or "unknown") or "unknown"
        base = plex.make_basename(info.title, info.year)
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
            info=info,
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
                await self._safe_notify(job, force=True)
            finally:
                self._queue.task_done()

    async def _safe_notify(self, job: Job, force: bool = False) -> None:
        try:
            await self._notify(job, force=force)
        except Exception:
            pass

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
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id, text=text
        )

    def _cmd(self, job: Job) -> list[str]:
        fmt = format_args(self.config.video_format, job.audio_mode)
        out = job.target_dir / (job.base + ".%(ext)s")
        return [
            self.ytdlp_bin,
            "--no-playlist",
            "--newline",
            "--no-colors",
            "--progress-delta",
            "1",
            "--progress-template",
            "download: PROG %(progress._percent_str)s %(speed_str)s %(eta_str)s",
            "--output",
            str(out),
            *fmt,
            job.url,
        ]

    async def _run(self, job: Job) -> None:
        job.state = "downloading"
        job.error = ""
        await self._notify(job, force=True)
        pre = _snapshot(job.target_dir)
        jpath = write_journal(job)
        proc = None
        try:
            proc = await self.spawner(self._cmd(job))
            job.proc = proc
            async for line in proc.lines():
                p = parse_progress(line)
                if p:
                    job.pct, job.speed, job.eta = p
                    await self._notify(job)
                elif job.state == "cancelled":
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break
            rc = await proc.wait()
            if job.state == "cancelled":
                self._cleanup(job, pre)  # cancel() already notified the user
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
            await self._safe_notify(job, force=True)
        finally:
            _remove(jpath)

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
            if name.startswith(job.base) and name[len(job.base):].startswith("."):
                _remove(p)

    async def _finalize(self, job: Job) -> None:
        info = job.info
        d = job.target_dir
        video = _find_video(d, job.base)
        if video is None:
            # fall back to the newest video file the run produced
            vids = [
                p
                for p in d.iterdir()
                if p.suffix.lower().lstrip(".") in VIDEO_EXTS
            ]
            video = max(vids, key=lambda p: p.stat().st_mtime) if vids else None
        if video is None:
            job.state = "failed"
            job.error = "download finished but no output file was found"
            await self._safe_notify(job, force=True)
            return
        size = video.stat().st_size

        tracks = list(info.audio_tracks) if info else []
        embedded = tracks if job.audio_mode == "all" else tracks[:1]
        job.audio_count = len(embedded)

        # thumbnail (stem must match the video file)
        if info and info.thumbnail and self.http is not None:
            await plex.download_image(
                self.http, info.thumbnail, d / f"{job.base}.jpg"
            )
        # channel poster (best-effort)
        poster_url = plex.pick_poster_url(info) if info else None
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
                    added_at=added_at,
                    final_path=video.as_posix(),
                    size_bytes=size,
                    duration=info.duration,
                    year=info.year,
                    audio_tracks=[
                        {"language": a.language, "codec": a.codec}
                        for a in embedded
                    ],
                    genres=list(info.genres),
                    tags=list(info.tags),
                ),
            )

        self.archive.add(
            f"{job.site}:{info.id if info else job.id}",
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
        await self._notify(job, force=True)
