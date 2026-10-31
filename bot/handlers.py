"""PTB handlers: menu, add/confirm flows, queue, library, stats, language, guard.

All user-facing text goes through :func:`bot.i18n.t`. Callback data is kept short
(<=64 bytes); per-user lookups live in ``context.user_data`` under stable keys.
Unknown or stale callbacks fall back to the main menu. One bad probe or download is
always rendered as a short, user-safe error — handlers never raise into the framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import plex, probe as probe_mod
from .config import Config
from .i18n import LanguageStore, t, user_display
from .probe import Entry, ProbeError, UrlInfo
from .store import Archive, Library, Stats
from .updater import Updater
from .ytdlp import DownloadManager, Job

URL_RE = re.compile(r"^\s*(https?://\S+)\s*$", re.I)


@dataclass
class Services:
    config: Config
    lang_store: LanguageStore
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


def _answer(q: Update) -> None:
    try:
        q.callback_query.answer()
    except Exception:
        pass


# --- main menu --------------------------------------------------------------

def main_menu_keyboard(s: Services, lang: str) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(t(lang, "menu_add"), callback_data="m_add")],
        [
            InlineKeyboardButton(t(lang, "menu_queue"), callback_data="m_queue"),
            InlineKeyboardButton(t(lang, "menu_library"), callback_data="m_lib"),
        ],
        [
            InlineKeyboardButton(t(lang, "menu_stats"), callback_data="m_stats"),
            InlineKeyboardButton(t(lang, "menu_lang"), callback_data="m_lang"),
        ],
    ]
    return InlineKeyboardMarkup(kb)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update, context):
        return
    s = svc(context)
    text = t(lang_for(context, update), "menu_title")
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=main_menu_keyboard(s, lang_for(context, update))
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=main_menu_keyboard(s, lang_for(context, update))
        )


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

    context.user_data["pending"] = {"info": info, "page": 0}
    try:
        await _render_confirm(update, context, info)
    except Exception:
        await status.edit_text(L(context, update, "error_generic"))


def _audio_label(s: Services, lang: str, info: UrlInfo) -> str:
    if not info.audio_tracks:
        return t(lang, "audio_none")
    parts = [f"{a.language} · {a.codec}" for a in info.audio_tracks[:8]]
    return ", ".join(parts)


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
    update: Update, context: ContextTypes.DEFAULT_TYPE, info: UrlInfo, page: int = 0
) -> None:
    s = svc(context)
    lang = lang_for(context, update)
    lines = _detail_lines(context, update, info)

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

        rows: list[list[InlineKeyboardButton]] = []
        rows.append(
            [
                InlineKeyboardButton(
                    t(lang, "btn_download_all_n", n=n), callback_data="da"
                )
            ]
        )
        if s.config.multi_audio:
            rows.append([InlineKeyboardButton(t(lang, "btn_best_audio"), callback_data="db")])
        for i, e in enumerate(chunk):
            idx = start + i
            label = f"{idx + 1}. {e.title[:30]}"
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
        rows = []
        if s.config.multi_audio:
            rows.append(
                [
                    InlineKeyboardButton(t(lang, "btn_download_all"), callback_data="da"),
                    InlineKeyboardButton(t(lang, "btn_best_audio"), callback_data="db"),
                ]
            )
        else:
            rows.append([InlineKeyboardButton(t(lang, "btn_download_all"), callback_data="db")])
        rows.append([InlineKeyboardButton(t(lang, "btn_cancel"), callback_data="c")])
        markup = InlineKeyboardMarkup(rows)

    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
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
) -> Job | None:
    """Enqueue one job and create its single status message. None if already archived."""
    s = svc(context)
    key = f"{info.site}:{info.id}"
    if s.archive.contains(key):
        return None
    job = s.manager.enqueue(
        info, requestor=req, audio_mode=audio_mode, url=info.url
    )
    job.lang = lang
    job.bot = context.bot
    job.chat_id = user_id(update)
    # the one status message the manager will edit through the lifecycle
    text = t(lang, "queued", pos=job.position)
    msg = await update.effective_message.reply_text(text)
    job.msg_id = msg.message_id
    return job


async def _enqueue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    audio_mode: str,
    entry_idx: int | None = None,
) -> None:
    s = svc(context)
    pending = context.user_data.get("pending") or {}
    base_info: UrlInfo = pending["info"]
    req = _requestor(update)
    lang = lang_for(context, update)

    if entry_idx is not None:
        # a single playlist entry
        job = await _add_job(update, context, _target_info(context, entry_idx), req, lang, audio_mode)
        context.user_data["pending"] = None
        if job is None:
            await _reply(update, context, "already_in_library")
        return

    if not base_info.is_playlist:
        # single video
        job = await _add_job(update, context, base_info, req, lang, audio_mode)
        context.user_data["pending"] = None
        if job is None:
            await _reply(update, context, "already_in_library")
        return

    # playlist: fan out to one independently cancellable job per entry
    added = skipped = 0
    for i in range(len(base_info.entries)):
        job = await _add_job(update, context, _target_info(context, i), req, lang, audio_mode)
        if job is None:
            skipped += 1
        else:
            added += 1
    context.user_data["pending"] = None
    if added and skipped:
        await _reply(update, context, "added_many_skip", n=added, m=skipped)
    elif added:
        await _reply(update, context, "added_many", n=added)
    else:
        await _reply(update, context, "already_in_library")


async def _reply(update, context, key: str, **kw) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(L(context, update, key, **kw))
    elif update.effective_message:
        await update.effective_message.reply_text(L(context, update, key, **kw))


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

        # confirm flow
        if data in ("da", "db"):
            mode = "all" if data == "da" else "best"
            if not s.config.multi_audio:
                mode = "best"
            await _enqueue(update, context, mode)
            return
        if data.startswith("e") and data[1:].isdigit():
            idx = int(data[1:])
            mode = "all" if s.config.multi_audio else "best"
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
            await _reply(update, context, "cancelled")
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

        # language
        if data in ("lang_en", "lang_ar"):
            code = data.split("_", 1)[1]
            s.lang_store.set(user_id(update), code)
            await _reply(update, context, "lang_set", lang=code)
            return

        # fallback -> menu
        await cmd_menu(update, context)
    except Exception:
        _answer(update)
        try:
            if q and q.message:
                await q.message.reply_text(L(context, update, "error_generic"))
        except Exception:
            pass


# --- registration ----------------------------------------------------------

def register(app, s: Services) -> None:
    app.add_handler(CommandHandler("start", cmd_menu))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("queue", render_queue))
    app.add_handler(CommandHandler("library", render_library))
    app.add_handler(CommandHandler("stats", render_stats))
    app.add_handler(CommandHandler("lang", render_lang))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
