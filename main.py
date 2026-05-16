"""
LINE 投資早報主程式 v4.2
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
    av_key   = os.environ.get("AV_API_KEY", "")
    session  = os.environ.get("SESSION", "morning").lower().strip()

    if not token or not user_id:
        print("[ERROR] LINE_CHANNEL_TOKEN 或 LINE_USER_ID 未設定")
        sys.exit(1)

    now      = datetime.now(TST)
    date_str = now.strftime("%Y/%m/%d")
    print(f"SESSION={session}  date={date_str}")

    messages = []

    if session == "morning":
        if not av_key:
            print("[ERROR] AV_API_KEY 未設定")
            sys.exit(1)
        macro, etf_signals, vix = run_morning_session(fred_key, av_key)
        messages.append(build_text_message("morning", macro, etf_signals, date_str))
        messages.append(build_macro_card(macro, date_str))
        sig_0050 = next((s for s in etf_signals if "0050" in s.ticker), None)
        if sig_0050:
            messages.append(build_etf_card(sig_0050, date_str, "morning"))
        sig_bond = next((s for s in etf_signals if "00679B" in s.ticker), None)
        if sig_bond:
            messages.append(build_bond_card(sig_bond, macro, date_str))

    elif session == "evening":
        if not av_key:
            print("[ERROR] AV_API_KEY 未設定")
            sys.exit(1)
        macro, sig_voo, vix = run_evening_session(fred_key, av_key)
        messages.append(build_text_message(
            "evening", macro, [sig_voo] if sig_voo else [], date_str))
        if sig_voo:
            messages.append(build_etf_card(sig_voo, date_str, "evening"))

    else:
        print(f"[ERROR] 未知 SESSION={session}")
        sys.exit(1)

    print(f"準備推送 {len(messages)} 則訊息")
    ok = send_line_messages(messages[:5], token, user_id)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
