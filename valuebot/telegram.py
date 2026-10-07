import requests


def send(cfg, text, chat_id=None):
    chat_id = chat_id or cfg.telegram_chat_id
    if not chat_id or not text:
        return
    # Telegram accetta al massimo 4096 caratteri: si spezza sui paragrafi
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > 3800:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    for p in parts:
        r = requests.post(f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage",
                          json={"chat_id": chat_id, "text": p, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        if not r.ok:
            print("telegram", r.status_code, r.text[:300])


def set_webhook(cfg, url):
    r = requests.post(f"https://api.telegram.org/bot{cfg.telegram_token}/setWebhook",
                      json={"url": url, "secret_token": cfg.secret, "allowed_updates": ["message"]}, timeout=20)
    return r.text
