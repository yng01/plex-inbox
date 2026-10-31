"""Updater tests: version parsing, upgrade success, failed-upgrade-keeps-old,
up-to-date, disabled, and unknown-version paths. No network: the runner and the
PyPI fetch are fakes.
"""

import pytest

from bot.config import load_config
from bot.updater import Updater, is_newer, parse_version
from fakes import RecordingRunner


def make_config(tmp_path, **over):
    env = {
        "BOT_TOKEN": "123:abc",
        "ALLOWED_USER_IDS": "all",
        "DATA_DIR": str(tmp_path),
    }
    env.update(over)
    return load_config(env)


def make_updater(tmp_path, runner, pypi_fetch=None, **env_over):
    cfg = make_config(tmp_path, **env_over)
    return Updater(cfg, runner=runner, pypi_fetch=pypi_fetch)


# --- pure version helpers ----------------------------------------------------


@pytest.mark.parametrize(
    ("v", "expected"),
    [
        ("2026.01.15", (2026, 1, 15)),
        ("2025.12.02+dev", (2025, 12, 2)),
        ("2025-11-01", (2025, 11, 1)),
        ("garbage", (0,)),
        ("", (0,)),
    ],
)
def test_parse_version(v, expected):
    assert parse_version(v) == expected


def test_is_newer_matrix():
    assert is_newer("2025.12.02", "2026.01.15") is True
    assert is_newer("2026.01.15", "2025.12.02") is False
    assert is_newer("2026.01.15", "2026.01.15") is False
    assert is_newer("2025.12.9", "2025.12.10") is True
    # 12 > 9 as digits, not string comparison
    assert is_newer("2026.01.9", "2026.01.10") is True


# --- check_and_maybe_upgrade -------------------------------------------------


def _fetch(version):
    async def fetch(url):
        fetch.calls += 1
        return version

    fetch.calls = 0
    return fetch


async def test_upgrade_success_updates_version_and_state(tmp_path):
    runner = RecordingRunner()
    runner.push("2025.12.02\n", "", 0)  # yt-dlp --version (current)
    runner.push("", "Successfully installed yt-dlp-2026.01.15", 0)  # pip upgrade
    runner.push("2026.01.15\n", "", 0)  # yt-dlp --version (verify)
    up = make_updater(tmp_path, runner, pypi_fetch=_fetch("2026.01.15"))

    st = await up.check_and_maybe_upgrade()

    assert st.upgraded is True
    assert st.error == ""
    assert st.current == "2026.01.15"
    assert st.latest == "2026.01.15"
    assert st.checked_at  # timestamp recorded
    # the pip upgrade was actually invoked, with the right shape
    pip_cmd = runner.commands[1]
    assert "pip" in pip_cmd and "install" in pip_cmd and "yt-dlp" in pip_cmd
    assert "--upgrade" in pip_cmd
    assert "yt-dlp" in runner.commands[0] and runner.commands[0][-1] == "--version"


async def test_upgrade_failure_keeps_current_version(tmp_path):
    runner = RecordingRunner()
    runner.push("2025.12.02\n", "", 0)
    runner.push("", "ERROR: Could not install the following packages", 1)
    up = make_updater(tmp_path, runner, pypi_fetch=_fetch("2026.01.15"))

    st = await up.check_and_maybe_upgrade()

    assert st.upgraded is False
    assert "Could not install" in st.error
    assert st.current == "2025.12.02"  # old version still reported
    # no verification run after a failed upgrade
    assert len(runner.commands) == 2


async def test_up_to_date_does_not_upgrade(tmp_path):
    runner = RecordingRunner()
    runner.push("2026.01.15\n", "", 0)
    up = make_updater(tmp_path, runner, pypi_fetch=_fetch("2026.01.15"))

    st = await up.check_and_maybe_upgrade()

    assert st.upgraded is False
    assert st.error == ""
    assert st.current == "2026.01.15"
    assert len(runner.commands) == 1  # only --version, no pip


async def test_disabled_never_upgrades(tmp_path):
    runner = RecordingRunner()
    runner.push("2025.12.02\n", "", 0)
    up = make_updater(
        tmp_path, runner, pypi_fetch=_fetch("2026.01.15"), AUTO_UPDATE="0"
    )

    st = await up.check_and_maybe_upgrade()

    assert st.upgraded is False
    assert st.error == "disabled"
    assert len(runner.commands) == 1  # only --version, no pip
    assert all("pip" not in c for c in runner.commands)


async def test_unknown_version_short_circuits(tmp_path):
    # yt-dlp itself broken: current version unreadable -> no upgrade attempt
    runner = RecordingRunner()
    runner.push("", "yt-dlp: not found", 127)
    up = make_updater(tmp_path, runner, pypi_fetch=_fetch("2026.01.15"))

    st = await up.check_and_maybe_upgrade()

    assert st.upgraded is False
    assert st.error == "unknown-version"
    assert len(runner.commands) == 1

    # ...or PyPI unreachable
    runner2 = RecordingRunner()
    runner2.push("2025.12.02\n", "", 0)
    up2 = make_updater(tmp_path, runner2, pypi_fetch=_fetch(None))
    st2 = await up2.check_and_maybe_upgrade()
    assert st2.error == "unknown-version"
    assert st2.upgraded is False
    assert len(runner2.commands) == 1


async def test_state_is_persisted_on_updater(tmp_path):
    runner = RecordingRunner()
    runner.push("2025.12.02\n", "", 0)
    runner.push("", "ok", 0)
    runner.push("2026.01.15\n", "", 0)
    up = make_updater(tmp_path, runner, pypi_fetch=_fetch("2026.01.15"))

    st = await up.check_and_maybe_upgrade()

    assert up.state is st  # stats view can read the last result
    assert up.state.upgraded is True


async def test_state_persists_across_instances(tmp_path):
    runner = RecordingRunner()
    runner.push("2025.12.02\n", "", 0)
    runner.push("", "ok", 0)
    runner.push("2026.01.15\n", "", 0)
    cfg = make_config(tmp_path)
    state_path = str(tmp_path / "update-state.json")
    up = Updater(
        cfg, runner=runner, pypi_fetch=_fetch("2026.01.15"), state_path=state_path
    )
    await up.check_and_maybe_upgrade()

    assert (tmp_path / "update-state.json").exists()

    # a fresh instance (e.g. after a restart) restores the last result
    again = Updater(cfg, state_path=state_path)
    assert again.state.upgraded is True
    assert again.state.current == "2026.01.15"
    assert again.state.latest == "2026.01.15"


async def test_state_load_tolerates_bad_file(tmp_path):
    cfg = make_config(tmp_path)
    p = tmp_path / "update-state.json"
    p.write_text("not json", encoding="utf-8")
    up = Updater(cfg, state_path=str(p))
    assert up.state == Updater(cfg).state  # empty state, no crash
