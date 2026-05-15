"""
LINE Flex Message 建構器
支援平日模式（即時行情）與週六模式（本週回顧）
"""

import requests
import json
from datetime import datetime
from typing import Optional


# ── 顏色常數 ──────────────────────────────────────────

PHASE_HERO_COLORS = {
    "成長": {"bg": "#0d5c3a", "accent": "#1D9E75"},
    "希望": {"bg": "#7a4800", "accent": "#EF9F27"},
    "絕望": {"bg": "#7a1a1a", "accent": "#E24B4A"},
    "樂觀": {"bg": "#6b3200", "accent": "#D85A30"},
}

def _phase_colors(phase: str) -> dict:
    for key, val in PHASE_HERO_COLORS.items():
        if key in phase:
            return val
    return {"bg": "#1a3a5c", "accent": "#378ADD"}


def _z_color(z: float) -> str:
    if z <= -2.0: return "#E24B4A"
    if z <= -1.0: return "#EF9F27"
    if z <= 1.5:  return "#1D9E75"
    if z <= 2.5:  return "#BA7517"
    return "#A32D2D"


def _z_label(z: float) -> str:
    if z <= -2.0: return "獵人加碼"
    if z <= -1.0: return "開始布局"
    if z <= 1.5:  return "鑑賞家"
    if z <= 2.5:  return "泡沫預警"
    return "刺客防禦"


def _chg_color(pct: float) -> str:
    return "#E24B4A" if pct < 0 else "#1D9E75"


def _chg_symbol(pct: float) -> str:
    return "▼" if pct < 0 else "▲"


# ── ETF 區塊（單一 ETF 列）────────────────────────────

def _etf_row(name: str, ticker: str, price: float, chg_pct: float,
             z: float, is_holiday: bool = False) -> dict:
    """建立單一 ETF 的 Flex 行"""
    if is_holiday:
        right_contents = [
            {"type": "text", "text": "休市", "size": "sm",
             "color": "#888888", "weight": "bold"},
            {"type": "text", "text": f"前收 {price:,.2f}",
             "size": "xxs", "color": "#aaaaaa"}
        ]
    else:
        chg_color  = _chg_color(chg_pct)
        chg_symbol = _chg_symbol(chg_pct)
        z_color    = _z_color(z)
        z_lbl      = _z_label(z)
        right_contents = [
            {"type": "text", "text": f"{price:,.2f}",
             "size": "sm", "weight": "bold", "color": "#111111"},
            {"type": "text",
             "text": f"{chg_symbol} {abs(chg_pct):.2f}%",
             "size": "xxs", "color": chg_color},
            {"type": "text",
             "text": f"Z={z:+.2f}  {z_lbl}",
             "size": "xxs", "color": z_color},
        ]

    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            {
                "type": "box", "layout": "vertical", "flex": 3,
                "contents": [
                    {"type": "text", "text": name,
                     "size": "sm", "weight": "bold", "color": "#222222"},
                    {"type": "text", "text": ticker,
                     "size": "xxs", "color": "#888888"},
                ]
            },
            {
                "type": "box", "layout": "vertical", "flex": 2,
                "alignItems": "flex-end",
                "contents": right_contents,
            }
        ],
        "paddingTop": "8px",
        "paddingBottom": "8px",
        "borderWidth": "1px",
        "borderColor": "#f0f0f0",
    }


# ── 總經指標區塊 ──────────────────────────────────────

def _macro_row(label: str, value: str, status_color: str = "#444444") -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            {"type": "text", "text": label, "size": "xxs",
             "color": "#666666", "flex": 4},
            {"type": "text", "text": value,  "size": "xxs",
             "color": status_color, "flex": 3, "align": "end", "weight": "bold"},
        ],
        "paddingTop": "3px",
        "paddingBottom": "3px",
    }


def _macro_status_color(value: float, thresholds: tuple,
                         colors: tuple = ("#1D9E75", "#EF9F27", "#E24B4A")) -> str:
    """通用閾值顏色判斷（低→綠, 中→橘, 高→紅）"""
    low, high = thresholds
    if value <= low:  return colors[0]
    if value <= high: return colors[1]
    return colors[2]


# ── 操作建議文字 ──────────────────────────────────────

ADVICE_TEXT = {
    "成長": "各項指標健康，照常 1.0x 定期投入 VOO / 0050，00679B 維持基本防禦倉。",
    "希望": "市場出現復甦訊號，建議加碼至 1.5x，重點布局 VOO / 0050，伺機減少 00679B。",
    "絕望": "恐慌超跌，獵人模式啟動！建議 2~3x 加速買入 VOO / 0050，這是長線好機會。",
    "樂觀": "市場過熱警訊，刺客防禦模式。降至 0.5x，資金轉往 00679B / 現金等待。",
}

def _advice(phase: str) -> str:
    for key, text in ADVICE_TEXT.items():
        if key in phase:
            return text
    return "維持常態配置，定期觀察指標變化。"


# ── 主卡片建構：平日 ──────────────────────────────────

def build_weekday_flex(etfs: list, macro, date_str: str) -> dict:
    """
    建構平日 Flex Message Bubble
    etfs: list[ETFData]
    macro: MacroIndicators
    """
    colors = _phase_colors(macro.cycle_phase)

    # 信用利差顏色
    cs_color = _macro_status_color(
        macro.credit_spread,
        thresholds=(4.5, 6.5),
        colors=("#1D9E75", "#EF9F27", "#E24B4A")
    )
    # 薩姆規則顏色（越高越危險）
    sahm_color = "#E24B4A" if macro.sahm_triggered else (
        "#EF9F27" if macro.sahm_indicator > 0.3 else "#1D9E75"
    )
    # 實質利率（過高傷害股市）
    rr_color = "#E24B4A" if macro.real_rate > 3.0 else (
        "#EF9F27" if macro.real_rate > 1.5 else "#1D9E75"
    )
    # VIX
    vix_val = getattr(macro, "vix", 20.0)
    vix_color = "#E24B4A" if vix_val >= 35 else (
        "#EF9F27" if vix_val >= 25 else "#1D9E75"
    )

    etf_rows = []
    etf_map = {e.ticker: e for e in etfs}
    for ticker, label in [("VOO", "Vanguard S&P 500"),
                           ("0050.TW", "元大台灣 50"),
                           ("00679B.TW", "元大美債 20年")]:
        e = etf_map.get(ticker)
        if e:
            etf_rows.append(_etf_row(label, ticker.replace(".TW", ""),
                                     e.price, e.change_pct, e.z_score))
        else:
            etf_rows.append(_etf_row(label, ticker.replace(".TW", ""),
                                     0.0, 0.0, 0.0, is_holiday=True))

    cape_text = (f"{macro.cape_ratio:.1f} → 10y≈{macro.cape_10y_return:+.1f}%/yr"
                 if macro.cape_ratio else "資料不足")
    cape_color = ("#E24B4A" if (macro.cape_10y_return or 5) < 1.0
                  else "#EF9F27" if (macro.cape_10y_return or 5) < 3.0
                  else "#1D9E75")

    bubble = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": colors["bg"],
            "paddingAll": "16px",
            "contents": [
                {"type": "text",
                 "text": f"📊 ETF 行情 & 量化訊號",
                 "color": "#ffffff", "size": "md", "weight": "bold"},
                {"type": "text",
                 "text": f"{date_str}  ·  09:30 TST",
                 "color": "#cccccc", "size": "xxs", "margin": "sm"},
                {
                    "type": "box", "layout": "horizontal",
                    "margin": "md", "contents": [
                        {
                            "type": "box", "layout": "vertical",
                            "backgroundColor": "rgba(255,255,255,0.15)",
                            "cornerRadius": "6px", "paddingAll": "6px",
                            "contents": [
                                {"type": "text", "text": macro.cycle_phase,
                                 "color": "#ffffff", "size": "sm", "weight": "bold"},
                                {"type": "text",
                                 "text": f"資金乘數：{macro.fund_multiplier}x",
                                 "color": colors["accent"], "size": "xs"},
                            ]
                        }
                    ]
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "14px",
            "spacing": "none",
            "contents": [
                # ETF 區塊標題
                {"type": "text", "text": "ETF 行情", "size": "xs",
                 "color": "#888888", "weight": "bold", "margin": "none"},
                *etf_rows,
                # 分隔
                {"type": "separator", "margin": "md", "color": "#eeeeee"},
                # 總經指標
                {"type": "text", "text": "⚙️ 總經量化指標",
                 "size": "xs", "color": "#888888", "weight": "bold",
                 "margin": "md"},
                {
                    "type": "box", "layout": "vertical",
                    "backgroundColor": "#f8f8f8", "cornerRadius": "8px",
                    "paddingAll": "10px", "margin": "sm",
                    "contents": [
                        _macro_row("信用利差 HY OAS",
                                   f"{macro.credit_spread:.1f}%  {macro.credit_signal.split(' ')[0]}",
                                   cs_color),
                        _macro_row("實質利率 r=i−π",
                                   f"{macro.real_rate:+.2f}%（{macro.nominal_rate:.1f}%−{macro.inflation:.1f}%）",
                                   rr_color),
                        _macro_row("薩姆規則",
                                   f"{'⚠️ 觸發！' if macro.sahm_triggered else macro.sahm_indicator:.2f}{'%' if not macro.sahm_triggered else ''}",
                                   sahm_color),
                        _macro_row("Michez 法則",
                                   f"{macro.sahm_indicator:.2f}%",
                                   "#1D9E75"),
                        _macro_row("VIX 恐慌指數",
                                   f"{vix_val:.1f}",
                                   vix_color),
                        _macro_row("CAPE → 10y報酬",
                                   cape_text,
                                   cape_color),
                    ]
                },
                # 分隔
                {"type": "separator", "margin": "md", "color": "#eeeeee"},
                # 操作建議
                {
                    "type": "box", "layout": "vertical",
                    "backgroundColor": "#f0faf5", "cornerRadius": "8px",
                    "paddingAll": "10px", "margin": "md",
                    "contents": [
                        {"type": "text", "text": "📋 今日操作建議",
                         "size": "xs", "weight": "bold", "color": "#0d5c3a"},
                        {"type": "text", "text": _advice(macro.cycle_phase),
                         "size": "xs", "color": "#0d5c3a",
                         "wrap": True, "margin": "sm"},
                    ]
                },
            ]
        }
    }
    return {"type": "flex", "altText": f"投資早報 {date_str}", "contents": bubble}


# ── 主卡片建構：週六 ──────────────────────────────────

def build_saturday_flex(etfs: list, macro, date_str: str,
                         weekly_returns: dict) -> dict:
    """
    週六版本：本週回顧，顯示週漲跌幅，ETF 標記休市
    weekly_returns: {"VOO": 1.23, "0050.TW": -0.45, "00679B.TW": 0.12}
    """
    colors = _phase_colors(macro.cycle_phase)

    etf_rows = []
    ticker_labels = [("VOO",      "Vanguard S&P 500"),
                     ("0050.TW",  "元大台灣 50"),
                     ("00679B.TW","元大美債 20年")]
    etf_map = {e.ticker: e for e in etfs}

    for ticker, label in ticker_labels:
        e = etf_map.get(ticker)
        weekly_r = weekly_returns.get(ticker, 0.0)
        wk_color  = _chg_color(weekly_r)
        wk_symbol = _chg_symbol(weekly_r)
        price = e.price if e else 0.0

        etf_rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "box", "layout": "vertical", "flex": 3,
                    "contents": [
                        {"type": "text", "text": label, "size": "sm",
                         "weight": "bold", "color": "#222222"},
                        {"type": "text", "text": f"{ticker.replace('.TW','')}  ·  週五收盤",
                         "size": "xxs", "color": "#888888"},
                    ]
                },
                {
                    "type": "box", "layout": "vertical", "flex": 2,
                    "alignItems": "flex-end",
                    "contents": [
                        {"type": "text", "text": f"{price:,.2f}",
                         "size": "sm", "weight": "bold", "color": "#111111"},
                        {"type": "text",
                         "text": f"本週 {wk_symbol} {abs(weekly_r):.2f}%",
                         "size": "xxs", "color": wk_color},
                        {"type": "text", "text": "今日休市",
                         "size": "xxs", "color": "#aaaaaa"},
                    ]
                }
            ],
            "paddingTop": "8px", "paddingBottom": "8px",
            "borderWidth": "1px", "borderColor": "#f0f0f0",
        })

    bubble = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": colors["bg"],
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": "📋 本週投資回顧",
                 "color": "#ffffff", "size": "md", "weight": "bold"},
                {"type": "text",
                 "text": f"{date_str}（週六）",
                 "color": "#cccccc", "size": "xxs", "margin": "sm"},
                {
                    "type": "box", "layout": "horizontal", "margin": "md",
                    "contents": [{
                        "type": "box", "layout": "vertical",
                        "backgroundColor": "rgba(255,255,255,0.15)",
                        "cornerRadius": "6px", "paddingAll": "6px",
                        "contents": [
                            {"type": "text", "text": macro.cycle_phase,
                             "color": "#ffffff", "size": "sm", "weight": "bold"},
                            {"type": "text",
                             "text": f"乘數 {macro.fund_multiplier}x  ·  下週展望",
                             "color": colors["accent"], "size": "xs"},
                        ]
                    }]
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "14px",
            "spacing": "none",
            "contents": [
                {"type": "text", "text": "本週收盤價 & 週漲跌",
                 "size": "xs", "color": "#888888", "weight": "bold"},
                *etf_rows,
                {"type": "separator", "margin": "md", "color": "#eeeeee"},
                {
                    "type": "box", "layout": "vertical",
                    "backgroundColor": "#f0faf5", "cornerRadius": "8px",
                    "paddingAll": "10px", "margin": "md",
                    "contents": [
                        {"type": "text", "text": "📅 下週操作建議",
                         "size": "xs", "weight": "bold", "color": "#0d5c3a"},
                        {"type": "text",
                         "text": f"景氣維持「{macro.cycle_phase}」，下週建議延續 {macro.fund_multiplier}x 扣款。"
                                 + _advice(macro.cycle_phase),
                         "size": "xs", "color": "#0d5c3a",
                         "wrap": True, "margin": "sm"},
                    ]
                },
            ]
        }
    }
    return {"type": "flex", "altText": f"本週投資回顧 {date_str}", "contents": bubble}


# ── 文字訊息（訊息 1）────────────────────────────────

def build_text_message(macro, date_str: str, is_saturday: bool) -> dict:
    if is_saturday:
        text = (
            f"🗓️ {date_str}（週六）週末好！\n\n"
            f"本週景氣判定：{macro.cycle_phase}\n"
            f"建議資金乘數：{macro.fund_multiplier}x\n\n"
            f"今日美股與台股休市，以下為本週回顧與下週展望。"
        )
    else:
        text = (
            f"☀️ {date_str} 早安！\n\n"
            f"今日景氣判定：{macro.cycle_phase}\n"
            f"建議資金乘數：{macro.fund_multiplier}x\n\n"
            + _advice(macro.cycle_phase)
        )

    # 薩姆規則觸發時加警示
    if macro.sahm_triggered:
        text += "\n\n🚨【薩姆規則觸發】衰退訊號確認，請參考下方卡片操作建議。"

    return {"type": "text", "text": text}


# ── LINE Push API ─────────────────────────────────────

def send_line_messages(messages: list, channel_token: str, user_id: str) -> bool:
    """
    推送訊息到 LINE（單次 request，計費 1 則）
    messages: list of LINE message objects（最多 5 個）
    """
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {channel_token}",
        "Content-Type": "application/json",
    }
    payload = {"to": user_id, "messages": messages}

    resp = requests.post(url, headers=headers, json=payload, timeout=15)

    if resp.status_code == 200:
        print(f"[LINE] ✓ 訊息推送成功（{len(messages)} 個物件）")
        return True
    else:
        print(f"[LINE] ✗ 推送失敗 {resp.status_code}: {resp.text}")
        return False
