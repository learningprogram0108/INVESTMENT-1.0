"""
LINE 投資夜盤主程式 v7 — 新架構
標的：VOO + QQQ + GLD + VGIT + TYD + GRID
新增：市場體制偵測（RISK_ON/TRANSITION/RISK_OFF）
      三色信號燈（純規則，非 LLM）
      尾端風險清單（硬編碼）
      信號 delta 追蹤（prev_light 比對）
      兩層 Gemini 分析（體制敘事 + ETF 逐行）
"""

import os
import sys
import json
import pathlib
from datetime import datetime, timezone, timedelta
from dataclasses import asdict

TST = timezone(timedelta(hours=8))

from src.quant_engine import (
    run_evening_session,
    detect_market_regime,
    calc_signal_light,
    eval_tail_risks,
)
from src.line_builder import (
    build_text_message, build_macro_card,
    build_etf_card,
    send_line_messages,
)
from src.gemini_summary import build_gemini_summary
from src.data_fetcher import setup_openbb_credentials
from src.dcc_garch import run_dcc_analysis, format_dcc_for_prompt

REPORT_PATH = pathlib.Path("docs/data/report.json")


# ─────────────────────────────────────────────
# 輔助函式
# ─────────────────────────────────────────────

def _load_prev_signal_lights() -> dict:
    """
    讀取上次 report.json 的 signal_lights，供 delta 計算用。
    Returns: {ticker: {"light": str}} 或空 dict
    """
    if not REPORT_PATH.exists():
        return {}
    try:
        with open(REPORT_PATH, encoding="utf-8") as f:
            prev = json.load(f)
        return prev.get("signal_lights", {})
    except Exception:
        return {}


def _calc_signal_lights(etf_signals: list, regime: dict, prev_lights: dict) -> dict:
    """
    計算所有 ETF 信號燈，並附加 delta（前次 vs 本次）。
    Returns: {ticker: {"light", "reason", "prev_light", "changed"}}
    """
    result = {}
    for sig in etf_signals:
        sl        = calc_signal_light(sig, regime)
        prev_sl   = prev_lights.get(sig.ticker, {})
        prev_light = prev_sl.get("light", "")
        result[sig.ticker] = {
            "light":      sl["light"],
            "reason":     sl["reason"],
            "prev_light": prev_light,
            "changed":    prev_light != "" and prev_light != sl["light"],
        }
    return result


def _build_report_json(
    macro, etf_signals, gemini_text,
    hrp_weights, dcc_result, tyd_timing,
    regime, signal_lights, tail_risks,
    date_str, now,
) -> dict:
    """構建完整 report.json 資料結構"""
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
        }

    return {
        "generated_at":  now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
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
        "regime":          regime,           # 新：體制偵測結果
        "signal_lights":   signal_lights,    # 新：{ticker: {light,reason,prev_light,changed}}
        "tail_risks":      tail_risks,       # 新：[{id, warning}]
        "etf_signals":     [_sig_to_dict(s) for s in etf_signals],
        "gemini_analysis": gemini_text,
        "hrp_weights":     hrp_weights,
        "dcc":             dcc_result or {},
        "tyd_timing":      tyd_timing,
    }


def _write_report_json(report: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[JSON] report.json 已寫出 → {REPORT_PATH}")


# ─────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────

def main():
    token      = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id    = os.environ.get("LINE_USER_ID", "")
    fred_key   = os.environ.get("FRED_API_KEY", "")
    av_key     = os.environ.get("AV_API_KEY", "")
    av_key_2   = os.environ.get("AV_API_KEY_2", "")
    av_keys    = [k for k in [av_key, av_key_2] if k]
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    session    = os.environ.get("SESSION", "evening").lower().strip()

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

    # ── OpenBB 憑證初始化 ──────────────────────────────────────────────
    setup_openbb_credentials(
        fred_key=fred_key,
        av_key=av_keys[0] if av_keys else "",
    )

    # ── 量化分析 ────────────────────────────────────────────────────────
    macro, etf_signals, vix, hrp_weights, tyd_timing = run_evening_session(fred_key, av_keys)
    tyd_score, tyd_label = tyd_timing

    # ── 體制偵測 + 信號燈 + 尾端風險（純規則，不呼叫 LLM）──────────────
    print("  [Regime] 市場體制偵測...")
    regime = detect_market_regime(macro)
    print(f"  [Regime] {regime['regime']} | 信用:{regime['credit']} | PMI:{regime['pmi_trend']} | 曲線:{regime['curve']}")

    prev_lights   = _load_prev_signal_lights()
    signal_lights = _calc_signal_lights(etf_signals, regime, prev_lights)
    for t, sl in signal_lights.items():
        delta = f"（{sl['prev_light']}→{sl['light']}）" if sl["changed"] else ""
        print(f"  [Signal] {t}: {sl['light']} {delta} — {sl['reason']}")

    tail_risks = eval_tail_risks(macro, etf_signals)
    if tail_risks:
        print(f"  [TailRisk] 已觸發 {len(tail_risks)} 條警示：")
        for r in tail_risks:
            print(f"    ⚠ {r['warning']}")
    else:
        print("  [TailRisk] 無觸發警示")

    # ── DCC-GARCH 分析（VOO / VGIT / GLD）─────────────────────────────
    dcc_result = None
    dcc_text   = ""
    try:
        print("  [DCC] 執行 DCC-GARCH(1,1) 分析...")
        dcc_result = run_dcc_analysis(av_key=av_keys[0] if av_keys else None)
        dcc_text   = format_dcc_for_prompt(dcc_result)
        print(f"  [DCC] 完成：α={dcc_result['dcc_alpha']:.4f} β={dcc_result['dcc_beta']:.4f}")
    except Exception as e:
        print(f"  [DCC] 分析失敗（{e}），略過")

    # ── Gemini 兩層分析（體制敘事 + ETF 逐行）──────────────────────────
    gemini_msg = build_gemini_summary(
        macro, etf_signals, "evening", gemini_key,
        dcc_text=dcc_text,
        regime=regime,
        signal_lights=signal_lights,
        tail_risks=tail_risks,
    )
    gemini_text = gemini_msg["text"] if gemini_msg else ""

    # ── 寫出 report.json ───────────────────────────────────────────────
    report = _build_report_json(
        macro, etf_signals, gemini_text,
        hrp_weights, dcc_result,
        {"score": tyd_score, "label": tyd_label},
        regime, signal_lights, tail_risks,
        date_str, now,
    )
    _write_report_json(report)

    # ── LINE 訊息（5 則上限）─────────────────────────────────────────
    # Msg 1: 文字摘要
    # Msg 2: AI 市場解讀（新兩層架構）
    # Msg 3: 總經指標卡
    # Msg 4: VOO ETF 卡
    # Msg 5: QQQ ETF 卡
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
