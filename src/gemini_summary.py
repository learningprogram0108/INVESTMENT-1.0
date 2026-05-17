"""
Gemini AI 市場摘要模組
模型：gemini-2.0-flash-lite（RPM/15、TPM/250K）
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

SYSTEM_PROMPT = """你是一位熟悉以下投資大師思想的市場分析師，請嚴格依照大師框架進行分析：

一、價值投資與週期大師
1. 霍華．馬克斯（Howard Marks）：「第二層思考」—超越大眾表面思維；「鐘擺效應」—市場情緒在極端樂觀與極端悲觀間擺盪；風險＝永久損失資本的可能性，而非學術波動率。
2. 雷．達利歐（Ray Dalio）：長短期債務週期分析；風險平價配置；理解宏觀流動性週期。

二、投資心理與行為財務學專家
1. 摩根．豪瑟（Morgan Housel）：「致富心態」—行為紀律比智商更重要；安全邊際—為未知意外預留容錯空間；歷史以人性規律不斷重演。
2. 李．弗里曼-修爾（Lee Freeman-Shor）：「執行的藝術」—頂尖投資人面對虧損像「刺客」果斷停損，或像「獵人」在便宜時加碼，切忌像「兔子」僵住不動。

三、總體經濟與歷史週期專家
1. 彼得．奧本海默（Peter C. Oppenheimer）：市場週期四階段（絕望→希望→成長→樂觀）；透過總經領先指標辨識週期轉折。
2. 愛德華．錢思樂（Edward Chancellor）：「時間的代價」—利率是金錢的時間價格；低利率扭曲資產估值、催生泡沫。"""


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
    session_label = "早盤（台股 09:30 TST）" if session == "morning" else "夜盤（美股 22:00 TST）"
    lines = [
        f"【{session_label}市場數據】",
        f"衰退機率：{macro.recession_prob:.0f}%",
        f"薩姆法則：{'觸發 ⚠️' if macro.sahm_triggered else '未觸發'}（指標={macro.sahm_indicator:.2f}%）",
        f"HY 信用利差：{macro.hy_spread:.2f}%（{macro.credit_signal}）",
        f"US10Y 殖利率：{macro.us10y:.2f}%",
        f"殖利率曲線 10y-2y：{macro.yield_curve:+.2f}%",
        f"ISM PMI：{macro.ism_pmi[-1]:.1f}" if macro.ism_pmi else "",
        "",
        "ETF 技術指標：",
    ]
    for s in etf_signals:
        ticker = s.ticker.replace(".TW", "")
        macd_dir = "多頭" if (s.macd_line or 0) > (s.macd_signal or 0) else "空頭"
        hist_dir = "擴大" if (s.macd_hist or 0) > 0 else "收縮"
        lines.append(
            f"  {ticker}（{s.name}）"
            f"  價格={s.price}  Z={s.z_score:+.2f}"
            f"  情緒={s.sentiment_score:.0f}/100（{s.sentiment_label}）"
            f"  MACD={macd_dir}柱狀{hist_dir}"
            f"  乘數={s.fund_multiplier}x（{s.multiplier_mode}）"
        )
    return "\n".join(l for l in lines if l is not None)


def build_gemini_summary(
    macro: MacroIndicators,
    etf_signals: list,
    session: str,
    api_key: str,
) -> dict | None:
    if not api_key:
        print("  [Gemini] 未設定 GEMINI_API_KEY，略過")
        return None

    print("  [Gemini] 產生 AI 摘要...")
    data_text = _format_data(macro, etf_signals, session)

    user_content = (
        f"{data_text}\n\n"
        "請依照上述投資大師框架，用繁體中文分析當前市場狀態：\n"
        "1. 條列 3 至 5 個關鍵觀察（每點 15 字以內，直接寫數字加點，不用其他符號）\n"
        "2. 接著空一行，寫一段 200 字以內的綜合說明，引用至少一位大師的概念\n"
        "禁止輸出任何 Markdown 符號（# ** * - [ ] 等），只輸出純文字。"
    )

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 512,
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
        full_text = f"{session_emoji} AI 大師觀點\n\n{text}"
        print(f"  [Gemini] 摘要完成（{len(text)} 字）")
        return {"type": "text", "text": full_text}
    except (KeyError, IndexError) as e:
        print(f"  [Gemini] 解析回應失敗：{e}")
        return None
