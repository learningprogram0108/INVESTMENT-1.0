"""
Gemini AI 市場摘要模組 v2 — 多 Agent 架構
模型：gemini-3.1-flash-lite（RPM/15、TPM/250K）
架構：技術分析師 + 價值投資師 + 總經分析師 → 投資組合經理綜合決策
錯誤處理：429/503 指數退避重試，最多 4 次
"""

import time
import requests
from src.quant_engine import MacroIndicators, ETFSignal

GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_URL   = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
MAX_RETRIES = 4
BASE_WAIT   = 5  # 秒，指數倍增：5→10→20→40

# ── 多 Agent 角色系統 Prompt ──────────────────
SYSTEM_PROMPT = """你是一個由四個角色組成的投資分析委員會，各角色嚴格依照其所屬大師框架進行分析：

【技術分析師】— 李．弗里曼-修爾（Lee Freeman-Shor）框架
專注「執行的藝術」：頂尖投資人面對虧損像刺客（果斷停損）或獵人（便宜加碼），切忌兔子（僵住不動）。
根據技術面數據（MACD、RSI、Z-Score、布林帶）判斷短線動能與進場時機。

【價值投資師】— 霍華．馬克斯 + 愛德華．錢思樂框架
馬克斯：第二層思考—超越大眾表面思維；鐘擺效應—情緒在極端間擺盪；風險＝永久損失資本。
錢思樂：時間的代價—利率是金錢的時間價格；低利率扭曲估值、催生泡沫。
根據估值數據（CAPE、ERP、10Y預期報酬）判斷安全邊際與長期價值。

【總經分析師】— 雷．達利歐 + 彼得．奧本海默框架
達利歐：長短期債務週期、風險平價、宏觀流動性分析。
奧本海默：市場週期四階段（絕望→希望→成長→樂觀），透過總經領先指標辨識轉折。
根據總經數據（薩姆法則、HY利差、PMI、殖利率曲線）評估週期位置與系統性風險。

【投資組合經理】— 摩根．豪瑟（Morgan Housel）框架
行為紀律比智商更重要；安全邊際—為未知意外預留容錯空間；歷史以人性規律不斷重演。
綜合三位分析師觀點，給出最終決策與核心觀察。"""


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


def _format_data(macro: MacroIndicators, etf_signals: list, session: str) -> str:
    """組裝多 Agent 結構化輸入數據"""
    session_label = "早盤（台股 09:30 TST）" if session == "morning" else "夜盤（美股 22:00 TST）"

    # ── 技術面數據 ──
    tech_lines = [f"【{session_label} 技術面數據】"]
    for s in etf_signals:
        ticker = s.ticker.replace(".TW", "")
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

    # ── 估值面數據 ──
    val_lines = ["【估值面數據】"]
    for s in etf_signals:
        if s.cape is not None or s.erp is not None:
            ticker = s.ticker.replace(".TW", "")
            val_lines.append(
                f"  {ticker}  CAPE={s.cape:.1f}x"
                f"  ERP={s.erp:+.2f}%"
                f"  10Y預期={s.predicted_10y_return:+.1f}%/yr"
                if (s.cape and s.erp and s.predicted_10y_return)
                else f"  {ticker}  估值數據暫無"
            )
        else:
            ticker = s.ticker.replace(".TW", "")
            val_lines.append(f"  {ticker}  （債券，估值不適用）")

    # ── 總經面數據 ──
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

    # ── 新聞標題（如有）──
    news_lines = []
    all_headlines = []
    for s in etf_signals:
        if s.news_headlines:
            all_headlines.extend([f"  [{s.ticker.replace('.TW','')}] {h}"
                                   for h in s.news_headlines[:3]])
    if all_headlines:
        news_lines = ["【最新新聞標題（請納入總經分析師評分）】"] + all_headlines

    all_parts = tech_lines + [""] + val_lines + [""] + macro_lines
    if news_lines:
        all_parts += [""] + news_lines
    return "\n".join(all_parts)


def _build_prompt(data_text: str, etf_signals: list) -> str:
    """組裝多 Agent 輸出要求"""
    ticker_list = "、".join(s.ticker.replace(".TW", "") for s in etf_signals)
    return (
        f"{data_text}\n\n"
        f"---\n"
        f"請針對上述數據，依序以四個角色輸出（禁止使用任何 Markdown 符號如 # ** * - [ ]，全部純文字）：\n\n"
        f"針對標的：{ticker_list}\n\n"
        f"技術分析師：[BUY/HOLD/SELL] 信心XX% — （15字內理由）\n"
        f"價值投資師：[BUY/HOLD/SELL] 信心XX% — （15字內理由）\n"
        f"總經分析師：[BUY/HOLD/SELL] 信心XX% — （15字內理由）\n\n"
        f"投資組合經理決策：[買入/持有/賣出]\n"
        f"重點觀察：\n"
        f"1. （15字內）\n"
        f"2. （15字內）\n"
        f"3. （15字內）\n\n"
        f"綜合說明：（150字以內，引用至少一位大師的概念，不用換行）"
    )


def build_gemini_summary(
    macro: MacroIndicators,
    etf_signals: list,
    session: str,
    api_key: str,
) -> dict | None:
    if not api_key:
        print("  [Gemini] 未設定 GEMINI_API_KEY，略過")
        return None
    if not etf_signals:
        print("  [Gemini] 無 ETF 信號，略過")
        return None

    print("  [Gemini] 產生 AI 多視角摘要...")
    data_text    = _format_data(macro, etf_signals, session)
    user_content = _build_prompt(data_text, etf_signals)

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": 0.65,
            "maxOutputTokens": 600,
        },
    }
    headers = {"Content-Type": "application/json"}
    url = f"{GEMINI_URL}?key={api_key}"

    resp = _call_with_retry(url, headers, payload)
    if resp is None:
        return None

    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        session_emoji = "☀️" if session == "morning" else "🌙"
        full_text = f"{session_emoji} AI 多視角分析\n\n{text}"
        print(f"  [Gemini] 摘要完成（{len(text)} 字）")
        return {"type": "text", "text": full_text}
    except (KeyError, IndexError) as e:
        print(f"  [Gemini] 解析回應失敗：{e}")
        return None
