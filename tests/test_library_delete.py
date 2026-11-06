"""Library delete flow: 🗑 on a file -> confirm -> everything for the item is
gone (media + NFO + thumbnail + sidecar + both dedupe slots) and Plex is
refreshed. Cancelling leaves the item intact; stale taps are harmless.

Deletion must keep invariants in step: files AND ledger entries are removed
together, the item stays re-downloadable, and a single bad item never breaks
the flow.
"""

import json

from bot import handlers
from bot.i18n import t
from bot.store import dedupe_key

from fakes import FakeCallbackQuery, FakeMessage, FakeUpdate, FakeUser
from test_confirm_flow import make_env


def _mk_item(tmp_path, vid="abc123", title="Great Video"):
    """One realistic library item on disk (as _finalize lays it out)."""
    d = tmp_path / "data" / "YouTube" / "Cool Channel"
    d.mkdir(parents=True, exist_ok=True)
    media = d / f"{vid}.mkv"
    media.write_bytes(b"x" * 32)
    (d / f"{vid}.nfo").write_text("<video><title/></video>", encoding="utf-8")
    (d / f"{vid}.jpg").write_bytes(b"jpg")
    (d / "poster.jpg").write_bytes(b"poster")  # shared channel poster
    sidecar = d / f"{vid}.info.json"
    sidecar.write_text(
        json.dumps(
            {
                "id": vid,
                "site": "YouTube",
                "channel": "Cool Channel",
                "title": title,
                "url": f"https://youtube.com/watch?v={vid}",
                "requestor": "ali",
                "added_at": "2026-01-01T00:00:00Z",
                "final_path": media.as_posix(),
                "size_bytes": 32,
                "duration": 10.0,
                "year": 2026,
                "audio_tracks": [{"language": "en", "codec": "aac"}],
                "genres": [],
                "tags": [],
            }
        ),
        encoding="utf-8",
    )
    return d, media, sidecar


def _cb(data: str, message) -> FakeUpdate:
    return FakeUpdate(
        user=FakeUser(), callback_query=FakeCallbackQuery(data=data, message=message)
    )


def _button_labels(msg: FakeMessage) -> list[str]:
    kb = msg.keyboard
    # InlineKeyboardMarkup -> .inline_keyboard, ReplyKeyboardMarkup -> .keyboard
    rows = getattr(kb, "inline_keyboard", None) or getattr(kb, "keyboard", None) or []
    return [b.text for row in rows for b in row]


async def test_file_screen_lists_a_delete_button_per_file(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    d, media, sidecar = _mk_item(tmp_path)
    ctx.user_data["lib_stack"] = ["YouTube", "Cool Channel"]
    upd = FakeUpdate(user=FakeUser(), message=FakeMessage(text="/library", id=1))
    await handlers.render_library(upd, ctx)

    screen = upd.effective_message.last_reply
    assert screen is not None
    assert "🗑 Great Video" in _button_labels(screen)
    # the tapped 🗑 button must resolve to this sidecar on disk
    assert ctx.user_data["lib_files"] == [sidecar.as_posix()]


async def test_delete_yes_removes_files_and_ledger_slots(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    d, media, sidecar = _mk_item(tmp_path)
    audio = d / "abc123.m4a"
    audio.write_bytes(b"y" * 8)
    svc.archive.add(
        dedupe_key("YouTube", "abc123", False), "ali", "YouTube/Cool Channel/abc123.mkv"
    )
    svc.archive.add(
        dedupe_key("YouTube", "abc123", True), "ali", "YouTube/Cool Channel/abc123.m4a"
    )

    ctx.user_data["lib_stack"] = ["YouTube", "Cool Channel"]
    ctx.user_data["lib_files"] = [sidecar.as_posix()]
    msg = FakeMessage(text="library screen", id=1)

    # tap 🗑 on the file -> confirm screen with the item's real title
    await handlers.on_callback(_cb("ld0", msg), ctx)
    assert t("en", "delete_confirm", title="Great Video") in msg.edits
    assert t("en", "delete_yes") in _button_labels(msg)
    assert t("en", "delete_no") in _button_labels(msg)

    # confirm -> every artifact of the item is gone
    await handlers.on_callback(_cb("dl_yes", msg), ctx)
    assert not media.exists()
    assert not (d / "abc123.nfo").exists()
    assert not (d / "abc123.jpg").exists()
    assert not sidecar.exists()
    assert not audio.exists()
    assert (d / "poster.jpg").exists()  # shared channel poster is kept
    # both dedupe slots are free -> the item can be re-downloaded
    keys = {e.key for e in svc.archive.entries()}
    assert "YouTube:abc123:video" not in keys
    assert "YouTube:abc123:audio" not in keys
    assert t("en", "deleted_ok") in msg.edits


async def test_delete_no_cancels_and_keeps_the_item(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    d, media, sidecar = _mk_item(tmp_path)
    svc.archive.add(
        dedupe_key("YouTube", "abc123", False), "ali", "YouTube/Cool Channel/abc123.mkv"
    )

    ctx.user_data["lib_stack"] = ["YouTube", "Cool Channel"]
    ctx.user_data["lib_files"] = [sidecar.as_posix()]
    msg = FakeMessage(text="library screen", id=1)

    await handlers.on_callback(_cb("ld0", msg), ctx)
    await handlers.on_callback(_cb("dl_no", msg), ctx)

    assert media.exists() and sidecar.exists()
    assert svc.archive.contains(dedupe_key("YouTube", "abc123", False))
    assert "deleted_ok" not in "".join(msg.edits)
    # back on the file list (the 🗑 button is offered again)
    assert "Great Video" in msg.edits[-1]
    assert f"🗑 Great Video" in _button_labels(msg)


async def test_stale_dl_yes_without_target_is_harmless(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    msg = FakeMessage(text="confirm screen", id=1)
    await handlers.on_callback(_cb("dl_yes", msg), ctx)
    assert t("en", "delete_nothing") in msg.edits


async def test_delete_confirm_is_rendered_in_the_users_language(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    svc.lang_store.set(111, "ar")  # FakeUser's id
    d, media, sidecar = _mk_item(tmp_path)
    ctx.user_data["lib_stack"] = ["YouTube", "Cool Channel"]
    ctx.user_data["lib_files"] = [sidecar.as_posix()]
    msg = FakeMessage(text="library screen", id=1)

    await handlers.on_callback(_cb("ld0", msg), ctx)
    assert t("ar", "delete_confirm", title="Great Video") in msg.edits
    assert t("ar", "delete_yes") in _button_labels(msg)


async def test_delete_refreshes_plex(tmp_path):
    from dataclasses import replace

    cfg, svc, ctx = make_env(tmp_path)
    # Config is frozen — swap in an equivalent one with Plex enabled
    svc.config = replace(cfg, plex_url="http://plex:32400", plex_token="secret")
    d, media, sidecar = _mk_item(tmp_path)
    ctx.user_data["lib_stack"] = ["YouTube", "Cool Channel"]
    ctx.user_data["lib_files"] = [sidecar.as_posix()]
    msg = FakeMessage(text="library screen", id=1)

    await handlers.on_callback(_cb("ld0", msg), ctx)
    await handlers.on_callback(_cb("dl_yes", msg), ctx)

    refresh = [p for p in svc.http.posts if p["url"].endswith("/library/sections/refresh")]
    assert refresh, "Plex refresh was not triggered after deletion"
    assert refresh[0]["headers"].get("X-Plex-Token") == "secret"
