# AGENTS.md

Guidance for AI coding agents (and new contributors) working in this repository.
Read this before editing. The goal: changes stay consistent, tested, and never
regress the invariants below.

## What this is

**plex-inbox** — a private, self-hosted Telegram bot that turns a video link (any
yt-dlp-supported site) **or a video file sent to the bot** into a **Plex-ready
on-disk library**: id-based file names, NFO metadata, thumbnail + channel poster,
a JSON sidecar, all audio tracks embedded, and dedupe so nothing downloads twice.

- Bot framework: `python-telegram-bot==22.8` (async).
- Downloading: `yt-dlp` (spawned as a subprocess) + `ffmpeg` (audio merging).
- HTTP (thumbnails/posters/Plex refresh): `httpx`.
- Runtime: Python 3.12 in Docker (3.11+ locally). No ports published — the bot only
  makes outbound connections.

## Repository layout

| Path | Purpose |
|---|---|
| `bot/config.py` | Load + validate config from env (`.env`). |
| `bot/main.py` | Entry point: build the PTB `Application`, wire base URLs, wait for the Bot API server, start polling. |
| `bot/handlers.py` | All Telegram command/message handlers: menu, add-by-URL, `on_video`, queue/library/stats views, `on_video` download path. |
| `bot/i18n.py` | **All** user-facing strings, in 8 languages (`LANGS`: en, ar, tr, es, ru, fr, de, pt). `t(lang, key, **kw)` renders. |
| `bot/settings.py` | Per-user download defaults (audio mode, quality, auto-download); JSON on the data volume, keyed by user id. |
| `bot/probe.py` | Probe a URL with yt-dlp → `UrlInfo` (title, id, entries, audio tracks, …). |
| `bot/store.py` | `Archive` (dedupe ledger), `Library` (browse sidecars), `Stats`. |
| `bot/plex.py` | `sanitize_filename`, NFO/sidecar writers, poster/thumbnail selection + download. |
| `bot/ytdlp.py` | `DownloadManager`, `Job`, process spawner, progress parsing, concurrency, cleanup. |
| `bot/updater.py` | Optional auto-upgrade of yt-dlp from PyPI. |
| `tests/` | pytest suite (network-free, token-free) + `fakes.py` (FakeBot/FakeHttp/RecordingSpawner/FakeLineProc/FakeVideo/…). |
| `conftest.py` | Makes the repo root importable (`import bot`) without installing. |
| `ruff.toml` | Canonical lint/format rule set (also mirrored to `~/.config/ruff/ruff.toml`). |
| `Dockerfile` | One self-contained image: Python 3.12 + ffmpeg + yt-dlp + PTB. |
| `docker-compose.yml` | `bot`, `bot-api` (profile `local-api`), `test` services. |
| `.github/workflows/docker.yml` | CI: ruff check + format check, run tests, then build + push multi-arch image to GHCR with `:latest`/short-sha/`v*` semver tags. |

## Running tests

Tests need **no Telegram token and no network** — they run against the fakes in
`tests/fakes.py`. Two equivalent ways:

```bash
# local (uses the repo .venv)
.venv/bin/python -m pytest -q

# or the same Docker image as production
docker compose run --rm --build test
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so async tests need no decorator.

## Linting

`ruff.toml` at the repo root is the **canonical** rule set (line-length 88,
target `py311`, an explicit curated `select` + documented `ignore`s). A copy
also lives at `~/.config/ruff/ruff.toml` as a user-global fallback for other
projects — keep the two in sync.

```bash
ruff check .            # lint (CI runs exactly this)
ruff format --check .   # formatting (CI runs exactly this)
ruff format .           # to apply formatting
```

CI pins `ruff==0.17.4` and gates the image build on `ruff check` +
`ruff format --check`.

## Running the bot

```bash
cp .env.example .env          # set BOT_TOKEN + ALLOWED_USER_IDS
docker compose up -d --build bot                                  # default: 20 MB file cap
docker compose --profile local-api up -d --build bot              # any-size video files
```

The `local-api` profile starts a local Bot API Server (official `--local` mode) that
proxies Bot API calls and stores received files on a shared volume, so the 20 MB
bot-download cap no longer applies.

## Invariants — do not break these

1. **i18n parity.** Every user-facing string lives in `bot/i18n.py` `STRINGS` with a
   value for every language in `LANGS`. Handlers never hardcode user-visible text —
   they call `t(...)`. `tests/test_i18n.py` enforces (a) every key has a non-empty
   value for **each** language, and (b) every `t("…")` key used in the code is
   defined. **Adding a new message means adding every language** or the suite fails.
   Unknown language falls back to `en`; an unknown key raises `KeyError` (a loud
   bug, by design).

2. **Never crash on one bad item.** A single failing probe, download, or metadata
   step must fail *that* job (with a friendly localized message) and let the rest of
   the queue/playlist proceed. Wrap per-item work; the manager must survive any
   subprocess or network error.

3. **Dedupe = ledger AND file present.** `Archive.contains(key)` returns true only if
   the item is in `archive.db` (SQLite) **and** its on-disk file (the path recorded
   at download time) still exists. A manually-deleted file is therefore re-downloadable.
   Keep the two in step: write the ledger entry only after the file is on disk.
   **Keys are mode-scoped** (`store.dedupe_key(site, id, audio_only)`):
   `<site>:<id>:video` for any video mode, `<site>:<id>:audio` for audio-only —
   so the two modes of the same item can coexist and never block each other.
   Never use a bare `site:id` key again; legacy bare entries are migrated to
   `:video`/`:audio` (by file extension) in `Archive._reload` — keep that
   migration idempotent.

4. **id-based on-disk names.** Files land at
   `<DATA_DIR>/<site>/<channel>/<id>.<ext>` (id = YouTube video id, Telegram
   `file_unique_id`, …). Title/description stay in the NFO + `.info.json` sidecar for
   Plex. `sanitize_filename("")` → `"untitled"`.

5. **Button-first, multilingual UI.** Prefer buttons over typed input; keep parity
   across every language in `LANGS`.
   The **main menu is a reply-keyboard** (sticky, always visible above the input)
   rendered by `cmd_menu`, which edits ONE canonical menu message per chat in
   place (`context.chat_data["menu_msg_id"]`) so repeats never duplicate; a
   reply-keyboard tap arrives as plain text in `on_text` and is routed via
   `_MENU_LABELS`. Sub-screens stay inline-keyboard with a back-to-menu button.
   Access is gated by `ALLOWED_USER_IDS`.

6. **Tests stay offline + token-free.** Introduce no network or real-token
   dependency; extend the fakes instead.

## Gotchas (why the code looks the way it does)

- **PTB does not auto-read `TELEGRAM_BOT_API_BASE_URL`/`_FILE_URL` from env.** They
  are wired explicitly in `bot/config.py` + `bot/main.py` via
  `ApplicationBuilder().base_url()/.base_file_url()`.
- **Base URL form is no-slash, no `{token}`.** The local server routes
  `/bot<token>/<method>` (token glued to `bot`), so use
  `http://bot-api:8081/bot` and `http://bot-api:8081/file/bot`. A trailing slash or a
  `{token}` placeholder breaks it (you'll see `Invalid port: '8081<token>'`).
- **`getFile` blocks until the whole file is on disk** in `--local` mode, so PTB's
  5 s default read timeout is too short for large uploads. `bot/handlers.py` passes a
  1 h `read_timeout` to `get_file()`/`download_to_drive()`. Don't remove it.
- **No `depends_on` for `bot-api`** (it conflicts with the `local-api` profile in
  default mode). The bot waits for the endpoint itself (`_wait_for_bot_api`) before
  starting to poll.
- **The `bot-api-data` volume is keyed by the compose project name (the directory
  name).** Don't rename the working directory without a fixed project name, or the
  cached received files are orphaned.

## Conventions

- **Commits:** imperative, specific subject + a body explaining *why* (what problem,
  what was verified, test count). Example:
  `Re-download items whose file was deleted from the library`.
- **Identity:** the project is `plex-inbox`; the Python logger is `plexinbox`. The
  strings `yt-dlp` that remain in code/docs refer to the *tool*, not the project.
- **Language:** code, comments, and this file are in English; the product UI is
  English, Arabic, Turkish, Spanish, Russian, French, German, and Portuguese.

## When you change behavior

1. Add/extend a test (offline, against the fakes) that pins the new behavior.
2. If a new user-facing message is needed, add both `en` and `ar` in `bot/i18n.py`.
3. Run `ruff check .` and `ruff format --check .` — both must be clean before you stop.
4. Run `.venv/bin/python -m pytest -q` — the whole suite must pass before you stop.
5. If the change touches the running bot, rebuild: `docker compose --profile local-api up -d --build bot`.
