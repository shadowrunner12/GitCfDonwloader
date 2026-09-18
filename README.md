# 📥 GitCfDonwloader

A Telegram bot that downloads video/audio from **YouTube, Instagram, Twitter/X, and SoundCloud**, running entirely on **GitHub Actions** — no server to maintain, no VPS bill.

Send a link, pick a quality, and get your file back either straight in the chat or as a **GitHub Release** link (with automatic splitting for anything over GitHub's 2GB asset limit).

> 🇮🇷 Bot messages are in Persian (فارسی) by default — see [Customizing messages](#-customizing-messages) to change that.

---

## ✨ Features

- **Multi-platform downloads** — YouTube (incl. playlists), Instagram (posts/reels/photos), Twitter/X, SoundCloud
- **Quality picker** — inline keyboard with real, per-quality file-size estimates (1080p / 720p / 480p / 360p / 240p, or audio-only)
- **Playlist support** — pick a range (e.g. `1-10`) and a quality for the whole batch
- **Two delivery modes**
  - **Chat delivery** — sent directly as a Telegram video/audio/animation
  - **GitHub Releases** — uploaded as a release asset and linked back, for files too big for Telegram
- **Automatic large-file splitting** — files over 2GB are stream-copy split into parts (no re-encoding, no quality loss)
- **GIF conversion** — convert short clips to GIF on request, with automatic trimming to a safe duration
- **Cookie support** — supply base64-encoded cookies for YouTube, Instagram, Twitter, and SoundCloud to unlock age-restricted, private, or rate-limited content
- **Local Bot API server aware** — automatically detects a local Telegram Bot API server and raises the upload limit from ~50MB to ~1.9GB
- **Retry logic** — automatic retries on transient download failures, plus a one-tap "🔄 Try again" button on errors
- **Filename sanitization** — every downloaded file is normalized to a safe ASCII name before upload, so it never trips over `gh`'s CLI or GitHub's own asset-name rewriting

---

## 🏗️ How it works

```
Telegram user
     │  sends a link / taps a button
     ▼
Telegram Bot API  ──webhook──▶  (companion webhook / dispatcher)
                                        │  triggers a workflow run with inputs
                                        ▼
                              GitHub Actions workflow
                                        │  runs bot.py
                                        ▼
                         yt-dlp → ffmpeg/ffprobe → gh CLI
                                        │
                        ┌───────────────┴────────────────┐
                        ▼                                 ▼
              sent back to Telegram chat        uploaded to a GitHub Release
```

`bot.py` is the worker that actually runs inside the GitHub Actions job. Each run is driven entirely by environment variables (see below), so the same script handles listing formats, downloading a single video, and walking a playlist — depending on which `MODE` it's invoked with.

---

## 🚀 Getting started

### Prerequisites

The GitHub Actions runner needs these on `PATH` (a standard `ubuntu-latest` runner plus a couple of extra steps covers it):

- Python 3 with [`requirements.txt`](./requirements.txt) installed
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)
- `ffmpeg` / `ffprobe`
- [`gh`](https://cli.github.com/) (GitHub CLI), authenticated for the repo

### 1. Create a Telegram bot

Talk to [@BotFather](https://t.me/BotFather), create a bot, and grab the token.

### 2. Configure repository secrets

| Secret | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Your bot's token |
| `YTDLP_COOKIES_B64` | optional | Base64-encoded `cookies.txt` for YouTube |
| `IG_COOKIES_B64` | optional | Base64-encoded cookies for Instagram |
| `TWITTER_COOKIES_B64` | optional | Base64-encoded cookies for Twitter/X |
| `SOUNDCLOUD_COOKIES_B64` | optional | Base64-encoded cookies for SoundCloud |

Generate a cookies secret with:

```bash
base64 -w 0 cookies.txt
```

`GITHUB_TOKEN` / `GITHUB_REPOSITORY` are provided automatically by Actions and are used to create the release that hosts downloaded files.

### 3. Wire up triggering

`bot.py` expects to be launched (e.g. via `workflow_dispatch` inputs or `repository_dispatch`) with the environment variables listed below already set. Typically this comes from a small webhook (a Cloudflare Worker, a serverless function, etc.) that receives Telegram updates and kicks off a workflow run per message/callback — see [`.github/workflows`](./.github/workflows) for the workflow definitions in this repo.

---

## ⚙️ Environment variables

| Variable | Description | Default |
|---|---|---|
| `MODE` | `download` \| `list` \| `playlist_range` | `download` |
| `VIDEO_URL` | The URL to act on | — |
| `CHAT_ID` | Telegram chat to reply to | required |
| `FORMAT` | Quality tier (`1080`/`720`/`480`/`360`/`240`), `audio`, or `best` | `720` |
| `STATUS_MESSAGE_ID` | Message ID to edit with progress/result | — |
| `RANGE_START` / `RANGE_END` | Playlist item range | — |
| `LIST_ID` | Playlist ID (used with `playlist_range` mode) | — |
| `RAW_CALLBACK_DATA` | Original callback payload, replayed on retry | — |
| `DESTINATION` | `chat` or `github` | `github` |
| `MEDIA_TYPE` | `video` or `gif` | `video` |

---

## 🖼️ Typical flow

1. User sends a link → bot replies with a `MODE=list` run, showing quality buttons (with size estimates) and, for playlists, a numbered listing
2. User taps a quality → a `MODE=download` (or `playlist_range`) run fires
3. Bot downloads with `yt-dlp`, using the right cookie jar and client args for the detected platform
4. Bot delivers the result:
   - Small enough for Telegram + `DESTINATION=chat` → sent inline as video/audio/GIF
   - Otherwise → uploaded to a GitHub Release; too big for one asset → split into parts first
5. Status message is edited in place with a ✅ / ❌ result, and a retry button appears on failure

---

## 🌍 Supported platforms

| Platform | Single item | Playlists | Notes |
|---|---|---|---|
| YouTube | ✅ | ✅ | Full quality ladder, audio-only option |
| Instagram | ✅ | — | Posts, reels, and photo posts |
| Twitter / X | ✅ | — | Handles retweets/quote-tweets by re-resolving the original tweet ID |
| SoundCloud | ✅ (audio) | — | Best-bitrate audio |

---

## 🗣️ Customizing messages

All user-facing strings in `bot.py` are in Persian. They're plain string literals, so translating the bot is a matter of editing the `send_message` / `send_photo` calls to your language of choice.

---

## ⚠️ Limitations

- Telegram chat delivery is capped at **50MB** (or **~1.9GB** if a local Bot API server is detected)
- GitHub Releases cap a single asset at **2GB** — larger files are auto-split into parts
- Playlists are capped at **20 items per run** to keep Actions job time reasonable
- GIFs are trimmed to **15 seconds**

---

## 📄 License

No license file is currently included in this repository — all rights reserved by default until one is added. Check with the repo owner before reuse.
