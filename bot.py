"""
TeraBox Ad-Free Video Telegram Bot
Universal - Sabhi TeraBox links chalega.
Engine: Cloudflare HLS Stream via teraplayer API (retry + fallback).
"""
import sys, os, re, json, asyncio, logging, urllib.parse, html, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
sys.stdout.reconfigure(encoding="utf-8")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBAPP_BASE = "https://sumitgautamad-rgb.github.io/teraplayer/"

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Video cache: {url: {'data': {...}, 'time': float}}
video_cache = {}
CACHE_EXPIRY = 3 * 3600  # 3 hours

# Session with connection pooling
session = requests.Session()
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
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
    urls = re.findall(r'https?://[^\s<>"\']+', text)
    for u in urls:
        if any(d in u.lower() for d in TERABOX_DOMAINS):
            return u
    return None

# ============================================================
# Core: Extract video info via teraplayer API
# ============================================================
API_URL = 'https://teraplayer-backend-temp.onrender.com/api/download'
MAX_RETRIES = 3
RETRY_DELAYS = [3, 8, 15]  # seconds between retries

def extract_video(terabox_url: str) -> dict | None:
    """
    Call teraplayer API with retry + exponential backoff.
    Works for ALL TeraBox links universally.
    Returns dict with: filename, size_str, duration_str, resolution, stream_url, thumbnail
    Returns None on failure.
    """
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Attempt {attempt + 1}/{MAX_RETRIES} for: {terabox_url}")
            r = session.post(
                API_URL,
                json={'url': terabox_url},
                timeout=30,  # 30s for cold start
            )

            if r.status_code == 200:
                d = r.json()
                if d.get('ok') and d.get('stream_url'):
                    title = d.get('title') or 'TeraBox Video'
                    size_str = d.get('size_str') or ''
                    duration = d.get('duration') or 0
                    resolution = d.get('resolution') or ''
                    stream_url = d.get('stream_url', '')
                    thumbnail = d.get('thumbnail') or ''

                    # Format duration as MM:SS
                    duration_str = ''
                    if duration:
                        m, s = divmod(int(duration), 60)
                        duration_str = f"{m}:{s:02d}"

                    logger.info(f"✅ Got stream for: {title} ({size_str})")
                    return {
                        'filename': title,
                        'size_str': size_str,
                        'duration_str': duration_str,
                        'resolution': resolution,
                        'stream_url': stream_url,
                        'thumbnail': thumbnail,
                    }
                else:
                    err = d.get('error') or 'Unknown error from API'
                    logger.warning(f"API ok=false: {err}")
                    if 'password' in str(err).lower():
                        return {'error': '🔒 Yeh link password protected hai.'}

            elif r.status_code == 503 or r.status_code == 502:
                logger.warning(f"Server unavailable (attempt {attempt + 1}), retrying...")
            else:
                logger.warning(f"API returned status {r.status_code}")

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt + 1}")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error attempt {attempt + 1}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error attempt {attempt + 1}: {e}")

        # Wait before retry (except on last attempt)
        if attempt < MAX_RETRIES - 1:
            delay = RETRY_DELAYS[attempt]
            logger.info(f"Waiting {delay}s before retry...")
            time.sleep(delay)

    return None


def process_terabox_link(url: str) -> dict:
    """
    Process with caching. Returns result dict or {'error': '...'}.
    """
    now = time.time()

    # Check cache
    if url in video_cache:
        item = video_cache[url]
        if now - item['time'] < CACHE_EXPIRY:
            logger.info(f"Cache hit: {url}")
            return item['data']
        else:
            del video_cache[url]

    data = extract_video(url)

    if not data:
        return {'error': '❌ Video link process nahi ho paya.\n\nKaran:\n• TeraBox server busy hai\n• Link expire ho gaya\n• Private/deleted file\n\nThodi der baad dobara try karein.'}

    if 'error' in data:
        return data

    stream_url = data['stream_url']
    filename = data['filename']
    size_str = data['size_str']
    duration_str = data.get('duration_str', '')
    resolution = data.get('resolution', '')
    thumbnail = data.get('thumbnail', '')

    webapp_url = (
        f"{WEBAPP_BASE}?v={urllib.parse.quote(stream_url, safe='')}"
        f"&title={urllib.parse.quote(filename)}"
        f"&size={urllib.parse.quote(size_str)}"
    )

    result = {
        'filename': filename,
        'size_str': size_str,
        'duration_str': duration_str,
        'resolution': resolution,
        'stream_url': stream_url,
        'thumbnail': thumbnail,
        'webapp_url': webapp_url,
    }

    video_cache[url] = {'data': result, 'time': now}
    return result


# ============================================================
# Telegram Handlers
# ============================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "🎬 <b>TeraBox Ad-Free Player Bot</b>\n\n"
        "Koi bhi TeraBox link bhejo — video Telegram ke andar hi chalega!\n"
        "Zero Ads. Zero Redirects. 100% Free.\n\n"
        "📌 <b>Supported sites:</b>\n"
        "• 1024terabox.com\n"
        "• terabox.com / teraboxapp.com\n"
        "• freeterabox.com\n"
        "• Aur bhi sabhi TeraBox mirror sites\n\n"
        "✨ <b>Features:</b>\n"
        "• 🚫 No Ads, No Popups\n"
        "• ▶️ Seedha Telegram mein play\n"
        "• 🌐 Browser mein bhi dekh sakte hain\n"
        "• ⚡ Fast HLS streaming\n"
        "• 🔄 Har link support"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.HTML)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ''
    terabox_url = find_terabox_url(text)

    if not terabox_url:
        await update.message.reply_text(
            "⚠️ <b>TeraBox link nahi mila!</b>\n\n"
            "Kripya valid TeraBox link bhejein.\n"
            "Example:\n"
            "<code>https://1024terabox.com/s/1xxxxxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    status_msg = await update.message.reply_text(
        "⚡ <b>Processing...</b>\n"
        "Video link extract ho raha hai, please 5-10 second wait karein...",
        parse_mode=ParseMode.HTML
    )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, process_terabox_link, terabox_url)

    if 'error' in result:
        await status_msg.edit_text(result['error'], parse_mode=ParseMode.HTML)
        return

    filename = result['filename']
    size_str = result['size_str']
    duration_str = result.get('duration_str', '')
    resolution = result.get('resolution', '')
    webapp_url = result['webapp_url']
    thumbnail = result.get('thumbnail', '')

    safe_filename = html.escape(filename)

    # Build caption
    lines = [f"🎬 <b>{safe_filename}</b>\n"]
    if size_str:
        lines.append(f"📦 <b>Size:</b> <code>{html.escape(size_str)}</code>")
    if resolution:
        lines.append(f"🖥️ <b>Quality:</b> <code>{html.escape(resolution)}</code>")
    if duration_str:
        lines.append(f"⏱️ <b>Duration:</b> <code>{html.escape(duration_str)}</code>")
    lines.append("\n🛡️ <b>100% Ad-Free</b> — Koi ads nahi!")
    lines.append("\n👇 <b>Niche button tap karein:</b>")

    caption = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Play Video (No Ads)",
                web_app=WebAppInfo(url=webapp_url)
            )
        ],
        [
            InlineKeyboardButton(
                "🌐 Browser mein kholein",
                url=webapp_url
            )
        ]
    ])

    try:
        # Try to send with thumbnail if available
        if thumbnail:
            try:
                await status_msg.delete()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=thumbnail,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
                return
            except Exception as e:
                logger.warning(f"Thumbnail send failed: {e}, falling back to text")

        # Text message
        await status_msg.edit_text(
            caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Message send error: {e}")
        # Plain text fallback
        plain = (
            f"🎬 {filename}\n\n"
            f"📦 Size: {size_str}\n"
            f"🛡️ 100% Ad-Free\n\n"
            "👇 Niche button tap karein:"
        )
        try:
            await status_msg.edit_text(plain, reply_markup=keyboard)
        except Exception:
            await update.message.reply_text(plain, reply_markup=keyboard)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"TeraBox Bot is running 24/7!")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 0))
    if port:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"Health server started on port {port} (Render ready)")


def main():
    logger.info("🤖 TeraBox Bot starting...")
    start_health_server()
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing in .env file!")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    logger.info("✅ Bot polling started! Sabhi TeraBox links support hain.")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
