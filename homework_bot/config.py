"""
Environment configuration, feature flags and the tiny health-check server.

Everything here is read once at import time from environment variables, so
this module has no dependency on the rest of the package — it's safe to
import first, from anywhere.
"""

import logging
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

try:
    import requests
except ImportError:
    requests = None


PARENT_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Token comes from an environment variable (BOT_TOKEN) so it's never
# committed to git or hard-coded in the file. Set it in Render's dashboard
# under Environment. Locally you can export it before running, e.g.:
#   export BOT_TOKEN="123456:ABC..."   (Windows: set BOT_TOKEN=123456:ABC...)
# Only required to actually start the bot (checked in main()) — importing
# this module for other purposes (e.g. migrate_to_postgres.py) works without it.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Where the SQLite file lives. On Render, point this (via the DB_PATH env
# var) at your persistent disk's mount path, e.g. /var/data/homework.db —
# otherwise the database is wiped on every deploy/restart (Render's default
# filesystem is ephemeral). Locally this just defaults to a file next to
# this script. IGNORED if DATABASE_URL is set (see below).
DB_PATH = Path(os.environ.get("DB_PATH", str(PARENT_DIR / "homework.db")))
# If set, the bot uses this Postgres database instead of the local SQLite
# file — this is the "online DB" that survives redeploys/restarts on its
# own, with no disk to attach. Get a free, permanent Postgres database from
# https://neon.tech (or Supabase, or Render's own Postgres — Render's free
# Postgres auto-deletes after 30 days, so Neon/Supabase are the safer free
# choice). The connection string looks like:
#   postgresql://user:password@host/dbname?sslmode=require
# Put it in the DATABASE_URL environment variable, locally or on Render.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
if USE_POSTGRES and psycopg2 is None:
    raise SystemExit(
        "DATABASE_URL is set but psycopg2 isn't installed. "
        "Run: pip install -r requirements.txt"
    )
# Optional failover databases (Postgres only — e.g. two more free instances
# from Neon/Supabase/ElephantSQL). If set, the bot writes/reads DATABASE_URL
# as usual, but automatically switches to DATABASE_URL_BACKUP2 then
# DATABASE_URL_BACKUP3 if the current one stops responding, and a scheduled
# job mirrors all data from the active DB into them on a schedule (every 6h
# into #2, once a night into #3) — see db.py's "Multi-database failover"
# section and jobs.py's backup_to_secondary/backup_to_tertiary. Leaving
# these unset disables failover entirely; everything behaves exactly as
# before.
DATABASE_URL_BACKUP2 = os.environ.get("DATABASE_URL_BACKUP2", "").strip()
DATABASE_URL_BACKUP3 = os.environ.get("DATABASE_URL_BACKUP3", "").strip()
TIMEZONE = ZoneInfo("Asia/Almaty")
REMINDER_HOUR = 8
REMINDER_MINUTE = 0
SCHEDULE_HOUR = 7
SCHEDULE_MINUTE = 30
POLL_HOUR = 7
POLL_MINUTE = 45
ADMIN_IDS = {1762280778}
# Voice/audio/video transcription (via Groq's free Whisper API). Get a free
# key (no card needed) at https://console.groq.com -> API Keys, then set it
# as the GROQ_API_KEY environment variable. Any voice message, audio file,
# video note or video sent to the bot is auto-transcribed and replied to —
# the actual speech-to-text work happens on Groq's servers, so this needs
# almost no CPU/RAM locally, which matters on Render's free tier.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_WHISPER_MODEL = "whisper-large-v3"
TRANSCRIBE_ENABLED = bool(GROQ_API_KEY)
# Translation: reply to any message and mention the bot (@botusername) in
# your reply — the bot translates the original message into Russian.
# Reuses the same GROQ_API_KEY as transcription, via Groq's free chat
# models (no separate setup needed).
GROQ_TRANSLATE_MODEL = "openai/gpt-oss-20b"
TRANSLATE_ENABLED = bool(GROQ_API_KEY)
# Every homework task is shared: everyone who talks to the bot sees the same
# list, regardless of who added it. Internally this is done by always
# storing/reading tasks under one fixed pseudo chat_id instead of each
# person's own chat_id. (Schedules and room photos are NOT affected by this
# — those stay personal per user, as before.)
SHARED_TASKS_ID = 0

# Video/audio downloader (Instagram/TikTok/Twitter/YouTube links pasted into
# chat). Uses yt-dlp, which is pure Python — but converting to mp3 also
# needs the `ffmpeg` binary installed on the machine (apt-get install
# ffmpeg on Debian/Ubuntu; on Render, add it via a buildpack or Dockerfile —
# the default "Web Service"/"Background Worker" images don't include it).
try:
    import yt_dlp
except ImportError:
    yt_dlp = None

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
MEDIA_DOWNLOAD_ENABLED = yt_dlp is not None
# Telegram bots can only upload files up to 50 MB through the regular Bot
# API (larger needs a self-hosted Local Bot API Server, out of scope here).
MAX_DOWNLOAD_MB = int(os.environ.get("MAX_DOWNLOAD_MB", "50"))

# YouTube increasingly demands proof a download request comes from a real
# signed-in browser ("Sign in to confirm you're not a bot"), which blocks
# yt-dlp outright without this. Export cookies for youtube.com from a
# logged-in browser session (e.g. with the "Get cookies.txt LOCALLY"
# extension, Netscape format) and point the bot at them — Instagram/TikTok/
# Twitter don't need this, only YouTube does.
#
# Two ways to provide them, because most hosts (Render included) don't give
# you a normal place to just drop a file next to the bot:
#   - YTDLP_COOKIES_FILE: an actual path on disk (e.g. a Render Persistent
#     Disk you've mounted, or just testing locally).
#   - YTDLP_COOKIES_CONTENT: the *contents* of that cookies.txt file, pasted
#     directly into an env var. This is what you want on a normal Render
#     Web Service/Background Worker with no persistent disk — paste the
#     whole file as one (long) environment variable, and the bot writes it
#     to a temp file on every startup.
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

YTDLP_COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "").strip() or None
_cookies_content = os.environ.get("YTDLP_COOKIES_CONTENT", "").strip()

# Common misconfiguration: someone pastes the *contents* of cookies.txt into
# YTDLP_COOKIES_FILE (which is supposed to be a path) instead of using
# YTDLP_COOKIES_CONTENT. Netscape cookie file lines start with a domain and
# are tab-separated and way longer than any real filesystem path, so this is
# an easy, cheap way to catch it early with a clear message instead of a
# cryptic "[Errno 2] No such file or directory: '.youtube.com...'" the first
# time someone tries to download a YouTube video.
if YTDLP_COOKIES_FILE and ("\t" in YTDLP_COOKIES_FILE or len(YTDLP_COOKIES_FILE) > 512):
    logger.warning(
        "YTDLP_COOKIES_FILE looks like it contains cookie *data*, not a file "
        "path (it has tabs / is very long). Did you mean to put this in "
        "YTDLP_COOKIES_CONTENT instead? Ignoring YTDLP_COOKIES_FILE for now."
    )
    if not _cookies_content:
        _cookies_content = YTDLP_COOKIES_FILE
    YTDLP_COOKIES_FILE = None
elif YTDLP_COOKIES_FILE and not Path(YTDLP_COOKIES_FILE).is_file():
    logger.warning(
        "YTDLP_COOKIES_FILE=%s does not point to an existing file — ignoring it.",
        YTDLP_COOKIES_FILE,
    )
    YTDLP_COOKIES_FILE = None

if not YTDLP_COOKIES_FILE and _cookies_content:
    # yt-dlp (via Python's http.cookiejar.MozillaCookieJar) refuses to load a
    # cookies file unless its very first line is the Netscape magic header —
    # otherwise it fails with "does not look like a Netscape format cookies
    # file", even if every cookie line after it is perfectly valid. That
    # header is easy to lose when copy-pasting just the cookie rows into an
    # env var (e.g. skipping the "# Netscape HTTP Cookie File" comment at the
    # top), so make sure it's always there regardless of what was pasted.
    _NETSCAPE_HEADER = "# Netscape HTTP Cookie File"
    if _NETSCAPE_HEADER not in _cookies_content.splitlines()[:1]:
        _cookies_content = _NETSCAPE_HEADER + "\n" + _cookies_content
    _cookies_path = Path(tempfile.gettempdir()) / "yt_dlp_cookies.txt"
    _cookies_path.write_text(_cookies_content + "\n", encoding="utf-8")
    YTDLP_COOKIES_FILE = str(_cookies_path)

# Logged once at import time so a glance at the startup logs on Render
# immediately answers "did my cookies env var actually get picked up?"
# without needing to trigger a download first.
if YTDLP_COOKIES_FILE:
    logger.info(
        "yt-dlp cookies loaded from %s (%s)",
        YTDLP_COOKIES_FILE,
        "YTDLP_COOKIES_CONTENT env var" if _cookies_content else "YTDLP_COOKIES_FILE path",
    )
else:
    logger.info(
        "yt-dlp cookies not configured (YTDLP_COOKIES_FILE / YTDLP_COOKIES_CONTENT "
        "both unset) — YouTube downloads may hit 'Sign in to confirm you're not a bot'."
    )



def start_health_check_server():
    """Render's free 'Web Service' plan requires the process to bind to
    $PORT and answer HTTP requests, or it kills the instance as unhealthy.
    A background worker (paid plan) does NOT need this at all. This only
    starts if a PORT env var is present, so running locally or as a
    background worker is unaffected."""
    port = os.environ.get("PORT")
    if not port:
        return

    class _Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass  # keep the bot's own logs clean

    server = HTTPServer(("0.0.0.0", int(port)), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health-check server listening on port %s", port)
