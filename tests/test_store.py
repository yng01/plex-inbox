"""Dedupe-ledger semantics: ``Archive.contains`` must reflect *both* the
ledger and the on-disk file. A manually-deleted video is no longer "archived",
so re-requesting it re-downloads instead of skipping (the user's report)."""

import sqlite3

from bot.store import Archive, dedupe_key


def test_contains_true_when_ledger_and_file_present(tmp_path):
    a = Archive(tmp_path)
    rel = "YouTube/Chan/abc.mkv"
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"x" * 16)
    a.add("YouTube:abc", "ali", rel)
    assert a.contains("YouTube:abc")


def test_contains_false_when_file_deleted(tmp_path):
    a = Archive(tmp_path)
    rel = "YouTube/Chan/abc.mkv"
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    f = tmp_path / rel
    f.write_bytes(b"x" * 16)
    a.add("YouTube:abc", "ali", rel)
    assert a.contains("YouTube:abc")
    # user removed the video from the library by hand
    f.unlink()
    # the ledger entry remains, but the item is no longer "in the library"
    assert not a.contains("YouTube:abc")


def test_contains_false_when_key_absent(tmp_path):
    a = Archive(tmp_path)
    assert not a.contains("YouTube:missing")


def test_contains_true_again_after_re_download(tmp_path):
    a = Archive(tmp_path)
    rel = "YouTube/Chan/abc.mkv"
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    f = tmp_path / rel
    # first download -> archived
    f.write_bytes(b"x" * 16)
    a.add("YouTube:abc", "ali", rel)
    assert a.contains("YouTube:abc")
    # user deletes, then re-requests: re-download re-writes + re-records
    f.unlink()
    assert not a.contains("YouTube:abc")
    f.write_bytes(b"y" * 24)
    a.add("YouTube:abc", "ali", rel)
    assert a.contains("YouTube:abc")
    assert f.read_bytes() == b"y" * 24


def test_contains_handles_absolute_rel_path(tmp_path):
    a = Archive(tmp_path)
    # rel recorded as an absolute path (relative_to failed at download time)
    f = tmp_path / "abs" / "abc.mkv"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 16)
    a.add("YouTube:abc", "ali", f.as_posix())
    assert a.contains("YouTube:abc")
    f.unlink()
    assert not a.contains("YouTube:abc")


# --- mode-scoped slots + legacy-key migration ---------------------------------


def test_dedupe_key_scopes_by_mode():
    assert dedupe_key("YouTube", "abc123", False) == "YouTube:abc123:video"
    assert dedupe_key("YouTube", "abc123", True) == "YouTube:abc123:audio"


def test_video_and_audio_only_are_independent_slots(tmp_path):
    a = Archive(tmp_path)
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    video = tmp_path / "YouTube" / "Chan" / "abc.mkv"
    audio = tmp_path / "YouTube" / "Chan" / "abc.m4a"
    video.write_bytes(b"x" * 16)
    audio.write_bytes(b"y" * 16)
    a.add("YouTube:abc:video", "ali", "YouTube/Chan/abc.mkv")
    a.add("YouTube:abc:audio", "ali", "YouTube/Chan/abc.m4a")
    assert a.contains("YouTube:abc:video")
    assert a.contains("YouTube:abc:audio")
    # deleting one file un-archives only that mode
    video.unlink()
    assert not a.contains("YouTube:abc:video")
    assert a.contains("YouTube:abc:audio")


def test_legacy_bare_key_migrates_to_video_slot(tmp_path):
    a = Archive(tmp_path)
    rel = "YouTube/Chan/abc.mkv"
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"x" * 16)
    a.add("YouTube:abc", "ali", rel)  # pre-mode key, as older releases wrote it

    # a fresh process (fresh cache) sees the suffixed key on disk
    b = Archive(tmp_path)
    assert b.contains("YouTube:abc:video")
    assert not b.contains("YouTube:abc:audio")
    assert not b.contains("YouTube:abc")
    assert {e.key for e in b.entries()} == {"YouTube:abc:video"}
    # migration is idempotent: reload again, still one entry
    c = Archive(tmp_path)
    assert len(c.entries()) == 1


def test_legacy_bare_key_with_audio_file_migrates_to_audio_slot(tmp_path):
    a = Archive(tmp_path)
    rel = "YouTube/Chan/abc.m4a"
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"x" * 16)
    a.add("YouTube:abc", "ali", rel)

    b = Archive(tmp_path)
    assert b.contains("YouTube:abc:audio")
    assert not b.contains("YouTube:abc:video")


def test_imports_legacy_txt_ledger(tmp_path):
    """A pre-SQLite archive.txt is imported (and mode-migrated) into plexinbox.db."""
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    (tmp_path / "YouTube" / "Chan" / "abc.mkv").write_bytes(b"x" * 16)
    (tmp_path / "archive.txt").write_text(
        "YouTube:abc\tali\t2024-01-01T00:00:00Z\tYouTube/Chan/abc.mkv\n",
        encoding="utf-8",
    )

    a = Archive(tmp_path)

    assert a.contains("YouTube:abc:video")
    assert not a.contains("YouTube:abc")
    # the legacy file is consumed so the import never re-runs
    assert not (tmp_path / "archive.txt").exists()
    assert (tmp_path / "plexinbox.db").exists()


def test_imports_legacy_archive_db(tmp_path):
    """A pre-consolidation archive.db is copied into the shared plexinbox.db."""
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    (tmp_path / "YouTube" / "Chan" / "abc.mkv").write_bytes(b"x" * 16)
    conn = sqlite3.connect(str(tmp_path / "archive.db"))
    conn.execute(
        "CREATE TABLE archive (key TEXT PRIMARY KEY,"
        " requestor TEXT NOT NULL DEFAULT '',"
        " ts TEXT NOT NULL DEFAULT '', rel_path TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO archive VALUES (?, ?, ?, ?)",
        ("YouTube:abc:video", "ali", "2024-01-01T00:00:00Z", "YouTube/Chan/abc.mkv"),
    )
    conn.commit()
    conn.close()

    a = Archive(tmp_path)

    assert a.contains("YouTube:abc:video")
    assert not (tmp_path / "archive.db").exists()
    assert (tmp_path / "plexinbox.db").exists()


def test_remove_item_clears_both_mode_slots(tmp_path):
    a = Archive(tmp_path)
    (tmp_path / "YouTube" / "Chan").mkdir(parents=True)
    (tmp_path / "YouTube" / "Chan" / "abc.mkv").write_bytes(b"x" * 16)
    (tmp_path / "YouTube" / "Chan" / "abc.m4a").write_bytes(b"y" * 16)
    (tmp_path / "YouTube" / "Chan" / "other.mkv").write_bytes(b"z" * 16)
    a.add(dedupe_key("YouTube", "abc", False), "ali", "YouTube/Chan/abc.mkv")
    a.add(dedupe_key("YouTube", "abc", True), "ali", "YouTube/Chan/abc.m4a")
    a.add(dedupe_key("YouTube", "other", False), "ali", "YouTube/Chan/other.mkv")

    removed = a.remove_item("YouTube", "abc")

    assert removed == 2
    keys = {e.key for e in a.entries()}
    assert "YouTube:abc:video" not in keys
    assert "YouTube:abc:audio" not in keys
    assert "YouTube:other:video" in keys  # other items are untouched
    # idempotent: nothing left to remove for that item
    assert a.remove_item("YouTube", "abc") == 0
    assert a.remove_item("YouTube", "missing") == 0
