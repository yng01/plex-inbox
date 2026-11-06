"""Repro: drive the 'da' (download-all-audio) callback path with fakes.

Run: docker compose run --rm test python /work/tests/repro_da.py
"""

import asyncio
import sys
import tempfile
import traceback

sys.path.insert(0, "/work/tests")
sys.path.insert(0, "/work")

from fakes import (
    FakeCallbackQuery,
    FakeContext,
    FakeHttp,
    FakeLineProc,
    FakeUpdate,
    FakeUser,
    RecordingSpawner,
)

from bot import handlers
from bot.config import load_config
from bot.i18n import open_store
from bot.probe import UrlInfo
from bot.settings import SettingsStore
from bot.store import Archive, Library, Stats
from bot.updater import Updater
from bot.ytdlp import DownloadManager


def build():
    tmp = tempfile.mkdtemp(prefix="repro-")
    cfg = load_config(
        {
            "BOT_TOKEN": "123:abc",
            "ALLOWED_USER_IDS": "all",
            "DATA_DIR": f"{tmp}/data",
        }
    )
    archive = Archive(cfg.data_dir)
    spawner = RecordingSpawner([FakeLineProc(lines=["ok"], returncode=0)])
    svc = handlers.Services(
        config=cfg,
        lang_store=open_store(cfg.data_dir, "en"),
        settings_store=SettingsStore(cfg.data_dir),
        probe_spawner=object(),
        archive=archive,
        library=Library(cfg.data_dir),
        stats=Stats(cfg.data_dir),
        manager=DownloadManager(
            cfg, archive, spawner=spawner, http=FakeHttp(), notify_interval=0.01
        ),
        updater=Updater(cfg, state_path=f"{tmp}/upd.json"),
        http=FakeHttp(),
    )
    return cfg, svc


async def main() -> None:
    cfg, svc = build()
    ctx = FakeContext()
    ctx.bot_data["svc"] = svc

    info = UrlInfo(
        id="abc123",
        title="Great Video",
        site="YouTube",
        url="https://youtube.com/watch?v=abc123",
        channel="Cool Channel",
        duration=372.5,
        is_playlist=False,
        raw={},
    )
    ctx.user_data["pending"] = {"info": info, "page": 0}

    cq = FakeCallbackQuery(data="da")
    upd = FakeUpdate(
        user=FakeUser(id=111, username="tester"),
        message=cq.message,  # PTB exposes this as update.effective_message
        callback_query=cq,
    )

    try:
        await handlers.on_callback(upd, ctx)
        print("on_callback returned without raising")
    except Exception:
        print("on_callback RAISED:")
        traceback.print_exc()

    print("pending after:", ctx.user_data.get("pending"))
    print("edits:", cq.message.edits)
    print("replies:", cq.message.replies)

    # let a worker pick the job up, then inspect its fate
    await svc.manager.start()
    await asyncio.sleep(0.3)
    jobs = svc.manager._jobs
    for j in jobs.values():
        print(f"job {j.id}: state={j.state} error={j.error!r}")
    await svc.manager.stop()


asyncio.run(main())
