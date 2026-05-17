"""
LINE Flex Message 建構器 v2
早盤：共用總經 + 0050 + 00679B（4 張卡）
夜盤：VOO（2 張卡）
"""

import requests
from src.quant_engine import MacroIndicators, ETFSignal

# ─────────────────────────────────────────────
# 顏色工具
# ─────────────────────────────────────────────

def _phase_color(phase: str) -> str:
    if "絕望" in phase or "衰退" in phase: return "#7a1a1a"
    if "希望" in phase:  return "#7a4800"
    if "樂觀" in phase:  return "#6b3200"
    return "#0d5c3a"

def _phase_accent(phase: str) -> str:
    if "絕望" in phase or "衰退" in phase: return "#E24B4A"
    if "希望" in phase:  return "#EF9F27"
    if "樂觀" in phase:  return "#D85A30"
    return "#1D9E75"

def _z_color(z: float) -> str:
    if z <= -2.0: return "#E24B4A"
    if z <= -1.0: return "#EF9F27"
    if z <= 1.5:  return "#1D9E75"
    if z <= 2.5:  return "#BA7517"
    return "#A32D2D"

def _spread_color(s: float) -> str:
    if s < 3.5:  return "#A32D2D"
    if s < 4.5:  return "#BA7517"
    if s < 6.5:  return "#1D9E75"
    return "#E24B4A"

def _real_rate_color(r: float) -> str:
    if r < 0:    return "#E24B4A"
    if r < 1.5:  return "#1D9E75"
    if r < 3.0:  return "#EF9F27"
    return "#E24B4A"

def _sentiment_color(s: float) -> str:
    if s >= 80: return "#A32D2D"
    if s >= 60: return "#BA7517"
    if s >= 40: return "#1D9E75"
    if s >= 20: return "#378ADD"
    return "#185FA5"

def _mult_color(m: float) -> str:
    if m >= 2.0: return "#E24B4A"
    if m >= 1.5: return "#EF9F27"
    if m == 1.0: return "#1D9E75"
    return "#BA7517"

def _pmi_label(v: float) -> str:
    return "擴張" if v >= 50 else "收縮"

def _pmi_color(v: float) -> str:
    return "#1D9E75" if v >= 50 else "#E24B4A"

def _f2_label(v: float) -> str:
    if v > 0.3:  return "動能改善"
    if v > 0:    return "緩步改善"
    if v > -0.3: return "動能惡化"
    return "急速惡化"

def _f2_color(v: float) -> str:
    return "#1D9E75" if v > 0 else "#E24B4A"

def _yield_curve_label(v: float) -> str:
    if v > 0.5:  return "正常"
    if v > 0:    return "平坦·觀察"
    return "倒掛·警戒"

def _yield_curve_color(v: float) -> str:
    if v > 0.5:  return "#1D9E75"
    if v > 0:    return "#EF9F27"
    return "#E24B4A"

def _erp_color(e: float) -> str:
    if e > 3:   return "#1D9E75"
    if e > 1:   return "#EF9F27"
    return "#E24B4A"

def _pred_color(r: float) -> str:
    if r > 5:   return "#1D9E75"
    if r > 2:   return "#EF9F27"
    return "#E24B4A"

# ─────────────────────────────────────────────
# Flex 元件
# ─────────────────────────────────────────────

def _row(label: str, value: str, color: str = "#333333") -> dict:
    return {
        "type": "box", "layout": "horizontal",
        "contents": [
            {"type": "text", "text": label, "size": "xxs",
             "color": "#888888", "flex": 5},
            {"type": "text", "text": value, "size": "xxs",
             "color": color, "flex": 4, "align": "end", "weight": "bold"},
        ],
        "paddingTop": "3px", "paddingBottom": "3px",
    }

def _sep() -> dict:
    return {"type": "separator", "margin": "sm", "color": "#eeeeee"}

def _section(label: str) -> dict:
    return {"type": "text", "text": label, "size": "xxs",
            "color": "#aaaaaa", "weight": "bold", "margin": "md"}

def _advice_box(text: str, bg: str, color: str) -> dict:
    return {
        "type": "box", "layout": "vertical",
        "backgroundColor": bg, "cornerRadius": "6px",
        "paddingAll": "8px", "margin": "md",
        "contents": [
            {"type": "text", "text": "操作建議",
             "size": "xxs", "weight": "bold", "color": color},
            {"type": "text", "text": text, "size": "xxs",
             "color": color, "wrap": True, "margin": "sm"},
        ]
    }

ADVICE = {
    "獵人加碼": "恐慌超跌，啟動獵人模式！逢低大力加碼，複利時機。",
    "積極布局": "復甦訊號出現，積極布局，伺機加大扣款。",
    "鑑賞家巡航": "市場健康，照常定期投入，讓利潤奔跑。",
    "刺客防禦": "過熱或衰退警戒，刺客防禦，保留子彈等待。",
}
ADVICE_BG = {
    "獵人加碼": "#fcebeb", "積極布局": "#faeeda",
    "鑑賞家巡航": "#e1f5ee", "刺客防禦": "#faeeda",
}
ADVICE_FG = {
    "獵人加碼": "#7a1a1a", "積極布局": "#7a4800",
    "鑑賞家巡航": "#085041", "刺客防禦": "#7a4800",
}

def _hero(title: str, sub: str, phase: str, mult: float, mode: str) -> dict:
    bg = _phase_color(phase)
    accent = _phase_accent(phase)
    return {
        "type": "box", "layout": "vertical",
        "backgroundColor": bg, "paddingAll": "14px",
        "contents": [
            {"type": "text", "text": title,
             "color": "#ffffff", "size": "sm", "weight": "bold"},
            {"type": "text", "text": sub,
             "color": "#cccccc", "size": "xxs", "margin": "xs"},
            {
                "type": "box", "layout": "horizontal", "margin": "md",
                "contents": [
                    {
                        "type": "box", "layout": "vertical",
                        "backgroundColor": "#1a5c40",
                        "cornerRadius": "6px", "paddingAll": "6px", "flex": 1,
                        "contents": [
                            {"type": "text", "text": phase,
                             "color": "#ffffff", "size": "xxs", "weight": "bold"},
                            {"type": "text",
                             "text": f"乘數 {mult}x  ·  {mode}",
                             "color": accent, "size": "xxs"},
                        ]
                    }
                ]
            }
        ]
    }

# ─────────────────────────────────────────────
# 卡片：共用總經底層
# ─────────────────────────────────────────────

def build_macro_card(macro: MacroIndicators, date_str: str) -> dict:
    pmi = macro.ism_pmi
    pmi_str = " → ".join([f"{v:.1f}" for v in pmi])
    u3m = macro.unemployment_3m
    u_str = " → ".join([f"{v:.1f}%" for v in u3m])

    body_contents = [
        _section("衰退偵測"),
        _row("薩姆規則",
             f"{'⚠️ 觸發！' if macro.sahm_triggered else f'{macro.sahm_indicator:.2f}%'}",
             "#E24B4A" if macro.sahm_triggered else "#1D9E75"),
        _row("Michez m 值",
             f"{'⚠️ 觸發！' if macro.michez_triggered else f'{macro.michez_m:.2f}%'}",
             "#E24B4A" if macro.michez_triggered else "#1D9E75"),
        _row("衰退機率", f"{macro.recession_prob:.0f}%",
             "#E24B4A" if macro.recession_prob > 50 else
             "#EF9F27" if macro.recession_prob > 20 else "#1D9E75"),
        _sep(),
        _section("利率環境"),
        _row("US10Y / US02Y",
             f"{macro.us10y:.2f}% / {macro.us02y:.2f}%", "#333333"),
        _row("殖利率曲線 10y−2y",
             f"{macro.yield_curve:+.2f}%  {_yield_curve_label(macro.yield_curve)}",
             _yield_curve_color(macro.yield_curve)),
        _row("實質利率 r=i−π",
             f"{macro.real_rate:+.2f}%",
             _real_rate_color(macro.real_rate)),
        _row("CPI π（美國）",
             f"{macro.cpi_yoy:.1f}%",
             "#EF9F27" if macro.cpi_yoy > 3.5 else "#1D9E75"),
        _sep(),
        _section("信用市場"),
        _row("HY 信用利差",
             f"{macro.hy_spread:.1f}%  {macro.credit_signal}",
             _spread_color(macro.hy_spread)),
        _sep(),
        _section("景氣領先指標"),
        _row("ISM PMI 近三期", pmi_str,
             _pmi_color(pmi[-1]) if pmi else "#333333"),
        _row("PMI f''(t)",
             f"{macro.pmi_second_deriv:+.2f}  {_f2_label(macro.pmi_second_deriv)}",
             _f2_color(macro.pmi_second_deriv)),
        _row("失業率 近三月", u_str, "#333333"),
    ]

    bubble = {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#1a3a5c", "paddingAll": "12px",
            "contents": [
                {"type": "text", "text": "總體經濟底層指標",
                 "color": "#ffffff", "size": "sm", "weight": "bold"},
                {"type": "text",
                 "text": f"{date_str}  ·  共用  ·  所有 ETF 適用",
                 "color": "#cccccc", "size": "xxs", "margin": "xs"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "none",
            "contents": body_contents,
        }
    }
    return {"type": "flex", "altText": f"總經底層 {date_str}", "contents": bubble}

# ─────────────────────────────────────────────
# 卡片：ETF 景氣面板（0050 / VOO 通用）
# ─────────────────────────────────────────────

def build_etf_card(sig: ETFSignal, date_str: str, session: str) -> dict:
    session_label = "09:30 TST" if session == "morning" else "22:00 TST"

    cape_str = f"{sig.cape:.1f}x" if sig.cape else "N/A"
    erp_str  = (f"{sig.erp:+.2f}%" if sig.erp is not None else "N/A")
    pred_str = (f"{sig.predicted_10y_return:+.1f}%/yr"
                if sig.predicted_10y_return is not None else "N/A")
    erp_color  = _erp_color(sig.erp or 0)
    pred_color = _pred_color(sig.predicted_10y_return or 5)

    body_contents = [
        _section("情緒 & 動能"),
        _row("情緒溫度計",
             f"{sig.sentiment_score:.0f}/100  {sig.sentiment_label}",
             _sentiment_color(sig.sentiment_score)),
        _row("EMA Z-Score",
             f"{sig.z_score:+.2f}",
             _z_color(sig.z_score)),
        _sep(),
        _section("MACD (12/26/9)"),
        _row("MACD 線",
             f"{sig.macd_line:+.4f}" if sig.macd_line is not None else "N/A",
             "#1D9E75" if (sig.macd_line or 0) > (sig.macd_signal or 0) else "#E24B4A"),
        _row("訊號線",
             f"{sig.macd_signal:+.4f}" if sig.macd_signal is not None else "N/A",
             "#888888"),
        _row("柱狀圖",
             (f"{sig.macd_hist:+.4f}  {'▲擴大' if (sig.macd_hist or 0) > 0 else '▼收縮'}"
              if sig.macd_hist is not None else "N/A"),
             "#1D9E75" if (sig.macd_hist or 0) > 0 else "#E24B4A"),
        _row("VIX 恐慌指數",
             f"{sig.vix:.1f}",
             "#E24B4A" if sig.vix >= 35 else
             "#EF9F27" if sig.vix >= 25 else "#1D9E75"),
        _row("VIX 布林突破",
             "突破上軌 ⚠️" if sig.vix_bollinger_break else "未突破",
             "#E24B4A" if sig.vix_bollinger_break else "#1D9E75"),
        _sep(),
        _section("估值"),
        _row("CAPE / PE", cape_str,
             "#E24B4A" if (sig.cape or 0) > 35 else
             "#EF9F27" if (sig.cape or 0) > 25 else "#1D9E75"),
        _row("ERP 股權風險溢酬", erp_str, erp_color),
        _row("預估 10y 年化報酬", pred_str, pred_color),
        _sep(),
        _section("凱利準則"),
        _row("f* 最佳資金比例",
             f"{sig.kelly_f:.3f}  ({sig.kelly_f*100:.1f}%)",
             "#378ADD"),
        _row("乘數上限 (f*×3)",
             f"{sig.kelly_f*3:.1f}x", "#378ADD"),
        _sep(),
        _section("資金乘數"),
        _row("景氣階段", sig.cycle_phase.split("（")[0], "#333333"),
        {
            "type": "box", "layout": "horizontal",
            "margin": "sm",
            "contents": [
                {
                    "type": "box", "layout": "vertical",
                    "backgroundColor": "#f0f0f0",
                    "cornerRadius": "6px", "paddingAll": "8px", "flex": 1,
                    "contents": [
                        {"type": "text", "text": "建議乘數",
                         "size": "xxs", "color": "#888888"},
                        {"type": "text",
                         "text": f"{sig.fund_multiplier}x",
                         "size": "xl", "weight": "bold",
                         "color": _mult_color(sig.fund_multiplier)},
                        {"type": "text", "text": sig.multiplier_mode,
                         "size": "xxs",
                         "color": _mult_color(sig.fund_multiplier)},
                    ]
                }
            ]
        },
        _advice_box(
            ADVICE.get(sig.multiplier_mode, "維持常態配置。"),
            ADVICE_BG.get(sig.multiplier_mode, "#f8f8f8"),
            ADVICE_FG.get(sig.multiplier_mode, "#333333"),
        ),
    ]

    bubble = {
        "type": "bubble", "size": "giga",
        "header": _hero(
            f"{sig.name}  ({sig.ticker.replace('.TW','')})",
            f"{date_str}  ·  {session_label}",
            sig.cycle_phase, sig.fund_multiplier, sig.multiplier_mode
        ),
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "none",
            "contents": body_contents,
        }
    }
    return {"type": "flex", "altText": f"{sig.name} 景氣面板 {date_str}",
            "contents": bubble}

# ─────────────────────────────────────────────
# 卡片：00679B（債券專屬）
# ─────────────────────────────────────────────

def build_bond_card(sig: ETFSignal, macro: MacroIndicators, date_str: str) -> dict:
    body_contents = [
        _section("債券環境"),
        _row("EMA Z-Score",
             f"{sig.z_score:+.2f}",
             _z_color(sig.z_score)),
        _row("實質利率 r=i−π",
             f"{macro.real_rate:+.2f}%",
             _real_rate_color(macro.real_rate)),
        _row("US10Y",
             f"{macro.us10y:.2f}%", "#333333"),
        _row("殖利率曲線 10y−2y",
             f"{macro.yield_curve:+.2f}%  {_yield_curve_label(macro.yield_curve)}",
             _yield_curve_color(macro.yield_curve)),
        _row("HY 信用利差",
             f"{macro.hy_spread:.1f}%  {macro.credit_signal}",
             _spread_color(macro.hy_spread)),
        _sep(),
        _section("凱利準則"),
        _row("f* 最佳資金比例",
             f"{sig.kelly_f:.3f}  ({sig.kelly_f*100:.1f}%)", "#378ADD"),
        _row("乘數上限 (f*×3)",
             f"{sig.kelly_f*3:.1f}x", "#378ADD"),
        _sep(),
        _section("資金乘數"),
        {
            "type": "box", "layout": "horizontal", "margin": "sm",
            "contents": [{
                "type": "box", "layout": "vertical",
                "backgroundColor": "#f0f0f0",
                "cornerRadius": "6px", "paddingAll": "8px", "flex": 1,
                "contents": [
                    {"type": "text", "text": "建議乘數",
                     "size": "xxs", "color": "#888888"},
                    {"type": "text", "text": f"{sig.fund_multiplier}x",
                     "size": "xl", "weight": "bold",
                     "color": _mult_color(sig.fund_multiplier)},
                    {"type": "text", "text": sig.multiplier_mode,
                     "size": "xxs",
                     "color": _mult_color(sig.fund_multiplier)},
                ]
            }]
        },
        _advice_box(
            ADVICE.get(sig.multiplier_mode, "維持常態配置。"),
            ADVICE_BG.get(sig.multiplier_mode, "#f8f8f8"),
            ADVICE_FG.get(sig.multiplier_mode, "#333333"),
        ),
    ]

    bubble = {
        "type": "bubble", "size": "giga",
        "header": _hero(
            "00679B  元大美債 20年",
            f"{date_str}  ·  09:30 TST",
            sig.cycle_phase, sig.fund_multiplier, sig.multiplier_mode
        ),
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "none",
            "contents": body_contents,
        }
    }
    return {"type": "flex", "altText": f"00679B 景氣面板 {date_str}",
            "contents": bubble}

# ─────────────────────────────────────────────
# 文字訊息
# ─────────────────────────────────────────────

def build_text_message(session: str, macro: MacroIndicators,
                       etf_signals: list, date_str: str) -> dict:
    if session == "morning":
        phases = [f"{s.ticker.replace('.TW','')}={s.multiplier_mode}" for s in etf_signals]
        phase_str = "  ".join(phases) if phases else "資料擷取中"
        # 薩姆警報併入文字訊息（不單獨佔一則，避免擠掉卡片）
        if macro.sahm_triggered:
            warn = (
                f"\n🚨【衰退警報】薩姆={macro.sahm_indicator:.2f}%  "
                f"衰退機率={macro.recession_prob:.0f}%"
            )
        else:
            warn = ""
        text = (
            f"☀️ {date_str} 早安{warn}\n\n"
            f"衰退機率 {macro.recession_prob:.0f}%  |  HY利差 {macro.hy_spread:.1f}%\n"
            f"{phase_str}\n\n"
            f"詳細指標請查看下方卡片。"
        )
    else:
        if etf_signals:
            lines = []
            for s in etf_signals:
                ticker = s.ticker.replace(".TW", "")
                lines.append(
                    f"{ticker}：{s.multiplier_mode}  乘數 {s.fund_multiplier}x  "
                    f"Z={s.z_score:+.2f}"
                )
            etf_str = "\n".join(lines)
            text = (
                f"🌙 {date_str} 美股收盤\n\n"
                f"{etf_str}\n\n"
                f"詳細分析請查看下方卡片。"
            )
        else:
            text = f"🌙 {date_str} 資料暫時無法取得，請稍後手動確認。"
    return {"type": "text", "text": text}

# ─────────────────────────────────────────────
# LINE Push API
# ─────────────────────────────────────────────

def send_line_messages(messages: list, token: str, user_id: str) -> bool:
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"to": user_id, "messages": messages[:5]},
        timeout=15,
    )
    if resp.status_code == 200:
        print(f"[LINE] ✓ 推送成功（{len(messages)} 則）")
        return True
    print(f"[LINE] ✗ 失敗 {resp.status_code}: {resp.text}")
    return False
