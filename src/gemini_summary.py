"""
Gemini AI 市場解讀模組 v4 — 兩層架構
模型：gemini-3.1-flash-lite（RPM/15、TPM/250K）

架構：
  Layer 1 — 體制敘事：解釋「今天市場是什麼狀態」（純事實，不給建議）
  Layer 2 — ETF 逐行：解釋「為何每個 ETF 得到這個信號」（翻譯量化結論，禁止更改信號）

設計原則：
  - Gemini 是「文字翻譯器」，量化邏輯由 quant_engine 負責
  - 移除傳奇投資人角色（信心 XX% 是假精度，角色名字是身份劇場）
  - 尾端風險由硬編碼清單觸發，不依賴 LLM 自由發揮
"""

import re
import time
import requests
from src.quant_engine import MacroIndicators, ETFSignal

GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_URL   = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
MAX_RETRIES = 4
BASE_WAIT   = 5   # 指數退避：5→10→20→40s

# ── 系統角色（精簡化，移除劇場名字）──────────────────
SYSTEM_PROMPT = (
    "你是一位簡潔的宏觀市場評論員，負責將量化分析結果轉譯為中文說明。\n"
    "工作守則：\n"
    "① 量化指標的結論已由模型計算完成，你只負責解釋這些結論在當前環境下的意義。\n"
    "② 禁止自行推導投資建議或更改信號結論。\n"
    "③ 不使用任何 Markdown 符號（禁止 # ** * - [ ]），全部純文字輸出。\n"
    "④ 文字直接、精簡、不重複廢話。"
)


# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────

def _call_with_retry(url: str, headers: dict, payload: dict):
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"  [Gemini] 網路錯誤：{e}，略過")
            return None

        if r.status_code == 200:
            return r

        if r.status_code in (429, 503):
            wait = BASE_WAIT * (2 ** attempt)
            if r.status_code == 429:
                wait = max(wait, int(r.headers.get("Retry-After", wait)))
            print(f"  [Gemini] HTTP {r.status_code}，{wait}s 後重試 ({attempt+1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        print(f"  [Gemini] HTTP {r.status_code}，略過")
        return None

    print("  [Gemini] 超過重試次數，略過")
    return None


def _gemini_call(user_text: str, api_key: str, max_tokens: int = 300) -> str | None:
    """單次 Gemini API 呼叫，返回純文字或 None"""
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents":           [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig":   {"temperature": 0.55, "maxOutputTokens": max_tokens},
    }
    headers = {"Content-Type": "application/json"}
    url     = f"{GEMINI_URL}?key={api_key}"
    resp    = _call_with_retry(url, headers, payload)
    if resp is None:
        return None
    try:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        return None


# ─────────────────────────────────────────────
# Layer 1 — 體制敘事
# ─────────────────────────────────────────────

def _build_regime_prompt(
    macro: MacroIndicators,
    regime: dict,
    tail_risks: list,
    dcc_text: str = "",
) -> str:
    """組裝體制敘事的 Prompt 輸入"""
    pmi_val = macro.ism_pmi[-1] if macro.ism_pmi else 50.0

    lines = [
        f"【今日市場體制】{regime['regime']}",
        f"  信用面：{regime['credit']}（HY利差={macro.hy_spread:.2f}%）",
        f"  景氣面：{regime['pmi_trend']}（ISM PMI={pmi_val:.1f}，動能{'+' if macro.pmi_second_deriv > 0 else ''}{macro.pmi_second_deriv:.3f}）",
        f"  殖利率曲線：{regime['curve']}（{macro.yield_curve:+.2f}%）",
        f"  就業衰退訊號：{'已觸發' if regime['recession_flag'] else '未觸發'}",
        f"  實質利率：{macro.real_rate:+.2f}%　CPI年增：{macro.cpi_yoy:.1f}%",
    ]

    if dcc_text:
        lines += ["", dcc_text]

    if tail_risks:
        lines += ["", "【已觸發尾端風險】"]
        for r in tail_risks:
            lines.append(f"  ⚠ {r['warning']}")

    lines += [
        "",
        "請用100字以內說明此體制對持有美股ETF散戶的含義。",
        "只陳述事實與風險，不給具體買賣建議，不重複以上數字。",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Layer 2 — ETF 逐行信號說明
# ─────────────────────────────────────────────

def _build_etf_prompt(etf_signals: list, signal_lights: dict) -> str:
    """組裝 ETF 逐行說明的 Prompt 輸入"""
    lines = [
        "以下是量化模型已計算完成的各 ETF 信號燈（禁止更改信號結論）：",
        "",
    ]
    for s in etf_signals:
        sl = signal_lights.get(s.ticker, {})
        light  = sl.get("light",  "🟡 維持")
        reason = sl.get("reason", "")
        lines.append(f"  {s.ticker}（{s.name}）  {light}  依據：{reason}")

    lines += [
        "",
        "請對每個 ETF 輸出一行解釋（15字以內），說明「為什麼是這個信號」。",
        "格式（嚴格遵守，禁止換行）：",
        "TICKER 信號燈 — 說明文字",
        "",
        "範例：",
        "VOO 🟢 加碼 — MACD動能強，體制順風",
        "GLD 🟡 維持 — Z-Score偏高，等待回調",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 格式化舊版數據（內部用，供 _format_data 向下相容）
# ─────────────────────────────────────────────

def _format_data(macro: MacroIndicators, etf_signals: list, session: str,
                 dcc_text: str = "") -> str:
    """
    組裝結構化輸入數據（向下相容用，仍可由 preview_messages 驗證 prompt 格式）
    """
    session_label = "夜盤（美股 22:00 TST）"
    tech_lines = [f"【{session_label} 技術面數據】"]
    for s in etf_signals:
        ticker   = s.ticker.replace(".TW", "")
        hist_dir = "▲擴大" if (s.macd_hist or 0) > 0 else "▼收縮"
        rsi_note = "超買" if s.rsi > 70 else "超賣" if s.rsi < 30 else "正常"
        bb_note  = "突破上軌" if s.bb_pct > 1 else "突破下軌" if s.bb_pct < 0 else "帶內"
        tech_lines.append(
            f"  {ticker}（{s.name}）"
            f"  MACD柱={s.macd_hist:+.4f}({hist_dir})"
            f"  RSI={s.rsi:.1f}({rsi_note})"
            f"  Z={s.z_score:+.2f}"
            f"  BB%B={s.bb_pct:.2f}({bb_note})"
            f"  Sharpe(1Y)={s.sharpe_1y:+.2f}"
            f"  MaxDD={s.max_drawdown:+.1f}%"
        )

    val_lines = ["【估值面數據】"]
    for s in etf_signals:
        ticker = s.ticker.replace(".TW", "")
        if s.cape is not None or s.erp is not None:
            val_lines.append(
                f"  {ticker}  CAPE={s.cape:.1f}x"
                f"  ERP={s.erp:+.2f}%"
                f"  10Y預期={s.predicted_10y_return:+.1f}%/yr"
                if (s.cape and s.erp and s.predicted_10y_return)
                else f"  {ticker}  估值數據暫無"
            )
        else:
            val_lines.append(f"  {ticker}  （債券，估值不適用）")

    pmi_val = macro.ism_pmi[-1] if macro.ism_pmi else 50.0
    macro_lines = [
        "【總經面數據】",
        f"  衰退機率={macro.recession_prob:.0f}%"
        f"  薩姆法則={'觸發⚠️' if macro.sahm_triggered else f'正常({macro.sahm_indicator:.2f}%)'}"
        f"  HY利差={macro.hy_spread:.2f}%({macro.credit_signal})",
        f"  US10Y={macro.us10y:.2f}%  殖利率曲線={macro.yield_curve:+.2f}%"
        f"  實質利率(TIPS)={macro.real_rate:+.2f}%",
        f"  ISM PMI={pmi_val:.1f}({'擴張' if pmi_val>=50 else '收縮'})"
        f"  PMI動能={'加速' if macro.pmi_second_deriv>0 else '減速'}({macro.pmi_second_deriv:+.3f})",
    ]

    # 市場情緒背景（新聞標題）
    news_lines = []
    all_headlines = []
    for s in etf_signals:
        if s.news_headlines:
            ticker_tag = s.ticker.replace(".TW", "")
            for h in s.news_headlines[:3]:
                if isinstance(h, dict):
                    zh_title  = h.get("title_zh") or h.get("title", "")
                    publisher = h.get("publisher", "")
                    summary   = h.get("summary_zh") or h.get("summary", "")
                else:
                    zh_title, publisher, summary = str(h), "", ""
                if not zh_title:
                    continue
                pub_tag = f"[{publisher}]" if publisher else ""
                line = f"  [{ticker_tag}]{pub_tag} {zh_title}"
                if summary:
                    brief = summary[:60].rstrip() + ("…" if len(summary) > 60 else "")
                    line += f"\n    → {brief}"
                all_headlines.append(line)
    if all_headlines:
        news_lines = [
            "【市場情緒背景（僅供輔助參考）】",
            "  注意：以下為新聞標題，文字精簡可能缺乏完整脈絡，",
            "        請以殖利率曲線、HY利差、PMI 等量化指標為主要依據，",
            "        新聞僅用於感知市場情緒方向，勿直接影響信心分數。",
        ] + all_headlines

    all_parts = tech_lines + [""] + val_lines + [""] + macro_lines
    if news_lines:
        all_parts += [""] + news_lines
    if dcc_text:
        all_parts += ["", dcc_text]
    return "\n".join(all_parts)


# ─────────────────────────────────────────────
# 主入口：兩層 Gemini 分析 → 合併輸出
# ─────────────────────────────────────────────

def build_gemini_summary(
    macro: MacroIndicators,
    etf_signals: list,
    session: str,
    api_key: str,
    dcc_text: str = "",
    regime: dict | None = None,
    signal_lights: dict | None = None,
    tail_risks: list | None = None,
) -> dict | None:
    """
    兩層 Gemini 分析：
      Layer 1 → 體制敘事（100字以內宏觀事實）
      Layer 2 → ETF 逐行信號說明（每行 ≤15字）
    """
    if not api_key:
        print("  [Gemini] 未設定 GEMINI_API_KEY，略過")
        return None
    if not etf_signals:
        print("  [Gemini] 無 ETF 信號，略過")
        return None

    # 若未提供 regime/signal_lights，使用降級模式（單次呼叫）
    if regime is None or signal_lights is None:
        print("  [Gemini] 降級模式（未傳入 regime/signal_lights）...")
        data_text = _format_data(macro, etf_signals, session, dcc_text=dcc_text)
        ticker_list = "、".join(s.ticker for s in etf_signals)
        prompt = (
            f"{data_text}\n\n---\n"
            f"重要：量化指標為主要依據，新聞標題僅為情緒背景輔助。\n\n"
            f"針對 {ticker_list}，用150字內給出今日市場簡評（純文字，不用角色扮演）。"
        )
        text = _gemini_call(prompt, api_key, max_tokens=400)
        if not text:
            return None
        return {"type": "text", "text": f"AI 市場解讀\n\n{text}"}

    # ── Layer 1：體制敘事 ──
    print("  [Gemini] Layer 1：體制敘事...")
    regime_prompt = _build_regime_prompt(macro, regime, tail_risks or [], dcc_text)
    layer1 = _gemini_call(regime_prompt, api_key, max_tokens=200)

    # ── Layer 2：ETF 逐行 ──
    print("  [Gemini] Layer 2：ETF 信號說明...")
    etf_prompt = _build_etf_prompt(etf_signals, signal_lights)
    layer2 = _gemini_call(etf_prompt, api_key, max_tokens=300)

    # ── 組合輸出 ──
    regime_label = regime.get("regime", "")
    credit_label = regime.get("credit", "")
    pmi_label    = regime.get("pmi_trend", "")
    curve_label  = regime.get("curve", "")
    header_line  = f"體制：{regime_label} · 信用{credit_label} · {pmi_label} · 曲線{curve_label}"

    parts = [f"AI 市場解讀\n", f"【{header_line}】"]

    if tail_risks:
        for r in tail_risks:
            parts.append(f"⚠ {r['warning']}")

    if layer1:
        parts.append(f"\n{layer1}")

    if layer2:
        parts.append(f"\n【各標的信號】\n{layer2}")

    full_text = "\n".join(parts)
    total_chars = len(full_text)
    print(f"  [Gemini] 完成（{total_chars} 字，Layer1={len(layer1 or '')} Layer2={len(layer2 or '')}）")
    return {"type": "text", "text": full_text}
