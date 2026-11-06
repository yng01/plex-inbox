"""Deduplication archive + library browsing + stats.

``plexinbox.db`` is the shared SQLite database; its ``archive`` table is the dedupe
ledger (one row per downloaded item, keyed by ``<site>:<id>:<mode>`` where ``<mode>``
is ``video`` or ``audio``). Video and audio-only downloads of the same item are
independent entries — each can be skipped, removed and re-downloaded on its own. The
rich per-item record lives in the on-disk sidecar ``<id>.info.json`` (written by
``plex.py``), which is the source of truth for the library browser and the stats
views.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .db import connect, db_path

#: Extensions that mark an on-disk file as an *audio-only* download. Used to
#: classify legacy ledger entries (pre-mode keys) during migration. Keep in
#: sync with :data:`bot.ytdlp.AUDIO_EXTS`.
_AUDIO_EXTS = {"m4a", "mp3", "opus", "ogg", "oga", "aac", "flac", "wma", "mka"}


def dedupe_key(site: str, vid: str, audio_only: bool) -> str:
    """Ledger key for one downloaded item, scoped to its download mode.

    ``audio_only`` selects the ``:audio`` suffix; anything else (every video
    mode) is ``:video``. This is what lets a re-requested video download
    proceed after the same item was fetched audio-only (and vice versa).
    """
    suffix = "audio" if audio_only else "video"
    return f"{site}:{vid}:{suffix}"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ArchiveEntry:
    key: str  # "site:id:video" | "site:id:audio"
    requestor: str
    ts: str
    rel_path: str


class Archive:
    """Dedupe ledger, persisted in the shared SQLite database (``plexinbox.db``).

    One row per downloaded item, keyed by the mode-scoped ``<site>:<id>:<mode>``
    key. The on-disk ``<id>.info.json`` sidecar stays the source of truth for the
    library browser; this table is only the dedupe index plus the recorded file
    path used to verify the file is still present (see :meth:`contains`).
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = db_path(data_dir)
        self._legacy_txt = self.data_dir / "archive.txt"
        self._legacy_db = self.data_dir / "archive.db"
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        """Open (once) and prepare the database, importing any legacy ledger."""
        if self._conn is None:
            conn = connect(self.data_dir)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS archive ("
                " key TEXT PRIMARY KEY,"
                " requestor TEXT NOT NULL DEFAULT '',"
                " ts TEXT NOT NULL DEFAULT '',"
                " rel_path TEXT NOT NULL DEFAULT ''"
                ")"
            )
            conn.commit()
            self._conn = conn
            self._import_legacy_db()
            self._import_legacy_txt()
            self._migrate_bare_keys()
        return self._conn

    @staticmethod
    def _migrate_key(key: str, rel_path: str) -> str:
        if key.endswith((":video", ":audio")):
            return key
        ext = Path(rel_path).suffix.lower().lstrip(".") if rel_path else ""
        suffix = "audio" if ext in _AUDIO_EXTS else "video"
        return f"{key}:{suffix}"

    def _import_legacy_db(self) -> None:
        """Copy a pre-consolidation ``archive.db`` into the shared DB, then remove it.

        Before settings/languages moved into ``plexinbox.db``, the archive lived in
        its own ``archive.db`` file. Migrate that table into the shared database so
        nothing is lost on upgrade. Idempotent: the file is gone afterwards.
        """
        legacy = self._legacy_db
        if not legacy.exists():
            return
        assert self._conn is not None
        try:
            legacy_conn = sqlite3.connect(str(legacy))
        except sqlite3.Error:
            return  # unreadable file; leave it alone
        try:
            rows = legacy_conn.execute(
                "SELECT key, requestor, ts, rel_path FROM archive"
            ).fetchall()
        except sqlite3.Error:
            rows = []  # not a SQLite DB, or no archive table
        finally:
            legacy_conn.close()
        if not rows:
            return
        self._conn.executemany(
            "INSERT OR REPLACE INTO archive (key, requestor, ts, rel_path)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        legacy.unlink(missing_ok=True)

    def _import_legacy_txt(self) -> None:
        """One-time import of a pre-SQLite ``archive.txt`` ledger, then remove it.

        Migrates bare ``site:id`` keys to mode-suffixed keys as it goes, so the
        upgrade path is transparent and idempotent (the file is gone afterwards).
        """
        txt = self._legacy_txt
        if not txt.exists():
            return
        assert self._conn is not None
        for line in txt.read_text(encoding="utf-8").splitlines():
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            key = parts[0].strip() if len(parts) > 0 else ""
            requestor = parts[1].strip() if len(parts) > 1 else ""
            ts = parts[2].strip() if len(parts) > 2 else ""
            rel = parts[3].strip() if len(parts) > 3 else ""
            if not key:
                continue
            nk = self._migrate_key(key, rel)
            self._conn.execute(
                "INSERT OR REPLACE INTO archive (key, requestor, ts, rel_path)"
                " VALUES (?, ?, ?, ?)",
                (nk, requestor, ts, rel),
            )
        self._conn.commit()
        txt.unlink(missing_ok=True)

    def _migrate_bare_keys(self) -> None:
        """Rewrite any legacy bare ``site:id`` keys to mode-suffixed keys.

        New writes are always mode-scoped (via :func:`dedupe_key`), so this only
        ever touches keys written by pre-mode releases. Idempotent.
        """
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT key, requestor, ts, rel_path FROM archive"
        ).fetchall()
        changed = False
        for key, requestor, ts, rel in rows:
            nk = self._migrate_key(key, rel)
            if nk == key:
                continue
            changed = True
            self._conn.execute("DELETE FROM archive WHERE key = ?", (key,))
            self._conn.execute(
                "INSERT OR REPLACE INTO archive (key, requestor, ts, rel_path)"
                " VALUES (?, ?, ?, ?)",
                (nk, requestor, ts, rel),
            )
        if changed:
            self._conn.commit()

    def contains(self, key: str) -> bool:
        """True iff the item is in the ledger AND its file still exists on disk.

        A ledger entry whose on-disk file was deleted (e.g. the user removed a
        video from the library) no longer counts as archived, so re-requesting
        it re-downloads instead of skipping. The file path is the one recorded
        at download time (``rel_path``), resolved against ``data_dir`` (or used
        as-is when absolute).
        """
        row = (
            self._db()
            .execute("SELECT rel_path FROM archive WHERE key = ?", (key,))
            .fetchone()
        )
        if row is None:
            return False
        rel = row[0]
        if not rel:
            # Ledger present but no path recorded (shouldn't happen); trust it.
            return True
        path = Path(rel)
        if not path.is_absolute():
            path = self.data_dir / path
        return path.exists()

    def add(
        self, key: str, requestor: str, rel_path: str, ts: str | None = None
    ) -> None:
        conn = self._db()
        conn.execute(
            "INSERT OR REPLACE INTO archive (key, requestor, ts, rel_path)"
            " VALUES (?, ?, ?, ?)",
            (key, requestor, ts or _now_iso(), rel_path),
        )
        conn.commit()

    def remove_item(self, site: str, vid: str) -> int:
        """Remove every ledger row for one item — both the ``:video`` and the
        ``:audio`` slot — used by the library delete flow. Once the item's files
        are gone its slots must be free, so a re-request starts a fresh download
        (a manually-deleted file is re-downloadable). Returns how many rows were
        removed (0 when the item had no ledger entry).
        """
        cur = self._db().execute(
            "DELETE FROM archive WHERE key IN (?, ?)",
            (dedupe_key(site, vid, False), dedupe_key(site, vid, True)),
        )
        removed = cur.rowcount
        self._db().commit()
        return removed

    def entries(self) -> list[ArchiveEntry]:
        rows = (
            self._db()
            .execute("SELECT key, requestor, ts, rel_path FROM archive ORDER BY key")
            .fetchall()
        )
        return [ArchiveEntry(k, r, t, rel) for k, r, t, rel in rows]


@dataclass
class LibraryFile:
    name: str
    site: str
    channel: str
    size_bytes: int
    date: str
    audio: list[str]  # language codes, may be empty
    requestor: str
    year: int | None
    sidecar: Path  # <data>/<site>/<channel>/<id>.info.json — the item on disk


class Library:
    """Browse the on-disk archive by walking sidecar files."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def _sidecars(self) -> Iterator[Path]:
        if not self.data_dir.exists():
            return
        for p in self.data_dir.glob("**/*.info.json"):
            if p.is_file():
                yield p

    def _read_sidecar(self, p: Path) -> dict | None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return None

    def all_files(self) -> list[LibraryFile]:
        out: list[LibraryFile] = []
        for p in self._sidecars():
            d = self._read_sidecar(p)
            if not d:
                continue
            site = str(d.get("site") or "")
            channel = str(d.get("channel") or "unknown")
            # site/channel also derivable from path parts
            if not site:
                rel = p.relative_to(self.data_dir)
                parts = rel.parts
                site = parts[0] if len(parts) > 1 else ""
                channel = parts[1] if len(parts) > 2 else "unknown"
            tracks = d.get("audio_tracks") or []
            audio = [str(t.get("language", "und")) for t in tracks]
            out.append(
                LibraryFile(
                    name=str(d.get("title") or p.parent.name),
                    site=site,
                    channel=channel,
                    size_bytes=int(d.get("size_bytes") or 0),
                    date=str(d.get("added_at") or ""),
                    audio=audio,
                    requestor=str(d.get("requestor") or ""),
                    year=d.get("year"),
                    sidecar=p,
                )
            )
        out.sort(key=lambda f: (f.site.lower(), f.channel.lower(), f.name.lower()))
        return out

    def sites(self) -> list[tuple[str, int]]:
        files = self.all_files()
        counts: dict[str, int] = {}
        for f in files:
            counts[f.site or "unknown"] = counts.get(f.site or "unknown", 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def channels(self, site: str) -> list[tuple[str, int]]:
        files = [f for f in self.all_files() if (f.site or "unknown") == site]
        counts: dict[str, int] = {}
        for f in files:
            counts[f.channel or "unknown"] = counts.get(f.channel or "unknown", 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def files(self, site: str, channel: str) -> list[LibraryFile]:
        return [
            f
            for f in self.all_files()
            if (f.site or "unknown") == site and (f.channel or "unknown") == channel
        ]


class Stats:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.library = Library(data_dir)

    def compute(self, queue_length: int = 0) -> dict:
        files = self.library.all_files()
        total_size = sum(f.size_bytes for f in files)
        per_site: dict[str, int] = {}
        per_requestor: dict[str, int] = {}
        for f in files:
            s = f.site or "unknown"
            per_site[s] = per_site.get(s, 0) + 1
            who = f.requestor or "unknown"
            per_requestor[who] = per_requestor.get(who, 0) + 1
        return {
            "total_downloads": len(files),
            "total_size_bytes": total_size,
            "per_site": dict(sorted(per_site.items(), key=lambda kv: -kv[1])),
            "per_requestor": dict(sorted(per_requestor.items(), key=lambda kv: -kv[1])),
            "queue_length": queue_length,
        }
