import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

KV_REST_API_URL = os.getenv("KV_REST_API_URL", "").strip()
KV_REST_API_TOKEN = os.getenv("KV_REST_API_TOKEN", "").strip()

WORK_START_HOUR = 9
WORK_END_HOUR = 22
MIN_GAP_HOURS = 3
MAX_GAP_HOURS = 5
MAX_DAILY_POSTS = 3
TZ_NAME = "Europe/Kyiv"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
