"""Per-user download preferences: default audio mode, quality, and auto-download.

Persisted in SQLite on the data volume, keyed by numeric Telegram user id. This
drives the "send a link and it just downloads" behavior: when a user has
auto-download enabled, a link is queued immediately with their saved audio mode
and quality instead of showing the confirm screen. With auto-download off (the
default, "ask me every time"), the confirm screen is shown as usual and the
user's saved values simply pre-fill it.

The data model is intentionally small and total: every read returns a valid
:class:`UserSettings` even for a user who has never touched settings (defaults
apply), so a missing or corrupt file can never crash a download.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .db import connect

#: Height caps offered as a "quality" choice (lower = smaller). ``None`` means
#: "best available" (no cap). Keep in sync with the confirm-screen and
#: settings-screen quality buttons in :mod:`bot.handlers`.
QUALITIES: tuple[int, ...] = (360, 480, 720, 1080)

#: Audio modes a user can set as their default. These mirror the confirm-screen
#: download choices: "all" (all audio tracks), "orig" (original-language audio),
#: "audio" (audio only, no video).
AUDIO_MODES: tuple[str, ...] = ("all", "orig", "audio")

_DEFAULT_MODE = "orig"


@dataclass
class UserSettings:
    """One user's saved download preferences (always fully populated)."""

    audio_mode: str = _DEFAULT_MODE
    quality: int | None = None  # None = best available
    auto_download: bool = False


class SettingsStore:
    """Per-user :class:`UserSettings`, persisted in the shared SQLite database.

    Best-effort persistence (a failed write never raises into a handler) and
    total reads (unknown users get the defaults). Mirrors the :class:`LanguageStore`
    pattern so the two per-user stores stay consistent.
    """

    def __init__(
        self,
        data_dir: str | Path,
        default_mode: str = _DEFAULT_MODE,
        default_quality: int | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.default_mode = (
            default_mode if default_mode in AUDIO_MODES else _DEFAULT_MODE
        )
        self.default_quality = (
            default_quality
            if default_quality in QUALITIES or default_quality is None
            else None
        )
        self._legacy_json = self.data_dir / "settings.json"
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = connect(self.data_dir)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                " user_id TEXT PRIMARY KEY,"
                " audio_mode TEXT NOT NULL,"
                " quality INTEGER,"
                " auto_download INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            conn.commit()
            self._conn = conn
            self._import_legacy_json()
        return self._conn

    def _import_legacy_json(self) -> None:
        """One-time import of a pre-SQLite ``settings.json`` file, then remove it."""
        legacy = self._legacy_json
        if not legacy.exists():
            return
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # corrupt -> leave it, fall back to defaults
        if not isinstance(raw, dict):
            return
        assert self._conn is not None
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            mode = v.get("audio_mode")
            if mode not in AUDIO_MODES:
                mode = self.default_mode
            quality = v.get("quality", self.default_quality)
            if quality is not None and quality not in QUALITIES:
                quality = None  # stale/unknown value -> fall back to best
            auto = int(bool(v.get("auto_download", False)))
            self._conn.execute(
                "INSERT OR REPLACE INTO settings"
                " (user_id, audio_mode, quality, auto_download) VALUES (?, ?, ?, ?)",
                (str(k), mode, quality, auto),
            )
        self._conn.commit()
        legacy.unlink(missing_ok=True)

    def get(self, user_id: int) -> UserSettings:
        row = (
            self._db()
            .execute(
                "SELECT audio_mode, quality, auto_download FROM settings"
                " WHERE user_id = ?",
                (str(user_id),),
            )
            .fetchone()
        )
        if row is None:
            return UserSettings(
                audio_mode=self.default_mode,
                quality=self.default_quality,
                auto_download=False,
            )
        mode, quality, auto = row
        if mode not in AUDIO_MODES:
            mode = self.default_mode
        if quality is not None and quality not in QUALITIES:
            quality = None
        return UserSettings(audio_mode=mode, quality=quality, auto_download=bool(auto))

    def _update(self, user_id: int, **fields: object) -> None:
        cur = self.get(user_id)
        mode = fields.get("audio_mode", cur.audio_mode)
        quality = fields.get("quality", cur.quality)
        auto = fields.get("auto_download", cur.auto_download)
        conn = self._db()
        conn.execute(
            "INSERT OR REPLACE INTO settings"
            " (user_id, audio_mode, quality, auto_download) VALUES (?, ?, ?, ?)",
            (str(user_id), mode, quality, int(bool(auto))),
        )
        conn.commit()

    def set_audio_mode(self, user_id: int, mode: str) -> None:
        if mode not in AUDIO_MODES:
            raise ValueError(f"Invalid audio mode: {mode!r}")
        self._update(user_id, audio_mode=mode)

    def set_quality(self, user_id: int, quality: int | None) -> None:
        if quality is not None and quality not in QUALITIES:
            raise ValueError(f"Invalid quality: {quality!r}")
        self._update(user_id, quality=quality)

    def set_auto_download(self, user_id: int, flag: bool) -> None:
        self._update(user_id, auto_download=bool(flag))


def open_settings(
    data_dir: str | Path,
    default_mode: str = _DEFAULT_MODE,
    default_quality: int | None = None,
) -> SettingsStore:
    """Build a fresh :class:`SettingsStore` (re-exported so tests import one helper)."""
    return SettingsStore(data_dir, default_mode, default_quality)
