"""Confirm screen (URL add flow): its buttons must close once a choice is made.

Regression: pressing a download button (or Cancel) changed the confirm
message's text but left its inline keyboard in place — PTB drops a ``None``
``reply_markup`` from the request, so the Bot API kept the old keyboard and
the stale audio/download/cancel buttons stayed visible and clickable (a
click after Cancel even crashed into a cleared ``pending``). Closing the
screen = editing the message with an *empty* ``InlineKeyboardMarkup``.
"""

import json

from telegram import InlineKeyboardMarkup

from bot import handlers
from bot.config import load_config
from bot.i18n import open_store, t
from bot.settings import SettingsStore
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
    FakeProbeSpawner,
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
        settings_store=SettingsStore(tmp_path / "settings.json"),
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


async def test_audio_only_button_sets_mode_and_closes_keyboard(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("ao")

    await handlers.on_callback(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "audio"
    _assert_closed(cq.message)


async def test_download_of_archived_item_reports_skip_and_closes(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    rel = "YouTube/Cool Channel/abc123.mkv"
    (cfg.data_dir / "YouTube" / "Cool Channel").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / rel).write_bytes(b"vid")
    svc.archive.add("YouTube:abc123:video", "ali", rel)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("da")

    await handlers.on_callback(upd, ctx)

    assert svc.manager._jobs == {}
    assert cq.message.edits[-1] == t("en", "already_in_library")
    _assert_closed(cq.message)


async def test_audio_only_archived_does_not_block_video_button(tmp_path):
    """Regression: after fetching an item audio-only, a *video* button press
    must still queue a real download — the two slots are independent."""
    cfg, svc, ctx = make_env(tmp_path)
    rel = "YouTube/Cool Channel/abc123.m4a"
    (cfg.data_dir / "YouTube" / "Cool Channel").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / rel).write_bytes(b"aud")
    svc.archive.add("YouTube:abc123:audio", "ali", rel)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("da")

    await handlers.on_callback(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "all"
    assert "already_in_library" not in cq.message.edits[-1]
    _assert_closed(cq.message)


async def test_video_archived_does_not_block_audio_only_button(tmp_path):
    """Mirror of the above: an archived *video* must not hide the audio-only
    option for the same item."""
    cfg, svc, ctx = make_env(tmp_path)
    rel = "YouTube/Cool Channel/abc123.mkv"
    (cfg.data_dir / "YouTube" / "Cool Channel").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / rel).write_bytes(b"vid")
    svc.archive.add("YouTube:abc123:video", "ali", rel)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("ao")

    await handlers.on_callback(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "audio"
    assert "already_in_library" not in cq.message.edits[-1]
    _assert_closed(cq.message)


async def test_confirm_screen_hides_modes_already_in_library(tmp_path):
    """A mode that is already archived is not offered again: only the
    still-missing mode(s) + Cancel remain on the confirm screen."""
    cfg, svc, ctx = make_env(tmp_path)
    rel = "YouTube/Cool Channel/abc123.m4a"
    (cfg.data_dir / "YouTube" / "Cool Channel").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / rel).write_bytes(b"aud")
    svc.archive.add("YouTube:abc123:audio", "ali", rel)

    status = FakeMessage(text="Fetching…")
    upd = FakeUpdate(user=FakeUser(id=111, username="ali"), message=FakeMessage(text="url"))
    await handlers._render_confirm(upd, ctx, _video(), status_msg=status)

    markup = status.edit_markups[-1]
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "ao" not in buttons  # audio already archived -> button hidden
    assert "da" in buttons  # video still missing -> offered
    assert "c" in buttons


async def test_confirm_screen_shows_all_buttons_when_library_empty(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    status = FakeMessage(text="Fetching…")
    upd = FakeUpdate(user=FakeUser(id=111, username="ali"), message=FakeMessage(text="url"))
    await handlers._render_confirm(upd, ctx, _video(), status_msg=status)

    buttons = [b.callback_data for row in status.edit_markups[-1].inline_keyboard for b in row]
    assert "da" in buttons
    assert "db" in buttons
    assert "ao" in buttons
    assert "c" in buttons


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


async def test_playlist_audio_only_fans_out_with_audio_mode(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _playlist(), "page": 0}
    cq, upd = _callback("ao")

    await handlers.on_callback(upd, ctx)

    jobs = list(svc.manager._jobs.values())
    assert len(jobs) == 3
    assert all(j.audio_mode == "audio" for j in jobs)
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


# --- URL → confirm: the status message must be replaced, never left dangling ---

PROBE_JSON = {
    "id": "abc123",
    "title": "Great Video",
    "extractor_key": "YouTube",
    "channel": "Cool Channel",
    "webpage_url": "https://youtube.com/watch?v=abc123",
    "duration": 372.5,
    "resolution": "1920x1080",
}


def _url_update() -> tuple[FakeMessage, FakeUpdate]:
    msg = FakeMessage(text="https://youtube.com/watch?v=abc123")
    upd = FakeUpdate(user=FakeUser(id=111, username="ali"), message=msg)
    return msg, upd


async def test_url_confirm_replaces_probing_message(tmp_path):
    """Regression (user-reported 'stuck on 🔎 Fetching video info…'): a
    successful probe used to send the confirm screen as a *second* message and
    leave the "Fetching…" status behind, so the first message looked stuck
    forever. The status message must be edited into the confirm screen
    instead — one message, no dangling status."""
    cfg, svc, ctx = make_env(tmp_path)
    svc.probe_spawner = FakeProbeSpawner(stdout=json.dumps(PROBE_JSON))
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    status = msg.last_reply  # the '🔎 Fetching…' message
    assert status is not None
    assert status.edits, "status message was never edited into the confirm screen"
    assert t("en", "detail_title") in status.edits[-1]
    assert t("en", "detail_site") in status.edits[-1]
    markup = status.edit_markups[-1]
    assert isinstance(markup, InlineKeyboardMarkup)
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "da" in datas and "db" in datas and "ao" in datas and "c" in datas
    # no *second* confirm message below the status one
    assert msg.replies == [t("en", "probing")]
    assert ctx.user_data["pending"]["info"].id == "abc123"


async def test_url_probe_error_edits_probing_message(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    svc.probe_spawner = FakeProbeSpawner(
        stdout="", stderr="ERROR: This video is private", returncode=1
    )
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    status = msg.last_reply
    assert status.edits[-1] == t("en", "error_probe_private")
    assert msg.replies == [t("en", "probing")]


# --- language screen ----------------------------------------------------------


async def test_lang_button_switches_to_arabic_and_confirms(tmp_path):
    """Tapping a language button sets it and confirms in place (regression:
    the ``lang=`` kwarg collided with ``t()``'s own ``lang`` parameter)."""
    cfg, svc, ctx = make_env(tmp_path)
    cq, upd = _callback("lang_ar")

    await handlers.on_callback(upd, ctx)

    assert svc.lang_store.get(111) == "ar"
    # confirmed on the tapped message, in the newly chosen language, naming
    # the language by its display name
    assert cq.message.edits[-1] == t("ar", "lang_set", name=t("ar", "lang_ar"))
    assert t("ar", "error_generic") not in cq.message.replies


async def test_lang_button_switches_to_english_and_confirms(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    cq, upd = _callback("lang_en")

    await handlers.on_callback(upd, ctx)

    assert svc.lang_store.get(111) == "en"
    assert cq.message.edits[-1] == t("en", "lang_set", name=t("en", "lang_en"))
