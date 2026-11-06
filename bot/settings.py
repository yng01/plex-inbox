"""Per-user download preferences: default audio mode, quality, and auto-download.

Persisted as JSON on the data volume, keyed by numeric Telegram user id. This
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
from dataclasses import dataclass
from pathlib import Path

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
    """Per-user :class:`UserSettings`, persisted as JSON on the data volume.

    Best-effort persistence (a failed write never raises into a handler) and
    total reads (unknown users get the defaults). Mirrors the :class:`LanguageStore`
    pattern so the two per-user stores stay consistent.
    """

    def __init__(
        self,
        path: Path,
        default_mode: str = _DEFAULT_MODE,
        default_quality: int | None = None,
    ):
        self.path = Path(path)
        self.default_mode = (
            default_mode if default_mode in AUDIO_MODES else _DEFAULT_MODE
        )
        self.default_quality = (
            default_quality
            if default_quality in QUALITIES or default_quality is None
            else None
        )
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._data = {}

    def get(self, user_id: int) -> UserSettings:
        raw = self._data.get(str(user_id), {})
        mode = raw.get("audio_mode")
        if mode not in AUDIO_MODES:
            mode = self.default_mode
        quality = raw.get("quality", self.default_quality)
        if quality is not None and quality not in QUALITIES:
            quality = None  # stale/unknown value -> fall back to best
        auto = bool(raw.get("auto_download", False))
        return UserSettings(audio_mode=mode, quality=quality, auto_download=auto)

    def _update(self, user_id: int, **fields: object) -> None:
        cur = dict(self._data.get(str(user_id), {}))
        cur.update(fields)
        self._data[str(user_id)] = cur
        self._save()

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

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            # Persistence is best-effort; never crash the bot over it.
            pass


def open_settings(
    path: Path,
    default_mode: str = _DEFAULT_MODE,
    default_quality: int | None = None,
) -> SettingsStore:
    """Build a fresh :class:`SettingsStore` (re-exported so tests import one helper)."""
    return SettingsStore(path, default_mode, default_quality)
