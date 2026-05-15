"""
LINE 投資每日早報 — 主程式
執行方式：python main.py
環境變數（GitHub Secrets）：
  LINE_CHANNEL_TOKEN  LINE Messaging API Channel Access Token
  LINE_USER_ID        推送目標的 LINE User ID（Uxxxxxxxxx）
  FRED_API_KEY        FRED 總經資料 API Key（可空，降級用估算值）
  TRIGGERED_AT        由 Cloudflare 帶入的觸發時間（可空）
"""

import os
import sys
from datetime import datetime, timezone, timedelta

from src.quant_engine import run_quant_engine, fetch_vix
from src.line_builder import (
    build_text_message,
    build_weekday_flex,
    build_saturday_flex,
    send_line_messages,
)

# ── 時區設定 ──────────────────────────────────────────
TST = timezone(timedelta(hours=8))   # 台灣標準時間 UTC+8


def fetch_weekly_returns(etf_results: list) -> dict:
    """
    計算本週（週一至週五）漲跌幅，週六模式使用
    """
    import yfinance as yf
    weekly = {}
    for e in etf_results:
        try:
            hist = yf.Ticker(e.ticker).history(period="5d")
            if len(hist) >= 2:
                start = float(hist["Close"].iloc[0])
                end   = float(hist["Close"].iloc[-1])
                weekly[e.ticker] = round((end - start) / start * 100, 2)
            else:
                weekly[e.ticker] = 0.0
        except Exception:
            weekly[e.ticker] = 0.0
    return weekly


def main():
    # ── 環境變數讀取 ────────────────────────────────────
    channel_token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id       = os.environ.get("LINE_USER_ID", "")
    fred_api_key  = os.environ.get("FRED_API_KEY", "")
    triggered_at  = os.environ.get("TRIGGERED_AT", "")

    if not channel_token or not user_id:
        print("[ERROR] LINE_CHANNEL_TOKEN 或 LINE_USER_ID 未設定")
        sys.exit(1)

    # ── 時間判斷 ────────────────────────────────────────
    now_tst      = datetime.now(TST)
    is_saturday  = (now_tst.weekday() == 5)           # 5 = 週六
    date_str     = now_tst.strftime("%Y/%m/%d")
    weekday_name = ["週一","週二","週三","週四","週五","週六","週日"][now_tst.weekday()]

    print(f"{'='*50}")
    print(f"LINE 投資早報  {date_str}（{weekday_name}）")
    print(f"觸發時間: {triggered_at or now_tst.strftime('%H:%M TST')}")
    print(f"模式: {'週六回顧' if is_saturday else '平日早報'}")
    print(f"{'='*50}")

    # ── 量化引擎執行 ────────────────────────────────────
    try:
        etf_results, macro = run_quant_engine(fred_api_key=fred_api_key)
    except Exception as e:
        print(f"[ERROR] 量化引擎執行失敗: {e}")
        # 發送錯誤通知
        send_line_messages(
            [{"type": "text",
              "text": f"⚠️ {date_str} 投資早報擷取資料失敗，請手動確認。\n錯誤：{str(e)[:100]}"}],
            channel_token, user_id
        )
        sys.exit(1)

    # VIX 補充到 macro（供 line_builder 使用）
    try:
        macro.vix = fetch_vix()
    except Exception:
        macro.vix = 20.0

    # ── 建構 LINE 訊息 ──────────────────────────────────
    messages = []

    # 訊息 1：文字摘要
    messages.append(build_text_message(macro, date_str, is_saturday))

    # 訊息 2：Flex 卡片
    if is_saturday:
        weekly_returns = fetch_weekly_returns(etf_results)
        messages.append(
            build_saturday_flex(etf_results, macro, date_str, weekly_returns)
        )
    else:
        messages.append(
            build_weekday_flex(etf_results, macro, date_str)
        )

    # 訊息 3（選用）：薩姆規則緊急警報
    if macro.sahm_triggered:
        messages.append({
            "type": "text",
            "text": (
                "🚨【衰退警報】薩姆規則已觸發！\n"
                f"指標值：{macro.sahm_indicator:.2f}%（閾值 0.5%）\n\n"
                "根據大師策略：\n"
                "• 刺客模式：停止定期扣款或降至 0.5x\n"
                "• 資金轉入 00679B（美債）等待降息\n"
                "• 若 Z-Score < -2.0，反而啟動獵人模式左側建倉\n\n"
                "請根據個人風險承受度審慎評估。"
            )
        })

    # ── LINE 推送 ───────────────────────────────────────
    # 單次 request 最多 5 個 message object，計費只算 1 則
    success = send_line_messages(messages[:5], channel_token, user_id)

    if success:
        print(f"\n✓ 完成！共推送 {len(messages)} 個訊息物件")
        print(f"  景氣階段：{macro.cycle_phase}")
        print(f"  資金乘數：{macro.fund_multiplier}x")
        print(f"  薩姆規則：{'⚠️ 觸發' if macro.sahm_triggered else f'{macro.sahm_indicator:.2f}%'}")
    else:
        print("\n✗ LINE 推送失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
