"""Environment parsing and validation.

Every configurable knob is read from environment variables (or, in tests, from an
arbitrary ``Mapping[str, str]``). ``load_config`` raises :class:`ConfigError` on
anything invalid so the bot fails fast at startup with a clear message instead of
misbehaving at runtime.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import LANGS


class ConfigError(Exception):
    """Raised when the environment does not describe a valid configuration."""


def _flag(value: str | None, name: str, default: bool) -> bool:
    """Parse a 1/0 (or true/false/yes/no) flag. ``None`` -> default."""
    if value is None or value.strip() == "":
        return default
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be 1 or 0, got {value!r}")


def _int(value: str | None, name: str, default: int, minimum: int = 1) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        n = int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc
    if n < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {n}")
    return n


def _float(value: str | None, name: str, default: float, minimum: float = 0.1) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        n = float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc
    if n < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {n}")
    return n


def parse_allowed_user_ids(value: str | None) -> tuple[bool, list[int]]:
    """Return ``(open_access, ids)``.

    ``all`` / ``*`` / empty -> open access (empty list). Otherwise a comma-separated
    list of numeric Telegram user IDs.
    """
    if value is None:
        raise ConfigError("ALLOWED_USER_IDS is required (use 'all' for open access)")
    v = value.strip()
    if v in {"", "all", "*"}:
        return True, []
    ids: list[int] = []
    for part in v.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as exc:
            raise ConfigError(
                "ALLOWED_USER_IDS must be 'all' or comma-separated numeric IDs, "
                f"got {part!r}"
            ) from exc
    if not ids:
        raise ConfigError("ALLOWED_USER_IDS lists no valid user IDs")
    return False, ids


@dataclass(frozen=True)
class Config:
    bot_token: str
    open_access: bool
    allowed_user_ids: list[int] = field(default_factory=list)
    data_dir: Path = Path("/data")
    max_concurrent: int = 2
    concurrent_fragments: int = 4
    multi_audio: bool = True
    auto_update: bool = True
    update_check_hours: float = 24.0
    write_nfo: bool = True
    plex_url: str | None = None
    plex_token: str | None = None
    default_lang: str = "en"
    debug_access: bool = False
    video_format: str | None = None
    # Optional: route all Bot API traffic (incl. file downloads) through a local
    # Bot API Server — required to receive uploaded files larger than 20 MB.
    bot_api_base_url: str | None = None
    bot_api_base_file_url: str | None = None

    def is_allowed(self, user_id: int) -> bool:
        return self.open_access or user_id in self.allowed_user_ids

    @property
    def plex_enabled(self) -> bool:
        return bool(self.plex_url and self.plex_token)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a validated :class:`Config` from ``env`` (defaults to ``os.environ``)."""
    e = dict(os.environ) if env is None else dict(env)

    token = (e.get("BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN is required")

    open_access, allowed = parse_allowed_user_ids(e.get("ALLOWED_USER_IDS"))

    data_dir_raw = (e.get("DATA_DIR") or "/data").strip() or "/data"

    default_lang = (e.get("DEFAULT_LANG") or "en").strip().lower()
    if default_lang not in LANGS:
        raise ConfigError(
            f"DEFAULT_LANG must be one of {', '.join(LANGS)}, got {default_lang!r}"
        )

    plex_url = (e.get("PLEX_URL") or "").strip() or None
    plex_token = (e.get("PLEX_TOKEN") or "").strip() or None
    if (plex_url and not plex_token) or (plex_token and not plex_url):
        raise ConfigError("PLEX_URL and PLEX_TOKEN must both be set (or both unset)")
    if plex_url and not plex_url.startswith(("http://", "https://")):
        raise ConfigError(
            f"PLEX_URL must start with http:// or https://, got {plex_url!r}"
        )

    video_format = (e.get("VIDEO_FORMAT") or "").strip() or None

    def _base_url(name: str) -> str | None:
        raw = (e.get(name) or "").strip()
        if not raw:
            return None
        if not raw.startswith(("http://", "https://")):
            raise ConfigError(
                f"{name} must start with http:// or https://, got {raw!r}"
            )
        return raw

    return Config(
        bot_token=token,
        open_access=open_access,
        allowed_user_ids=allowed,
        data_dir=Path(data_dir_raw).expanduser(),
        max_concurrent=_int(e.get("MAX_CONCURRENT"), "MAX_CONCURRENT", 2),
        concurrent_fragments=_int(
            e.get("CONCURRENT_FRAGMENTS"), "CONCURRENT_FRAGMENTS", 4
        ),
        multi_audio=_flag(e.get("MULTI_AUDIO"), "MULTI_AUDIO", True),
        auto_update=_flag(e.get("AUTO_UPDATE"), "AUTO_UPDATE", True),
        update_check_hours=_float(
            e.get("UPDATE_CHECK_HOURS"), "UPDATE_CHECK_HOURS", 24.0
        ),
        write_nfo=_flag(e.get("WRITE_NFO"), "WRITE_NFO", True),
        plex_url=plex_url,
        plex_token=plex_token,
        default_lang=default_lang,
        debug_access=_flag(e.get("DEBUG_ACCESS"), "DEBUG_ACCESS", False),
        video_format=video_format,
        bot_api_base_url=_base_url("TELEGRAM_BOT_API_BASE_URL"),
        bot_api_base_file_url=_base_url("TELEGRAM_BOT_API_BASE_FILE_URL"),
    )
