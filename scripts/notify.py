#!/usr/bin/env python3
"""
可选 S 级强提醒推送脚本（仅在配置了对应 GitHub Secrets 时触发，平时静默不打扰）
支持：
- BARK_URL (iPhone Bark，例如 https://api.day.app/你的Key/)
- TG_BOT_TOKEN + TG_CHAT_ID (Telegram)
- WEBHOOK_URL (飞书 / 钉钉 / 企业微信自定义机器人 Webhook)
"""

import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVE_PATH = ROOT / "docs" / "data" / "active.json"
NOTIFY_STATE_PATH = ROOT / "docs" / "data" / "notified_ids.json"


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def send_notifications(title: str, body: str, dashboard_url: str = "") -> None:
    bark_url = os.environ.get("BARK_URL", "").strip()
    if bark_url:
        base = bark_url.rstrip("/")
        url = f"{base}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
        if dashboard_url:
            url += f"?url={urllib.parse.quote(dashboard_url)}"
        try:
            urllib.request.urlopen(url, timeout=10).read()
            print("[Notify] Bark sent")
        except Exception as e:
            print(f"[Notify] Bark failed: {e}")

    tg_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TG_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        text = f"*{title}*\n{body}"
        if dashboard_url:
            text += f"\n\n打开看板：{dashboard_url}"
        try:
            post_json(f"https://api.telegram.org/bot{tg_token}/sendMessage", {
                "chat_id": tg_chat,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            print("[Notify] Telegram sent")
        except Exception as e:
            print(f"[Notify] Telegram failed: {e}")

    webhook = os.environ.get("WEBHOOK_URL", "").strip()
    if webhook:
        full_text = f"{title}\n{body}" + (f"\n看板直达：{dashboard_url}" if dashboard_url else "")
        payload = {"msg_type": "text", "content": {"text": full_text}} if "feishu" in webhook else {
            "msgtype": "text", "text": {"content": full_text}
        }
        try:
            post_json(webhook, payload)
            print("[Notify] Webhook sent")
        except Exception as e:
            print(f"[Notify] Webhook failed: {e}")


def main():
    active = load_json(ACTIVE_PATH, {"deals": []}).get("deals", [])
    notified = set(load_json(NOTIFY_STATE_PATH, []))
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()

    to_push = []
    for d in active:
        if d.get("tier") != "S":
            continue
        if d["id"] not in notified:
            to_push.append(f"【新S级·{d['score']}分】{d['title']}｜{d['benefit']}")
            notified.add(d["id"])
        elif d.get("deadline"):
            try:
                dl = dt.date.fromisoformat(d["deadline"])
                days_left = (dl - today).days
                remind_key = f"exp-{d['id']}"
                if 0 <= days_left <= 2 and remind_key not in notified:
                    to_push.append(f"【即将截止·剩{days_left}天】{d['title']}｜{d['benefit']}")
                    notified.add(remind_key)
            except ValueError:
                pass

    if not to_push:
        print("[Notify] No new S-tier deals to push.")
        return

    dashboard_url = os.environ.get("DASHBOARD_URL", "").strip()
    send_notifications("国内羊毛 S 级提醒", "\n".join(to_push[:5]), dashboard_url)
    NOTIFY_STATE_PATH.write_text(json.dumps(sorted(notified)[-200:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
