"""All user-facing strings, in English and Arabic.

Every string that a handler can show lives here as a key with both an ``en`` and an
``ar`` value. The :func:`t` helper selects the user's language and applies ``str.format``
kwargs. A completeness test (``tests/test_i18n.py``) enforces that every key is present
in both languages and that there are no orphan keys, so nothing can ship half-translated.

Nothing in the handlers hardcodes user-facing text — they all call :func:`t`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

LANGS: tuple[str, ...] = ("en", "ar")
FALLBACK_LANG = "en"

# key -> {en: ..., ar: ...}
# All values are final user-facing copy. Keep tags/labels lowercase where they are
# meant to be filtered (see plex.py).
STRINGS: dict[str, dict[str, str]] = {
    # --- Menu ---
    "menu_title": {
        "en": "📺 Video Archive",
        "ar": "📺 أرشيف الفيديو",
    },
    "menu_add": {
        "en": "➕ Add a video",
        "ar": "➕ إضافة فيديو",
    },
    "menu_queue": {
        "en": "🕒 Queue",
        "ar": "🕒 قائمة الانتظار",
    },
    "menu_library": {
        "en": "📚 Library",
        "ar": "📚 المكتبة",
    },
    "menu_stats": {
        "en": "📊 Stats",
        "ar": "📊 الإحصائيات",
    },
    "menu_lang": {
        "en": "🌐 Language",
        "ar": "🌐 اللغة",
    },

    # --- Add flow ---
    "add_prompt": {
        "en": "Paste a video, playlist, or channel URL (any site yt-dlp supports):",
        "ar": "ألصق رابط فيديو أو قائمة تشغيل أو قناة (أي موقع يدعمه yt-dlp):",
    },
    "probing": {
        "en": "🔎 Fetching video info…",
        "ar": "🔎 جاري جلب معلومات الفيديو…",
    },
    "not_a_url": {
        "en": "That doesn't look like a URL. Paste a link to a video, playlist, or channel.",
        "ar": "هذا لا يبدو رابطاً. ألصق رابط فيديو أو قائمة تشغيل أو قناة.",
    },
    "detail_title": {"en": "Title", "ar": "العنوان"},
    "detail_site": {"en": "Site", "ar": "الموقع"},
    "detail_channel": {"en": "Channel", "ar": "القناة"},
    "detail_duration": {"en": "Duration", "ar": "المدة"},
    "detail_resolution": {"en": "Quality", "ar": "الجودة"},
    "detail_audio": {"en": "Audio tracks", "ar": "مسارات الصوت"},
    "audio_none": {
        "en": "(single default track)",
        "ar": "(مسار افتراضي واحد)",
    },
    "btn_download_all": {
        "en": "⬇️ Download (all audio)",
        "ar": "⬇️ تحميل (كل المسارات)",
    },
    "btn_original_audio": {
        "en": "⬇️ Download (original audio)",
        "ar": "⬇️ تحميل (الصوت الأصلي)",
    },
    "btn_cancel": {"en": "❌ Cancel", "ar": "❌ إلغاء"},
    "btn_download_all_n": {
        "en": "⬇️ Download all {n}",
        "ar": "⬇️ تحميل الكل ({n})",
    },
    "btn_next": {"en": "⏭️ Next", "ar": "⏭️ التالي"},
    "btn_back": {"en": "⬅️ Back", "ar": "⬅️ رجوع"},
    "already_in_library": {
        "en": "✅ Already in your library — skipped.",
        "ar": "✅ موجود مسبقاً في مكتبتك — تم التجاهل.",
    },
    "added_one": {
        "en": "📥 Added to queue (position {pos}).",
        "ar": "📥 أُضيف إلى قائمة الانتظار (الموضع {pos}).",
    },
    "added_many": {
        "en": "📥 Added {n} videos to the queue.",
        "ar": "📥 أُضيف {n} فيديو إلى قائمة الانتظار.",
    },
    "added_many_skip": {
        "en": "📥 Added {n} to the queue; {m} already in library.",
        "ar": "📥 أُضيف {n} إلى قائمة الانتظار؛ {m} موجود مسبقاً في المكتبة.",
    },
    "playlist_warn": {
        "en": "⚠️ This playlist/channel has {n} entries. “Download all” will queue every one of them.",
        "ar": "⚠️ هذه القائمة/القناة تحتوي على {n} عنصر. «تحميل الكل» سيضيف جميعها إلى قائمة الانتظار.",
    },

    # --- Video file (uploaded to the bot) ---
    "video_receiving": {
        "en": "📥 Receiving your video…",
        "ar": "📥 جاري استلام الفيديو…",
    },
    "video_error": {
        "en": "😕 Couldn't receive that video file. Please try again.",
        "ar": "😕 تعذّر استلام ملف الفيديو. حاول مرة أخرى.",
    },
    "video_too_big": {
        "en": "😕 This video is over 20 MB, which the standard Bot API can't receive. Please send me a link to the video instead.",
        "ar": "😕 هذا الفيديو أكبر من 20 ميغابايت ولا يمكن استقباله عبر واجهة البوت القياسية. أرسل لي رابط الفيديو بدلاً من ذلك.",
    },
    "video_default_title": {
        "en": "Uploaded Video",
        "ar": "فيديو مرفوع",
    },

    # --- Download lifecycle ---
    "queued": {
        "en": "⏳ In queue (position {pos})…",
        "ar": "⏳ في قائمة الانتظار (الموضع {pos})…",
    },
    "downloading": {
        "en": "⬇️ Downloading {title}",
        "ar": "⬇️ جارٍ تنزيل {title}",
    },
    "progress": {
        "en": "📶 {pct}%  ·  {speed}  ·  ETA {eta}",
        "ar": "📶 {pct}%  ·  {speed}  ·  المتبقي {eta}",
    },
    "done": {
        "en": "✅ Done\n📁 {path}\n💾 {size}\n🎵 {tracks} audio track(s)",
        "ar": "✅ اكتمل\n📁 {path}\n💾 {size}\n🎵 عدد مسارات الصوت: {tracks}",
    },
    "failed": {
        "en": "⚠️ Failed: {reason}",
        "ar": "⚠️ فشل: {reason}",
    },
    "cancelled": {
        "en": "🚫 Cancelled — partial files removed.",
        "ar": "🚫 أُلغي — حُذفت الملفات الجزئية.",
    },

    # --- Queue view ---
    "queue_title": {"en": "🕒 Queue", "ar": "🕒 قائمة الانتظار"},
    "no_jobs": {"en": "🕒 Queue is empty.", "ar": "🕒 قائمة الانتظار فارغة."},
    "job_running": {
        "en": "⬇️ {title} — {pct}%",
        "ar": "⬇️ {title} — {pct}%",
    },
    "job_queued": {"en": "⏳ {title}", "ar": "⏳ {title}"},
    "job_done": {"en": "✅ {title}", "ar": "✅ {title}"},
    "job_failed": {
        "en": "⚠️ {title} — {reason}",
        "ar": "⚠️ {title} — {reason}",
    },
    "job_cancel": {"en": "🚫 Cancel", "ar": "🚫 إلغاء"},
    "queue_menu": {"en": "🏠 Menu", "ar": "🏠 القائمة"},

    # --- Library ---
    "library_title": {"en": "📚 Library", "ar": "📚 المكتبة"},
    "library_empty": {
        "en": "Your library is empty. Add a video to get started!",
        "ar": "مكتبتك فارغة. أضف فيديو للبدء!",
    },
    "library_site": {"en": "📺 {site} ({n})", "ar": "📺 {site} ({n})"},
    "library_channel": {"en": "📁 {channel} ({n})", "ar": "📁 {channel} ({n})"},
    "library_file": {
        "en": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "ar": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
    },
    "library_back": {"en": "⬆️ Up", "ar": "⬆️ أعلى"},
    "library_menu": {"en": "🏠 Menu", "ar": "🏠 القائمة"},
    "library_file_audio_single": {
        "en": "1 track",
        "ar": "مسار واحد",
    },
    "library_file_audio_none": {
        "en": "no track info",
        "ar": "لا توجد معلومات",
    },

    # --- Stats ---
    "stats_title": {"en": "📊 Stats", "ar": "📊 الإحصائيات"},
    "stats_total": {
        "en": "Total downloads: {n}",
        "ar": "إجمالي التنزيلات: {n}",
    },
    "stats_size": {
        "en": "Total size: {size}",
        "ar": "الحجم الإجمالي: {size}",
    },
    "stats_sites": {"en": "By site:", "ar": "حسب الموقع:"},
    "stats_site": {"en": "  • {site}: {n}", "ar": "  • {site}: {n}"},
    "stats_requestors": {
        "en": "By requestor:",
        "ar": "حسب المُضيف:",
    },
    "stats_requestor": {"en": "  • {who}: {n}", "ar": "  • {who}: {n}"},
    "stats_queue": {
        "en": "Queue: {n} job(s)",
        "ar": "قائمة الانتظار: {n} مهمة(ات)",
    },
    "stats_version": {
        "en": "yt-dlp: {v}",
        "ar": "yt-dlp: {v}",
    },
    "stats_updated": {
        "en": "Last update check: {t}",
        "ar": "آخر فحص للتحديث: {t}",
    },
    "stats_none": {"en": "  (none yet)", "ar": "  (لا يوجد بعد)"},

    # --- Language ---
    "lang_title": {
        "en": "🌐 Choose your language",
        "ar": "🌐 اختر لغتك",
    },
    "lang_en": {"en": "English", "ar": "English"},
    "lang_ar": {"en": "العربية", "ar": "العربية"},
    "lang_set": {
        "en": "✅ Language set to {lang}.",
        "ar": "✅ تم ضبط اللغة على {lang}.",
    },

    # --- Errors (user-safe, short) ---
    "error_generic": {
        "en": "😕 Something went wrong. Please try again.",
        "ar": "😕 حدث خطأ ما. حاول مرة أخرى.",
    },
    "error_probe_unsupported": {
        "en": "😕 That site isn't supported by yt-dlp.",
        "ar": "😕 هذا الموقع غير مدعوم في yt-dlp.",
    },
    "error_probe_private": {
        "en": "🔒 That video is private or requires login.",
        "ar": "🔒 هذا الفيديو خاص أو يتطلب تسجيل الدخول.",
    },
    "error_probe_region": {
        "en": "🌍 That video is region-locked.",
        "ar": "🌍 هذا الفيديو مقيد جغرافياً.",
    },
    "error_probe_unavailable": {
        "en": "🚫 That video is unavailable.",
        "ar": "🚫 هذا الفيديو غير متوفر.",
    },
    "error_probe_timeout": {
        "en": "⏱️ Timed out fetching info. Try again.",
        "ar": "⏱️ انتهت المهلة أثناء جلب المعلومات. حاول مرة أخرى.",
    },

    # --- Misc ---
    "debug_id": {
        "en": "🔧 Your Telegram user ID is {id}",
        "ar": "🔧 معرف مستخدم تيليجرام الخاص بك هو {id}",
    },
}


def known_keys() -> set[str]:
    return set(STRINGS)


def t(lang: str | None, key: str, **kwargs) -> str:
    """Return the localized string for ``key`` in ``lang`` (falls back to English).

    Unknown languages fall back to the default; unknown keys raise ``KeyError`` so a
    missing translation is a loud bug during development, not a silent ``{key}`` leak.
    """
    entry = STRINGS.get(key)
    if entry is None:
        raise KeyError(f"Unknown i18n key: {key!r}")
    l = lang if lang in LANGS else FALLBACK_LANG
    text = entry.get(l) or entry.get(FALLBACK_LANG) or ""
    if kwargs:
        text = text.format(**kwargs)
    return text


def user_display(username: str | None, user_id: int) -> str:
    """Canonical requestor label: the username, or ``user:<id>`` if there is none."""
    if username:
        return username.strip().lower()
    return f"user:{user_id}"


class LanguageStore:
    """Per-user language preference, persisted as JSON on the data volume."""

    def __init__(self, path: Path, default: str = "en"):
        self.path = Path(path)
        self.default = default if default in LANGS else "en"
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): v for k, v in raw.items() if v in LANGS}
        except (OSError, ValueError):
            self._data = {}

    def is_set(self, user_id: int) -> bool:
        return str(user_id) in self._data

    def get(self, user_id: int) -> str:
        return self._data.get(str(user_id), self.default)

    def set(self, user_id: int, lang: str) -> None:
        if lang not in LANGS:
            raise ValueError(f"Invalid language: {lang!r}")
        self._data[str(user_id)] = lang
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            # Language persistence is best-effort; never crash the bot over it.
            pass

    def detect(self, client_lang: str | None) -> str:
        """Map a Telegram client language code to our supported set."""
        code = (client_lang or "").strip().lower()
        if code in LANGS:
            return code
        return self.default


def _default_store_path(data_dir: Path) -> Path:
    return Path(data_dir) / "langs.json"


def make_store(data_dir: str | Path, default: str = "en") -> LanguageStore:
    return LanguageStore(_default_store_path(Path(data_dir)), default)


# Re-exported so tests can import a single helper for a fresh store.
def open_store(path: Path, default: str = "en") -> LanguageStore:
    return LanguageStore(path, default)


# Silence unused-import lint for Mapping (kept for type documentation parity).
_ = Mapping
