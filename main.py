"""
LINE 投資早報主程式 v2
SESSION=morning → 09:30 TST 早盤（總經 + 0050 + 00679B）
SESSION=evening → 22:00 TST 夜盤（VOO）
"""

import os, sys
from datetime import datetime, timezone, timedelta

TST = timezone(timedelta(hours=8))

from src.quant_engine import run_morning_session, run_evening_session
from src.line_builder import (
    build_text_message, build_macro_card,
    build_etf_card, build_bond_card,
    send_line_messages,
)

def main():
    token    = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id  = os.environ.get("LINE_USER_ID", "")
    fred_key = os.environ.get("FRED_API_KEY", "")
    session  = os.environ.get("SESSION", "morning").lower()

    if not token or not user_id:
        print("[ERROR] LINE_CHANNEL_TOKEN 或 LINE_USER_ID 未設定")
        sys.exit(1)

    now = datetime.now(TST)
    date_str = now.strftime("%Y/%m/%d")
    print(f"SESSION={session}  date={date_str}")

    messages = []

    if session == "morning":
        macro, etf_signals, vix = run_morning_session(fred_key)

        # 若薩姆觸發，額外推一則緊急警報
        if macro.sahm_triggered:
            messages.append({
                "type": "text",
                "text": (
                    f"🚨【衰退警報】薩姆規則觸發！\n"
                    f"指標值：{macro.sahm_indicator:.2f}%（閾值 0.5%）\n"
                    f"衰退機率：{macro.recession_prob:.0f}%\n\n"
                    f"建議：停止加碼，轉入 00679B 等待降息。"
                )
            })

        # 訊息 1：文字問候
        messages.append(build_text_message("morning", macro, etf_signals, date_str))
        # 訊息 2：共用總經底層
        messages.append(build_macro_card(macro, date_str))
        # 訊息 3：0050
        sig_0050 = next((s for s in etf_signals if "0050" in s.ticker), None)
        if sig_0050:
            messages.append(build_etf_card(sig_0050, date_str, "morning"))
        # 訊息 4：00679B
        sig_bond = next((s for s in etf_signals if "00679B" in s.ticker), None)
        if sig_bond:
            messages.append(build_bond_card(sig_bond, macro, date_str))

    else:  # evening
        macro, sig_voo, vix = run_evening_session(fred_key)

        messages.append(build_text_message(
            "evening", macro,
            [sig_voo] if sig_voo else [], date_str
        ))
        if sig_voo:
            messages.append(build_etf_card(sig_voo, date_str, "evening"))

    ok = send_line_messages(messages[:5], token, user_id)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
