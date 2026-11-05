"""Video files sent to the bot: the ``on_video`` handler + local-file archive.

Exercises the full path with fakes: the handler pulls the media with a real
``telegram.File`` whose ``file_path`` is a local file (the Bot-API local-mode
seam, so ``download_to_drive(custom_path=...)`` is a plain copy — no network),
then routes it through the normal archive flow (queue, single status message,
dedupe ledger, sidecar, NFO). The spawner must *never* be invoked for a local
job.
"""

import asyncio
import json
import tempfile
from datetime import datetime, timezone

from telegram import File as TelegramFile
from telegram.error import BadRequest

from bot import handlers
from bot.config import load_config
from bot.i18n import open_store, t
from bot.probe import UrlInfo
from bot.store import Archive, Library, Stats
from bot.updater import Updater
from bot.ytdlp import DownloadManager, human_size, real_ffprobe_audio

from fakes import (
    FakeBot,
    FakeContext,
    FakeHttp,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    FakeVideo,
    FakeVideoNote,
    RecordingSpawner,
)


# --- shared env ---------------------------------------------------------------


def make_env(tmp_path):
    """Config + services + context, wired exactly like the handler layer uses it."""
    cfg = load_config(
        {
            "BOT_TOKEN": "123:abc",
            "ALLOWED_USER_IDS": "all",
            "DATA_DIR": str(tmp_path / "data"),
        }
    )
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner()
    http = FakeHttp()

    async def fake_probe(path):
        return 2  # the fake "video" has two audio streams

    manager = DownloadManager(cfg, archive, spawner=spawner, http=http, notify_interval=0.01)
    manager.ffprobe_audio = fake_probe
    svc = handlers.Services(
        config=cfg,
        lang_store=open_store(str(tmp_path / "langs.json"), "en"),
        probe_spawner=object(),
        archive=archive,
        library=Library(cfg.data_dir),
        stats=Stats(cfg.data_dir),
        manager=manager,
        updater=Updater(cfg, state_path=str(tmp_path / "upd.json")),
        http=http,
    )
    ctx = FakeContext(bot=FakeBot())
    ctx.bot_data["svc"] = svc
    return cfg, svc, spawner, ctx


def _user() -> FakeUser:
    return FakeUser(id=111, username="ali")


def _update(msg: FakeMessage) -> FakeUpdate:
    return FakeUpdate(user=_user(), message=msg)


def _wire(job):
    """Attach the notification context a handler would have set."""
    job.bot = FakeBot()
    job.chat_id = 111
    job.msg_id = 5
    job.lang = "en"
    return job


async def _run_to_completion(svc):
    await svc.manager.start()
    try:
        await svc.manager.wait_idle(timeout=5.0)
    finally:
        await svc.manager.stop(grace=0.2)


def _capture_tmp_dirs(tmp_path, monkeypatch):
    """Make ``tempfile.mkdtemp`` create dirs under ``tmp_path`` (assertable)."""
    base = tmp_path / "ups"
    base.mkdir(exist_ok=True)
    counter = {"n": 0}

    def mkdtemp(prefix="", **kw):
        counter["n"] += 1
        p = base / f"{prefix}{counter['n']}"
        p.mkdir()
        return str(p)

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    return base


# --- tests --------------------------------------------------------------------


async def test_on_video_archives_file_with_metadata(tmp_path, monkeypatch):
    base = _capture_tmp_dirs(tmp_path, monkeypatch)
    cfg, svc, spawner, ctx = make_env(tmp_path)

    # the "Telegram-side" file (as if stored by a local Bot API server)
    src = tmp_path / "sent.mkv"
    payload = b"fake-video-bytes" * 100
    src.write_bytes(payload)
    tg_file = TelegramFile(
        file_id="AgADfake", file_unique_id="uniq-777", file_path=str(src)
    )
    video = FakeVideo(
        tg_file,
        file_name="My Clip.mkv",
        duration=42.0,
        width=640,
        height=360,
        file_size=len(payload),
        file_unique_id="uniq-777",
    )
    msg = FakeMessage(id=10, video=video)

    await handlers.on_video(_update(msg), ctx)
    await _run_to_completion(svc)

    # one job, done, with requester identity and an honest track count
    jobs = list(svc.manager._jobs.values())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.state == "done"
    assert job.error == ""
    assert job.user_id == 111
    assert job.username == "ali"
    assert job.audio_count == 2
    # local file was copied in, not downloaded with yt-dlp
    assert spawner.commands == []

    # the file landed in <DATA_DIR>/Upload/<requestor>/ named after the file id
    year = datetime.now(timezone.utc).year
    d = cfg.data_dir / "Upload" / "ali"
    final = d / "uniq-777.mkv"
    assert final.exists()
    assert final.read_bytes() == payload
    assert job.final_path == final.as_posix()
    assert job.size_bytes == len(payload)

    # the temp copy was consumed and its dir dropped (the original stays)
    assert src.exists()
    assert job.source_path is not None
    assert not job.source_path.exists()
    assert not list(base.iterdir()), "per-upload temp dirs must be removed"

    # sidecar: dedupe id + requester + honest audio tracks
    sc = json.loads((d / "uniq-777.info.json").read_text(encoding="utf-8"))
    assert sc["id"] == "uniq-777"
    assert sc["site"] == "Upload"
    assert sc["channel"] == "ali"
    assert sc["requestor"] == "ali"
    assert sc["user_id"] == 111
    assert sc["username"] == "ali"
    assert sc["size_bytes"] == len(payload)
    assert len(sc["audio_tracks"]) == 2
    assert sc["duration"] == 42.0

    # NFO + dedupe ledger
    nfo = (d / "uniq-777.nfo").read_text(encoding="utf-8")
    assert "<title>My Clip</title>" in nfo
    assert f"<year>{year}</year>" in nfo
    assert "<tag>site:upload</tag>" in nfo
    ledger = (cfg.data_dir / "archive.txt").read_text(encoding="utf-8")
    assert "Upload:uniq-777:video" in ledger

    # the user saw one message: "receiving…" then "queued" (single status msg)
    status = msg.last_reply
    assert t("en", "video_receiving") in msg.replies
    assert t("en", "queued", pos=1) in status.edits
    # …and the final "done" edit went to the same message id
    done_texts = [e[2] for e in ctx.bot.edits]
    expected = t(
        "en",
        "done",
        path=final.as_posix(),
        size=human_size(len(payload)),
        tracks=2,
    )
    assert expected in done_texts
    assert all(e[1] == status.message_id for e in ctx.bot.edits)


async def test_on_video_same_file_twice_is_noop(tmp_path, monkeypatch):
    base = _capture_tmp_dirs(tmp_path, monkeypatch)
    cfg, svc, spawner, ctx = make_env(tmp_path)

    def send(name: str, file_unique_id: str) -> FakeMessage:
        src = tmp_path / name
        src.write_bytes(b"whatever-bytes")
        tg_file = TelegramFile(
            file_id=f"AgAD{file_unique_id}",
            file_unique_id=file_unique_id,
            file_path=str(src),
        )
        return FakeMessage(
            id=20,
            video=FakeVideo(tg_file, file_name="My Clip.mkv", file_unique_id=file_unique_id),
        )

    # first send: archived (the file is streamed in once)
    msg1 = send("first.mkv", "uniq-777")
    await handlers.on_video(_update(msg1), ctx)
    await _run_to_completion(svc)
    assert svc.archive.contains("Upload:uniq-777:video")
    assert msg1.video.get_file_calls == 1  # first send does the transfer

    # second send of the same file: deduped *before* any download, so the whole
    # transfer is skipped and the user gets an instant "already in library" reply
    # (no "receiving…" status, no temp dir, no second job)
    msg2 = send("second.mkv", "uniq-777")
    await handlers.on_video(_update(msg2), ctx)
    assert len(svc.manager._jobs) == 1
    assert msg2.video.get_file_calls == 0, "duplicate must not re-download"
    assert t("en", "already_in_library") in msg2.replies
    assert t("en", "video_receiving") not in msg2.replies, "no 'receiving' for a duplicate"
    assert not list(base.iterdir()), "no temp dir for the skipped send"
    assert spawner.commands == []


async def test_on_video_download_failure_reports_error(tmp_path, monkeypatch):
    _capture_tmp_dirs(tmp_path, monkeypatch)
    cfg, svc, spawner, ctx = make_env(tmp_path)

    class BrokenVideo:
        file_name = None

        async def get_file(self, **kw):
            raise RuntimeError("bot api exploded")

    msg = FakeMessage(id=30, video=BrokenVideo())
    await handlers.on_video(_update(msg), ctx)

    status = msg.last_reply
    assert t("en", "video_error") in status.edits
    assert svc.manager.queue_length() == 0
    assert not svc.manager._jobs


async def test_too_big_file_gets_specific_message(tmp_path, monkeypatch):
    """'File is too big' (standard Bot API's 20 MB cap) → a specific, kind message,
    not the generic error, and no job is queued."""
    _capture_tmp_dirs(tmp_path, monkeypatch)
    cfg, svc, spawner, ctx = make_env(tmp_path)

    video = FakeVideo(
        TelegramFile(file_id="AgADbig", file_unique_id="uniq-big", file_path="/x/big.mkv"),
        get_file_error=BadRequest("File is too big"),
    )
    msg = FakeMessage(id=35, video=video)

    await handlers.on_video(_update(msg), ctx)

    status = msg.last_reply
    assert t("en", "video_too_big") in status.edits
    assert t("en", "video_error") not in status.edits
    assert not svc.manager._jobs
    assert spawner.commands == []


async def test_on_video_video_note(tmp_path, monkeypatch):
    _capture_tmp_dirs(tmp_path, monkeypatch)
    cfg, svc, spawner, ctx = make_env(tmp_path)

    src = tmp_path / "circle.mp4"
    payload = b"circles" * 50
    src.write_bytes(payload)
    tg_file = TelegramFile(file_id="AgADvn", file_unique_id="uniq-vn", file_path=str(src))
    vn = FakeVideoNote(tg_file, duration=7.0, length=7, file_size=len(payload))
    msg = FakeMessage(id=40, video_note=vn)

    await handlers.on_video(_update(msg), ctx)
    await _run_to_completion(svc)

    jobs = list(svc.manager._jobs.values())
    assert len(jobs) == 1
    assert jobs[0].state == "done"

    # no file_name -> default title (in the sidecar); no mime -> mp4 extension;
    # on-disk name is the Telegram file id
    final = cfg.data_dir / "Upload" / "ali" / "uniq-vn.mp4"
    assert final.exists()
    assert final.read_bytes() == payload
    sc = json.loads((cfg.data_dir / "Upload" / "ali" / "uniq-vn.info.json").read_text())
    assert sc["title"] == t("en", "video_default_title")
    assert sc["duration"] == 7.0
    assert spawner.commands == []


async def test_local_job_missing_source_fails_cleanly(tmp_path):
    cfg, svc, spawner, ctx = make_env(tmp_path)
    info = UrlInfo(
        id="loc1",
        title="Ghost",
        site="Upload",
        url="telegram://upload",
        channel="ali",
    )
    job = _wire(
        svc.manager.enqueue(
            info, requestor="ali", audio_mode="all", source_path=tmp_path / "nope.mp4"
        )
    )
    await _run_to_completion(svc)

    assert job.state == "failed"
    assert "local source missing" in job.error
    assert spawner.commands == []
    # no partials left in the target dir
    d = cfg.data_dir / "Upload" / "ali"
    if d.exists():
        assert [p.name for p in d.iterdir() if not p.name.startswith(".inflight-")] == []


def test_real_ffprobe_audio_bad_binary_returns_zero(tmp_path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x")
    assert asyncio.run(real_ffprobe_audio("definitely-not-a-binary", p, timeout=2.0)) == 0


async def test_local_job_audio_count_from_injected_probe(tmp_path):
    cfg, svc, spawner, ctx = make_env(tmp_path)

    async def fake_probe(path):
        assert path.exists()
        return 3

    svc.manager.ffprobe_audio = fake_probe
    src = tmp_path / "multi.mkv"
    src.write_bytes(b"streams" * 10)
    info = UrlInfo(
        id="loc2",
        title="Multi",
        site="Upload",
        url="telegram://upload",
        channel="ali",
    )
    job = _wire(
        svc.manager.enqueue(info, requestor="ali", audio_mode="all", source_path=src)
    )
    await _run_to_completion(svc)

    assert job.state == "done"
    assert job.audio_count == 3
    sc = json.loads((cfg.data_dir / "Upload" / "ali" / "loc2.info.json").read_text())
    assert len(sc["audio_tracks"]) == 3
