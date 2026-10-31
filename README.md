# yt-dlp-telegram-bot

A **private, self-hosted Telegram bot** that turns any yt-dlp-supported video into a
**Plex-ready archive**: proper file names, artwork, NFO metadata, and — when a video
has several audio tracks — **all of them embedded as switchable streams**.

Everything runs in **one Docker container** using long polling — no inbound ports, no
web UI, no auth layer. Send a link, tap **Download**, and the video lands in
`<site>/<channel>/<Title> (Year).mkv` with its thumbnail, poster, and metadata,
attributed to the person who requested it.

## Features

- **Add by URL** — `yt-dlp` probes the link first; a confirmation screen shows the
  title, site, channel, duration, resolution, and every audio track with its language
  before anything downloads. Playlists offer *download all* vs. picking entries.
- **Who requested it** — the requestor (username, or `user:<id>` when there is none)
  is recorded in the dedupe archive, the sidecar JSON, and the NFO
  (`requested:ali`) so Plex collections can filter by person.
- **Multi-audio** — when `MULTI_AUDIO=1` and a video has several audio tracks, all of
  them are embedded as separate streams in MKV and exposed in Plex as selectable
  audio. Single-track videos are unaffected (best video+audio, MP4).
- **Plex-ready on disk** — `<Title> (Year)` naming, per-video thumbnail with a matching
  stem, channel poster, and a Kodi-style NFO carrying `<title>`, `<summary>`,
  `<year>`, `<genre>`, and lowercase single-line `<tags>`
  (`site:youtube`, `channel:cool channel`, `requested:ali`, `audio:en`, `audio:ar`,
  `year:2024`, `duration:6m12s`, `genre:documentary`, …).
- **Live progress** — one status message per download, edited in place with % and
  speed (throttled), ending in ✅ done (path, size, audio-track count) or ⚠️ failed
  (short reason). One bad download never takes down the bot.
- **Queue & cancel** — a concurrency cap (default 2) queues the excess; the queue is
  browsable and every job — queued or downloading — is cancellable. Cancelling kills
  the process and removes its partial files (thumbnail, NFO, journals) without
  touching other videos in the same folder.
- **No duplicates** — a global `archive.txt` ledger means re-adding an already
  archived video is skipped with “already in your library”.
- **Auto-updates** — at startup and at most once every `UPDATE_CHECK_HOURS`, the bot
  compares the installed `yt-dlp` version against PyPI and upgrades in place when a
  newer release exists. Because each download spawns a fresh `yt-dlp` process, the new
  version applies to the next download with no restart. A failed upgrade never blocks
  or crashes the bot; it keeps the old version and retries next cycle.
- **Bilingual** — full English + Arabic UI; per-user language is auto-detected,
  overridable with **Language** / `/lang`, and persisted per user.
- **Whitelist** — `ALLOWED_USER_IDS` restricts who may use the bot; unknown users are
  dropped silently (or told their ID with `DEBUG_ACCESS=1`).

## Quick start

```bash
cp .env.example .env          # set BOT_TOKEN (from @BotFather) and ALLOWED_USER_IDS
docker compose up -d --build
```

Point Plex at `./data` (the container's `/data`) as a videos library. That's it — no
ports to open, nothing to expose.

> First run on an empty `.env`? Set `DEBUG_ACCESS=1`, message the bot, and it will
> reply with your numeric Telegram ID to add to `ALLOWED_USER_IDS`.

## Docker image (GHCR)

The repository also publishes the image to GitHub Container Registry (see
[Repository & CI](#repository--ci)). To run the prebuilt image instead of building
locally:

```bash
docker pull ghcr.io/<owner>/yt-dlp-telegram-bot:latest
docker run -d --name yt-dlp-bot --restart unless-stopped \
  -v "$PWD/data:/data" \
  --env-file .env \
  ghcr.io/<owner>/yt-dlp-telegram-bot:latest
```

or in compose:

```yaml
services:
  bot:
    image: ghcr.io/<owner>/yt-dlp-telegram-bot:latest
    restart: unless-stopped
    env_file: .env
    environment:
      DATA_DIR: /data
    volumes:
      - ./data:/data
```

The image is as private as the repository: pulling requires a GitHub token with
`read:packages` scope (log in with `docker login ghcr.io` using your username and a
PAT, or set the registry credentials in `~/.docker/config.json`).

## Using the bot

- **➕ Add a video** — tap it (or send `/add <url>`, or just paste a link). The bot
  probes the URL and shows title / site / channel / duration / resolution / audio
  tracks with **Download** and **Cancel**. Playlist URLs additionally offer
  **Download all** and per-entry browsing.
- **⏳ Queue** — see active and queued downloads; **Cancel** works on any of them.
- **📚 Library** — browse site → channel → files (size, date, audio tracks), with paging.
- **📊 Stats** — totals, per-site and per-requestor breakdowns, queue length, and the
  current `yt-dlp` version plus when it was last updated.
- **🌐 Language** — pick English or Arabic (also `/lang`).

Slash commands (`/add /queue /library /stats /lang /help`) are shortcuts to the menu.

## Disk layout

```
data/
├── archive.txt                      # dedupe ledger: one line per archived video
├── langs.json                       # per-user language preferences
└── YouTube/
    └── Cool Channel/
        ├── poster.jpg               # channel avatar (once per channel)
        ├── Great Video (2024).mkv   # video, all audio streams embedded
        ├── Great Video (2024).jpg   # thumbnail, stem matches the video
        ├── Great Video (2024).nfo   # Kodi/Plex metadata + tags
        └── abc123.info.json         # sidecar: requestor, date, path, size, audio tracks
```

File names come from the probe (sanitized, ` (Year)` appended when the probe provides
one) — never from raw video IDs.

## Plex: collections via tags

Every NFO's `<tags>` are lowercase, single-line, `key:value`, so they're trivial to
filter. Suggested smart collections:

| Collection | Plex filter |
|---|---|
| Videos I archived | `tag contains requested:<your-user>` |
| Everything from one channel | `tag contains channel:cool channel` |
| Multi-audio videos | `tag starts with audio:` |
| 2024 releases | `tag is year:2024` |
| Genres | `tag is genre:documentary` |

If you set `PLEX_URL` + `PLEX_TOKEN`, the bot sends a library refresh after each
finished download so new files appear immediately (failures are silent).

## Configuration

All configuration is via environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | — | Telegram bot token (**required**) |
| `ALLOWED_USER_IDS` | `all` | `all`/`*` or comma-separated numeric user IDs |
| `DEBUG_ACCESS` | `0` | Reply not-allowed users with their numeric ID |
| `DATA_DIR` | `/data` | Library root |
| `MAX_CONCURRENT` | `2` | Parallel downloads; excess queues |
| `MULTI_AUDIO` | `1` | Embed all audio tracks (MKV) when available |
| `VIDEO_FORMAT` | — | Optional yt-dlp format override (wins over `MULTI_AUDIO`) |
| `AUTO_UPDATE` | `1` | Auto-upgrade yt-dlp from PyPI |
| `UPDATE_CHECK_HOURS` | `24` | Minimum hours between PyPI checks |
| `WRITE_NFO` | `1` | Write NFO + sidecar JSON per video |
| `PLEX_URL` / `PLEX_TOKEN` | — | Optional post-download refresh of Plex |
| `DEFAULT_LANG` | `en` | Base language before the user picks one (`en`/`ar`) |

## How downloads work

1. **Probe** — `yt-dlp -J <url>` (60 s timeout). Failures are classified into short,
   user-safe messages (unsupported / private / region / unavailable / timeout).
2. **Confirm** — details + inline buttons; nothing downloads until you say so.
3. **Queue** — at most `MAX_CONCURRENT` jobs run at once; the rest wait in an
   ordered queue.
4. **Download** — `yt-dlp` runs as a child process with a progress template; the bot
   edits one status message with %/speed (throttled to ~2 s). Multi-audio uses
   `bv*+mergeall[vcodec=none] --audio-multistreams` (MKV); single-track uses
   `bv*+ba/b`.
5. **Finalize** — thumbnail download, NFO + sidecar write, channel poster, archive
   ledger entry, optional Plex refresh, final ✅ message.
6. **Failure / cancel** — the process is killed and exactly the files that appeared
   during this run are removed (scoped per job, so concurrent jobs in the same
   channel folder are safe). A journal file records the in-flight run, and startup
   cleanup removes leftovers from a previously interrupted bot.

## Development

```bash
docker compose run --rm --build test   # full pytest suite, no network/token needed
```

The suite covers i18n completeness (EN/AR parity), config parsing, probe parsing
with canned `yt-dlp -J` fixtures (including multi-audio), naming/Plex metadata,
format-selection, the full download state machine with a fake subprocess
(queued → downloading → done / failed / cancelled, partial-file cleanup,
concurrency cap), and the updater (upgrade success, failure keeps the old version,
disabled). Every external effect — subprocesses, HTTP — is injectable, so tests run
with no network and no bot token.

Modules:

| File | Responsibility |
|---|---|
| `bot/config.py` | env parsing/validation (fail fast with `ConfigError`) |
| `bot/i18n.py` | all user-facing strings, EN + AR, per-user language store |
| `bot/probe.py` | `yt-dlp -J` probe, URL/entry/audio-track parsing, error classification |
| `bot/ytdlp.py` | download manager: queue, workers, progress, cancel, cleanup |
| `bot/plex.py` | naming, NFO, sidecar, poster, Plex refresh |
| `bot/store.py` | dedupe archive, library browser, stats |
| `bot/updater.py` | yt-dlp version check + in-place upgrade |
| `bot/handlers.py` | Telegram UI: menu, add/confirm, queue, library, stats, language |
| `bot/main.py` | wiring, startup/shutdown lifecycle |

## Repository & CI

`.github/workflows/docker.yml` runs on every push, PR, tag, or manual dispatch:

1. **Test** — the full pytest suite in the same Docker image (no secrets needed).
2. **Build & push** (skipped for PRs) — multi-arch build (`linux/amd64`,
   `linux/arm64`) pushed to GHCR:
   - `ghcr.io/<owner>/yt-dlp-telegram-bot:<sha>` — every push
   - `ghcr.io/<owner>/yt-dlp-telegram-bot:latest` — pushes to the default branch
   - `ghcr.io/<owner>/yt-dlp-telegram-bot:<tag>` — version tags like `v1.0.0`

It uses the built-in `GITHUB_TOKEN` with `packages: write` (default for repo
workflows) — nothing to configure. First push creates the package at
**_Packages_** in the repository; check the workflow badge/runs page for status.

To publish the repository:

```bash
# create the repo on github.com (public or private), then:
git remote add origin git@github.com:<owner>/yt-dlp-telegram-bot.git
git push -u origin master
```
