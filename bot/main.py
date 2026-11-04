"""Wiring and lifecycle: config -> services -> Telegram app -> updater loop.

Startup:
    1. remove partial files a previously interrupted run left behind
    2. run the startup update check (a failure never blocks startup)
    3. start the download manager's workers and the periodic updater loop
    4. long-poll for Telegram updates (no inbound ports)

Shutdown (SIGTERM/SIGINT via PTB):
    1. stop polling, let in-flight downloads finish within a 30s grace window
    2. kill what remains, clean up their partial files
    3. leftovers from a hard kill are removed on the next startup
"""

from __future__ import annotations

import asyncio
import logging
import sys
from urllib.parse import urlparse

import httpx
from telegram.ext import Application, ApplicationBuilder

from . import handlers, ytdlp as ytdlp_mod
from .config import Config, load_config
from .i18n import open_store
from .probe import RealProbeSpawner
from .store import Archive, Library, Stats
from .updater import Updater
from .ytdlp import DownloadManager

log = logging.getLogger("plexinbox")


def build_services(
    config: Config, http: httpx.AsyncClient | None = None
) -> handlers.Services:
    """Assemble every collaborator the handlers need (all injectable in tests)."""
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    # Verify the volume is actually writable *by this process* — a bind mount
    # can exist but be owned by another uid, which used to surface much later
    # as a cryptic PermissionError on the first download.
    try:
        probe = data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"DATA_DIR '{data_dir}' is not writable by the bot process: {exc}. "
            f"Fix ownership, e.g. on the host: chown -R $(id -u) <path-to-data-dir>"
        ) from exc
    if http is None:
        http = httpx.AsyncClient(timeout=30.0)
    archive = Archive(data_dir)
    manager = DownloadManager(config, archive, http=http)
    return handlers.Services(
        config=config,
        lang_store=open_store(data_dir / "langs.json", config.default_lang),
        probe_spawner=RealProbeSpawner(),
        archive=archive,
        library=Library(data_dir),
        stats=Stats(data_dir),
        manager=manager,
        updater=Updater(config, state_path=str(data_dir / "update-state.json")),
        http=http,
    )


async def _updater_loop(svc: handlers.Services, stop: asyncio.Event) -> None:
    """Re-check PyPI at most once per ``UPDATE_CHECK_HOURS``; never raise."""
    interval = svc.config.update_check_hours * 3600
    while not stop.is_set():
        try:
            st = await svc.updater.check_and_maybe_upgrade()
            extra = f" error={st.error}" if st.error else ""
            log.info(
                "update check: current=%s latest=%s upgraded=%s%s",
                st.current,
                st.latest,
                st.upgraded,
                extra,
            )
        except Exception as exc:  # an update failure must never stop the bot
            log.warning("update check failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except (TimeoutError, asyncio.TimeoutError):
            pass


async def _startup(config: Config, svc: handlers.Services) -> None:
    removed = ytdlp_mod.cleanup_leftovers(config.data_dir)
    if removed:
        log.info(
            "removed %d leftover file(s) from a previous interrupted run", removed
        )
    try:
        st = await svc.updater.check_and_maybe_upgrade()
        if st.upgraded:
            log.info("yt-dlp upgraded to %s", st.current)
        elif st.error:
            log.info("update check: %s (keeping %s)", st.error, st.current)
        else:
            log.info("yt-dlp %s is up to date", st.current)
    except Exception as exc:
        log.warning("startup update check failed: %s", exc)


async def _wait_for_bot_api(config: Config, timeout: float = 90.0) -> None:
    """When traffic is routed through a local Bot API Server, wait until it
    accepts connections before polling. PTB's one-shot ``get_me`` at startup has
    no retry, so starting first would crash the bot; this is the ordering gate.
    """
    if not config.bot_api_base_url:
        return
    u = urlparse(config.bot_api_base_url)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    if not host or not port:
        return
    log.info("waiting for Bot API server at %s:%s …", host, port)
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            # A successful TCP connect proves the server is listening; that is
            # all we need (a bare connect leaves the peer to close its side, so
            # we don't await wait_closed, which could hang on a quiet server).
            _reader, writer = await asyncio.open_connection(host, port)
        except OSError:
            if asyncio.get_event_loop().time() >= deadline:
                log.warning(
                    "Bot API server %s:%s still unreachable after %ss — trying anyway",
                    host,
                    port,
                    int(timeout),
                )
                return
            await asyncio.sleep(0.5)
            continue
        writer.close()
        log.info("Bot API server %s:%s is reachable", host, port)
        return


def run(config: Config | None = None) -> None:
    """Blocking entry point: wire everything and long-poll until SIGTERM/SIGINT."""
    config = config or load_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    svc = build_services(config)
    asyncio.run(_startup(config, svc))

    stop_evt = asyncio.Event()
    holder: dict[str, asyncio.Task] = {}

    async def on_post_init(application: Application) -> None:
        await svc.manager.start()
        log.info("download manager started (%d worker(s))", config.max_concurrent)
        holder["updater"] = asyncio.create_task(_updater_loop(svc, stop_evt))

    async def on_post_shutdown(application: Application) -> None:
        stop_evt.set()
        log.info("shutting down: giving in-flight downloads a grace window…")
        await svc.manager.stop(grace=30.0)
        task = holder.get("updater")
        if task is not None:
            task.cancel()
        log.info("shutdown complete")

    builder = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(on_post_init)
        .post_shutdown(on_post_shutdown)
    )
    # Route Bot API traffic through a local Bot API Server when configured. In
    # that mode getFile returns a *local* path, lifting the 20 MB cap on files
    # a bot can receive. (PTB does not read these from the environment itself.)
    if config.bot_api_base_url:
        builder = builder.base_url(config.bot_api_base_url)
    if config.bot_api_base_file_url:
        builder = builder.base_file_url(config.bot_api_base_file_url)
    app = builder.build()
    # handlers read services via context.bot_data["svc"]; wire it before the first update
    app.bot_data["svc"] = svc
    handlers.register(app, svc)
    # If we're proxying through a local Bot API Server, make sure it's listening
    # before the one-shot startup get_me (which has no retry) runs.
    asyncio.run(_wait_for_bot_api(config))
    app.run_polling(drop_pending_updates=True)


def cli() -> None:
    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    try:
        run(config)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
