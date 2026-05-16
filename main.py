"""
LINE 投資早報主程式 v4.1
SESSION=report  → 07:30 TST 宏觀新聞摘要（Telegraph 發布）
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
from src.report_fetcher import fetch_macro_report
from src.report_builder import (
    get_or_create_telegraph_token,
    publish_to_telegraph,
    build_report_line_messages,
)


def run_report_session(fred_key: str, av_key: str, date_str: str):
    print("=" * 50)
    print("報告模式：07:30 TST")
    print("=" * 50)

    # 1. 取得 ETF 狀態供 Smart Beta 使用
    z_voo, hy_spread = 0.0, 4.5
    try:
        from src.data_fetcher import av_daily_close, fetch_hy_spread_fred
        from src.quant_engine import calc_ema_zscore
        import time
        prices = av_daily_close("VOO", av_key, days=300)
        z_voo  = calc_ema_zscore(prices) if not prices.empty else 0.0
        time.sleep(13)
        hy_spread = fetch_hy_spread_fred(fred_key)
    except Exception as e:
        print(f"  [WARN] ETF 狀態擷取失敗: {e}")

    # 2. 擷取宏觀報告資料
    report = fetch_macro_report(fred_key, hy_spread=hy_spread, z_voo=z_voo)

    # 3. 發布到 Telegraph
    report_url = ""
    try:
        token = get_or_create_telegraph_token("telegraph_token.txt")
        report_url = publish_to_telegraph(report, token)
    except Exception as e:
        print(f"  [WARN] Telegraph 發布失敗: {e}")

    # 4. 建構 LINE 訊息
    return build_report_line_messages(report, report_url)


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

    if session == "report":
        messages = run_report_session(fred_key, av_key, date_str)

    elif session == "morning":
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
