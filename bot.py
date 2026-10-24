"""
TeraBox Ad-Free Video Telegram Bot
Universal - Sabhi TeraBox links chalega.
Engine: Ultra-Fast Multi-API extraction with instant fallback + Live /logs diagnostic.
Features: Force Channel Subscription (F-Sub) + Health check for 24/7 Render deployment.
"""
import sys, os, re, json, asyncio, logging, urllib.parse, html, time, threading, collections
from http.server import HTTPServer, BaseHTTPRequestHandler
sys.stdout.reconfigure(encoding="utf-8")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode, ChatAction, ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    filters,
    ContextTypes,
)

load_dotenv()

# In-memory log buffer for live diagnostics via /logs endpoint
log_buffer = collections.deque(maxlen=150)

class BufferLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_buffer.append(msg)
        except Exception:
            pass

buffer_handler = BufferLogHandler()
buffer_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(buffer_handler)

# Bot Token with direct fallback so it NEVER fails on Render
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or "8696081950:AAEBf-VeL_3AiFZ6O3cGYrnrWJKIPdUVY_g"
)

# Player pages
WEBAPP_BASE = "https://sumitgautamad-rgb.github.io/teraplayer/"
TERAPLAYER_URL = "https://www.teraplayer.in/"

# ============================================================
# Force Channel Subscription Configuration
# ============================================================
FORCE_CHANNEL_ID = -1003944764432
FORCE_CHANNEL_LINK = "https://t.me/+idhRMGF5qO8xNDU1"
CHANNEL_FILE = "channel_id.txt"

def get_channel_id() -> int:
    global FORCE_CHANNEL_ID
    if os.path.exists(CHANNEL_FILE):
        try:
            with open(CHANNEL_FILE, "r") as f:
                cid = int(f.read().strip())
                if cid:
                    return cid
        except Exception:
            pass
    return FORCE_CHANNEL_ID

def save_channel_id(cid: int):
    global FORCE_CHANNEL_ID
    FORCE_CHANNEL_ID = cid
    try:
        with open(CHANNEL_FILE, "w") as f:
            f.write(str(cid))
        logging.info(f"Saved channel ID to {CHANNEL_FILE}: {cid}")
    except Exception as e:
        logging.error(f"Failed to save channel id: {e}")

async def is_subscribed(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Check if the user has joined the compulsory channel."""
    cid = get_channel_id()
    if not cid:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=cid, user_id=user_id)
        status_str = str(getattr(member, 'status', member)).lower()
        is_sub = any(s in status_str for s in ['member', 'admin', 'owner', 'creator'])
        logging.info(f"User {user_id} subscription status: {status_str} -> {is_sub}")
        return is_sub
    except Exception as e:
        logging.warning(f"Subscription check error for {user_id}: {e} (bypassed)")
        return True

def get_force_sub_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=FORCE_CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Joined / Check Status", callback_data="check_sub")]
    ])

FORCE_SUB_MSG = (
    "⚠️ <b>Access Restricted!</b>\n\n"
    "Bot ko use karne ke liye pehle hamara Telegram Channel join karna compulsory hai.\n\n"
    "1️⃣ Niche <b>'📢 Join Channel'</b> par click karke channel join karein.\n"
    "2️⃣ Uske baad <b>'✅ Joined / Check Status'</b> button dabayein."
)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

# Video cache: {url: {'data': {...}, 'time': float}}
video_cache = {}
CACHE_EXPIRY = 3 * 3600  # 3 hours

session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('https://', adapter)
session.mount('http://', adapter)
session.headers.update({
    'User-Agent': UA,
    'Accept': 'application/json, */*',
})

# ============================================================
# All TeraBox/mirror domains
# ============================================================
TERABOX_DOMAINS = [
    'terabox.com', 'terabox.app', 'teraboxapp.com',
    '1024tera.com', '1024terabox.com',
    'freeterabox.com', 'mirrobox.com', 'nephobox.com',
    '4funbox.com', 'momerybox.com', 'tibibox.com',
    'teraboxlink.com', 'terafileshare.com', 'terasharelink.com',
    'www.terabox.club', 'terabox.club',
]

def find_terabox_url(text):
    if not text:
        return None
    urls = re.findall(r'https?://[^\s<>"\']+', text)
    for u in urls:
        if any(d in u.lower() for d in TERABOX_DOMAINS):
            return u
    return None

def extract_quick_title(terabox_url: str) -> str:
    """Extract a clean readable title from the URL itself as instant fallback."""
    try:
        parts = terabox_url.rstrip('/').split('/')
        code = parts[-1].split('?')[0]
        if code:
            return f"TeraBox Video ({code[:8]})"
    except Exception:
        pass
    return "TeraBox Video"

# ============================================================
# Fast Metadata Extraction (Max 3 Seconds Timeout)
# ============================================================
def extract_metadata_fast(terabox_url: str) -> dict:
    """Tries fast APIs with strict 3-sec timeout. If slow/sleeping, instantly falls back."""
    try:
        r = session.post(
            'https://teraplayer-backend-temp.onrender.com/api/preview',
            json={'url': terabox_url},
            timeout=3,
        )
        if r.status_code == 200:
            d = r.json()
            title = d.get('title') or ''
            thumbnail = d.get('thumbnail') or ''
            stream_url = d.get('stream_url') or ''
            size_str = d.get('size_str') or ''
            duration = d.get('duration') or 0

            if title:
                title = re.sub(r'\s*-\s*Shared via TeraBox\s*$', '', title, flags=re.IGNORECASE)
                title = re.sub(r'\s*-\s*Share Files Online.*$', '', title, flags=re.IGNORECASE)
                title = title.strip()

            if title or thumbnail:
                return {
                    'filename': title or 'TeraBox Video',
                    'thumbnail': thumbnail,
                    'stream_url': stream_url,
                    'size_str': size_str,
                    'duration': duration,
                    'source': 'preview-fast',
                }
    except Exception as e:
        logging.info(f"Fast preview skipped: {e}")

    # Fallback immediately in < 0.1s
    return {
        'filename': extract_quick_title(terabox_url),
        'thumbnail': '',
        'stream_url': '',
        'size_str': '',
        'duration': 0,
        'source': 'instant-fallback',
    }

def process_terabox_link(url: str) -> dict:
    """Process TeraBox link and return video info with player URLs."""
    now = time.time()
    if url in video_cache:
        item = video_cache[url]
        if now - item['time'] < CACHE_EXPIRY:
            return item['data']
        else:
            del video_cache[url]

    data = extract_metadata_fast(url)

    filename = data.get('filename') or 'TeraBox Video'
    size_str = data.get('size_str', '')
    duration = data.get('duration') or 0
    stream_url = data.get('stream_url') or ''
    thumbnail = data.get('thumbnail', '')

    duration_str = ''
    if duration:
        m, s = divmod(int(duration), 60)
        duration_str = f"{m}:{s:02d}"

    # Browser URL: Opens directly on teraplayer.in which autoplays
    browser_url = f"{TERAPLAYER_URL}?url={urllib.parse.quote(url, safe='')}"

    # WebApp URL: sumitgautamad-rgb player on GitHub Pages (with your Adsterra Ads!)
    if stream_url:
        webapp_url = (
            f"{WEBAPP_BASE}?v={urllib.parse.quote(stream_url, safe='')}"
            f"&title={urllib.parse.quote(filename)}"
            f"&size={urllib.parse.quote(size_str)}"
        )
    else:
        webapp_url = (
            f"{WEBAPP_BASE}?url={urllib.parse.quote(url, safe='')}"
            f"&title={urllib.parse.quote(filename)}"
            f"&size={urllib.parse.quote(size_str)}"
        )

    result = {
        'filename': filename,
        'size_str': size_str,
        'duration_str': duration_str,
        'stream_url': stream_url,
        'thumbnail': thumbnail,
        'webapp_url': webapp_url,
        'browser_url': browser_url,
        'source': data.get('source', 'fast'),
    }

    video_cache[url] = {'data': result, 'time': now}
    return result

# ============================================================
# Telegram Handlers
# ============================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    logging.info(f"--> /start received from user {user_id}")

    # Force Subscription check
    if not await is_subscribed(context, user_id):
        await update.message.reply_text(
            FORCE_SUB_MSG,
            reply_markup=get_force_sub_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    welcome = (
        "🎬 <b>TeraBox Video Player Bot</b>\n\n"
        "Koi bhi TeraBox link bhejo — video seedha play hoga!\n\n"
        "📌 <b>Supported sites:</b>\n"
        "• 1024terabox.com\n"
        "• terabox.com / teraboxapp.com\n"
        "• freeterabox.com aur sabhi mirror sites\n\n"
        "✨ <b>Kaise use karein:</b>\n"
        "Apna TeraBox video link yahan paste karein aur bhejein!"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.HTML)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    user_id = query.from_user.id
    logging.info(f"--> Callback query: {query.data} from {user_id}")

    if query.data == "check_sub":
        if await is_subscribed(context, user_id):
            await query.answer("✅ Verified! Ab aap bot use kar sakte hain.", show_alert=True)
            welcome_after = (
                "✅ <b>Welcome! Channel verified.</b>\n\n"
                "Ab aap koi bhi TeraBox link yahan bhej sakte hain, video play ho jayegi! 🎬"
            )
            try:
                await query.edit_message_text(welcome_after, parse_mode=ParseMode.HTML)
            except Exception:
                if query.message:
                    await query.message.reply_text(welcome_after, parse_mode=ParseMode.HTML)
        else:
            await query.answer(
                "❌ Aapne abhi tak channel join nahi kiya hai! Kripya pehle 'Join Channel' par click karke channel join karein.",
                show_alert=True
            )

async def setchannel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if context.args:
        try:
            cid = int(context.args[0])
            save_channel_id(cid)
            await update.message.reply_text(
                f"✅ Force Sub Channel ID set to: <code>{cid}</code>\n"
                f"Ab compulsory channel join system active hai!",
                parse_mode=ParseMode.HTML
            )
            return
        except ValueError:
            pass
    current = get_channel_id()
    await update.message.reply_text(
        f"Usage: <code>/setchannel -100xxxxxxxxxx</code>\n\n"
        f"Current Channel ID: <code>{current or 'Not Set'}</code>",
        parse_mode=ParseMode.HTML
    )

async def chat_member_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.my_chat_member:
        chat = update.my_chat_member.chat
        if chat.type in ["channel", "supergroup"]:
            save_channel_id(chat.id)
            logging.info(f"Auto-saved Channel ID: {chat.id} ({chat.title})")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    text = update.message.text or ''
    logging.info(f"--> User {user_id} sent message: {text[:80]}")

    # Auto-detect channel ID if admin forwards a post from channel to bot
    forward_chat = None
    if update.message.forward_from_chat:
        forward_chat = update.message.forward_from_chat
    elif hasattr(update.message, 'forward_origin') and update.message.forward_origin:
        if hasattr(update.message.forward_origin, 'chat'):
            forward_chat = update.message.forward_origin.chat

    if forward_chat and forward_chat.type == "channel":
        save_channel_id(forward_chat.id)
        await update.message.reply_text(
            f"✅ <b>Channel ID Detect Ho Gaya:</b> <code>{forward_chat.id}</code>\n"
            f"📢 <b>Title:</b> {html.escape(forward_chat.title or '')}\n\n"
            f"Ab sabhi users ke liye channel join compulsory ho gaya hai! 🎉",
            parse_mode=ParseMode.HTML
        )
        return

    # Check Force Subscription
    if not await is_subscribed(context, user_id):
        logging.info(f"User {user_id} blocked: not subscribed")
        await update.message.reply_text(
            FORCE_SUB_MSG,
            reply_markup=get_force_sub_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    terabox_url = find_terabox_url(text)

    if not terabox_url:
        logging.info(f"No terabox URL found in message from {user_id}")
        await update.message.reply_text(
            "⚠️ <b>TeraBox link nahi mila!</b>\n\n"
            "Kripya valid TeraBox link bhejein.\n"
            "Example:\n"
            "<code>https://1024terabox.com/s/1xxxxxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    logging.info(f"Processing URL for {user_id}: {terabox_url}")
    # Send processing status immediately so user sees bot response in < 0.5s!
    status_msg = await update.message.reply_text(
        "⚡ <b>Processing...</b>\n"
        "Video player link tayyar ho raha hai...",
        parse_mode=ParseMode.HTML
    )

    result = await asyncio.to_thread(process_terabox_link, terabox_url)
    logging.info(f"Extraction result: {result.get('filename')} (source: {result.get('source')})")

    filename = result['filename']
    size_str = result.get('size_str', '')
    duration_str = result.get('duration_str', '')
    webapp_url = result['webapp_url']
    browser_url = result.get('browser_url', webapp_url)
    thumbnail = result.get('thumbnail', '')

    safe_filename = html.escape(filename)

    lines = [f"🎬 <b>{safe_filename}</b>\n"]
    if size_str:
        lines.append(f"📦 <b>Size:</b> <code>{html.escape(size_str)}</code>")
    if duration_str:
        lines.append(f"⏱️ <b>Duration:</b> <code>{html.escape(duration_str)}</code>")
    lines.append("\n👇 <b>Niche button tap karein:</b>")

    caption = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Play Video",
                web_app=WebAppInfo(url=webapp_url)
            )
        ],
        [
            InlineKeyboardButton(
                "🌐 Browser mein kholein",
                url=browser_url
            )
        ]
    ])

    try:
        await status_msg.edit_text(
            caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        logging.info(f"✅ Successfully sent video buttons to user {user_id}")
    except Exception as e:
        logging.error(f"Message edit error: {e}")
        try:
            await update.message.reply_text(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("Exception while handling an update:", exc_info=context.error)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        if self.path == '/logs':
            output = "\n".join(log_buffer) or "Bot running. No logs yet."
            self.wfile.write(output.encode('utf-8', errors='replace'))
        else:
            self.wfile.write(b"TeraBox Bot is running 24/7! Visit /logs for real-time logs.")

    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.environ.get("PORT", 0))
    if port:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logging.info(f"Health server started on port {port} (diagnostics available at /logs)")

def main():
    logging.info("🤖 TeraBox Bot starting...")
    start_health_server()
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing in .env file!")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("setchannel", setchannel_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(ChatMemberHandler(chat_member_update_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    logging.info("✅ Bot polling started with Force Channel Subscription!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
