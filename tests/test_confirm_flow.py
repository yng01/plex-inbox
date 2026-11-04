"""Confirm screen (URL add flow): its buttons must close once a choice is made.

Regression: pressing a download button (or Cancel) changed the confirm
message's text but left its inline keyboard in place — PTB drops a ``None``
``reply_markup`` from the request, so the Bot API kept the old keyboard and
the stale audio/download/cancel buttons stayed visible and clickable (a
click after Cancel even crashed into a cleared ``pending``). Closing the
screen = editing the message with an *empty* ``InlineKeyboardMarkup``.
"""

from telegram import InlineKeyboardMarkup

from bot import handlers
from bot.config import load_config
from bot.i18n import open_store, t
from bot.probe import Entry, UrlInfo
from bot.store import Archive, Library, Stats
from bot.updater import Updater
from bot.ytdlp import DownloadManager

from fakes import (
    FakeBot,
    FakeCallbackQuery,
    FakeContext,
    FakeHttp,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    RecordingSpawner,
)

DETAILS = "Title: Great Video\nSite: YouTube"


def make_env(tmp_path):
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
    manager = DownloadManager(cfg, archive, spawner=spawner, http=http, notify_interval=0.01)
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
    return cfg, svc, ctx


def _video() -> UrlInfo:
    return UrlInfo(
        id="abc123",
        title="Great Video",
        site="YouTube",
        url="https://youtube.com/watch?v=abc123",
        channel="Cool Channel",
        duration=10.0,
        is_playlist=False,
        raw={},
    )


def _playlist() -> UrlInfo:
    entries = [
        Entry(id=f"ep{i}", title=f"Episode {i}", url=f"https://site/ep{i}", channel="Cool Channel")
        for i in range(3)
    ]
    return UrlInfo(
        id="pl1",
        title="Some Show",
        site="YouTube",
        url="https://youtube.com/playlist?list=pl1",
        channel="Cool Channel",
        is_playlist=True,
        entries=entries,
        raw={},
    )


def _callback(data: str, text: str = DETAILS):
    msg = FakeMessage(text=text)
    cq = FakeCallbackQuery(data=data, message=msg)
    upd = FakeUpdate(
        user=FakeUser(id=111, username="ali"),
        message=msg,  # PTB sets effective_message = q.message for callback updates
        callback_query=cq,
    )
    return cq, upd


def _assert_closed(msg: FakeMessage) -> None:
    """The confirm message's last edit removed the keyboard entirely."""
    markup = msg.edit_markups[-1]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert not markup.inline_keyboard


# --- single video ---------------------------------------------------------------


async def test_download_button_queues_job_and_closes_keyboard(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("da")

    await handlers.on_callback(upd, ctx)

    assert len(svc.manager._jobs) == 1
    assert ctx.user_data["pending"] is None
    # the confirm message keeps its details text but loses its buttons
    assert cq.message.edits[-1] == DETAILS
    _assert_closed(cq.message)


async def test_original_audio_button_sets_mode_and_closes_keyboard(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("db")

    await handlers.on_callback(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "orig"
    _assert_closed(cq.message)


async def test_download_of_archived_item_reports_skip_and_closes(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    rel = "YouTube/Cool Channel/abc123.mkv"
    (cfg.data_dir / "YouTube" / "Cool Channel").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / rel).write_bytes(b"vid")
    svc.archive.add("YouTube:abc123", "ali", rel)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("da")

    await handlers.on_callback(upd, ctx)

    assert svc.manager._jobs == {}
    assert cq.message.edits[-1] == t("en", "already_in_library")
    _assert_closed(cq.message)


async def test_cancel_closes_keyboard(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("c")

    await handlers.on_callback(upd, ctx)

    assert svc.manager._jobs == {}
    assert ctx.user_data["pending"] is None
    assert cq.message.edits[-1] == t("en", "cancelled")
    _assert_closed(cq.message)


# --- playlist -------------------------------------------------------------------


async def test_playlist_download_fans_out_and_closes(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _playlist(), "page": 0}
    cq, upd = _callback("da")

    await handlers.on_callback(upd, ctx)

    assert len(svc.manager._jobs) == 3
    assert ctx.user_data["pending"] is None
    assert cq.message.edits[-1] == t("en", "added_many", n=3)
    _assert_closed(cq.message)


async def test_playlist_entry_download_adds_one_and_closes(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _playlist(), "page": 0}
    cq, upd = _callback("e1")

    await handlers.on_callback(upd, ctx)

    jobs = list(svc.manager._jobs.values())
    assert len(jobs) == 1
    assert jobs[0].base == "ep1"
    _assert_closed(cq.message)
