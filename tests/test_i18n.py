from bot.i18n import STRINGS, LanguageStore, t, user_display


def test_every_key_has_both_languages():
    assert STRINGS, "STRINGS must not be empty"
    for key, val in STRINGS.items():
        assert "en" in val, f"missing en for {key}"
        assert "ar" in val, f"missing ar for {key}"
        assert val["en"].strip(), f"empty en for {key}"
        assert val["ar"].strip(), f"empty ar for {key}"


def test_all_known_keys_are_defined():
    import bot.handlers as h
    import bot.probe as p
    import bot.store as s
    import bot.ytdlp as y

    # every t(...) key used anywhere must exist in STRINGS
    missing = set()
    for mod in (h, p, s, y):
        src = open(mod.__file__, encoding="utf-8").read()
        import re

        for m in re.finditer(r'\bt\(\s*["\']([a-z0-9_]+)["\']', src):
            if m.group(1) not in STRINGS:
                missing.add(m.group(1))
        for m in re.finditer(r'["\']([a-z0-9_]+)["\'],\s*(?:pos|n|m|pct|speed|eta|title|reason|path|size|tracks|site|channel|who|v|t=|lang=|label=)', src):
            # `or "fallback"` is not an i18n key — e.g. ``info.site or "unknown"``
            if src[max(0, m.start() - 3):m.start()].endswith("or "):
                continue
            if m.group(1) not in STRINGS:
                missing.add(m.group(1))
    assert not missing, f"i18n keys used but undefined: {sorted(missing)}"


def test_t_substitution_and_fallback():
    assert "5" in t("en", "queued", pos=5)
    # unknown language falls back to English
    assert isinstance(t("zz", "queued", pos=1), str)
    # unknown key is a loud bug
    import pytest

    with pytest.raises(KeyError):
        t("en", "no_such_key", x=1)


def test_user_display_prefers_username():
    assert user_display("alice", 1) == "alice"
    assert user_display(None, 123456789).startswith("user:")
    assert "123456789" in user_display(None, 123456789)


def test_language_store_roundtrip_and_default(tmp_path):
    p = tmp_path / "langs.json"
    store = LanguageStore(path=p, default="en")
    assert store.get(1) == "en"
    store.set(1, "ar")
    assert store.get(1) == "ar"
    assert store.get(2) == "en"
    # persistence
    again = LanguageStore(path=p, default="en")
    assert again.get(1) == "ar"
    assert p.exists()
