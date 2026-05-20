"""
LINE 投資夜盤主程式 v6 — 單一 22:00 TST 夜盤會話
標的：VOO + QQQ + GLD + VGIT + GRID
新增：DCC-GARCH(1,1) 兩階段量化分析（VOO/VGIT/GLD）
      5 資產 HRP 配置 + report.json 輸出（供 Cloudflare Pages Web App）
"""

import os
import sys
import json
import pathlib
from datetime import datetime, timezone, timedelta
from dataclasses import asdict

TST = timezone(timedelta(hours=8))

from src.quant_engine import run_evening_session
from src.line_builder import (
    build_text_message, build_macro_card,
    build_etf_card,
    send_line_messages,
)
from src.gemini_summary import build_gemini_summary, translate_headlines_zh, _format_data as _fmt_data
from src.data_fetcher import yahoo_news_headlines, setup_openbb_credentials
from src.dcc_garch import run_dcc_analysis, format_dcc_for_prompt


def _fetch_news(etf_signals: list) -> dict:
    """
    為每個 ETF 信號抓取最新新聞標題（list[dict{"title","url"}]）。
    Returns: {ticker: list[dict]} 供翻譯步驟使用
    """
    news_by_ticker: dict = {}
    for sig in etf_signals:
        if not sig.news_headlines:
            headlines = yahoo_news_headlines(sig.ticker, limit=3)
            sig.news_headlines = headlines
        news_by_ticker[sig.ticker] = sig.news_headlines
    return news_by_ticker


def _build_report_json(
    macro, etf_signals, gemini_text, hrp_weights, dcc_result, tyd_timing, date_str, now
) -> dict:
    """構建 report.json 資料結構"""
    def _sig_to_dict(s) -> dict:
        d = asdict(s)
        return {
            "ticker":              d["ticker"],
            "name":                d["name"],
            "price":               d["price"],
            "change_pct":          d["change_pct"],
            "z_score":             d["z_score"],
            "rsi":                 d["rsi"],
            "bb_pct":              d["bb_pct"],
            "sharpe_1y":           d["sharpe_1y"],
            "max_drawdown":        d["max_drawdown"],
            "macd_hist":           d["macd_hist"],
            "sentiment_score":     d["sentiment_score"],
            "combined_confidence": d["combined_confidence"],
            "confidence_signal":   d["confidence_signal"],
            "technical_score":     d["technical_score"],
            "value_score":         d["value_score"],
            "macro_score":         d["macro_score"],
            "cycle_phase":         d["cycle_phase"],
            "fund_multiplier":     d["fund_multiplier"],
            "multiplier_mode":     d["multiplier_mode"],
            "news_headlines":      d["news_headlines"],
        }

    report = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "macro": {
            "us10y":            macro.us10y,
            "us02y":            macro.us02y,
            "cpi_yoy":          macro.cpi_yoy,
            "breakeven":        macro.breakeven,
            "real_rate":        macro.real_rate,
            "yield_curve":      macro.yield_curve,
            "sahm_triggered":   macro.sahm_triggered,
            "sahm_indicator":   macro.sahm_indicator,
            "recession_prob":   macro.recession_prob,
            "hy_spread":        macro.hy_spread,
            "ism_pmi":          macro.ism_pmi,
            "pmi_second_deriv": macro.pmi_second_deriv,
        },
        "etf_signals":     [_sig_to_dict(s) for s in etf_signals],
        "gemini_analysis": gemini_text,
        "hrp_weights":     hrp_weights,
        "dcc":             dcc_result or {},
        "tyd_timing":      tyd_timing,
    }
    return report


def _write_report_json(report: dict) -> None:
    out_dir = pathlib.Path("docs/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[JSON] report.json 已寫出 → {out_path}")


def main():
    token       = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id     = os.environ.get("LINE_USER_ID", "")
    fred_key    = os.environ.get("FRED_API_KEY", "")
    av_key      = os.environ.get("AV_API_KEY", "")
    av_key_2    = os.environ.get("AV_API_KEY_2", "")
    av_keys     = [k for k in [av_key, av_key_2] if k]
    gemini_key  = os.environ.get("GEMINI_API_KEY", "")
    session     = os.environ.get("SESSION", "evening").lower().strip()

    if not token or not user_id:
        print("[ERROR] LINE_CHANNEL_TOKEN 或 LINE_USER_ID 未設定")
        sys.exit(1)

    if session != "evening":
        print(f"[WARN] SESSION={session} 不支援，強制使用 evening")

    if not av_keys:
        print("[ERROR] AV_API_KEY 未設定")
        sys.exit(1)

    now      = datetime.now(TST)
    date_str = now.strftime("%Y/%m/%d")
    print(f"SESSION=evening  date={date_str}")

    # ── OpenBB 憑證初始化 ─────────────────────────────────────────────────
    setup_openbb_credentials(
        fred_key=fred_key,
        av_key=av_keys[0] if av_keys else "",
    )

    # ── 量化分析 ──────────────────────────────────────
    macro, etf_signals, vix, hrp_weights, tyd_timing = run_evening_session(fred_key, av_keys)
    tyd_score, tyd_label = tyd_timing
    news_by_ticker = _fetch_news(etf_signals)

    # ── DCC-GARCH 分析（VOO / VGIT / GLD）─────────────
    dcc_result = None
    dcc_text   = ""
    try:
        print("  [DCC] 執行 DCC-GARCH(1,1) 分析...")
        dcc_result = run_dcc_analysis(av_key=av_keys[0] if av_keys else None)
        dcc_text   = format_dcc_for_prompt(dcc_result)
        print(f"  [DCC] 完成：α={dcc_result['dcc_alpha']:.4f} β={dcc_result['dcc_beta']:.4f}")
    except Exception as e:
        print(f"  [DCC] 分析失敗（{e}），略過")

    # ── 繁中新聞翻譯（Gemini 批量，一次 API 呼叫）─────
    if gemini_key and news_by_ticker:
        print("  [Translate] 翻譯新聞標題...")
        try:
            translated = translate_headlines_zh(news_by_ticker, gemini_key)
            for sig in etf_signals:
                if sig.ticker in translated:
                    sig.news_headlines = translated[sig.ticker]
        except Exception as e:
            print(f"  [Translate] 翻譯失敗（{e}），略過")

    # ── Gemini AI 摘要（含 DCC 數據）─────────────────
    gemini_msg  = build_gemini_summary(macro, etf_signals, "evening", gemini_key,
                                       dcc_text=dcc_text)
    gemini_text = gemini_msg["text"] if gemini_msg else ""

    # ── 寫出 report.json ──────────────────────────────
    report = _build_report_json(
        macro, etf_signals, gemini_text, hrp_weights, dcc_result,
        {"score": tyd_score, "label": tyd_label}, date_str, now
    )
    _write_report_json(report)

    # ── LINE 訊息組合（5 訊息上限）───────────────────
    # Msg 1: 文字摘要（5 ETF + 宏觀）
    # Msg 2: Gemini AI 傳奇投資人分析（含 DCC）
    # Msg 3: 總經指標卡
    # Msg 4: VOO ETF 卡
    # Msg 5: QQQ ETF 卡
    # GLD / VGIT / GRID → Web App only

    messages = []
    messages.append(build_text_message("evening", macro, etf_signals, date_str))
    if gemini_msg:
        messages.append(gemini_msg)
    messages.append(build_macro_card(macro, date_str))

    sig_voo = next((s for s in etf_signals if s.ticker == "VOO"), None)
    sig_qqq = next((s for s in etf_signals if s.ticker == "QQQ"), None)
    if sig_voo:
        messages.append(build_etf_card(sig_voo, date_str, "evening"))
    if sig_qqq:
        messages.append(build_etf_card(sig_qqq, date_str, "evening"))

    print(f"準備推送 {len(messages)} 則訊息")
    ok = send_line_messages(messages[:5], token, user_id)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
