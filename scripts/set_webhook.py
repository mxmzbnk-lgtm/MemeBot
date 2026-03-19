#!/usr/bin/env python3
"""One-time script to register the Telegram webhook with your Vercel deployment.

Usage:
    VERCEL_URL=https://your-app.vercel.app python scripts/set_webhook.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from lib.config import BOT_TOKEN, WEBHOOK_SECRET

VERCEL_URL = os.getenv("VERCEL_URL", "").strip().rstrip("/")


def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not set.")
        sys.exit(1)
    if not VERCEL_URL:
        print("ERROR: VERCEL_URL is not set. Example: https://your-app.vercel.app")
        sys.exit(1)

    webhook_url = f"{VERCEL_URL}/api/webhook"

    import httpx

    payload = {"url": webhook_url}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    resp = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json=payload,
        timeout=15,
    )
    data = resp.json()

    if data.get("ok"):
        print(f"Webhook set successfully: {webhook_url}")
    else:
        print(f"Failed to set webhook: {data}")
        sys.exit(1)


if __name__ == "__main__":
    main()
