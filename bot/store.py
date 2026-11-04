"""Deduplication archive + library browsing + stats.

``archive.txt`` is the dedupe ledger (one line per downloaded item, keyed by
``<site>:<id>``). The rich per-item record lives in the on-disk sidecar
``<id>.info.json`` (written by ``plex.py``), which is the source of truth for the
library browser and the stats views.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ArchiveEntry:
    key: str  # "site:id"
    requestor: str
    ts: str
    rel_path: str


class Archive:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "archive.txt"
        self._cache: dict[str, ArchiveEntry] | None = None

    def _reload(self) -> dict[str, ArchiveEntry]:
        entries: dict[str, ArchiveEntry] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 1:
                    key = parts[0].strip()
                    requestor = parts[1].strip() if len(parts) > 1 else ""
                    ts = parts[2].strip() if len(parts) > 2 else ""
                    rel = parts[3].strip() if len(parts) > 3 else ""
                    if key:
                        entries[key] = ArchiveEntry(key, requestor, ts, rel)
        self._cache = entries
        return entries

    def contains(self, key: str) -> bool:
        """True iff the item is in the ledger AND its file still exists on disk.

        A ledger entry whose on-disk file was deleted (e.g. the user removed a
        video from the library) no longer counts as archived, so re-requesting
        it re-downloads instead of skipping. The file path is the one recorded
        at download time (``rel_path``), resolved against ``data_dir`` (or used
        as-is when absolute).
        """
        if self._cache is None:
            self._reload()
        entry = self._cache.get(key)  # type: ignore[union-attr]
        if entry is None:
            return False
        rel = entry.rel_path
        if not rel:
            # Ledger present but no path recorded (shouldn't happen); trust it.
            return True
        path = Path(rel)
        if not path.is_absolute():
            path = self.data_dir / path
        return path.exists()

    def add(self, key: str, requestor: str, rel_path: str, ts: str | None = None) -> None:
        entries = self._reload()
        ts = ts or _now_iso()
        entry = ArchiveEntry(key, requestor, ts, rel_path)
        entries[key] = entry
        self._cache = entries
        self._flush(entries)

    def _flush(self, entries: dict[str, ArchiveEntry]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "\t".join([e.key, e.requestor, e.ts, e.rel_path]) for e in entries.values()
        ]
        tmp = self.path.with_suffix(".txt.tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self.path)

    def entries(self) -> list[ArchiveEntry]:
        if self._cache is None:
            self._reload()
        return list(self._cache.values())  # type: ignore[arg-type]


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
