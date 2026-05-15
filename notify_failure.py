import os, requests
token   = os.environ.get("LINE_CHANNEL_TOKEN", "")
uid     = os.environ.get("LINE_USER_ID", "")
session = os.environ.get("SESSION", "morning")
label   = "早盤" if session == "morning" else "夜盤"
if token and uid:
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"to": uid, "messages": [{
            "type": "text",
            "text": f"⚠️ 投資{label}早報發送失敗，請檢查 GitHub Actions log。"
        }]},
    )
