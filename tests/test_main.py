import asyncio
import socket
import threading
import time

from bot import main as bot_main
from bot.config import load_config

BASE = {"BOT_TOKEN": "123:abc", "ALLOWED_USER_IDS": "all"}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_listener() -> tuple[socket.socket, int]:
    """Plain-socket accept loop on a daemon thread — a guaranteed-closing peer,
    so nothing in the test can hang on a half-open asyncio connection."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = srv.getsockname()[1]

    def accept() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.close()

    threading.Thread(target=accept, daemon=True).start()
    return srv, port


def test_wait_no_base_url_returns_immediately():
    # Default mode: no server to wait for.
    c = load_config(BASE)
    t0 = time.monotonic()
    asyncio.run(bot_main._wait_for_bot_api(c, timeout=0.1))
    assert time.monotonic() - t0 < 1.0


def test_wait_reachable_port_returns_quickly():
    srv, port = _start_listener()
    try:
        c = load_config(
            {**BASE, "TELEGRAM_BOT_API_BASE_URL": f"http://127.0.0.1:{port}"}
        )
        t0 = time.monotonic()
        asyncio.run(bot_main._wait_for_bot_api(c, timeout=5.0))
        assert time.monotonic() - t0 < 2.0  # connected on the first attempt
    finally:
        srv.close()


def test_wait_unreachable_port_times_out_and_returns():
    port = _free_port()  # nothing listening here
    c = load_config({**BASE, "TELEGRAM_BOT_API_BASE_URL": f"http://127.0.0.1:{port}"})
    # Short timeout; must return (not raise) so startup can proceed and surface a
    # clear get_me error instead of hanging forever.
    t0 = time.monotonic()
    asyncio.run(bot_main._wait_for_bot_api(c, timeout=0.4))
    assert 0.3 <= time.monotonic() - t0 < 2.0
