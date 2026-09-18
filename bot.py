import os
import re
import json
import time
import math
import glob
import base64
import unicodedata
import subprocess
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _local_api_up():
    try:
        requests.get("http://127.0.0.1:8081", timeout=1.5)
        return True
    except requests.exceptions.RequestException:
        return False

LOCAL_API_UP = _local_api_up()
FILE_API = f"http://127.0.0.1:8081/bot{BOT_TOKEN}" if LOCAL_API_UP else API
if LOCAL_API_UP:
    print("local Telegram Bot API server detected — chat uploads capped at ~1.9GB instead of 50MB")

MODE = os.environ.get("MODE", "download")
URL = os.environ.get("VIDEO_URL", "")
CHAT_ID = os.environ["CHAT_ID"]
FORMAT = os.environ.get("FORMAT", "720")

STATUS_MESSAGE_ID = os.environ.get("STATUS_MESSAGE_ID") or None
RANGE_START = os.environ.get("RANGE_START") or None
RANGE_END = os.environ.get("RANGE_END") or None
LIST_ID = os.environ.get("LIST_ID") or None
RAW_CALLBACK_DATA = os.environ.get("RAW_CALLBACK_DATA") or None
DESTINATION = os.environ.get("DESTINATION") or "github"
MEDIA_TYPE = os.environ.get("MEDIA_TYPE") or "video"
MAX_PLAYLIST_ITEMS = 20
MAX_TELEGRAM_UPLOAD = (1900 if LOCAL_API_UP else 49) * 1024 * 1024
GIF_MAX_SECONDS = 15


def send_message(text, reply_markup=None):
    payload = {"chat_id": CHAT_ID, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{API}/sendMessage", json=payload)


def send_photo(photo_url, caption, reply_markup=None):
    payload = {"chat_id": CHAT_ID, "photo": photo_url, "caption": caption}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(f"{API}/sendPhoto", json=payload)
    if not resp.ok:
        send_message(caption, reply_markup)


def edit_message(message_id, text, reply_markup=None):
    if not message_id:
        return
    payload = {"chat_id": CHAT_ID, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{API}/editMessageText", json=payload)


def retry_keyboard():
    if not RAW_CALLBACK_DATA:
        return None
    return {"inline_keyboard": [[{"text": "🔄 دوباره امتحان کن", "callback_data": RAW_CALLBACK_DATA}]]}


def setup_cookies():
    cookies = {}
    for platform, env_name, filename in [
        ("youtube", "YTDLP_COOKIES_B64", "cookies_youtube.txt"),
        ("instagram", "IG_COOKIES_B64", "cookies_instagram.txt"),
        ("twitter", "TWITTER_COOKIES_B64", "cookies_twitter.txt"),
        ("soundcloud", "SOUNDCLOUD_COOKIES_B64", "cookies_soundcloud.txt"),
    ]:
        b64 = os.environ.get(env_name)
        if not b64:
            print(f"[{platform}] {env_name} not set — running without cookies")
            cookies[platform] = None
            continue
        with open(filename, "wb") as f:
            f.write(base64.b64decode(b64))
        size = os.path.getsize(filename)
        with open(filename, "r", errors="ignore") as f:
            first_line = f.readline().strip()
        print(f"[{platform}] cookies file: {size} bytes, first line: {first_line!r}")
        cookies[platform] = filename if size > 0 else None
    return cookies


def platform_of(url):
    if "instagram.com" in url:
        return "instagram"
    if "twitter.com" in url or "x.com" in url:
        return "twitter"
    if "soundcloud.com" in url:
        return "soundcloud"
    return "youtube"


def client_args(cookies, url):
    platform = platform_of(url)
    cookies_file = cookies.get(platform)
    if platform in ("instagram", "twitter", "soundcloud"):
        args = ["--cookies", cookies_file] if cookies_file else []
    elif cookies_file:
        args = ["--cookies", cookies_file, "--extractor-args", "youtube:player_client=web_safari,tv"]
    else:
        args = ["--extractor-args", "youtube:player_client=android,ios,web_safari,tv"]
    print(f"[{platform}] using cookies={bool(cookies_file)} args={args}")
    return args


def is_playlist_url(url):
    return "playlist?list=" in url or ("list=" in url and "watch?v=" not in url)


def build_ref(url, video_id):
    """Builds the short token embedded in callback_data, from which the
    original URL can be reconstructed later (see worker.js)."""
    platform = platform_of(url)
    if platform == "instagram":
        m = re.search(r"instagram\.com/(p|reel|reels|tv)/([\w-]+)", url)
        if m:
            return f"IG:{m.group(1)}:{m.group(2)}"
        return f"IG:p:{video_id}"
    if platform == "twitter":
        # Use the ID from the URL the user actually sent, not yt-dlp's
        # resolved info["id"] — for retweets/quote-tweets that can point
        # to a different underlying tweet, which breaks a standalone
        # re-fetch at download time.
        m = re.search(r"status/(\d+)", url)
        tw_id = m.group(1) if m else video_id
        return f"TW:{tw_id}"
    if platform == "soundcloud":
        m = re.search(r"soundcloud\.com/([^?#]+)", url)
        path = m.group(1).rstrip("/") if m else str(video_id)
        return f"SC:{path[:50]}"
    return video_id  # youtube — unprefixed, unchanged


def list_formats(url, cookies):
    if is_playlist_url(url):
        list_playlist_formats(url, cookies)
        return

    cmd = ["yt-dlp", "-J", "--no-warnings", "--no-playlist"] + client_args(cookies, url) + [url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stderr[-3000:])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:] or result.stdout[-800:])

    info = json.loads(result.stdout)
    video_id = info.get("id")
    ref = build_ref(url, video_id)
    title = info.get("title", "ویدیو")
    thumbnail = info.get("thumbnail")
    formats = info.get("formats", [])

    video_formats = [f for f in formats if f.get("vcodec") not in (None, "none")]

    # Some quote-tweets / embeds come back as a multi-entry result instead
    # of a flat single-video info dict — fall back to the first entry.
    if not video_formats and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if entries:
            first = entries[0]
            formats = first.get("formats", formats)
            video_formats = [f for f in formats if f.get("vcodec") not in (None, "none")]
            thumbnail = first.get("thumbnail") or thumbnail
            title = first.get("title") or title

    if not video_formats:
        print(f"no video formats found. top-level keys: {list(info.keys())}")
        print(f"formats count: {len(formats)}, sample: {formats[:2]}")

    platform = platform_of(url)

    def size_label(num_bytes):
        if not num_bytes:
            return ""
        mb = num_bytes / (1024 * 1024)
        return f" (~{mb / 1024:.1f}GB)" if mb >= 1024 else f" (~{mb:.0f}MB)"

    audio_formats = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    audio_size = 0
    if audio_formats:
        best_audio = max(audio_formats, key=lambda f: f.get("abr") or 0)
        audio_size = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0

    buttons = []

    if platform == "youtube":
        heights = sorted({f.get("height") for f in video_formats if f.get("height")}, reverse=True)
        max_h = heights[0] if heights else None
        tiers = [h for h in [1080, 720, 480, 360, 240] if max_h and h <= max_h]
        if not tiers and max_h:
            tiers = [max_h]

        for h in tiers:
            matching = [f for f in video_formats if f.get("height") == h] or \
                       [f for f in video_formats if f.get("height") and f["height"] <= h]
            vsize = 0
            if matching:
                best = max(matching, key=lambda f: f.get("height") or 0)
                vsize = best.get("filesize") or best.get("filesize_approx") or 0
            label = f"{h}p{size_label(vsize + audio_size)}"
            buttons.append([{"text": label, "callback_data": f"{ref}|{h}"}])

        has_audio = any(f.get("vcodec") == "none" and f.get("acodec") not in (None, "none") for f in formats)
        if has_audio and video_formats:
            buttons.append([{"text": f"فقط صدا 🎵{size_label(audio_size)}", "callback_data": f"{ref}|audio"}])
    elif platform == "soundcloud" and audio_formats:
        best_audio = max(audio_formats, key=lambda f: f.get("abr") or 0)
        asize = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
        buttons.append([{"text": f"دانلود 🎵{size_label(asize)}", "callback_data": f"{ref}|audio"}])
    elif video_formats:
        # Instagram/Twitter: usually just one real quality, and "height"
        # means something different for portrait video — don't build a
        # fake quality ladder, just offer the best format directly
        best = max(video_formats, key=lambda f: (f.get("height") or 0) * (f.get("width") or 1))
        vsize = best.get("filesize") or best.get("filesize_approx") or 0
        buttons.append([{"text": f"دانلود 📥{size_label(vsize)}", "callback_data": f"{ref}|best"}])

    if not buttons:
        if platform != "twitter" and formats:
            # e.g. an Instagram photo post — a real downloadable image,
            # not just a link-preview thumbnail, so offer to grab it
            buttons = [[{"text": "دانلود 📥", "callback_data": f"{ref}|best"}]]
        else:
            send_message("این پست هیچ ویدیو یا فایل قابل‌دانلودی نداره (شاید فقط متن/عکسه، یا محتوای اصلی توی یه پست دیگه‌ست که این پست فقط بهش اشاره کرده).")
            return

    caption = f"«{title}»\nکیفیت مورد نظر رو انتخاب کن:"
    reply_markup = {"inline_keyboard": buttons}

    if thumbnail:
        send_photo(thumbnail, caption[:1024], reply_markup)
    else:
        send_message(caption, reply_markup)


def list_playlist_formats(url, cookies):
    cmd = ["yt-dlp", "-J", "--flat-playlist", "--no-warnings"] + client_args(cookies, url) + [url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stderr[-3000:])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:] or result.stdout[-800:])

    info = json.loads(result.stdout)
    entries = [e for e in info.get("entries", []) if e.get("id")]
    title = info.get("title", "پلی‌لیست")
    count = len(entries)

    match = re.search(r"[?&]list=([\w-]+)", url)
    list_id = match.group(1) if match else info.get("id")

    if count == 0 or not list_id:
        send_message("هیچ ویدیویی در این پلی‌لیست پیدا نشد.")
        return

    display_count = min(count, 60)
    lines = [f"{i}. {e.get('title') or e.get('id')}" for i, e in enumerate(entries[:display_count], 1)]
    listing = "\n".join(lines)
    if count > display_count:
        listing += f"\n... و {count - display_count} مورد دیگر"

    send_message(f"«{title}» — {count} ویدیو:\n\n{listing}")
    send_message(
        f"از قسمت چند تا چند رو می‌خوای دانلود کنم؟ (مثلاً 1-10)\n"
        f"روی همین پیام ریپلای کن و بازه رو بنویس.\n\n"
        f"list_id:{list_id} total:{count}",
        {"force_reply": True},
    )


def playlist_range_quality(list_id, start, end):
    tiers = [1080, 720, 480, 360]
    buttons = [[{"text": f"{h}p", "callback_data": f"L:{list_id}:{start}:{end}|{h}"}] for h in tiers]
    buttons.append([{"text": "فقط صدا 🎵", "callback_data": f"L:{list_id}:{start}:{end}|audio"}])
    send_message(f"قسمت‌های {start} تا {end} — کیفیت رو انتخاب کن:", {"inline_keyboard": buttons})


def build_selector(fmt):
    if fmt == "audio":
        return "bestaudio/best"
    if fmt == "best":
        return "best"
    return f"bv*[height<={fmt}]+ba/b[height<={fmt}]"


def ascii_safe_name(filename):
    """Strip the filename down to plain ASCII letters/digits so it never
    trips up gh's upload syntax or GitHub's own asset-name rewriting."""
    name, ext = os.path.splitext(filename)
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"\.{2,}", ".", name)  # GitHub collapses repeated dots itself
    name = re.sub(r"_+", "_", name).strip("_.")
    if not name:
        name = "video"
    if not name[0].isalnum():
        name = f"v_{name}"
    return f"{name}{ext}"


def download_video(url, cookies, fmt):
    """Downloads the video and returns (safe_filename, display_title)."""
    before = set(os.listdir("."))
    cmd = ["yt-dlp", "-v", "-f", build_selector(fmt), "--no-playlist", "-o", "%(title)s.%(ext)s"]
    if fmt not in ("audio", "best"):
        cmd += ["--merge-output-format", "mp4"]
    cmd += client_args(cookies, url)
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout[-1500:])
    print("---- STDERR (full) ----")
    print(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:] or result.stdout[-800:])

    after = set(os.listdir("."))
    new_files = [
        f for f in (after - before)
        if not f.endswith((".part", ".ytdl", ".tmp")) and f != "cookies.txt"
    ]
    if not new_files:
        raise FileNotFoundError("downloaded file not found")

    original_name = new_files[0]
    title = os.path.splitext(original_name)[0]
    safe_name = ascii_safe_name(original_name)
    if safe_name != original_name:
        os.rename(original_name, safe_name)
    return safe_name, title


def download_video_with_retry(url, cookies, fmt, retries=None, delay=None):
    if retries is None:
        retries = 4 if platform_of(url) == "twitter" else 2
    if delay is None:
        delay = 8 if platform_of(url) == "twitter" else 5
    last_err = None
    for attempt in range(retries):
        try:
            return download_video(url, cookies, fmt)
        except Exception as e:
            last_err = e
            print(f"download attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    raise last_err


def upload_to_release(file_path, tag):
    create = subprocess.run(
        ["gh", "release", "create", tag, file_path, "--title", tag, "--notes", "auto upload"],
        capture_output=True, text=True,
    )
    print("gh release create stdout:", create.stdout)
    print("gh release create stderr:", create.stderr)
    if create.returncode != 0:
        raise RuntimeError(f"gh release create failed: {create.stderr[-500:]}")

    # file_path is guaranteed ASCII-safe (see ascii_safe_name), so GitHub
    # will never rename it and we can build the link directly — no need
    # to round-trip through `gh release view`.
    return f"https://github.com/{REPO}/releases/download/{tag}/{file_path}"


GITHUB_ASSET_LIMIT = 2 * 1024 * 1024 * 1024  # 2GiB (GitHub's hard limit)
TARGET_PART_SIZE = 1800 * 1024 * 1024  # leave some margin under the limit


def get_duration(file_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", file_path],
        capture_output=True, text=True,
    )
    print(f"ffprobe stdout={result.stdout!r} stderr={result.stderr!r} returncode={result.returncode}")
    duration = result.stdout.strip()
    if result.returncode != 0 or not duration:
        raise RuntimeError(f"ffprobe failed to read duration: {result.stderr[-500:] or 'no output'}")
    return float(duration)


def split_video(file_path):
    """Splits file_path into roughly equal parts (no re-encoding, just a
    stream copy) sized to fit under GitHub's asset limit, and returns the
    list of part file paths."""
    size = os.path.getsize(file_path)
    duration = get_duration(file_path)
    num_parts = max(2, -(-size // TARGET_PART_SIZE))  # ceil division
    part_duration = duration / num_parts

    base, ext = os.path.splitext(file_path)
    pattern = f"{base}_part%02d{ext}"
    cmd = [
        "ffmpeg", "-y", "-i", file_path, "-c", "copy", "-map", "0",
        "-f", "segment", "-segment_time", str(int(part_duration) + 1),
        "-reset_timestamps", "1", pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stderr[-1500:])
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg split failed: {result.stderr[-500:]}")

    prefix = f"{os.path.basename(base)}_part"
    parts = sorted(f for f in os.listdir(os.path.dirname(file_path) or ".") if f.startswith(prefix) and f.endswith(ext))
    dirpath = os.path.dirname(file_path)
    return [os.path.join(dirpath, p) if dirpath else p for p in parts]


def split_and_upload(file_path, tag_prefix):
    parts = split_video(file_path)
    links = []
    for i, part in enumerate(parts, 1):
        tag = f"{tag_prefix}-p{i}"
        links.append(upload_to_release(part, tag))
        os.remove(part)
    return links



def convert_to_gif(file_path):
    duration = get_duration(file_path)
    clip_duration = min(duration, GIF_MAX_SECONDS)
    base, _ = os.path.splitext(file_path)
    palette = f"{base}_palette.png"
    gif_path = f"{base}.gif"

    gen = subprocess.run(
        ["ffmpeg", "-y", "-t", str(clip_duration), "-i", file_path,
         "-vf", "fps=12,scale=480:-1:flags=lanczos", palette],
        capture_output=True, text=True,
    )
    if gen.returncode != 0:
        raise RuntimeError(f"gif palette generation failed: {gen.stderr[-500:]}")

    result = subprocess.run(
        ["ffmpeg", "-y", "-t", str(clip_duration), "-i", file_path, "-i", palette,
         "-lavfi", "fps=12,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse",
         gif_path],
        capture_output=True, text=True,
    )
    if os.path.exists(palette):
        os.remove(palette)
    if result.returncode != 0:
        raise RuntimeError(f"gif conversion failed: {result.stderr[-500:]}")

    trim_note = f" (فقط {GIF_MAX_SECONDS} ثانیه اول تبدیل شد، چون GIF سقف طول داره)" if duration > GIF_MAX_SECONDS else ""
    return gif_path, trim_note


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def send_file_to_chat(file_path, title, fmt):
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTS:
        method, field = "sendPhoto", "photo"
    elif fmt == "audio":
        method, field = "sendAudio", "audio"
    else:
        method, field = "sendVideo", "video"
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{FILE_API}/{method}",
            data={"chat_id": CHAT_ID, "caption": title[:1024]},
            files={field: f},
            timeout=1200,
        )
    if not resp.ok:
        raise RuntimeError(f"telegram upload failed: {resp.status_code} {resp.text[:300]}")


def send_animation_to_chat(file_path, caption):
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{FILE_API}/sendAnimation",
            data={"chat_id": CHAT_ID, "caption": caption[:1024]},
            files={"animation": f},
            timeout=600,
        )
    if not resp.ok:
        raise RuntimeError(f"telegram animation upload failed: {resp.status_code} {resp.text[:300]}")


def fetch_and_upload(url, cookies, fmt, destination="github", media_type="video"):
    """Downloads a single video at the requested quality (unchanged) and
    delivers it either straight into the chat (as a video/audio file or as
    a GIF) or via a GitHub Release link — falling back to GitHub, with a
    note, if chat delivery isn't possible. If the file is too big for
    GitHub's 2GB asset limit, splits it into parts (no quality loss, just
    a stream copy). Returns (title, links, note) — links is empty when the
    file was delivered directly into the chat."""
    file_path, title = download_video_with_retry(url, cookies, fmt)
    size_mb = os.path.getsize(file_path) / (1024 * 1024)

    is_image = os.path.splitext(file_path)[1].lower() in IMAGE_EXTS
    if destination == "chat" and media_type == "gif" and fmt != "audio" and not is_image:
        try:
            gif_path, trim_note = convert_to_gif(file_path)
            gif_size = os.path.getsize(gif_path) / (1024 * 1024)
            if gif_size <= MAX_TELEGRAM_UPLOAD / (1024 * 1024):
                send_animation_to_chat(gif_path, title)
                os.remove(gif_path)
                os.remove(file_path)
                return title, [], f" (به‌صورت GIF فرستاده شد ✅{trim_note})"
            os.remove(gif_path)
            print(f"gif too big ({gif_size:.1f}MB) even after trimming, falling back to normal video")
        except Exception as e:
            print(f"gif conversion failed, falling back to normal video: {e}")
        media_type = "video"  # fall through below, reusing the already-downloaded file_path

    if destination == "chat":
        if size_mb <= MAX_TELEGRAM_UPLOAD / (1024 * 1024):
            try:
                send_file_to_chat(file_path, title, fmt)
                os.remove(file_path)
                return title, [], " (در چت فرستاده شد ✅)"
            except Exception as e:
                print(f"telegram upload failed, falling back to GitHub: {e}")
                fallback_note = " (ارسال در چت شکست خورد، روی گیت‌هاب آپلود شد)"
        else:
            print(f"file too big for chat delivery ({size_mb:.1f}MB), falling back to GitHub")
            fallback_note = " (حجم فایل بیشتر از سقف ۵۰ مگابایتی ارسال در چت بود، روی گیت‌هاب آپلود شد)"
    else:
        fallback_note = ""

    tag = f"vid-{int(time.time())}-{os.getpid()}"
    try:
        link = upload_to_release(file_path, tag)
        os.remove(file_path)
        return title, [link], fallback_note
    except Exception as e:
        if "must be less than" not in str(e):
            os.remove(file_path)
            raise
        print(f"file too large for a single asset, splitting: {e}")

    try:
        links = split_and_upload(file_path, tag)
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
    note = fallback_note + f" (فایل به {len(links)} پارت تقسیم شد چون حجمش بیشتر از سقف ۲ گیگ گیت‌هاب بود)"
    return title, links, note


def format_links(title, links):
    if not links:
        return title
    if len(links) == 1:
        return f"{title}\n{links[0]}"
    parts_text = "\n".join(f"پارت {i}: {l}" for i, l in enumerate(links, 1))
    return f"{title}\n{parts_text}"


def download_and_send(url, cookies, fmt, status_message_id=None, label=None, destination="github", media_type="video"):
    title, links, note = fetch_and_upload(url, cookies, fmt, destination, media_type)
    edit_message(status_message_id, f"{label + ' ' if label else ''}دانلود تمام شد ✅{note}")
    if links:
        send_message(format_links(title, links))


def download_playlist(url, cookies, fmt, status_message_id=None, range_start=None, range_end=None, destination="github", media_type="video"):
    cmd = ["yt-dlp", "-J", "--flat-playlist", "--no-warnings"] + client_args(cookies, url) + [url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        edit_message(status_message_id, f"خطا در خواندن پلی‌لیست: {result.stderr[-500:]}")
        return

    info = json.loads(result.stdout)
    entries = [e for e in info.get("entries", []) if e.get("id")]
    if not entries:
        edit_message(status_message_id, "هیچ ویدیویی در این پلی‌لیست پیدا نشد.")
        return

    if range_start and range_end:
        try:
            s, e_ = int(range_start), int(range_end)
            entries = entries[max(s - 1, 0):e_]
        except ValueError:
            pass

    truncated = len(entries) > MAX_PLAYLIST_ITEMS
    if truncated:
        entries = entries[:MAX_PLAYLIST_ITEMS]

    done = 0
    for i, e in enumerate(entries, 1):
        edit_message(status_message_id, f"در حال دانلود ({i}/{len(entries)})... ⏳")
        video_url = f"https://www.youtube.com/watch?v={e['id']}"
        try:
            title, links, note = fetch_and_upload(video_url, cookies, fmt, destination, media_type)
            send_message(f"✅ ({i}/{len(entries)}) {note}\n{format_links(title, links)}")
            done += 1
        except Exception as ex:
            item_retry = {"inline_keyboard": [[{"text": "🔄 دوباره امتحان کن", "callback_data": f"{e['id']}|{fmt}|{destination}|{media_type}"}]]}
            send_message(f"❌ ({i}/{len(entries)}) خطا: {ex}", item_retry)
        time.sleep(3)

    edit_message(status_message_id, f"پایان: {done} از {len(entries)} ویدیو با موفقیت دانلود شد.{' (بازه محدود به ' + str(MAX_PLAYLIST_ITEMS) + ' ویدیو شد)' if truncated else ''} ✅")


def main():
    cookies = setup_cookies()

    if MODE == "list":
        try:
            list_formats(URL, cookies)
        except Exception as e:
            send_message(f"خطا در دریافت لیست فرمت‌ها: {e}")
        return

    if MODE == "playlist_range":
        playlist_range_quality(LIST_ID, RANGE_START, RANGE_END)
        return

    if is_playlist_url(URL):
        download_playlist(URL, cookies, FORMAT, STATUS_MESSAGE_ID, RANGE_START, RANGE_END, DESTINATION, MEDIA_TYPE)
        return

    try:
        download_and_send(URL, cookies, FORMAT, status_message_id=STATUS_MESSAGE_ID, destination=DESTINATION, media_type=MEDIA_TYPE)
    except Exception as e:
        edit_message(STATUS_MESSAGE_ID, f"خطا در دانلود: {e}", retry_keyboard())
        if not STATUS_MESSAGE_ID:
            send_message(f"خطا در دانلود: {e}", retry_keyboard())


if __name__ == "__main__":
    main()
