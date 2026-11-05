"""Per-user settings: default audio mode, quality, and auto-download.

Covers:
- ``SettingsStore``: defaults for unknown users, setters, persistence across
  reload, robustness to corrupt files and stale values.
- ``on_text``: auto-download **on** skips the confirm screen entirely and the
  job carries the saved audio mode + quality (the in-flight "probing…" status
  message becomes the job's status — no confirm screen, no second message);
  auto-download **off** (the default) shows the confirm screen pre-filled with
  the saved quality.
- confirm screen: quality buttons are selectable and enqueue uses the choice.
- settings screen: renders current state; callbacks mutate the store and
  re-render in place.
"""

import json

import pytest

from bot import handlers
from bot.i18n import t
from bot.settings import SettingsStore, UserSettings

from fakes import (
    FakeMessage,
    FakeProbeSpawner,
    FakeUpdate,
    FakeUser,
)
from test_confirm_flow import (
    PROBE_JSON,
    _callback,
    _playlist,
    _url_update,
    _video,
    make_env,
)

UID = 111


# --- SettingsStore ------------------------------------------------------------


def test_unknown_user_gets_defaults(tmp_path):
    st = SettingsStore(tmp_path / "settings.json")
    assert st.get(999) == UserSettings(
        audio_mode="orig", quality=None, auto_download=False
    )


def test_setters_persist_across_reload(tmp_path):
    path = tmp_path / "settings.json"
    st = SettingsStore(path)
    st.set_audio_mode(UID, "audio")
    st.set_quality(UID, 720)
    st.set_auto_download(UID, True)

    again = SettingsStore(path)
    s = again.get(UID)
    assert (s.audio_mode, s.quality, s.auto_download) == ("audio", 720, True)


def test_corrupt_file_falls_back_to_defaults_and_still_writes(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    st = SettingsStore(path)
    assert st.get(UID) == UserSettings()
    st.set_auto_download(UID, True)
    assert SettingsStore(path).get(UID).auto_download is True


def test_stale_quality_falls_back_to_best(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({str(UID): {"quality": 576, "audio_mode": "all"}}),
        encoding="utf-8",
    )
    st = SettingsStore(path)
    s = st.get(UID)
    assert s.quality is None
    assert s.audio_mode == "all"


def test_invalid_values_rejected(tmp_path):
    st = SettingsStore(tmp_path / "settings.json")
    with pytest.raises(ValueError):
        st.set_audio_mode(UID, "nope")
    with pytest.raises(ValueError):
        st.set_quality(UID, 576)


# --- on_text: auto-download ---------------------------------------------------


def _set_auto(svc, mode: str = "orig", quality: int | None = None) -> None:
    svc.settings_store.set_auto_download(UID, True)
    svc.settings_store.set_audio_mode(UID, mode)
    if quality is not None:
        svc.settings_store.set_quality(UID, quality)


async def test_auto_download_skips_confirm_and_uses_saved_options(tmp_path):
    """The headline behavior: link in -> download out, no confirm screen,
    using the user's saved audio mode and quality."""
    cfg, svc, ctx = make_env(tmp_path)
    _set_auto(svc, mode="orig", quality=480)
    svc.probe_spawner = FakeProbeSpawner(stdout=json.dumps(PROBE_JSON))
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    jobs = list(svc.manager._jobs.values())
    assert len(jobs) == 1
    assert jobs[0].audio_mode == "orig"
    assert jobs[0].quality == 480
    # the in-flight "Fetching…" message became the job's status message: it
    # was edited to "queued…" (never a confirm screen) and no second message
    # was sent
    status = msg.last_reply
    assert status is not None
    assert status.edits[-1] == t("en", "queued", pos=1)
    assert msg.replies == [t("en", "probing")]


async def test_auto_download_audio_only_mode_keeps_quality_field(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    _set_auto(svc, mode="audio", quality=720)  # quality stored, inert in audio mode
    svc.probe_spawner = FakeProbeSpawner(stdout=json.dumps(PROBE_JSON))
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "audio"
    assert job.quality == 720
    assert status_edited_to_queued(msg)


async def test_auto_download_all_mode_degrades_without_multi_audio(tmp_path):
    """'all audio tracks' only exists with MULTI_AUDIO; otherwise fall back to
    the original-language video instead of enqueuing a broken mode."""
    import dataclasses

    cfg, svc, ctx = make_env(tmp_path)
    svc.config = dataclasses.replace(svc.config, multi_audio=False)  # single-audio deployment
    _set_auto(svc, mode="all")
    svc.probe_spawner = FakeProbeSpawner(stdout=json.dumps(PROBE_JSON))
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "orig"


async def test_auto_download_of_archived_item_edits_status_in_place(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    rel = "YouTube/Cool Channel/abc123.mkv"
    (cfg.data_dir / "YouTube" / "Cool Channel").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / rel).write_bytes(b"vid")
    svc.archive.add("YouTube:abc123:video", "ali", rel)
    _set_auto(svc, mode="orig")
    svc.probe_spawner = FakeProbeSpawner(stdout=json.dumps(PROBE_JSON))
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    assert svc.manager._jobs == {}
    assert msg.last_reply.edits[-1] == t("en", "already_in_library")
    assert msg.replies == [t("en", "probing")]


def status_edited_to_queued(msg: FakeMessage) -> bool:
    return msg.last_reply is not None and msg.last_reply.edits[-1] == t("en", "queued", pos=1)


# --- on_text: "ask me every time" (default) -----------------------------------


async def test_ask_every_time_prefills_saved_quality_on_confirm(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    svc.settings_store.set_quality(UID, 720)  # auto-download stays off
    svc.probe_spawner = FakeProbeSpawner(stdout=json.dumps(PROBE_JSON))
    msg, upd = _url_update()

    await handlers.on_text(upd, ctx)

    assert ctx.user_data["pending"]["quality"] == 720
    status = msg.last_reply
    assert t("en", "detail_title") in status.edits[-1]
    markup = status.edit_markups[-1]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "q720" in datas and "qbest" in datas
    checked = [
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.text.startswith("✓")
    ]
    assert checked == ["q720"]


# --- confirm screen: quality selection ----------------------------------------


async def test_quality_selection_marks_button_and_updates_pending(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0}
    cq, upd = _callback("q720")

    await handlers.on_callback(upd, ctx)

    assert ctx.user_data["pending"]["quality"] == 720
    markup = cq.message.edit_markups[-1]
    checked = [
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.text.startswith("✓")
    ]
    assert checked == ["q720"]


async def test_quality_best_switches_selection_back(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0, "quality": 720}
    cq, upd = _callback("qbest")

    await handlers.on_callback(upd, ctx)

    assert ctx.user_data["pending"]["quality"] is None
    markup = cq.message.edit_markups[-1]
    checked = [
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.text.startswith("✓")
    ]
    assert checked == ["qbest"]


async def test_enqueue_uses_selected_quality(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _video(), "page": 0, "quality": 720}
    cq, upd = _callback("da")

    await handlers.on_callback(upd, ctx)

    job = next(iter(svc.manager._jobs.values()))
    assert job.audio_mode == "all"  # make_env config has MULTI_AUDIO on
    assert job.quality == 720


# --- settings screen ------------------------------------------------------------


def _settings_markup(cq):
    return cq.message.edit_markups[-1]


async def test_settings_screen_renders_current_state(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    svc.settings_store.set_audio_mode(UID, "audio")
    svc.settings_store.set_quality(UID, 720)
    cq, upd = _callback("m_settings")

    await handlers.on_callback(upd, ctx)

    last = cq.message.edits[-1]
    assert t("en", "settings_title") in last
    assert t("en", "settings_audio", label=t("en", "mode_audio")) in last
    assert t("en", "settings_quality", label="720p") in last
    markup = _settings_markup(cq)
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "sa_audio" in datas and "sq720" in datas and "sqbest" in datas and "sauto" in datas
    checked = [
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.text.startswith("✓")
    ]
    assert sorted(checked) == ["sa_audio", "sq720"]


async def test_settings_mode_change_persists(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    cq, upd = _callback("sa_audio")

    await handlers.on_callback(upd, ctx)

    assert svc.settings_store.get(UID).audio_mode == "audio"


async def test_settings_quality_change_persists(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    cq, upd = _callback("sq480")
    await handlers.on_callback(upd, ctx)
    assert svc.settings_store.get(UID).quality == 480

    cq2, upd2 = _callback("sqbest")
    await handlers.on_callback(upd2, ctx)
    assert svc.settings_store.get(UID).quality is None


async def test_settings_auto_download_toggles(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    cq, upd = _callback("sauto")
    await handlers.on_callback(upd, ctx)
    assert svc.settings_store.get(UID).auto_download is True

    cq2, upd2 = _callback("sauto")
    await handlers.on_callback(upd2, ctx)
    assert svc.settings_store.get(UID).auto_download is False


# --- playlist auto-download: status message carries the fan-out result ---------


async def test_auto_playlist_fans_out_and_reports_to_status_message(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    ctx.user_data["pending"] = {"info": _playlist(), "page": 0, "quality": 720}
    status = FakeMessage()
    upd = FakeUpdate(user=FakeUser(id=UID), message=FakeMessage(text="url"))

    await handlers._enqueue(upd, ctx, "orig", status_msg=status)

    jobs = list(svc.manager._jobs.values())
    assert len(jobs) == 3
    assert all(j.audio_mode == "orig" and j.quality == 720 for j in jobs)
    assert ctx.user_data["pending"] is None
    assert status.edits[-1] == t("en", "added_many", n=3)
