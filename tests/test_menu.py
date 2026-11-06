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

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

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


async def test_repeat_menu_same_language_confirms_without_resending(tmp_path):
    """A repeat ``/menu`` in the SAME language must confirm, not re-edit.

    The canonical message already shows exactly this menu, so an in-place edit
    is refused by the server (400 "message is not modified") and a fresh send
    would duplicate it. The sticky reply-keyboard bar is still up, so the menu
    is right there — we confirm instead of resending.
    """
    cfg, svc, ctx = make_env(tmp_path)
    first = _text_update("/menu")
    await handlers.cmd_menu(first, ctx)
    stored = ctx.chat_data["menu_msg_id"]
    # sanity: the reply keyboard shipped on the first (fresh) send
    kb = first.effective_message.last_reply.keyboard
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

    # a same-language repeat: confirmed, no edit, no duplicate menu message
    upd = _text_update("/menu")
    await handlers.cmd_menu(upd, ctx)
    assert ctx.chat_data["menu_msg_id"] == stored
    assert ctx.bot.edits == []
    # the repeat confirmed rather than re-sending the menu title
    assert t("en", "menu_here") in upd.effective_message.replies
    assert upd.effective_message.replies.count(t("en", "menu_title")) == 0


async def test_back_to_menu_tap_confirms_when_menu_already_open(tmp_path):
    """Tapping "back to menu" while the sticky menu bar is already up must
    confirm (and clear the spinner) — never resend a duplicate or attempt a
    refused in-place edit of the canonical message."""
    cfg, svc, ctx = make_env(tmp_path)
    # establish the canonical menu message (id 2), open in the user's language
    await handlers.cmd_menu(_text_update("/menu"), ctx)
    stored = ctx.chat_data["menu_msg_id"]

    # user taps the persistent "queue" button -> the queue screen is shown
    upd = _text_update(t("en", "menu_queue"))
    await handlers.on_text(upd, ctx)
    screen = upd.effective_message.last_reply
    assert screen is not None
    assert t("en", "no_jobs") in upd.effective_message.replies

    # user taps the "back to menu" button on that screen
    cq = FakeCallbackQuery(data="mmenu", message=screen)
    await handlers.on_callback(FakeUpdate(user=FakeUser(), callback_query=cq), ctx)
    # the callback was answered (spinner cleared) with the "already open" note
    assert cq.answered >= 1
    assert t("en", "menu_here") in cq.answer_texts
    # no in-place edit, no duplicate menu message, remembered id unchanged
    assert ctx.chat_data["menu_msg_id"] == stored
    assert ctx.bot.edits == []
    assert screen.replies == []


async def test_mmenu_tap_from_every_sub_screen_confirms(tmp_path):
    """Back-to-menu (mmenu) callbacks still resolve to the sticky menu. The
    inline "📺 Video Archive" button no longer ships on the Queue, Language and
    Settings sub-screens (the always-visible reply-keyboard bar is the reliable
    way back), so this pins the guarantee for any lingering mmenu tap — e.g. a
    message rendered before the button was removed: it must confirm on every
    sub-screen, never a refused in-place edit, a duplicate menu message, or a
    silently-spinning callback (the reported "clicking Video Archive, no
    reply")."""
    for label_key in ("menu_queue", "menu_lang", "menu_settings"):
        cfg, svc, ctx = make_env(tmp_path)
        # the menu bar is already open, in the user's language
        await handlers.cmd_menu(_text_update("/menu"), ctx)
        stored = ctx.chat_data["menu_msg_id"]
        assert ctx.chat_data.get("menu_rendered_lang") == "en"

        # open the sub-screen (its inline keyboard carries the mmenu button)
        open_upd = _text_update(t("en", label_key))
        await handlers.on_text(open_upd, ctx)
        screen = open_upd.effective_message.last_reply
        assert screen is not None

        # tap the "📺 Video Archive" button on that screen
        cq = FakeCallbackQuery(data="mmenu", message=screen)
        await handlers.on_callback(FakeUpdate(user=FakeUser(), callback_query=cq), ctx)

        # answered (spinner cleared) + confirmed; no edit, no duplicate
        assert cq.answered >= 1
        assert t("en", "menu_here") in cq.answer_texts
        assert ctx.chat_data["menu_msg_id"] == stored
        assert ctx.bot.edits == []


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


async def test_back_to_menu_from_inline_screen_sends_fresh_menu(tmp_path):
    """Live regression: a back-tap arrives from a sub-screen that has an
    *inline* keyboard, and Telegram refuses to edit such a message to a reply
    keyboard ("Inline keyboard expected"). With no canonical menu message
    remembered yet (fresh bot start), the menu must be (re)sent fresh — never
    as an edit of the tapped screen, never as an error.
    """
    cfg, svc, ctx = make_env(tmp_path)
    # open the language screen — an inline-keyboard message — and remember
    # nothing else (chat_data is empty, as right after a restart)
    upd = _text_update(t("en", "menu_lang"))
    await handlers.on_text(upd, ctx)
    screen = upd.effective_message.last_reply
    assert screen is not None
    assert isinstance(screen.keyboard, InlineKeyboardMarkup)
    assert "menu_msg_id" not in ctx.chat_data

    await handlers.on_callback(
        FakeUpdate(
            user=FakeUser(),
            callback_query=FakeCallbackQuery(data="mmenu", message=screen),
        ),
        ctx,
    )

    # the tapped screen was left untouched (no refused edit, no error text)
    assert screen.edits == []
    assert t("en", "error_generic") not in screen.replies
    # the menu came back as a FRESH message carrying the reply keyboard, and
    # its id is now the canonical one
    menu = screen.last_reply
    assert menu is not None
    assert screen.replies == [t("en", "menu_title")]
    assert isinstance(menu.keyboard, ReplyKeyboardMarkup)
    assert ctx.chat_data["menu_msg_id"] == menu.message_id


async def test_stored_id_on_an_inline_message_is_discarded_and_resent(tmp_path):
    """A remembered menu id pointing at an inline-keyboard message (leftover
    from a run before the menu became a reply keyboard) is dropped and the
    menu re-sent fresh — the refused edit must not surface as an error."""
    cfg, svc, ctx = make_env(tmp_path)
    ctx.bot.keyboards[7] = InlineKeyboardMarkup([])
    ctx.chat_data["menu_msg_id"] = 7

    upd = _text_update("/menu")
    await handlers.cmd_menu(upd, ctx)

    assert ctx.chat_data["menu_msg_id"] == 2
    assert upd.effective_message.replies == [t("en", "menu_title")]


async def test_menu_title_text_reopens_menu_both_languages(tmp_path):
    """Live report: sending/tapping the menu title ("📺 Video Archive" /
    "📺 أرشيف الفيديو") got no useful reply — it fell through the add-URL
    flow to "not a URL". Both languages' titles are now routed to the menu."""
    for lang in ("en", "ar"):
        cfg, svc, ctx = make_env(tmp_path)
        # the tapped label is in whatever language the menu was last rendered
        # in — which is the user's own language. Set it, then tap the title.
        svc.lang_store.set(111, lang)
        upd = _text_update(t(lang, "menu_title"))
        await handlers.on_text(upd, ctx)
        assert t("en", "not_a_url") not in upd.effective_message.replies
        assert t(lang, "menu_title") in upd.effective_message.replies
        assert ctx.chat_data.get("menu_msg_id") is not None


async def test_menu_title_repeat_confirms_without_resending(tmp_path):
    """Tapping/sending the title when the menu is already open in the same
    language must confirm — never re-edit (a refused "message is not modified")
    nor duplicate the menu message."""
    cfg, svc, ctx = make_env(tmp_path)
    await handlers.cmd_menu(_text_update("/menu"), ctx)
    stored = ctx.chat_data["menu_msg_id"]

    upd = _text_update(t("en", "menu_title"))
    await handlers.on_text(upd, ctx)

    assert ctx.chat_data["menu_msg_id"] == stored
    assert ctx.bot.edits == []
    # the repeat confirmed rather than re-sending the menu title
    assert t("en", "menu_here") in upd.effective_message.replies
    assert upd.effective_message.replies.count(t("en", "menu_title")) == 0


async def test_language_switch_re_renders_persistent_menu(tmp_path):
    """Live report: after lang_ar the menu still showed English. The canonical
    menu message AND the sticky reply-keyboard bar must follow the new
    language — and switching back restores English again."""
    cfg, svc, ctx = make_env(tmp_path)
    await handlers.cmd_menu(_text_update("/menu"), ctx)
    stored = ctx.chat_data["menu_msg_id"]

    # open the language screen, then tap "العربية"
    upd = _text_update(t("en", "menu_lang"))
    await handlers.on_text(upd, ctx)
    screen = upd.effective_message.last_reply
    assert screen is not None
    await handlers.on_callback(
        FakeUpdate(
            user=FakeUser(),
            callback_query=FakeCallbackQuery(data="lang_ar", message=screen),
        ),
        ctx,
    )

    # the canonical menu message was re-rendered in Arabic — text AND the
    # reply-keyboard bar (its labels are now the Arabic ones)
    ar_edit = [e for e in ctx.bot.edits if e[1] == stored][-1]
    assert ar_edit[2] == t("ar", "menu_title")
    assert ar_edit[3] is not None
    labels = [b.text for row in ar_edit[3].keyboard for b in row]
    for key in (
        "menu_add",
        "menu_queue",
        "menu_library",
        "menu_stats",
        "menu_lang",
        "menu_settings",
    ):
        assert t("ar", key) in labels, f"{key} not re-rendered in Arabic"
    # the confirmation itself is in the NEW language
    assert t("ar", "lang_set", name=t("ar", "lang_ar")) in screen.edits
    # still one canonical menu message
    assert ctx.chat_data["menu_msg_id"] == stored

    # switching back to English restores the EN menu (text + bar)
    await handlers.on_callback(
        FakeUpdate(
            user=FakeUser(),
            callback_query=FakeCallbackQuery(data="lang_en", message=screen),
        ),
        ctx,
    )
    en_edit = [e for e in ctx.bot.edits if e[1] == stored][-1]
    assert en_edit[2] == t("en", "menu_title")
    labels = [b.text for row in en_edit[3].keyboard for b in row]
    assert t("en", "menu_add") in labels
    assert t("ar", "menu_add") not in labels
