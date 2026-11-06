"""Keep yt-dlp current: compare installed version against PyPI and upgrade in place.

Because each download spawns a fresh yt-dlp process, an upgraded binary applies to
subsequent downloads without restarting the bot. Upgrades are best-effort: a failure
(network, disk, broken wheel) is logged, the current version keeps being used, and the
next cycle retries. ``AUTO_UPDATE=0`` pins to the image version.

All external effects (running ``yt-dlp --version``, the pip upgrade, the PyPI fetch)
are injectable so tests can drive success / failure / disabled paths deterministically.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from .config import Config

VersionCmd = Callable[[Sequence[str]], "VProc"]


class VProc:
    """Minimal async process surface for version/upgrade commands."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self._rc = returncode
        self.returncode: int | None = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout.encode(), self._stderr.encode()

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc


PyPIFetcher = Callable[[str], Awaitable[str | None]]


def _default_pypi(ytdlp_version_url: str) -> Callable[[str], Awaitable[str | None]]:
    async def fetch(url: str) -> str | None:
        try:
            import httpx
        except ImportError:
            return None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                return str(data.get("info", {}).get("version"))
        except Exception:
            return None

    return fetch


def parse_version(v: str) -> tuple[int, ...]:
    """Parse a yt-dlp version like ``2026.01.15`` or ``2025.12.02+dev`` to a tuple."""
    v = (v or "").strip().split("+")[0].strip()
    parts = re.split(r"[.\-]", v)
    out: list[int] = []
    for p in parts:
        m = re.match(r"\d+", p)
        if m:
            out.append(int(m.group()))
    return tuple(out) if out else (0,)


def is_newer(installed: str, latest: str) -> bool:
    return parse_version(latest) > parse_version(installed)


@dataclass
class UpdateState:
    current: str = ""
    latest: str = ""
    upgraded: bool = False
    error: str = ""
    checked_at: str = ""
    last_error: str = ""


class Updater:
    def __init__(
        self,
        config: Config,
        runner: VersionCmd | None = None,
        pypi_fetch: PyPIFetcher | None = None,
        ytdlp_bin: str = "yt-dlp",
        state_path: str | None = None,
    ):
        self.config = config
        self.ytdlp_bin = ytdlp_bin
        self._runner = runner
        self._pypi = pypi_fetch
        self.state_path = state_path
        self.state = self._load_state()

    def _load_state(self) -> UpdateState:
        if not self.state_path:
            return UpdateState()
        try:
            raw = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            known = {f: raw[f] for f in UpdateState.__dataclass_fields__ if f in raw}
            return UpdateState(**known)
        except Exception:
            return UpdateState()

    def _commit(self, st: UpdateState) -> None:
        self.state = st
        if not self.state_path:
            return
        try:
            Path(self.state_path).write_text(
                json.dumps(asdict(st), indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # persistence is best-effort; in-memory state still works

    async def _run(self, cmd: Sequence[str]) -> tuple[int, str, str]:
        if self._runner is not None:
            proc = self._runner(list(cmd))
            stdout, stderr = await proc.communicate()
            rc = proc.returncode if proc.returncode is not None else 0
            return (
                rc,
                stdout.decode("utf-8", "replace"),
                stderr.decode("utf-8", "replace"),
            )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def current_version(self) -> str:
        rc, out, _ = await self._run([self.ytdlp_bin, "--version"])
        if rc != 0:
            return ""
        return out.strip().splitlines()[0].strip() if out.strip() else ""

    async def latest_version(self) -> str:
        fetch = self._pypi
        if fetch is None:
            fetch = _default_pypi("https://pypi.org/pypi/yt-dlp/json")
        v = await fetch("https://pypi.org/pypi/yt-dlp/json")
        return v or ""

    async def upgrade(self) -> tuple[bool, str]:
        """Run the in-place pip upgrade. Returns (ok, detail)."""
        import sys

        cmd = [sys.executable, "-m", "pip", "install", "--user", "--upgrade", "yt-dlp"]
        rc, out, err = await self._run(cmd)
        detail = (err or out or "").strip().splitlines()
        detail = detail[-1] if detail else ""
        return (rc == 0), detail

    async def check_and_maybe_upgrade(self) -> UpdateState:
        from datetime import datetime, timezone

        st = UpdateState(
            checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        st.current = await self.current_version()
        st.latest = await self.latest_version()
        if not self.config.auto_update:
            st.error = "disabled"
            self._commit(st)
            return st
        if not st.current or not st.latest:
            st.error = "unknown-version"
            self._commit(st)
            return st
        if not is_newer(st.current, st.latest):
            self._commit(st)
            return st
        ok, detail = await self.upgrade()
        if not ok:
            st.error = detail or "upgrade-failed"
            st.last_error = st.error
            self._commit(st)
            return st
        newver = await self.current_version()
        st.upgraded = True
        st.current = newver or st.latest
        self._commit(st)
        return st


async def check_now(updater: Updater) -> UpdateState:
    return await updater.check_and_maybe_upgrade()
