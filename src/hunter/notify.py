"""The ONLY outbound-messaging module in the repo, by design and by guard
test. It can reach exactly one destination: Krish's own Telegram chat, using
the bot token and chat id in system_config. It never contacts anyone else,
never sends application materials anywhere, and degrades to stdout when the
Telegram credentials are absent (the Routine's own completion notification
still reaches Krish).
"""
from __future__ import annotations

import requests

from .config import Config


def send_summary(cfg: Config, text: str) -> dict:
    token = cfg.optional("hunter_telegram_bot_token")
    chat_id = cfg.optional("hunter_telegram_chat_id")
    if not token or not chat_id:
        print(text)
        return {"sent": False, "reason": "telegram credentials absent; summary printed"}
    # Telegram caps messages at 4096 chars; split on paragraph boundaries.
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > 3900:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    for chunk in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True},
            timeout=30)
        if r.status_code != 200:
            print(text)
            return {"sent": False,
                    "reason": f"telegram returned {r.status_code}; summary printed"}
    return {"sent": True, "chunks": len(chunks)}
