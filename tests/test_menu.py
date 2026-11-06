"""Sticky main menu: one canonical menu message per chat, never duplicated.

- The menu ships as a *reply-keyboard* button bar (always visible above the
  input box — no scrolling, and as chat state it cannot pile up duplicates).
- A repeat ``/menu`` (or any back-to-menu tap) edits the remembered menu
  message in place instead of sending a second one.
- If the remembered message is gone (deleted / "too old to edit"), a fresh menu
  is sent and the new id is remembered.
- Tapping a menu button arrives as its plain-text label — routed to the right
  screen in either language.
"""

from bot import handlers
from bot.i18n import t

from fakes import FakeCallbackQuery, FakeMessage, FakeUpdate, FakeUser
from test_confirm_flow import make_env


def _text_update(text: str) -> FakeUpdate:
    return FakeUpdate(user=FakeUser(), message=FakeMessage(text=text, id=1))


async def test_first_menu_sends_and_remembers(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    upd = _text_update("/menu")
    await handlers.cmd_menu(upd, ctx)
    # one fresh menu message, remembered for in-place edits on the next hit
    assert upd.effective_message.replies == [t("en", "menu_title")]
    assert ctx.chat_data["menu_msg_id"] == 2


async def test_repeat_menu_edits_in_place_not_duplicated(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    await handlers.cmd_menu(_text_update("/menu"), ctx)
    stored = ctx.chat_data["menu_msg_id"]

    await handlers.cmd_menu(_text_update("/menu"), ctx)
    # the SAME message id is edited — no second menu message, no new reply
    assert ctx.chat_data["menu_msg_id"] == stored
    assert ctx.bot.edits[-1][1] == stored
    assert ctx.bot.edits[-1][2] == t("en", "menu_title")
    # the reply keyboard is (re)applied on the edit
    kb = ctx.bot.edits[-1][3]
    assert kb is not None
    labels = [b.text for row in kb.keyboard for b in row]
    for key in (
        "menu_add",
        "menu_queue",
        "menu_library",
        "menu_stats",
        "menu_lang",
        "menu_settings",
    ):
        assert t("en", key) in labels, f"{key} missing from reply keyboard"


async def test_back_to_menu_tap_edits_canonical_message(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    # establish the canonical menu message (id 2)
    await handlers.cmd_menu(_text_update("/menu"), ctx)
    stored = ctx.chat_data["menu_msg_id"]

    # user taps the persistent "queue" button -> the queue screen is shown
    upd = _text_update(t("en", "menu_queue"))
    await handlers.on_text(upd, ctx)
    screen = upd.effective_message.last_reply
    assert screen is not None
    assert t("en", "no_jobs") in upd.effective_message.replies

    # user taps the "back to menu" button on that screen -> the menu must come
    # back on the SAME canonical message, not as a brand-new message
    await handlers.on_callback(
        FakeUpdate(
            user=FakeUser(),
            callback_query=FakeCallbackQuery(data="mmenu", message=screen),
        ),
        ctx,
    )
    # edited the stored canonical message (not a fresh send)
    assert ctx.bot.edits[-1][1] == stored
    assert ctx.bot.edits[-1][2] == t("en", "menu_title")
    # the remembered id is unchanged — still one canonical menu message
    assert ctx.chat_data["menu_msg_id"] == stored


async def test_menu_resends_when_stored_message_is_gone(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    # a menu message was remembered earlier but has since been deleted
    ctx.chat_data["menu_msg_id"] = 42
    ctx.bot.missing_ids.add(42)

    upd = _text_update("/menu")
    await handlers.cmd_menu(upd, ctx)
    # re-sent fresh exactly once, and the stored id now points at the new one
    assert upd.effective_message.replies == [t("en", "menu_title")]
    assert ctx.chat_data["menu_msg_id"] == 2
    assert 42 not in ctx.bot.edits and all(e[1] != 42 for e in ctx.bot.edits)


async def test_menu_button_label_routes_to_screen(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    # a tap on the reply-keyboard button arrives as its plain-text label
    upd = _text_update(t("en", "menu_queue"))
    await handlers.on_text(upd, ctx)
    # routed to the queue screen — NOT treated as "not a url"
    assert t("en", "not_a_url") not in upd.effective_message.replies
    assert t("en", "no_jobs") in upd.effective_message.replies


async def test_menu_add_button_shows_prompt(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    upd = _text_update(t("en", "menu_add"))
    await handlers.on_text(upd, ctx)
    assert t("en", "add_prompt") in upd.effective_message.replies


async def test_arabic_menu_label_also_routes(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    # the button was rendered in Arabic; the tap carries the Arabic label
    upd = _text_update(t("ar", "menu_queue"))
    await handlers.on_text(upd, ctx)
    assert t("en", "not_a_url") not in upd.effective_message.replies
    assert t("en", "no_jobs") in upd.effective_message.replies


async def test_plain_text_that_is_not_menu_or_url_still_rejected(tmp_path):
    cfg, svc, ctx = make_env(tmp_path)
    upd = _text_update("hello there")
    await handlers.on_text(upd, ctx)
    assert t("en", "not_a_url") in upd.effective_message.replies
