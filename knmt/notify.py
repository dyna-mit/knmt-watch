"""Telegram push notification for daily changes."""
from __future__ import annotations

import os
from html import escape

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3900  # Telegram hard limit is 4096; keep headroom.


def _line(v: dict) -> str:
    bits = [b for b in (v.get("city"), v.get("practice"), v.get("hours")) if b]
    meta = " · ".join(bits)
    title = escape(v.get("title") or v.get("slug", ""))
    line = f'• <a href="{escape(v["url"])}">{title}</a>'
    if meta:
        line += f"\n  <i>{escape(meta)}</i>"
    return line


def build_message(added: list[dict], removed: list[dict], dashboard_url: str = "") -> str:
    parts = ["<b>KNMT tandarts-vacatures — update</b>"]
    if added:
        parts.append(f"\n📌 <b>Nieuw ({len(added)})</b>")
        parts += [_line(v) for v in added[:25]]
        if len(added) > 25:
            parts.append(f"…en {len(added) - 25} meer")
    if removed:
        parts.append(f"\n❌ <b>Verdwenen ({len(removed)})</b>")
        parts += [
            f'• {escape(v.get("title") or v.get("slug",""))}' for v in removed[:15]
        ]
        if len(removed) > 15:
            parts.append(f"…en {len(removed) - 15} meer")
    if dashboard_url:
        parts.append(f'\n🔎 <a href="{escape(dashboard_url)}">Bekijk alle vacatures</a>')
    msg = "\n".join(parts)
    return msg[:MAX_LEN]


def send(text: str) -> bool:
    """Send a message using TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping send.")
        return False
    resp = requests.post(
        API.format(token=token),
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"[notify] Telegram error {resp.status_code}: {resp.text[:300]}")
    return resp.ok
