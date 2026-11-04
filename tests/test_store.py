"""Dedupe-ledger semantics: ``Archive.contains`` must reflect *both* the
ledger and the on-disk file. A manually-deleted video is no longer "archived",
so re-requesting it re-downloads instead of skipping (the user's report)."""

from bot.store import Archive


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
