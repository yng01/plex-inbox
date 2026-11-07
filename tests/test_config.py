import pytest

from bot.config import ConfigError, load_config, parse_allowed_user_ids

BASE = {"BOT_TOKEN": "123:abc", "ALLOWED_USER_IDS": "all"}


def test_defaults():
    c = load_config(BASE)
    assert c.max_concurrent == 2
    assert c.concurrent_fragments == 4
    assert c.multi_audio is True
    assert c.auto_update is True
    assert c.update_check_hours == 24.0
    assert c.write_nfo is True
    assert c.default_lang == "en"
    assert load_config({**BASE, "DEFAULT_LANG": "tr"}).default_lang == "tr"
    assert c.debug_access is False
    assert c.video_format is None
    assert c.bot_api_base_url is None
    assert c.bot_api_base_file_url is None
    assert c.open_access is True
    assert c.is_allowed(999)


def test_whitelist_all_variants():
    for v in ("all", "*", ""):
        c = load_config({**BASE, "ALLOWED_USER_IDS": v})
        assert c.open_access
        assert c.is_allowed(1) and c.is_allowed(2)


def test_whitelist_ids():
    c = load_config({**BASE, "ALLOWED_USER_IDS": "1, 22, 333"})
    assert not c.open_access
    assert c.allowed_user_ids == [1, 22, 333]
    assert c.is_allowed(22)
    assert not c.is_allowed(2)


def test_whitelist_invalid():
    with pytest.raises(ConfigError):
        parse_allowed_user_ids(None)
    with pytest.raises(ConfigError):
        parse_allowed_user_ids("1, banana")
    with pytest.raises(ConfigError):
        parse_allowed_user_ids(",,")
    with pytest.raises(ConfigError):
        load_config({"BOT_TOKEN": "x"})  # missing ALLOWED_USER_IDS
    with pytest.raises(ConfigError):
        load_config({**BASE, "ALLOWED_USER_IDS": "1", "MAX_CONCURRENT": "0"})
    with pytest.raises(ConfigError):
        load_config({**BASE, "MULTI_AUDIO": "maybe"})
    with pytest.raises(ConfigError):
        load_config({**BASE, "DEFAULT_LANG": "xx"})
    with pytest.raises(ConfigError):
        load_config({**BASE, "PLEX_URL": "plex://nope"})
    with pytest.raises(ConfigError):
        load_config({**BASE, "PLEX_TOKEN": "tok"})  # token without URL
    with pytest.raises(ConfigError):
        load_config({**BASE, "BOT_TOKEN": ""})


def test_plex_pair_required():
    c = load_config({**BASE, "PLEX_URL": "https://plex.example", "PLEX_TOKEN": "tok"})
    assert c.plex_enabled is True
    assert c.plex_url == "https://plex.example"
    assert load_config(BASE).plex_enabled is False


def test_bot_api_base_urls():
    c = load_config(
        {
            **BASE,
            "TELEGRAM_BOT_API_BASE_URL": "http://bot-api:8081",
            "TELEGRAM_BOT_API_BASE_FILE_URL": "http://bot-api:8081/file",
        }
    )
    assert c.bot_api_base_url == "http://bot-api:8081"
    assert c.bot_api_base_file_url == "http://bot-api:8081/file"
    # empty strings are treated as unset (default mode)
    assert (
        load_config(
            {
                **BASE,
                "TELEGRAM_BOT_API_BASE_URL": "",
                "TELEGRAM_BOT_API_BASE_FILE_URL": "",
            }
        ).bot_api_base_url
        is None
    )
    with pytest.raises(ConfigError):
        load_config({**BASE, "TELEGRAM_BOT_API_BASE_URL": "bot-api:8081"})


def test_values(tmp_path):
    c = load_config(
        {
            **BASE,
            "DATA_DIR": str(tmp_path),
            "MAX_CONCURRENT": "5",
            "CONCURRENT_FRAGMENTS": "8",
            "MULTI_AUDIO": "0",
            "AUTO_UPDATE": "0",
            "UPDATE_CHECK_HOURS": "12",
            "WRITE_NFO": "0",
            "DEBUG_ACCESS": "1",
            "VIDEO_FORMAT": "bv*[height<=720]+ba/b",
        }
    )
    assert c.data_dir == tmp_path
    assert c.max_concurrent == 5
    assert c.concurrent_fragments == 8
    assert c.multi_audio is False
    assert c.auto_update is False
    assert c.update_check_hours == 12.0
    assert c.write_nfo is False
    assert c.debug_access is True
    assert c.video_format == "bv*[height<=720]+ba/b"
