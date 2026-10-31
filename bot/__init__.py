"""yt-dlp Telegram bot: archive any yt-dlp-supported video, Plex-ready.

Submodules:
    config    env parsing / validation
    i18n      all user-facing strings (en/ar) + per-user language store
    probe     ``yt-dlp -J`` probe + URL parsing
    plex      filenames, NFO, sidecars, posters, Plex refresh
    store     archive dedupe, library view, stats
    ytdlp     download manager (queue, workers, progress, cancel, metadata)
    updater   yt-dlp auto-update
    handlers  PTB handlers (menu, add/confirm, queue, library, stats, lang)
    main      wiring + lifecycle
"""

__version__ = "0.1.0"
