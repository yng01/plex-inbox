"""PTB handlers: menu, add/confirm flows, queue, library, stats, language, guard.

All user-facing text goes through :func:`bot.i18n.t`. Callback data is kept short
(<=64 bytes); per-user lookups live in ``context.user_data`` under stable keys.
Unknown or stale callbacks fall back to the main menu. One bad probe or download is
always rendered as a short, user-safe error — handlers never raise into the framework.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

log = logging.getLogger("plexinbox")

from . import plex, probe as probe_mod
from .config import Config
from .i18n import LanguageStore, t, user_display
from .probe import Entry, ProbeError, UrlInfo
from .settings import AUDIO_MODES, QUALITIES, SettingsStore
from .store import Archive, Library, Stats, dedupe_key
from .updater import Updater
from .ytdlp import VIDEO_EXTS, DownloadManager, Job

URL_RE = re.compile(r"^\s*(https?://\S+)\s*$", re.I)

# How long to wait for the Bot API to hand us an uploaded file. With a local Bot
# API Server, getFile blocks until the *whole* file is on disk, so a large video
# (hundreds of MB) needs far more than PTB's 5 s default. 1 hour keeps genuinely
# slow transfers alive while a dead one still eventually surfaces.
FILE_FETCH_TIMEOUT = 3600.0


@dataclass
class Services:
    config: Config
    lang_store: LanguageStore
    settings_store: SettingsStore
    probe_spawner: object
    archive: Archive
    library: Library
    stats: Stats
    manager: DownloadManager
    updater: Updater
    http: object | None


def svc(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.bot_data["svc"]


def user_id(update: Update) -> int:
    return update.effective_user.id


def lang_for(context: ContextTypes.DEFAULT_TYPE, update: Update) -> str:
    return svc(context).lang_store.get(user_id(update))


def L(context: ContextTypes.DEFAULT_TYPE, update: Update, key: str, **kw) -> str:
    return t(lang_for(context, update), key, **kw)


def allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Whitelist guard. Returns True if the sender may proceed.

    Non-whitelisted senders are dropped silently; with DEBUG_ACCESS the bot replies
    with their numeric ID to make onboarding painless.
    """
    s = svc(context)
    uid = user_id(update)
    if s.config.is_allowed(uid):
        return True
    if s.config.debug_access:
        user = update.effective_user
        uname = f" (@{user.username})" if getattr(user, "username", None) else ""
        msg = L(context, update, "debug_id", id=uid)
        if uname:
            msg += uname
        if update.effective_message:
            update.effective_message.reply_text(msg)
        elif update.callback_query:
            update.callback_query.answer()
            update.callback_query.message.reply_text(msg)
    return False


async def _answer(q: Update) -> None:
    try:
        await q.callback_query.answer()
    except Exception:
        pass


# --- main menu --------------------------------------------------------------

def main_menu_reply_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """The menu as a *reply keyboard* — a button bar that stays pinned above the
    input box. That makes the menu always visible (no scrolling back up to reach
    it) and, because a reply keyboard is sticky chat state rather than a message,
    it can never accumulate into duplicate menu messages. Tapping a button sends
    its label as plain text, which :func:`on_text` routes to the matching screen.
    """
    kb = [
        [KeyboardButton(t(lang, "menu_add"))],
        [KeyboardButton(t(lang, "menu_queue")), KeyboardButton(t(lang, "menu_library"))],
        [KeyboardButton(t(lang, "menu_stats")), KeyboardButton(t(lang, "menu_lang"))],
        [KeyboardButton(t(lang, "menu_settings"))],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=False)


# A reply-keyboard button arrives as the label it was shown with, in whatever
# language the menu was rendered in. Map both languages' labels to an action so
# a tap is routed correctly no matter when the user last switched language.
_MENU_LABELS: dict[str, str] = {}
for _lang in ("en", "ar"):
    _MENU_LABELS[t(_lang, "menu_add")] = "add"
    _MENU_LABELS[t(_lang, "menu_queue")] = "queue"
    _MENU_LABELS[t(_lang, "menu_library")] = "library"
    _MENU_LABELS[t(_lang, "menu_stats")] = "stats"
    _MENU_LABELS[t(_lang, "menu_lang")] = "lang"
    _MENU_LABELS[t(_lang, "menu_settings")] = "settings"


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Re)show the menu on ONE canonical message per chat, edited in place.

    Repeated ``/menu``/``/start`` or back-to-menu taps never create a second menu
    message: if a menu message is already known for this chat it is edited in
    place, and only when that message is gone (deleted, or too old to edit) is a
    fresh one sent and remembered. The reply keyboard itself is sticky, so once
    installed the menu stays pinned and visible without being re-sent.
    """
    if not allowed(update, context):
        return
    lang = lang_for(context, update)
    text = t(lang, "menu_title")
    kb = main_menu_reply_keyboard(lang)
    chat = update.effective_chat
    chat_id = chat.id if chat is not None else None

    stored = context.chat_data.get("menu_msg_id") if chat_id is not None else None
    if stored is not None:
        try:
            await context.bot.edit_message_text(chat_id, stored, text, reply_markup=kb)
            return
        except Exception:
            # the remembered message is gone (deleted / "message is too old") —
            # fall through and (re)send a fresh menu, then remember the new id
            context.chat_data.pop("menu_msg_id", None)

    if update.callback_query:
        cq = update.callback_query
        await cq.edit_message_text(text, reply_markup=kb)
        context.chat_data["menu_msg_id"] = cq.message.message_id
    elif update.effective_message:
        msg = await update.effective_message.reply_text(text, reply_markup=kb)
        context.chat_data["menu_msg_id"] = msg.message_id


# --- add flow ---------------------------------------------------------------

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update, context):
        return
    context.user_data["pending"] = None
    await _reply(update, context, "add_prompt")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update, context):
        return
    msg = update.effective_message
    text = (msg.text or "").strip()
    # a tap on the persistent reply-keyboard menu arrives as the button's label
    # (plain text) — route it to the matching screen before URL handling
    action = _MENU_LABELS.get(text)
    if action is not None:
        if action == "add":
            await cmd_add(update, context)
        elif action == "queue":
            await render_queue(update, context)
        elif action == "library":
            context.user_data["lib_stack"] = []
            await render_library(update, context)
        elif action == "stats":
            await render_stats(update, context)
        elif action == "lang":
            await render_lang(update, context)
        elif action == "settings":
            await render_settings(update, context)
        return
    m = URL_RE.match(text)
    if not m:
        await _reply(update, context, "not_a_url")
        return
    url = m.group(1)
    s = svc(context)
    # show "probing", then replace with the confirm screen (or an error)
    status = await msg.reply_text(L(context, update, "probing"))
    try:
        info = await probe_mod.run_probe(s.probe_spawner, url)
    except ProbeError as e:
        key = {
            "unsupported": "error_probe_unsupported",
            "private": "error_probe_private",
            "region": "error_probe_region",
            "unavailable": "error_probe_unavailable",
            "timeout": "error_probe_timeout",
        }.get(e.code, "error_generic")
        await status.edit_text(L(context, update, key))
        return
    except Exception:
        await status.edit_text(L(context, update, "error_generic"))
        return

    if not info.is_playlist and (
        s.archive.contains(dedupe_key(info.site, info.id, False))
        and s.archive.contains(dedupe_key(info.site, info.id, True))
    ):
        # the video *and* the audio-only version are already in the library —
        # there is nothing left to offer, so close in place without a confirm
        # screen (whose buttons would all just report "skipped")
        await status.edit_text(L(context, update, "already_in_library"))
        return

    settings = s.settings_store.get(user_id(update))

    if settings.auto_download:
        # the user opted out of being asked: start the download now, using their
        # saved audio mode + quality, and reuse the in-flight status message as
        # the job's (no dangling "probing" message, no second message).
        mode = settings.audio_mode
        if mode == "all" and not s.config.multi_audio:
            mode = "orig"  # "all audio tracks" only exists with MULTI_AUDIO
        req = _requestor(update)
        lang = lang_for(context, update)
        if info.is_playlist:
            context.user_data["pending"] = {"info": info, "page": 0, "quality": settings.quality}
            await _enqueue(update, context, mode, status_msg=status)
        else:
            job = await _add_job(
                update, context, info, req, lang, mode,
                status_msg=status, quality=settings.quality,
            )
            if job is None:  # already archived (pre-check race)
                await status.edit_text(L(context, update, "already_in_library"))
        return

    # "ask me every time" (the default): the confirm screen, pre-filled with the
    # user's default quality
    context.user_data["pending"] = {"info": info, "page": 0, "quality": settings.quality}
    try:
        # edit the "🔎 Fetching…" status message *in place* into the confirm
        # screen (never send a second message and leave the status dangling)
        await _render_confirm(update, context, info, status_msg=status)
    except Exception:
        await status.edit_text(L(context, update, "error_generic"))


def _audio_label(s: Services, lang: str, info: UrlInfo) -> str:
    if not info.audio_tracks:
        return t(lang, "audio_none")
    parts = [f"{a.language} · {a.codec}" for a in info.audio_tracks[:8]]
    return ", ".join(parts)


def _audio_mode_label(lang: str, mode: str) -> str:
    """Human label for an audio mode (settings + confirm screen)."""
    return t(lang, {"all": "mode_all", "orig": "mode_orig", "audio": "mode_audio"}.get(mode, "mode_orig"))


def _quality_label(lang: str, quality: int | None) -> str:
    """Human label for a quality choice (``None`` = best available)."""
    if quality is None:
        return t(lang, "q_best")
    return f"{quality}p"


def _quality_options() -> list[int | None]:
    """Quality choices offered: best first, then the heights in descending order."""
    return [None, *sorted(QUALITIES, reverse=True)]


def _detail_lines(context, update, info: UrlInfo) -> list[str]:
    lang = lang_for(context, update)
    lines = [f"{t(lang, 'detail_title')}: {info.title}"]
    lines.append(f"{t(lang, 'detail_site')}: {info.site}")
    if info.channel and info.channel != "unknown":
        lines.append(f"{t(lang, 'detail_channel')}: {info.channel}")
    if info.duration:
        lines.append(f"{t(lang, 'detail_duration')}: {plex.format_duration(info.duration)}")
    if info.resolution:
        lines.append(f"{t(lang, 'detail_resolution')}: {info.resolution}")
    lines.append(f"{t(lang, 'detail_audio')}: {_audio_label(svc(context), lang, info)}")
    return lines


async def _render_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    info: UrlInfo,
    page: int = 0,
    status_msg: object | None = None,
) -> None:
    s = svc(context)
    lang = lang_for(context, update)
    pending = context.user_data.get("pending") or {}
    sel_q = pending.get("quality")
    lines = _detail_lines(context, update, info)

    def _quality_row() -> list[InlineKeyboardButton]:
        # video-resolution cap; the ✓ marks the current choice (best by default).
        # Only meaningful for video downloads — audio-only ignores it.
        return [
            InlineKeyboardButton(
                ("✓ " if sel_q == q else "") + _quality_label(lang, q),
                callback_data="qbest" if q is None else f"q{q}",
            )
            for q in _quality_options()
        ]

    if info.is_playlist:
        n = len(info.entries)
        if n > 20:
            lines.insert(0, t(lang, "playlist_warn", n=n))
        page_size = 5
        pages = max(1, (n + page_size - 1) // page_size)
        page = max(0, min(page, pages - 1))
        context.user_data["pending"]["page"] = page
        start = page * page_size
        chunk = info.entries[start : start + page_size]

        rows: list[list[InlineKeyboardButton]] = [_quality_row()]
        rows.append(
            [
                InlineKeyboardButton(
                    t(lang, "btn_download_all_n", n=n), callback_data="da"
                )
            ]
        )
        if s.config.multi_audio:
            rows.append([InlineKeyboardButton(t(lang, "btn_original_audio"), callback_data="db")])
        rows.append([InlineKeyboardButton(t(lang, "btn_audio_only_n", n=n), callback_data="ao")])
        for i, e in enumerate(chunk):
            idx = start + i
            label = f"{idx + 1}. {e.title[:30]}"
            if (
                s.archive.contains(dedupe_key(info.site, e.id, False))
                and s.archive.contains(dedupe_key(info.site, e.id, True))
            ):
                # both the video and the audio are already archived — mark it
                label = f"✓ {label}"
            rows.append([InlineKeyboardButton(label, callback_data=f"e{idx}")])
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(t(lang, "btn_back"), callback_data="bp"))
            if page < pages - 1:
                nav.append(InlineKeyboardButton(t(lang, "btn_next"), callback_data="np"))
            if nav:
                rows.append(nav)
        rows.append([InlineKeyboardButton(t(lang, "btn_cancel"), callback_data="c")])
        markup = InlineKeyboardMarkup(rows)
    else:
        # buttons for modes that are *not yet* in the library: an already-
        # downloaded mode would just report "skipped", so hide it instead
        video_done = s.archive.contains(dedupe_key(info.site, info.id, False))
        audio_done = s.archive.contains(dedupe_key(info.site, info.id, True))
        rows = [_quality_row()]
        if not video_done:
            if s.config.multi_audio:
                rows.append(
                    [
                        InlineKeyboardButton(t(lang, "btn_download_all"), callback_data="da"),
                        InlineKeyboardButton(t(lang, "btn_original_audio"), callback_data="db"),
                    ]
                )
            else:
                # single-track mode: original audio (the only option without MULTI_AUDIO)
                rows.append([InlineKeyboardButton(t(lang, "btn_original_audio"), callback_data="db")])
        if not audio_done:
            rows.append([InlineKeyboardButton(t(lang, "btn_audio_only"), callback_data="ao")])
        rows.append([InlineKeyboardButton(t(lang, "btn_cancel"), callback_data="c")])
        markup = InlineKeyboardMarkup(rows)

    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    elif status_msg is not None:
        # replace the "🔎 Fetching…" status message in place (a second message
        # would leave the status one dangling and looking stuck)
        await status_msg.edit_text(text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup)


def _target_info(context, entry_idx: int | None) -> UrlInfo:
    pending = context.user_data.get("pending") or {}
    info: UrlInfo = pending["info"]
    if entry_idx is None or not info.is_playlist:
        return info
    entry: Entry = info.entries[entry_idx]
    return UrlInfo(
        id=entry.id,
        title=entry.title,
        site=info.site,
        url=entry.url,
        channel=entry.channel or info.channel,
        duration=entry.duration,
        is_playlist=False,
        raw=dict(info.raw),
    )


def _requestor(update: Update) -> str:
    u = update.effective_user
    return user_display(getattr(u, "username", None), u.id)


async def _add_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    info: UrlInfo,
    req: str,
    lang: str,
    audio_mode: str,
    source_path: Path | None = None,
    status_msg: object | None = None,
    quality: int | None = None,
) -> Job | None:
    """Enqueue one job and create its single status message. None if already archived.

    ``source_path`` marks the job as a local-file archive (a video sent to the bot)
    instead of a URL download. ``status_msg``, when given, is an already-sent
    message (e.g. the "receiving…" reply) that becomes the job's status message so
    the user sees one continuous message rather than two. ``quality`` is the
    height cap (``None`` = best available) for video downloads.
    """
    s = svc(context)
    # mode-scoped key: an audio-only download of this item (or of a sibling one)
    # must not block the video, and vice versa
    key = dedupe_key(info.site, info.id, audio_mode == "audio")
    if s.archive.contains(key):
        return None
    job = s.manager.enqueue(
        info,
        requestor=req,
        audio_mode=audio_mode,
        url=info.url,
        source_path=source_path,
        quality=quality,
    )
    job.lang = lang
    job.bot = context.bot
    job.chat_id = user_id(update)
    job.user_id = user_id(update)
    job.username = update.effective_user.username
    # the one status message the manager will edit through the lifecycle
    text = t(lang, "queued", pos=job.position)
    if status_msg is not None:
        job.msg_id = status_msg.message_id
        await status_msg.edit_text(text)
    else:
        msg = await update.effective_message.reply_text(text)
        job.msg_id = msg.message_id
    return job


async def _enqueue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    audio_mode: str,
    entry_idx: int | None = None,
    status_msg: object | None = None,
) -> None:
    s = svc(context)
    pending = context.user_data.get("pending") or {}
    base_info: UrlInfo = pending["info"]
    quality = pending.get("quality")
    req = _requestor(update)
    lang = lang_for(context, update)

    if entry_idx is not None:
        # a single playlist entry
        job = await _add_job(update, context, _target_info(context, entry_idx), req, lang, audio_mode, quality=quality)
        context.user_data["pending"] = None
        if job is None:
            await _close_confirm(update, context, "already_in_library")
        else:
            await _close_confirm(update, context)
        return

    if not base_info.is_playlist:
        # single video
        job = await _add_job(update, context, base_info, req, lang, audio_mode, quality=quality)
        context.user_data["pending"] = None
        if job is None:
            await _close_confirm(update, context, "already_in_library")
        else:
            await _close_confirm(update, context)
        return

    # playlist: fan out to one independently cancellable job per entry
    added = skipped = 0
    for i in range(len(base_info.entries)):
        job = await _add_job(update, context, _target_info(context, i), req, lang, audio_mode, quality=quality)
        if job is None:
            skipped += 1
        else:
            added += 1
    context.user_data["pending"] = None
    if added and skipped:
        await _close_confirm(update, context, "added_many_skip", n=added, m=skipped, status_msg=status_msg)
    elif added:
        await _close_confirm(update, context, "added_many", n=added, status_msg=status_msg)
    else:
        await _close_confirm(update, context, "already_in_library", status_msg=status_msg)


async def _reply(update, context, key: str, **kw) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(L(context, update, key, **kw))
    elif update.effective_message:
        await update.effective_message.reply_text(L(context, update, key, **kw))


async def _close_confirm(
    update, context, key: str | None = None, status_msg: object | None = None, **kw
) -> None:
    """Close the confirm screen once a choice has been made.

    ``edit_message_text`` with no ``reply_markup`` (PTB drops ``None`` from the
    request) leaves the existing keyboard in place, so the stale audio/download/
    cancel buttons would stay visible and clickable; an empty
    ``InlineKeyboardMarkup`` removes it. Without ``key`` the current text (the
    item details) is kept.

    ``status_msg`` is the auto-download path's stand-in for the confirm screen:
    when there is no callback query (the user sent a link and auto-download is on)
    the result is written to that in-flight status message instead.
    """
    cq = update.callback_query
    if cq and cq.message:
        text = L(context, update, key, **kw) if key else (cq.message.text or "")
        await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup([]))
    elif status_msg is not None and key is not None:
        await status_msg.edit_text(L(context, update, key, **kw))


# --- video files (sent to the bot) ------------------------------------------

# mime-type -> extension for uploaded media (a subset Telegram actually serves)
_MIME_EXT = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "video/x-m4v": "m4v",
    "video/x-msvideo": "avi",
    "video/x-flv": "flv",
    "video/ogg": "ogv",
    "video/mpeg": "ts",
}


def _ext_for(video) -> str:
    """Best extension for the uploaded file (original name, else mime, else mp4)."""
    name = getattr(video, "file_name", None) or ""
    if name:
        suffix = Path(name).suffix.lower().lstrip(".")
        if suffix in VIDEO_EXTS:
            return suffix
    mime = (getattr(video, "mime_type", None) or "").split(";")[0].strip().lower()
    ext = _MIME_EXT.get(mime)
    if ext and ext in VIDEO_EXTS:
        return ext
    return "mp4"


class VideoTooBig(Exception):
    """Telegram rejected the file — the standard Bot API caps bot downloads at 20 MB."""


def _is_too_big(exc: BaseException) -> bool:
    return "too big" in str(exc).lower()


async def _download_video_file(context, video) -> Path | None:
    """Fetch the uploaded media to a fresh temp file. ``None`` on any failure.

    Uses ``video.get_file()`` -> ``File.download_to_drive(custom_path=...)``. When a
    local Bot API Server is configured, ``get_file`` blocks until the file is on disk
    (hence the long timeout) and ``download_to_drive`` is then a plain local copy;
    otherwise PTB streams the bytes from the Bot API (20 MB cap — surfaced as
    :class:`VideoTooBig` with a friendlier message).
    """
    try:
        tgfile = await video.get_file(read_timeout=FILE_FETCH_TIMEOUT)
    except Exception as exc:
        log.warning("video get_file failed: %s", exc)
        if _is_too_big(exc):
            raise VideoTooBig() from exc
        return None
    tmpdir = Path(tempfile.mkdtemp(prefix="plexinbox-up-"))
    dest = tmpdir / f"upload.{_ext_for(video)}"
    try:
        result = await tgfile.download_to_drive(
            custom_path=dest, read_timeout=FILE_FETCH_TIMEOUT
        )
    except Exception as exc:
        log.warning("video download failed: %s", exc)
        shutil.rmtree(tmpdir, ignore_errors=True)
        if _is_too_big(exc):
            raise VideoTooBig() from exc
        return None
    out = Path(result) if result else dest
    if not out.is_file():
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None
    return out


def _video_info(video, req: str, lang: str) -> UrlInfo:
    """Metadata for an uploaded file (there is no remote probe to run).

    The sender is the "channel" (who provided the content); the file's
    ``file_unique_id`` is a stable id that makes re-sending the same file a no-op.
    """
    name = getattr(video, "file_name", None) or ""
    stem = Path(name).stem if name else ""
    title = stem or t(lang, "video_default_title")
    file_id = getattr(video, "file_id", "") or ""
    uid = getattr(video, "file_unique_id", None) or file_id or "local"
    width = getattr(video, "width", None)
    height = getattr(video, "height", None)
    return UrlInfo(
        id=str(uid),
        title=title,
        site="Upload",
        url=f"telegram://file/{file_id}" if file_id else "telegram://upload",
        channel=req,
        duration=getattr(video, "duration", None),
        resolution=f"{width}x{height}" if (width and height) else None,
        year=datetime.now(timezone.utc).year,
        audio_tracks=[],
        is_playlist=False,
        raw={
            "file_id": file_id,
            "file_unique_id": str(uid),
            "mime_type": getattr(video, "mime_type", None),
            "file_size": getattr(video, "file_size", None),
        },
    )


def _rm_tree(path: Path | None) -> None:
    """Best-effort removal of a temp file and its (now empty) parent dir."""
    if not path:
        return
    p = Path(path)
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()
            parent = p.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Archive a video (or video note) sent directly to the bot.

    The media is pulled to a temp file, then routed through the *same* archive
    flow as a link (queue, single status message, dedupe, sidecar, notification) —
    the worker copies it into the library instead of invoking yt-dlp.
    """
    if not allowed(update, context):
        return
    msg = update.effective_message
    if msg is None:
        return
    video = getattr(msg, "video", None) or getattr(msg, "video_note", None)
    if video is None:
        return
    lang = lang_for(context, update)
    req = _requestor(update)
    # Dedupe *before* the (potentially huge) download. The dedupe id is the file's
    # file_unique_id, which comes from the message metadata (no transfer), so a
    # re-sent duplicate is skipped with a single reply instead of re-streaming the
    # whole file — mirroring the URL flow, which dedupes right after the cheap probe
    # and before the expensive download.
    info = _video_info(video, req, lang)
    if svc(context).archive.contains(dedupe_key(info.site, info.id, False)):
        await msg.reply_text(t(lang, "already_in_library"))
        return
    # immediate feedback; this message becomes the job's single status message
    status = await msg.reply_text(t(lang, "video_receiving"))
    try:
        tmp = await _download_video_file(context, video)
    except VideoTooBig:
        await status.edit_text(t(lang, "video_too_big"))
        return
    if tmp is None:
        await status.edit_text(t(lang, "video_error"))
        return
    try:
        job = await _add_job(
            update,
            context,
            info,
            req,
            lang,
            "all",
            source_path=tmp,
            status_msg=status,
        )
    except Exception as exc:
        log.warning("on_video: enqueue failed: %s", exc, exc_info=exc)
        _rm_tree(tmp)
        await status.edit_text(t(lang, "video_error"))
        return
    if job is None:  # rare race: archived between our pre-check and the enqueue
        _rm_tree(tmp)
        await status.edit_text(t(lang, "already_in_library"))


# --- queue view -------------------------------------------------------------

def _job_line(s: Services, lang: str, job) -> str:
    if job.state == "downloading":
        return t(lang, "job_running", title=job.title[:40], pct=int(job.pct))
    if job.state == "queued":
        return t(lang, "job_queued", title=job.title[:40])
    if job.state == "done":
        return t(lang, "job_done", title=job.title[:40])
    if job.state == "failed":
        return t(lang, "job_failed", title=job.title[:40], reason=job.error[:20])
    return f"• {job.title[:40]}"


async def render_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    lang = lang_for(context, update)
    jobs = s.manager.active_jobs()
    if not jobs:
        await _reply(update, context, "no_jobs")
        return
    lines = [t(lang, "queue_title")]
    code_map = {}
    for i, job in enumerate(jobs):
        code = f"j{i}"
        code_map[code] = job.id
        lines.append(_job_line(s, lang, job))
    context.user_data["job_codes"] = code_map
    rows = []
    for i, job in enumerate(jobs):
        rows.append(
            [
                InlineKeyboardButton(
                    t(lang, "job_cancel"), callback_data=f"cxj{i}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(t(lang, "queue_menu"), callback_data="mmenu")])
    markup = InlineKeyboardMarkup(rows)
    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=markup)
    else:
        await update.effective_message.reply_text("\n".join(lines), reply_markup=markup)


# --- library ---------------------------------------------------------------

async def render_library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    lang = lang_for(context, update)
    stack: list[str] = context.user_data.get("lib_stack", [])

    if not stack:
        sites = s.library.sites()
        if not sites:
            await _reply(update, context, "library_empty")
            return
        lines = [t(lang, "library_title")]
        code_map = {}
        rows = []
        for i, (site, n) in enumerate(sites):
            code = f"s{i}"
            code_map[code] = site
            lines.append(t(lang, "library_site", site=site, n=n))
            rows.append([InlineKeyboardButton(t(lang, "library_site", site=site, n=n), callback_data=f"lsc{code}")])
        context.user_data["lib_sites"] = code_map
        rows.append([InlineKeyboardButton(t(lang, "library_menu"), callback_data="lmenu")])
        markup = InlineKeyboardMarkup(rows)
    else:
        site = stack[0]
        if len(stack) == 1:
            chans = s.library.channels(site)
            lines = [t(lang, "library_title"), t(lang, "library_site", site=site, n=len(chans))]
            code_map = {}
            rows = []
            for i, (ch, n) in enumerate(chans):
                code = f"c{i}"
                code_map[code] = ch
                label = t(lang, "library_channel", channel=ch, n=n)
                lines.append(label)
                rows.append([InlineKeyboardButton(label, callback_data=f"lcc{code}")])
            context.user_data["lib_chans"] = code_map
            rows.append([InlineKeyboardButton(t(lang, "library_back"), callback_data="lback")])
            rows.append([InlineKeyboardButton(t(lang, "library_menu"), callback_data="lmenu")])
            markup = InlineKeyboardMarkup(rows)
        else:
            ch = stack[1]
            files = s.library.files(site, ch)
            lines = [t(lang, "library_title"), t(lang, "library_site", site=site, n=1), t(lang, "library_channel", channel=ch, n=len(files))]
            for f in files:
                audio = ",".join(f.audio) if f.audio else t(lang, "library_file_audio_none")
                if len(f.audio) == 1:
                    audio = t(lang, "library_file_audio_single")
                lines.append(t(lang, "library_file", name=f.name[:60], size=_size(f.size_bytes), date=f.date[:10] or "?", audio=audio))
            rows = [
                [InlineKeyboardButton(t(lang, "library_back"), callback_data="lback")],
                [InlineKeyboardButton(t(lang, "library_menu"), callback_data="lmenu")],
            ]
            markup = InlineKeyboardMarkup(rows)

    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=markup)
    else:
        await update.effective_message.reply_text("\n".join(lines), reply_markup=markup)


def _size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}B"


# --- stats -----------------------------------------------------------------

async def render_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    lang = lang_for(context, update)
    st = s.stats.compute(queue_length=s.manager.queue_length() + len(s.manager.active_jobs()))
    lines = [t(lang, "stats_title")]
    lines.append(t(lang, "stats_total", n=st["total_downloads"]))
    lines.append(t(lang, "stats_size", size=_size(st["total_size_bytes"])))
    lines.append(t(lang, "stats_sites"))
    if st["per_site"]:
        for site, n in st["per_site"].items():
            lines.append(t(lang, "stats_site", site=site, n=n))
    else:
        lines.append(t(lang, "stats_none"))
    lines.append(t(lang, "stats_requestors"))
    if st["per_requestor"]:
        for who, n in st["per_requestor"].items():
            lines.append(t(lang, "stats_requestor", who=who, n=n))
    else:
        lines.append(t(lang, "stats_none"))
    lines.append(t(lang, "stats_queue", n=st["queue_length"]))
    cur = s.updater.state.current
    if cur:
        lines.append(t(lang, "stats_version", v=cur))
    checked = s.updater.state.checked_at
    if checked:
        lines.append(t(lang, "stats_updated", t=checked))
    lines.append("")
    lines.append(t(lang, "queue_menu"))
    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.effective_message.reply_text(text)


# --- language --------------------------------------------------------------

async def render_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    lang = lang_for(context, update)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "lang_en"), callback_data="lang_en"),
                InlineKeyboardButton(t(lang, "lang_ar"), callback_data="lang_ar"),
            ],
            [InlineKeyboardButton(t(lang, "menu_title"), callback_data="mmenu")],
        ]
    )
    text = t(lang, "lang_title")
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)


# --- settings (per-user default download options) ---------------------------

async def render_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's default download options and let them change them.

    The current values are shown as text; the ✓-marked buttons make changes.
    Each change re-renders this screen in place so the new state is visible.
    """
    s = svc(context)
    lang = lang_for(context, update)
    uid = user_id(update)
    st = s.settings_store.get(uid)
    lines = [
        t(lang, "settings_title"),
        "",
        t(lang, "settings_audio", label=_audio_mode_label(lang, st.audio_mode)),
        t(lang, "settings_quality", label=_quality_label(lang, st.quality)),
        t(lang, "settings_auto", label=t(lang, "on") if st.auto_download else t(lang, "off")),
        "",
        t(lang, "settings_hint"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                ("✓ " if st.audio_mode == m else "") + _audio_mode_label(lang, m),
                callback_data=f"sa_{m}",
            )
            for m in AUDIO_MODES
        ],
        [
            InlineKeyboardButton(
                ("✓ " if st.quality == q else "") + _quality_label(lang, q),
                callback_data="sqbest" if q is None else f"sq{q}",
            )
            for q in _quality_options()
        ],
        [
            InlineKeyboardButton(
                t(lang, "auto_on") if st.auto_download else t(lang, "auto_off"),
                callback_data="sauto",
            )
        ],
        [InlineKeyboardButton(t(lang, "menu_title"), callback_data="mmenu")],
    ]
    markup = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup)


# --- callback dispatcher ----------------------------------------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update, context):
        return
    s = svc(context)
    q = update.callback_query
    data = q.data or ""

    try:
        if data == "mmenu" or data.startswith("unknown"):
            await cmd_menu(update, context)
            return
        if data == "m_add":
            await cmd_add(update, context)
            return
        if data == "m_queue":
            await render_queue(update, context)
            return
        if data == "m_lib":
            context.user_data["lib_stack"] = []
            await render_library(update, context)
            return
        if data == "m_stats":
            await render_stats(update, context)
            return
        if data == "m_lang":
            await render_lang(update, context)
            return
        if data == "m_settings":
            await render_settings(update, context)
            return

        # confirm flow
        # quality choice: store it on the pending item and re-render so the ✓
        # moves to the tapped button (the mode buttons then use this quality)
        if data == "qbest":
            pending = context.user_data.get("pending")
            if pending is not None:
                pending["quality"] = None
                await _render_confirm(update, context, pending["info"])
            return
        if data.startswith("q") and data[1:].isdigit():
            pending = context.user_data.get("pending")
            qval = int(data[1:])
            if pending is not None and qval in QUALITIES:
                pending["quality"] = qval
                await _render_confirm(update, context, pending["info"])
            return
        if data in ("da", "db", "ao"):
            if data == "ao":
                mode = "audio"
            else:
                mode = "all" if data == "da" else "orig"
                if not s.config.multi_audio:
                    mode = "orig"
            await _enqueue(update, context, mode)
            return
        if data.startswith("e") and data[1:].isdigit():
            idx = int(data[1:])
            mode = "all" if s.config.multi_audio else "orig"
            await _enqueue(update, context, mode, entry_idx=idx)
            return
        if data == "np" or data == "bp":
            pending = context.user_data.get("pending") or {}
            info: UrlInfo = pending["info"]
            page = pending.get("page", 0)
            n = len(info.entries)
            pages = max(1, (n + 4) // 5)
            page = max(0, min(pages - 1, page + (1 if data == "np" else -1)))
            await _render_confirm(update, context, info, page=page)
            return
        if data == "c":
            context.user_data["pending"] = None
            await _close_confirm(update, context, "cancelled")
            return

        # queue cancel
        if data.startswith("cx"):
            code = data[2:]
            job_id = (context.user_data.get("job_codes") or {}).get(code)
            if job_id:
                await s.manager.cancel(job_id)
                q.answer()
            return

        # library nav
        if data == "lmenu":
            context.user_data["lib_stack"] = []
            await cmd_menu(update, context)
            return
        if data == "lback":
            stack = context.user_data.get("lib_stack", [])
            if len(stack) > 1:
                context.user_data["lib_stack"] = stack[:1]
            else:
                context.user_data["lib_stack"] = []
            await render_library(update, context)
            return
        if data.startswith("lsc"):
            code = data[3:]
            site = (context.user_data.get("lib_sites") or {}).get(code)
            if site:
                context.user_data["lib_stack"] = [site]
                await render_library(update, context)
            return
        if data.startswith("lcc"):
            code = data[3:]
            ch = (context.user_data.get("lib_chans") or {}).get(code)
            stack = context.user_data.get("lib_stack", [])
            if ch and stack:
                context.user_data["lib_stack"] = [stack[0], ch]
                await render_library(update, context)
            return

        # settings changes (each re-renders the settings screen in place)
        if data.startswith("sa_"):
            mode = data[3:]
            if mode in AUDIO_MODES:
                s.settings_store.set_audio_mode(user_id(update), mode)
                await render_settings(update, context)
            return
        if data == "sqbest":
            s.settings_store.set_quality(user_id(update), None)
            await render_settings(update, context)
            return
        if data.startswith("sq") and data[2:].isdigit():
            qval = int(data[2:])
            if qval in QUALITIES:
                s.settings_store.set_quality(user_id(update), qval)
            await render_settings(update, context)
            return
        if data == "sauto":
            cur = s.settings_store.get(user_id(update))
            s.settings_store.set_auto_download(user_id(update), not cur.auto_download)
            await render_settings(update, context)
            return

        # language
        if data in ("lang_en", "lang_ar"):
            code = data.split("_", 1)[1]
            s.lang_store.set(user_id(update), code)
            await _reply(update, context, "lang_set", lang=code)
            return

        # fallback -> menu
        await cmd_menu(update, context)
    except Exception as exc:
        log.warning("callback %r failed: %s", data, exc, exc_info=exc)
        await _answer(update)
        try:
            if q and q.message:
                await q.message.reply_text(L(context, update, "error_generic"))
        except Exception as exc2:
            log.warning("error reply after callback failure also failed: %s", exc2)


# --- last-resort error handling --------------------------------------------

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch anything that slips past the per-handler guards.

    Log the real error (server-side) and send a short, user-safe reply when we
    can identify the conversation. Never raises into the framework.
    """
    log.error("unhandled error: %s", context.error, exc_info=context.error)
    try:
        if not isinstance(update, Update):
            return
        msg = update.effective_message
        if msg is None:
            return
        s = svc(context)
        lang = s.lang_store.get(update.effective_user.id)
        await msg.reply_text(t(lang, "error_generic"))
    except Exception:
        pass


# --- registration ----------------------------------------------------------

def register(app, s: Services) -> None:
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", cmd_menu))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("queue", render_queue))
    app.add_handler(CommandHandler("library", render_library))
    app.add_handler(CommandHandler("stats", render_stats))
    app.add_handler(CommandHandler("lang", render_lang))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, on_video))
    app.add_handler(CallbackQueryHandler(on_callback))
