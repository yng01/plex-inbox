# plex-inbox

A **private, self-hosted Telegram bot** that turns any yt-dlp-supported video into a
**Plex-ready archive**: proper file names, artwork, NFO metadata, and — when a video
has several audio tracks — **all of them embedded as switchable streams**.

Everything runs in **one Docker container** using long polling — no inbound ports, no
web UI, no auth layer. Send a link (or a video file), tap **Download**, and the video
lands in `<site>/<channel>/<video-id>.mkv` with its thumbnail, poster, and metadata,
attributed to the person who requested it.

## Features

- **Add by URL** — `yt-dlp` probes the link first; a confirmation screen shows the
  title, site, channel, duration, resolution, and every audio track with its language
  before anything downloads. Playlists offer *download all* vs. picking entries.
- **Or just send a video file** — the bot accepts video and video-note messages and
  archives them through the same flow (queue, single progress message, dedupe, NFO).
  They're named by their Telegram file id, so re-sending the same file is a no-op.
  By default Telegram caps files a bot can receive at **20 MB**; enable the optional
  local Bot API Server (one-time setup, [below](#receiving-video-files-larger-than-20-mb))
  to lift that cap for files of any size.
- **Who requested it** — the requestor (username, or `user:<id>` when there is none)
  is recorded in the dedupe archive, the sidecar JSON, and the NFO
  (`requested:ali`) so Plex collections can filter by person.
- **Multi-audio** — when `MULTI_AUDIO=1` and a video has several audio tracks, all of
  them are embedded as separate streams in MKV and exposed in Plex as selectable
  audio. Single-track videos are unaffected (best video+audio, MP4).
- **Quality selection + per-user defaults** — the confirmation screen offers a video
  resolution cap (best / 1080p / 720p / 480p / 360p). Under **⚙️ Settings**, each user
  sets their own defaults: audio mode (all tracks / original audio / audio-only),
  quality, and **auto-download** — with it on, sending a link skips the confirmation
  screen and starts the download immediately with the saved options (the "probing…"
  message just becomes the job's progress message). Off (the default) means "ask me
  every time", with the saved values pre-filling the screen.
- **Plex-ready on disk** — files are named after the item's own id (stable, never
  collides), per-video thumbnail with a matching
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
- **No duplicates** — a global SQLite ledger (`archive.db`) means re-adding an already
  archived video is skipped with “already in your library”. The ledger is
  **mode-scoped** (`…:video` / `…:audio`), so the video and the audio-only
  version of the same item are independent: you can have one without the other,
  and downloading one never blocks the other.
- **Delete from the bot** — the **📚 Library** → a channel's file list offers a 🗑
  button on every file. Confirm, and the item is removed for good: media file, NFO,
  thumbnail, and sidecar are deleted **and** its dedupe ledger entry (both the
  `:video` and `:audio` slots) is cleared, so the slot is free and you can re-add
  the link any time to download it again. The shared channel poster is kept, and a
  Plex refresh fires afterwards when `PLEX_URL`/`PLEX_TOKEN` are set.
- **Auto-updates** — at startup and at most once every `UPDATE_CHECK_HOURS`, the bot
  compares the installed `yt-dlp` version against PyPI and upgrades in place when a
  newer release exists. Because each download spawns a fresh `yt-dlp` process, the new
  version applies to the next download with no restart. A failed upgrade never blocks
  or crashes the bot; it keeps the old version and retries next cycle.
- **Multilingual** — UI in English, Arabic, Turkish, Spanish, Russian, French, German,
  and Portuguese; per-user language is auto-detected (incl. region codes like
  `pt-BR`), overridable with **Language** / `/lang`, and persisted per user.
- **Whitelist** — `ALLOWED_USER_IDS` restricts who may use the bot; unknown users are
  dropped silently (or told their ID with `DEBUG_ACCESS=1`).

## Quick start

```bash
cp .env.example .env          # set BOT_TOKEN (from @BotFather) and ALLOWED_USER_IDS
docker compose up -d --build bot   # name the service so the one-shot `test` job isn't run
```

Point Plex at `./data` (the container's `/data`) as a videos library. That's it — no
ports to open, nothing to expose.

> First run on an empty `.env`? Set `DEBUG_ACCESS=1`, message the bot, and it will
> reply with your numeric Telegram ID to add to `ALLOWED_USER_IDS`.

## Receiving video files larger than 20 MB

By default the bot talks straight to `api.telegram.org`, which caps files a bot can
**receive** at 20 MB (larger uploads reply with a “send a link instead” hint). To
accept video files of *any size*, run the included local Bot API Server. This is a
one-time setup:

1. Get a free API ID/Hash: <https://my.telegram.org> → **API development tools**
   (sign in with the same phone number as your bot's account; you'll confirm by SMS).
2. In `.env`, fill in the four `TELEGRAM_*` lines for this (uncomment them):
   ```env
   TELEGRAM_API_ID=<your api id>
   TELEGRAM_API_HASH=<your api hash>
   TELEGRAM_BOT_API_BASE_URL=http://bot-api:8081/bot
   TELEGRAM_BOT_API_BASE_FILE_URL=http://bot-api:8081/file/bot
   ```
3. Start with the `local-api` profile:
   ```bash
   docker compose --profile local-api up -d --build bot
   ```

The server stores received files on a Docker volume that the bot mounts read-only at
the same path, so its “download” is a plain local file copy (no re-streaming, no size
cap). Without step 3 the stack runs exactly as before (20 MB cap, direct to Telegram).

## Docker image (GHCR)

The repository also publishes the image to GitHub Container Registry (see
[Repository & CI](#repository--ci)). To run the prebuilt image instead of building
locally:

```bash
docker pull ghcr.io/<owner>/plex-inbox:latest
docker run -d --name plex-inbox --restart unless-stopped \
  -v "$PWD/data:/data" \
  --env-file .env \
  ghcr.io/<owner>/plex-inbox:latest
```

or in compose:

```yaml
services:
  bot:
    image: ghcr.io/<owner>/plex-inbox:latest
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
  tracks with download options (**all audio**, **original audio**, **audio only** —
  no video) and **Cancel**. Playlist URLs additionally offer **Download all** and
  per-entry browsing.
- **⏳ Queue** — see active and queued downloads; **Cancel** works on any of them.
- **📚 Library** — browse site → channel → files (size, date, audio tracks). Every file
  has a 🗑 button: tap it, confirm, and the item is deleted (files **and** its dedupe
  ledger entry), so it can be re-downloaded later if you want it back.
- **📊 Stats** — totals, per-site and per-requestor breakdowns, queue length, and the
  current `yt-dlp` version plus when it was last updated.
- **🌐 Language** — pick English or Arabic (also `/lang`).
- **⚙️ Settings** — default audio mode, default quality, and auto-download (see above).

The menu itself is a **persistent reply-keyboard button bar** pinned above the input
box — always visible, no scrolling. Re-sending `/menu` (or tapping back-to-menu from
any screen) updates the **same** menu message in place, so the chat never fills up
with duplicate menus. Tapping the menu's own title (📺 Video Archive / 📺 أرشيف
الفيديو) re-opens the menu. Slash commands (`/add /queue /library /stats /lang`) are
shortcuts to the same screens.

## Disk layout

```
data/
├── archive.db                       # dedupe ledger (SQLite): one row per item,
│                                    #   key = <site>:<id>:video | <site>:<id>:audio
├── langs.json                       # per-user language preferences
└── YouTube/
    └── Cool Channel/
        ├── poster.jpg               # channel avatar (once per channel)
        ├── jPjbMA0wvrI.mkv          # video, all audio streams embedded
        ├── jPjbMA0wvrI.m4a          # audio-only (independent slot; coexists)
        ├── jPjbMA0wvrI.jpg          # thumbnail, stem matches the video
        ├── jPjbMA0wvrI.nfo          # Kodi/Plex metadata + tags (carries the title)
        └── jPjbMA0wvrI.info.json    # sidecar: requestor, date, path, size, audio tracks
```

File names use the item's own id from the probe (sanitized) — the human-readable
title lives in the NFO and sidecar, so Plex still shows the proper title. For
videos you send to the bot directly, the id is the Telegram file id, which makes
re-sending the same file a no-op.

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
2. **Confirm** — details + inline buttons (audio mode **and** quality); nothing
   downloads until you say so. With auto-download enabled in **Settings**, this step
   is skipped and the job starts immediately with the user's saved options.
3. **Queue** — at most `MAX_CONCURRENT` jobs run at once; the rest wait in an
   ordered queue.
4. **Download** — `yt-dlp` runs as a child process with a progress template; the bot
   edits one status message with %/speed (throttled to ~2 s). Format selection:
   - **All audio** (`MULTI_AUDIO=1`): `bv*+mergeall[vcodec=none] --audio-multistreams` (MKV).
   - **Original audio**: `bv*+ba[language=<lang>]/bv*+ba/b` — best video + the
     original-language track (video is kept in every fallback, so it never degrades
     to audio-only).
   - **Audio only**: `ba[language=<lang>]/ba/b` — no video stream; the original-language
     track when the probe knows it, else the best audio (lands as `.m4a`/`.mp3`/…).
   - **Single track**: `bv*+ba/b`.
   - **Quality cap** (optional): the `bv*` part becomes `bv*[height<=<cap>]`, so the
     best video *at or below* that height is chosen. It applies to the video stream
     only — audio-only downloads ignore it (there is no video stream to cap).
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

The suite covers i18n completeness (every language in `LANGS` for every key), config parsing, probe parsing
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
| `bot/settings.py` | per-user download defaults (audio mode, quality, auto-download), JSON on the data volume |
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
   - `ghcr.io/<owner>/plex-inbox:<sha>` — every push
   - `ghcr.io/<owner>/plex-inbox:latest` — pushes to the default branch
   - `ghcr.io/<owner>/plex-inbox:<tag>` — version tags like `v1.0.0`

It uses the built-in `GITHUB_TOKEN` with `packages: write` (default for repo
workflows) — nothing to configure. First push creates the package at
**_Packages_** in the repository; check the workflow badge/runs page for status.

To publish the repository:

```bash
# create the repo on github.com (public or private), then:
git remote add origin git@github.com:<owner>/plex-inbox.git
git push -u origin master
```
