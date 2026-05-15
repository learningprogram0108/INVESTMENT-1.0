import os
import requests

token = os.environ.get("LINE_CHANNEL_TOKEN", "")
uid = os.environ.get("LINE_USER_ID", "")

if token and uid:
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "to": uid,
            "messages": [
                {
                    "type": "text",
                    "text": "⚠️ 投資早報發送失敗，請檢查 GitHub Actions log。",
                }
            ],
        },
    )
    print("失敗通知已發送")
