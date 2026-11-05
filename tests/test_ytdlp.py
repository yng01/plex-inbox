"""State-machine tests for the download engine: format args, progress parsing,
queue -> downloading -> done/failed/cancelled, cancel + partial-file cleanup,
startup leftover cleanup, concurrency cap, and metadata finalization.

No network, no real yt-dlp: the spawner returns scripted FakeLineProcs and the
http client is a recording fake.
"""

import asyncio
import json

import pytest

from bot.config import load_config
from bot.probe import AudioTrack, UrlInfo
from bot.store import Archive
from telegram.error import BadRequest

from bot.ytdlp import (
    DownloadManager,
    cleanup_leftovers,
    format_args,
    human_size,
    parse_progress,
    write_journal,
)
from fakes import FakeHttp, FakeLineProc, FakeBot, RecordingSpawner


# --- fixtures ---------------------------------------------------------------


def make_config(tmp_path, **over):
    env = {
        "BOT_TOKEN": "123:abc",
        "ALLOWED_USER_IDS": "all",
        "DATA_DIR": str(tmp_path / "data"),
    }
    env.update(over)
    return load_config(env)


def make_info(**over):
    base = dict(
        id="abc123",
        title="Great Video",
        site="YouTube",
        url="https://youtube.com/watch?v=abc123",
        channel="Cool Channel",
        duration=372.5,
        resolution="1920x1080",
        thumbnail="https://i.example/thumb.jpg",
        description="A description " * 20,
        year=2024,
        genres=["Documentary"],
        tags=["nature"],
        audio_tracks=[
            AudioTrack(language="en", codec="opus"),
            AudioTrack(language="ar", codec="opus"),
        ],
        raw={
            "channel_favicon": "https://i.example/avatar.jpg",
        },
    )
    base.update(over)
    return UrlInfo(**base)


async def wait_state(mgr, job, states, timeout=5.0):
    """Poll until the job leaves/gains one of ``states`` (any state listed)."""
    loop_end = asyncio.get_event_loop().time() + timeout
    while job.state not in states:
        if asyncio.get_event_loop().time() > loop_end:
            raise TimeoutError(f"job stayed {job.state!r}, wanted {states}")
        await asyncio.sleep(0.01)


# --- pure helpers -----------------------------------------------------------


def test_format_args_all_audio():
    assert format_args(None, "all") == [
        "-f",
        "bv*+mergeall[vcodec=none]",
        "--audio-multistreams",
    ]


def test_format_args_best_only():
    assert format_args(None, "best") == ["-f", "bv*+ba/b"]


def test_format_args_orig_prefers_original_language():
    assert format_args(None, "orig", "ar") == [
        "-f",
        "bv*+ba[language=ar]/bv*+ba/b",
    ]


def test_format_args_orig_without_language_falls_back_to_best():
    assert format_args(None, "orig") == ["-f", "bv*+ba/b"]


def test_format_args_audio_only_prefers_original_language():
    assert format_args(None, "audio", "ar") == ["-f", "ba[language=ar]/ba/b"]


def test_format_args_audio_only_without_language():
    assert format_args(None, "audio") == ["-f", "ba/b"]


@pytest.mark.parametrize("mode", ["all", "orig", "best", "audio"])
def test_format_args_user_override_wins(mode):
    assert format_args("137+140", mode) == ["-f", "137+140"]


def test_parse_progress_line():
    assert parse_progress(
        "download: PROG|45.0%|1.2MiB/s|00:10|DL=1048576|TOT=4194304|EST=NA"
    ) == (45.0, "1.2MiB/s", "00:10", 1048576.0, 4194304.0)


def test_parse_progress_full():
    assert (
        parse_progress("download: PROG|100.0%|2.0MiB/s|00:00|DL=1|TOT=1|EST=NA")[0]
        == 100.0
    )


def test_parse_progress_na_becomes_empty_and_estimate_is_used():
    pct, speed, eta, dl, total = parse_progress(
        "download: PROG|5.0%|NA|NA|DL=10|TOT=NA|EST=200"
    )
    assert (pct, speed, eta) == (5.0, "", "")
    assert dl == 10.0
    assert total == 200.0  # TOT unknown -> EST is used


def test_parse_progress_ignores_plain_ytdlp_lines():
    assert (
        parse_progress("[download]  45.0% of 10.00MiB at 1.2MiB/s ETA 00:10") is None
    )
    assert parse_progress("[download] Destination: /data/x.mkv") is None
    assert parse_progress("") is None


def test_parse_progress_bad_percent_falls_back_to_zero():
    pct, speed, eta, dl, total = parse_progress(
        "download: PROG|oops|1.0MiB/s|00:05|DL=5|TOT=10|EST=NA"
    )
    assert (pct, speed, eta) == (0.0, "1.0MiB/s", "00:05")
    assert (dl, total) == (5.0, 10.0)


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0 B"), (512, "512 B"), (1536, "1.5 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_human_size(n, expected):
    assert human_size(n) == expected


# --- enqueue / command ------------------------------------------------------


def test_enqueue_builds_target_path_and_job_fields(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    info = make_info()
    job = mgr.enqueue(info, requestor="ali", audio_mode="all")

    assert job.state == "queued"
    assert job.position == 1
    assert job.base == "abc123"  # on-disk name is the item id, not the title
    assert job.target_dir == cfg.data_dir / "YouTube" / "Cool Channel"
    assert job.target_dir.is_dir()
    # dedupe keys are mode-scoped: a video download (any audio mode) claims
    # the :video slot, an audio-only one claims the :audio slot — the two must
    # never block each other
    assert job.dedupe_key == "YouTube:abc123:video"
    audio_job = mgr.enqueue(make_info(), requestor="ali", audio_mode="audio")
    assert audio_job.dedupe_key == "YouTube:abc123:audio"
    assert mgr.get(job.id) is job
    assert mgr.queue_length() == 2


def test_enqueue_sanitizes_site_channel_and_falls_back(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    info = make_info(
        site="YouTube",
        channel="Cool/Channel: What?",
        title='Who? "A: B"',
    )
    job = mgr.enqueue(info, requestor="user:123", audio_mode="best")
    assert job.target_dir == cfg.data_dir / "YouTube" / "Cool Channel What"
    assert job.base == "abc123"  # id-based, regardless of a messy title


def test_enqueue_empty_channel_and_title_use_unknown_untitled(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    info = make_info(site="", channel="", title="   ", id="", year=None)
    job = mgr.enqueue(info, requestor="ali", audio_mode="best")
    assert job.target_dir == cfg.data_dir / "unknown" / "unknown"
    assert job.base == "untitled"  # empty id -> guaranteed non-empty stem


def test_cmd_all_audio_mode_and_output(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    job = mgr.enqueue(make_info(), requestor="ali", audio_mode="all")
    cmd = mgr._cmd(job)

    assert cmd[0] == "yt-dlp"
    assert "--no-playlist" in cmd
    assert "--newline" in cmd
    assert "--progress-delta" in cmd and cmd[cmd.index("--progress-delta") + 1] == "1"
    assert "--progress-template" in cmd
    assert "PROG" in cmd[cmd.index("--progress-template") + 1]
    out = cmd[cmd.index("--output") + 1]
    assert out == str(job.target_dir / "abc123.%(ext)s")
    assert "-f" in cmd
    assert cmd[cmd.index("-f") + 1] == "bv*+mergeall[vcodec=none]"
    assert "--audio-multistreams" in cmd
    assert cmd[-1] == job.url


def test_cmd_best_mode_omits_multistreams(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    job = mgr.enqueue(make_info(), requestor="ali", audio_mode="best")
    cmd = mgr._cmd(job)
    assert cmd[cmd.index("-f") + 1] == "bv*+ba/b"
    assert "--audio-multistreams" not in cmd


def test_cmd_orig_mode_selects_original_language(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    job = mgr.enqueue(make_info(language="ar"), requestor="ali", audio_mode="orig")
    cmd = mgr._cmd(job)
    assert cmd[cmd.index("-f") + 1] == "bv*+ba[language=ar]/bv*+ba/b"
    assert "--audio-multistreams" not in cmd


def test_cmd_audio_mode_selects_audio_only(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    job = mgr.enqueue(make_info(language="ar"), requestor="ali", audio_mode="audio")
    cmd = mgr._cmd(job)
    assert cmd[cmd.index("-f") + 1] == "ba[language=ar]/ba/b"
    assert "--audio-multistreams" not in cmd


def test_cmd_video_format_override(tmp_path):
    cfg = make_config(tmp_path, VIDEO_FORMAT="137+140")
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    job = mgr.enqueue(make_info(), requestor="ali", audio_mode="all")
    cmd = mgr._cmd(job)
    assert cmd[cmd.index("-f") + 1] == "137+140"
    assert "--audio-multistreams" not in cmd


def test_write_journal_records_base_and_is_removed_by_cleanup(tmp_path):
    cfg = make_config(tmp_path)
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=RecordingSpawner())
    job = mgr.enqueue(make_info(), requestor="ali", audio_mode="all")
    j = write_journal(job)
    assert j.exists()
    meta = json.loads(j.read_text(encoding="utf-8"))
    assert meta["base"] == "abc123"
    assert meta["info_id"] == "abc123"
    assert j.name.startswith(".inflight-")


# --- full lifecycle: queued -> downloading -> done --------------------------


def _wire(job, bot):
    """Attach the handler-side notification context to a job."""
    job.bot = bot
    job.chat_id = 42
    job.msg_id = 7
    job.lang = "en"
    return job


async def test_success_path_writes_full_metadata(tmp_path):
    cfg = make_config(
        tmp_path, PLEX_URL="http://plex:32400", PLEX_TOKEN="tok"
    )
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    http = FakeHttp()
    mgr = DownloadManager(
        cfg, archive, spawner=spawner, http=http, notify_interval=0
    )

    base_dir = cfg.data_dir / "YouTube" / "Cool Channel"
    base_dir.mkdir(parents=True, exist_ok=True)
    video = base_dir / "abc123.mkv"

    def on_finish():
        video.write_bytes(b"x" * 4096)

    spawner.push(
        FakeLineProc(
            lines=[
                "[download] Destination: abc123.mkv",
                "download: PROG|25.0%|1.2MiB/s|00:10|DL=1048576|TOT=4194304|EST=NA",
                "download: PROG|100.0%|1.5MiB/s|00:00|DL=4194304|TOT=4194304|EST=NA",
            ],
            returncode=0,
            on_finish=on_finish,
        )
    )
    bot = FakeBot()
    job = _wire(mgr.enqueue(make_info(), requestor="ali", audio_mode="all"), bot)
    job.user_id = 555
    job.username = "ali"

    await mgr.start()
    try:
        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "done"
    assert job.pct == 100.0
    assert job.audio_count == 2  # en + ar embedded
    assert job.final_path == video.as_posix()
    assert job.size_bytes == 4096

    d = base_dir
    assert video.exists()
    assert (d / "abc123.jpg").exists()          # thumbnail, stem matches the id
    assert (d / "poster.jpg").exists()          # channel poster
    nfo = (d / "abc123.nfo").read_text(encoding="utf-8")
    assert "<title>Great Video</title>" in nfo
    assert "<year>2024</year>" in nfo
    assert "<genre>Documentary</genre>" in nfo
    assert "<tag>site:youtube</tag>" in nfo
    assert "<tag>channel:cool channel</tag>" in nfo
    assert "<tag>requested:ali</tag>" in nfo
    assert "<tag>audio:en</tag>" in nfo and "<tag>audio:ar</tag>" in nfo
    assert "<tag>year:2024</tag>" in nfo
    assert "<tag>duration:6m12s</tag>" in nfo

    sc = json.loads((d / "abc123.info.json").read_text(encoding="utf-8"))
    assert sc["requestor"] == "ali"
    assert sc["user_id"] == 555
    assert sc["username"] == "ali"
    assert sc["final_path"] == video.as_posix()
    assert sc["size_bytes"] == 4096
    assert [t["language"] for t in sc["audio_tracks"]] == ["en", "ar"]
    assert sc["genres"] == ["Documentary"]

    assert archive.contains("YouTube:abc123:video")
    assert len(http.posts) == 1
    assert http.posts[0]["url"].endswith("/library/sections/refresh")
    assert http.posts[0]["headers"]["X-Plex-Token"] == "tok"
    assert len(http.gets) == 2  # thumbnail + poster

    texts = [e[2] for e in bot.edits]
    assert any("Downloading Great Video" in x for x in texts)
    assert any("25%" in x and "1.2MiB/s" in x for x in texts)
    done_text = texts[-1]
    assert "✅ Done" in done_text
    assert "4.0 KB" in done_text
    assert "2 audio track(s)" in done_text


async def test_best_mode_embeds_single_track_and_omits_audio_tags(tmp_path):
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    http = FakeHttp()
    mgr = DownloadManager(cfg, archive, spawner=spawner, http=http)

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    spawner.push(
        FakeLineProc(
            lines=["download: PROG|100.0%|1.0MiB/s|00:00|DL=100|TOT=100|EST=NA"],
            on_finish=lambda: (d / "abc123.mp4").write_bytes(b"y" * 100),
        )
    )
    job = mgr.enqueue(
        make_info(audio_tracks=[AudioTrack(language="und", codec="aac")]),
        requestor="user:999",
        audio_mode="best",
    )
    await mgr.start()
    try:
        await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "done"
    assert job.audio_count == 1
    nfo = (d / "abc123.nfo").read_text(encoding="utf-8")
    assert "<tag>audio:" not in nfo  # lone default track -> no audio tags
    sc = json.loads((d / "abc123.info.json").read_text(encoding="utf-8"))
    assert len(sc["audio_tracks"]) == 1
    assert sc["requestor"] == "user:999"


async def test_audio_mode_finalizes_audio_only_file(tmp_path):
    """Audio-only mode downloads just the audio track (e.g. an .m4a); finalize
    must recognize it as the job's output (not require a video extension) and
    still write NFO + sidecar and record the dedupe entry."""
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    http = FakeHttp()
    mgr = DownloadManager(cfg, archive, spawner=spawner, http=http)

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    spawner.push(
        FakeLineProc(
            lines=["download: PROG|100.0%|1.0MiB/s|00:00|DL=100|TOT=100|EST=NA"],
            on_finish=lambda: (d / "abc123.m4a").write_bytes(b"a" * 100),
        )
    )
    job = mgr.enqueue(
        make_info(audio_tracks=[AudioTrack(language="ar", codec="aac")]),
        requestor="user:999",
        audio_mode="audio",
    )
    await mgr.start()
    try:
        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "done"
    assert job.audio_count == 1
    assert job.final_path.endswith("abc123.m4a")
    assert (d / "abc123.m4a").exists()
    assert (d / "abc123.nfo").exists()
    sc = json.loads((d / "abc123.info.json").read_text(encoding="utf-8"))
    assert sc["requestor"] == "user:999"
    assert sc["final_path"].endswith("abc123.m4a")
    # audio-only downloads claim the :audio slot, not the video's
    assert archive.contains("YouTube:abc123:audio")
    assert not archive.contains("YouTube:abc123:video")


async def test_poster_prefers_youtube_channel_avatar_over_thumbnail(tmp_path):
    """Probe without an avatar field -> fetch the channel page, use its avatar."""
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    html = (
        b"<html><head>"
        b'<meta property="og:image" content="https://yt3.example/av.jpg">'
        b"</head></html>"
    )
    http = FakeHttp(get_status=200, get_content=html)
    mgr = DownloadManager(cfg, archive, spawner=spawner, http=http)

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    spawner.push(
        FakeLineProc(
            lines=["download: PROG|100.0%|1.0MiB/s|00:00|DL=100|TOT=100|EST=NA"],
            on_finish=lambda: (d / "abc123.mp4").write_bytes(b"y" * 100),
        )
    )
    info = make_info(raw={}, channel_id="UC123", thumbnail=None)
    job = _wire(mgr.enqueue(info, requestor="ali", audio_mode="all"), FakeBot())

    await mgr.start()
    try:
        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "done"
    assert (d / "poster.jpg").exists()
    assert any(g["url"] == "https://www.youtube.com/channel/UC123" for g in http.gets)


async def test_failed_job_reports_reason_and_removes_partials(tmp_path):
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    mgr = DownloadManager(cfg, archive, spawner=spawner, http=FakeHttp())

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)

    def on_finish():
        # yt-dlp wrote a partial file before dying
        (d / "abc123.mkv").write_bytes(b"partial")
        (d / "abc123.jpg").write_bytes(b"thumb")

    spawner.push(
        FakeLineProc(
            lines=["download: PROG|12.0%|1.0MiB/s|00:40|DL=122880|TOT=1024000|EST=NA"],
            returncode=1,
            stderr="ERROR: Could not find a version that satisfies the requirement",
            on_finish=on_finish,
        )
    )
    bot = FakeBot()
    job = _wire(mgr.enqueue(make_info(), requestor="ali", audio_mode="all"), bot)

    await mgr.start()
    try:
        await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "failed"
    assert "satisfies the requirement" in job.error
    assert not (d / "abc123.mkv").exists()
    assert not (d / "abc123.jpg").exists()
    assert not list(d.glob(".inflight-*.json"))
    assert not archive.contains("YouTube:abc123:video")
    assert bot.edits[-1][2].startswith("⚠️ Failed:")


# --- status-message edits: no-op edits are skipped, rejections are fatal-proof
# ----------------------------------------------------------------------------


class StrictBot(FakeBot):
    """FakeBot that, like Telegram, rejects an edit whose text is unchanged."""

    def __init__(self):
        super().__init__()
        self.last: str | None = None

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        if text == self.last:
            raise BadRequest(
                "Message is not modified: specified new message content and "
                "reply markup are exactly the same as a current content and "
                "reply markup of the message"
            )
        self.last = text
        await super().edit_message_text(chat_id, message_id, text, **kw)


class AlwaysRejectBot(FakeBot):
    """FakeBot that rejects *every* edit (flaky Telegram / lost race)."""

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        raise BadRequest("Message is not modified")


async def test_repeated_identical_progress_is_not_re_edited(tmp_path):
    """Several progress lines rendering the same status text must produce a
    single edit — re-editing identical content is what made Telegram answer
    'Message is not modified'."""
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    mgr = DownloadManager(
        cfg, archive, spawner=spawner, http=FakeHttp(), notify_interval=0
    )

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    video = d / "abc123.mkv"

    def on_finish():
        video.write_bytes(b"x" * 4096)

    # 0% with no speed/ETA renders exactly the same text as the initial
    # forced "downloading" edit.
    spawner.push(
        FakeLineProc(
            lines=[
                "download: PROG|0.0%|NA|NA|DL=0|TOT=4096|EST=NA",
                "download: PROG|0.0%|NA|NA|DL=0|TOT=4096|EST=NA",
                "download: PROG|0.0%|NA|NA|DL=0|TOT=4096|EST=NA",
                "download: PROG|100.0%|1.5MiB/s|00:00|DL=4096|TOT=4096|EST=NA",
            ],
            returncode=0,
            on_finish=on_finish,
        )
    )
    bot = StrictBot()
    job = _wire(mgr.enqueue(make_info(), requestor="ali", audio_mode="all"), bot)

    await mgr.start()
    try:
        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "done"
    assert video.exists()
    texts = [e[2] for e in bot.edits]
    # one "downloading 0%" + one "100%" progress + one "done" — no no-op edits
    assert len(bot.edits) == 3
    assert "0%" in texts[0]
    assert "100%" in texts[1]
    assert texts[2].startswith("✅ Done")


async def test_telegram_edit_rejection_never_fails_the_job(tmp_path):
    """Regression: 'Message is not modified' used to bubble up and fail the
    whole download. A status-edit rejection must stay cosmetic."""
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    mgr = DownloadManager(
        cfg, archive, spawner=spawner, http=FakeHttp(), notify_interval=0
    )

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    video = d / "abc123.mkv"

    def on_finish():
        video.write_bytes(b"x" * 4096)

    spawner.push(
        FakeLineProc(
            lines=[
                "download: PROG|0.0%|NA|NA|DL=0|TOT=4096|EST=NA",
                "download: PROG|100.0%|1.5MiB/s|00:00|DL=4096|TOT=4096|EST=NA",
            ],
            returncode=0,
            on_finish=on_finish,
        )
    )
    job = _wire(
        mgr.enqueue(make_info(), requestor="ali", audio_mode="all"),
        AlwaysRejectBot(),
    )

    await mgr.start()
    try:
        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job.state == "done"
    assert job.error == ""
    assert video.exists()
    assert archive.contains("YouTube:abc123:video")


# --- cancel: kill process, remove partials, keep foreign files --------------


class HangingProc:
    """Yields a couple of lines, then blocks until ``kill()`` unblocks it."""

    def __init__(self, partial: list):
        self.partial = partial
        self.killed = False
        self._gate = asyncio.Event()
        self.returncode = 0

    async def lines(self):
        yield "[download] Starting download"
        yield "download: PROG|10.0%|1.0MiB/s|01:00|DL=102400|TOT=1024000|EST=NA"
        await self._gate.wait()
        yield "[download] still going..."  # non-progress line -> break the loop
        await asyncio.sleep(30)

    def kill(self):
        self.killed = True
        self._gate.set()
        for p in self.partial:
            p.write_bytes(b"partial")

    async def wait(self):
        return 0

    @property
    def stderr_tail(self):
        return ""


class SlowProc:
    """Drips progress lines for a short while, then exits cleanly."""

    def __init__(self, on_finish=None):
        self.killed = False
        self.returncode = 0
        self._on_finish = on_finish
        self._finished = False

    async def lines(self):
        yield "download: PROG|10.0%|1.0MiB/s|00:30|DL=102400|TOT=1024000|EST=NA"
        await asyncio.sleep(0.1)
        yield "download: PROG|50.0%|1.0MiB/s|00:10|DL=512000|TOT=1024000|EST=NA"
        await asyncio.sleep(0.1)

    async def wait(self):
        if not self._finished:
            self._finished = True
            if self._on_finish:
                self._on_finish()
        return 0

    def kill(self):
        self.killed = True

    @property
    def stderr_tail(self):
        return ""


async def test_cancel_kills_process_and_removes_partials_keeps_foreign(tmp_path):
    cfg = make_config(tmp_path)
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    mgr = DownloadManager(cfg, archive, spawner=spawner, http=FakeHttp())

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    foreign = d / "Other Video (2020).mkv"
    foreign.write_bytes(b"keep me")  # belongs to a previous, finished job

    proc = HangingProc(
        [
            d / "abc123.mkv",
            d / "abc123.jpg",
        ]
    )
    spawner.push(proc)
    bot = FakeBot()
    job = _wire(mgr.enqueue(make_info(), requestor="ali", audio_mode="all"), bot)

    await mgr.start()
    try:
        await wait_state(mgr, job, {"downloading"})
        assert mgr.active_jobs() == [job]
        assert await mgr.cancel(job.id) is True
        await wait_state(mgr, job, {"cancelled"})
    finally:
        await mgr.stop(grace=0.5)

    assert job.state == "cancelled"
    assert proc.killed
    assert foreign.exists()  # foreign file untouched
    assert not (d / "abc123.mkv").exists()
    assert not (d / "abc123.jpg").exists()
    assert not list(d.glob(".inflight-*.json"))
    assert bot.edits[-1][2].startswith("🚫 Cancelled")


async def test_cancel_of_finished_job_is_rejected(tmp_path):
    cfg = make_config(tmp_path)
    spawner = RecordingSpawner()
    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    spawner.push(
        FakeLineProc(on_finish=lambda: (d / "abc123.mkv").write_bytes(b"z"))
    )
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=spawner, http=FakeHttp())
    job = mgr.enqueue(make_info(), requestor="ali", audio_mode="all")
    await mgr.start()
    try:
        await mgr.wait_idle(timeout=5.0)
        assert job.state == "done"
        assert await mgr.cancel(job.id) is False
        assert await mgr.cancel("no-such-id") is False
    finally:
        await mgr.stop(grace=0.2)


async def test_worker_survives_a_spawner_exception(tmp_path):
    """A job whose spawner blows up must not kill the worker: the next job runs."""
    cfg = make_config(tmp_path)

    class BoomSpawner:
        def __init__(self):
            self.calls = 0

        async def __call__(self, cmd):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return FakeLineProc(on_finish=lambda: None)

    spawner = BoomSpawner()
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=spawner)
    job1 = mgr.enqueue(make_info(id="a1"), requestor="ali", audio_mode="best")
    job2 = mgr.enqueue(
        make_info(id="a2", title="Second Video"),
        requestor="ali",
        audio_mode="best",
    )
    bot = FakeBot()
    _wire(job1, bot)
    await mgr.start()
    try:
        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.2)

    assert job1.state == "failed"
    assert "boom" in job1.error
    assert spawner.calls == 2  # worker came back for job2
    assert job2.state in ("done", "failed")  # either way, it was actually run
    assert bot.edits[-1][2].startswith("⚠️ Failed: boom")


async def test_concurrency_cap_queues_excess(tmp_path):
    cfg = make_config(tmp_path, MAX_CONCURRENT="1")
    spawner = RecordingSpawner()
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=spawner)

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    spawner.push(
        SlowProc(
            on_finish=lambda: (d / "b1.mkv").write_bytes(b"1")
        )
    )
    spawner.push(
        FakeLineProc(on_finish=lambda: (d / "b2.mkv").write_bytes(b"2"))
    )
    job1 = mgr.enqueue(make_info(id="b1"), requestor="ali", audio_mode="all")
    job2 = mgr.enqueue(
        make_info(id="b2", title="Second", channel="Cool Channel"),
        requestor="ali",
        audio_mode="all",
    )

    await mgr.start()
    try:
        await wait_state(mgr, job1, {"downloading"})
        await asyncio.sleep(0.05)
        assert job2.state == "queued"  # waiting behind the single worker
        assert mgr.queue_length() == 1
        assert [j for j in mgr.active_jobs() if j.state == "downloading"] == [job1]

        assert await mgr.wait_idle(timeout=5.0)
    finally:
        await mgr.stop(grace=0.5)

    assert job1.state == "done"
    assert job2.state == "done"
    assert len(spawner.commands) == 2


async def test_stop_cancels_queued_and_cleans_downloading(tmp_path):
    cfg = make_config(tmp_path, MAX_CONCURRENT="1")
    spawner = RecordingSpawner()
    mgr = DownloadManager(cfg, Archive(cfg.data_dir), spawner=spawner)

    d = cfg.data_dir / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    hanging = HangingProc([d / "abc123.mkv"])
    spawner.push(hanging)
    job1 = mgr.enqueue(make_info(), requestor="ali", audio_mode="all")
    job2 = mgr.enqueue(
        make_info(id="c2", title="Second"), requestor="ali", audio_mode="all"
    )
    bot = FakeBot()
    _wire(job1, bot)
    _wire(job2, bot)

    await mgr.start()
    await wait_state(mgr, job1, {"downloading"})
    await asyncio.sleep(0.05)
    assert job2.state == "queued"

    await mgr.stop(grace=0.3)

    assert job1.state == "cancelled"
    assert job2.state == "cancelled"  # never started
    assert hanging.killed
    assert not (d / "abc123.mkv").exists()
    assert not list(d.glob(".inflight-*.json"))
    texts = [e[2] for e in bot.edits]
    assert any(x.startswith("🚫 Cancelled") for x in texts)
    assert mgr.active_jobs() == []


# --- startup cleanup of leftovers from an interrupted run -------------------


def test_cleanup_leftovers_removes_run_files_and_journal(tmp_path):
    d = tmp_path / "site" / "channel"
    d.mkdir(parents=True)
    mkv = d / "abc123.mkv"
    jpg = d / "abc123.jpg"
    nfo = d / "abc123.nfo"
    foreign = d / "Finished Video (2020).mkv"
    for p in (mkv, jpg, nfo, foreign):
        p.write_bytes(b"x")
    (d / ".inflight-abc.json").write_text(
        json.dumps({"base": "abc123"}), encoding="utf-8"
    )

    removed = cleanup_leftovers(tmp_path)

    assert removed == 4  # mkv + jpg + nfo + journal
    assert not mkv.exists() and not jpg.exists() and not nfo.exists()
    assert foreign.exists()  # finished file untouched
    assert not list(d.glob(".inflight-*.json"))


def test_cleanup_leftovers_bad_journal_only_drops_journal(tmp_path):
    d = tmp_path / "s" / "c"
    d.mkdir(parents=True)
    (d / "Some Video.mkv").write_bytes(b"x")
    (d / ".inflight-zzz.json").write_text("not json", encoding="utf-8")
    assert cleanup_leftovers(tmp_path) == 1
    assert (d / "Some Video.mkv").exists()
    assert not (d / ".inflight-zzz.json").exists()


def test_cleanup_leftovers_missing_dir_is_noop(tmp_path):
    assert cleanup_leftovers(tmp_path / "does-not-exist") == 0


def test_cleanup_leftovers_finds_nested_journals(tmp_path):
    d = tmp_path / "YouTube" / "Cool Channel"
    d.mkdir(parents=True)
    (d / "ep1.mkv").write_bytes(b"x")
    (d / "ep1.nfo").write_text("<video/>", encoding="utf-8")
    (d / ".inflight-1.json").write_text(
        json.dumps({"base": "ep1"}), encoding="utf-8"
    )
    assert cleanup_leftovers(tmp_path) == 3

