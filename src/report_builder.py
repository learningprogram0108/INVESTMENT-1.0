"""
宏觀報告發布器 v2
使用 Telegraph API 發布報告（免費、不需帳號、手機排版佳）
LINE 發送短摘要 + Telegraph 連結
"""

import requests
import json
from datetime import datetime, timezone, timedelta
from src.report_fetcher import MacroReport

TST = timezone(timedelta(hours=8))

TELEGRAPH_API = "https://api.telegra.ph"


# ─────────────────────────────────────────────
# Telegraph 帳號管理
# ─────────────────────────────────────────────

def get_or_create_telegraph_token(token_file: str = "telegraph_token.txt") -> str:
        import os
        if os.path.exists(token_file):
                    with open(token_file, "r") as f:
                                    token = f.read().strip()
                                if token:
                        return token

                                        r = requests.post(
                                            f"{TELEGRAPH_API}/createAccount",
                                            json={
                                                "short_name": "MacroReport",
                                                "author_name": "宏觀經濟早報",
                                                "author_url": "https://github.com",
                                            },
                                            timeout=15
                                        )
                                        data = r.json()
                                        if data.get("ok"):
                                                    token = data["result"]["access_token"]
                                                    with open(token_file, "w") as f:
                                                                    f.write(token)
                                                                print(f"  [Telegraph] 新帳號建立成功")
                                                    return token
                                else:
        raise Exception(f"Telegraph 帳號建立失敗: {data}")


def _text(content: str) -> dict:
        return {"tag": "p", "children": [content]}

def _h3(content: str) -> dict:
        return {"tag": "h3", "children": [content]}

def _h4(content: str) -> dict:
        return {"tag": "h4", "children": [content]}

def _br() -> dict:
        return {"tag": "br"}

def _pre(content: str) -> dict:
        return {"tag": "pre", "children": [content]}

def _fmt(val, suffix="", decimals=1, na="N/A") -> str:
        if val is None:
                    return na
                return f"{val:.{decimals}f}{suffix}"

def _chg(now, prev) -> str:
        if now is None or prev is None:
                    return ""
                diff = now - prev
    arrow = "▲" if diff > 0 else "▼"
    return f" {arrow}{abs(diff):.1f}"

def _bar(pct: float, width: int = 12) -> str:
        filled = max(1, round(pct / 100 * width))
    return "█" * filled + "░" * (width - filled)


def build_telegraph_nodes(report: MacroReport) -> list:
        now_tst = datetime.now(TST)
    weekday = ["週一","週二","週三","週四","週五","週六","週日"][now_tst.weekday()]
    nodes = []

    nodes.append(_text(f"📅 {report.date}（{weekday}）· 07:30 TST · FRED / Atlanta Fed / NY Fed"))
    nodes.append(_br())
    nodes.append(_h3(f"📌 {report.top_event_title}"))
    nodes.append(_text(report.top_event_body))
    nodes.append(_text("標籤：" + " · ".join(report.top_event_tags)))
    nodes.append(_br())

    nodes.append(_h3("📊 總體經濟數據"))
    cpi_chg = _chg(report.cpi_latest, report.cpi_prev)
    u_chg   = _chg(report.unemployment, report.unemployment_prev)
    data_lines = [
                f"CPI 年增率（美）：{_fmt(report.cpi_latest)}%{cpi_chg}",
            f"核心 CPI：{_fmt(report.cpi_core)}%",
                f"PCE 年增率：{_fmt(report.pce_latest)}%",
                f"失業率：{_fmt(report.unemployment)}%{u_chg}",
                f"非農就業月增：{_fmt(report.nonfarm_payrolls, 'K', 0)}",
                f"聯邦基金利率：{_fmt(report.fed_funds_rate)}%",
    ]
    nodes.append(_pre("\n".join(data_lines)))
    nodes.append(_br())

    nodes.append(_h3("🏦 專業預測機構"))
    gdp_chg = _chg(report.gdpnow, report.gdpnow_prev)
        forecast_lines = [
            f"Atlanta Fed GDPNow：{_fmt(report.gdpnow)}%{gdp_chg}",
                    f"Cleveland Fed CPI 預測：{_fmt(report.cleveland_cpi_forecast)}%",
                    f"NY Fed 衰退機率：{_fmt(report.ny_fed_recession_prob)}%",
                    f"降息機率（代理值）：{_fmt(report.rate_cut_prob)}%",
        ]
    nodes.append(_pre("\n".join(forecast_lines)))
    nodes.append(_br())

    nodes.append(_h3("⚖️ Smart Beta 動態配置建議"))
    nodes.append(_text(f"調整理由：{report.allocation_reason}"))
    beta_lines = [
                f"VOO   （美股S&P500） {_bar(report.voo_weight)} {report.voo_weight:.0f}%",
                f"0050  （台灣50）     {_bar(report.etf_0050_weight)} {report.etf_0050_weight:.0f}%",
                f"00679B（美債20年）   {_bar(report.etf_00679b_weight)} {report.etf_00679b_weight:.0f}%",
    ]
    nodes.append(_pre("\n".join(beta_lines)))
    nodes.append(_br())

    nodes.append(_h3("🔍 深度分析"))
    nodes.append(_h4("Fed 政策展望"))
    ffr = report.fed_funds_rate
    cpi = report.cpi_latest
    u   = report.unemployment
    gdp = report.gdpnow
    cpi_comment = ("持續下行，有助建立降息信心" if (cpi or 3) < 3.0 else "仍高於 Fed 2% 目標，降息時程受限")
    u_comment   = ("勞動市場韌性強，Fed 不急於降息" if (u or 4) < 4.0 else "開始降溫，有利降息空間")
    gdp_comment = ("超預期強勁" if (gdp or 2) > 2.5 else "成長放緩" if (gdp or 2) < 1.5 else "溫和擴張")
    analysis_lines = [
                f"聯邦基金利率：{_fmt(ffr)}%",
                f"CPI 走向：{_fmt(cpi)}%，{cpi_comment}",
                f"就業市場：失業率 {_fmt(u)}%，{u_comment}",
                f"經濟成長：GDPNow {_fmt(gdp)}%，{gdp_comment}",
]
    nodes.append(_pre("\n".join(analysis_lines)))
    nodes.append(_br())

    nodes.append(_h4("投資含義"))
    nodes.append(_text(f"VOO（{report.voo_weight:.0f}%）：" + ("估值偏高，維持基礎倉位，等待回調" if report.voo_weight < 35 else "指標正常，維持定期扣款")))
    nodes.append(_text(f"0050（{report.etf_0050_weight:.0f}%）：" + ("受惠全球風險偏好改善" if report.etf_0050_weight >= 30 else "相對美股具防禦優勢")))
    nodes.append(_text(f"00679B（{report.etf_00679b_weight:.0f}%）：" + ("通膨下行 + 衰退機率上升，美債為當前最佳避險工具" if report.etf_00679b_weight > 40 else "維持基礎防禦倉位")))
    nodes.append(_br())
    nodes.append(_text("⚠️ 本報告由量化系統自動生成，所有建議僅供參考，不構成投資建議。"))
    nodes.append(_text(f"生成時間：{now_tst.strftime('%Y-%m-%d %H:%M')} TST"))

    return nodes


def publish_to_telegraph(report: MacroReport, token: str) -> str:
        now_tst = datetime.now(TST)
        title = f"宏觀早報 {report.date}"
        nodes = build_telegraph_nodes(report)
        r = requests.post(
            f"{TELEGRAPH_API}/createPage",
            json={
                "access_token": token,
                "title": title,
                "author_name": "宏觀經濟早報",
                "content": nodes,
                "return_content": False,
            },
            timeout=20
        )
        data = r.json()
        if data.get("ok"):
                    url = data["result"]["url"]
                    print(f"  [Telegraph] 報告已發布：{url}")
                    return url
else:
        print(f"  [Telegraph] 發布失敗：{data}")
            return ""


def build_report_line_messages(report: MacroReport, report_url: str) -> list:
        now_tst = datetime.now(TST)
    weekday = ["週一","週二","週三","週四","週五","週六","週日"][now_tst.weekday()]

    def _v(val, suffix="", dec=1):
                return f"{val:.{dec}f}{suffix}" if val is not None else "N/A"

    rec_str = f"{report.ny_fed_recession_prob:.0f}%" if report.ny_fed_recession_prob else "N/A"
    gdp_str = f"{report.gdpnow:.1f}%" if report.gdpnow else "N/A"
    cpi_str = f"{report.cpi_latest:.1f}%" if report.cpi_latest else "N/A"
    u_str   = f"{report.unemployment:.1f}%" if report.unemployment else "N/A"

    text_msg = {
                "type": "text",
                "text": (
            f"📰 {report.date}（{weekday}）宏觀早報\n\n"
                                f"【今日焦點】{report.top_event_title}\n\n"
                                f"CPI {cpi_str}  失業率 {u_str}\n"
                                f"GDPNow {gdp_str}  衰退機率 {rec_str}\n\n"
                                f"完整報告請點下方卡片連結 👇"
                )
    }

    def row(label, value, color="#333333"):
                return {
                                "type": "box", "layout": "horizontal",
                                "contents": [
                                                    {"type": "text", "text": label, "size": "xxs", "color": "#888888", "flex": 5},
                                                    {"type": "text", "text": value, "size": "xxs", "color": color, "flex": 4, "align": "end", "weight": "bold"},
                                ],
                                "paddingTop": "3px", "paddingBottom": "3px",
                }

    def sep():
                return {"type": "separator", "margin": "sm", "color": "#eeeeee"}

    def section(label):
                return {
                                "type": "box", "layout": "vertical", "margin": "md",
                                "contents": [
                                                    {"type": "text", "text": label, "size": "xxs", "color": "#aaaaaa", "weight": "bold"}
                                ]
                }

    def beta_row(name, pct, color):
                bar = "▓" * max(1, round(pct/100*10)) + "░" * (10 - max(1, round(pct/100*10)))
                return {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": name, "size": "xxs", "color": "#444", "flex": 3},
                        {"type": "text", "text": bar, "size": "xxs", "color": color, "flex": 5},
                        {"type": "text", "text": f"{pct:.0f}%", "size": "xxs", "color": color, "weight": "bold", "flex": 2, "align": "end"},
                    ],
                    "paddingTop": "2px", "paddingBottom": "2px",
                }

    cpi_c = ("#1D9E75" if (report.cpi_latest or 3) < 2.5 else "#EF9F27" if (report.cpi_latest or 3) < 3.5 else "#E24B4A")
    rec_c = ("#E24B4A" if (report.ny_fed_recession_prob or 0) > 35 else "#EF9F27" if (report.ny_fed_recession_prob or 0) > 20 else "#1D9E75")
    gdp_c = "#1D9E75" if (report.gdpnow or 2) > 2.0 else "#EF9F27"

    if report_url:
                link_button = [{"type": "button", "action": {"type": "uri", "label": "查看完整報告", "uri": report_url}, "style": "primary", "color": "#1a3a5c", "margin": "md", "height": "sm"}]
else:
            link_button = [{"type": "text", "text": "⚠️ 報告連結暫時無法生成", "size": "xxs", "color": "#888888", "margin": "md", "align": "center"}]

    bubble = {
                "type": "bubble", "size": "giga",
                "header": {
                                "type": "box", "layout": "vertical",
                                "backgroundColor": "#1a3a5c", "paddingAll": "12px",
                                "contents": [
                                                    {"type": "text", "text": "宏觀經濟每日早報", "color": "#ffffff", "size": "sm", "weight": "bold"},
                                                    {"type": "text", "text": f"{report.date}  ·  07:30 TST", "color": "#cccccc", "size": "xxs", "margin": "xs"},
                                                    {"type": "text", "text": f"📌 {report.top_event_title}", "color": "#7fb8f0", "size": "xs", "margin": "sm", "wrap": True},
                                ]
                },
                "body": {
                                "type": "box", "layout": "vertical",
                                "paddingAll": "12px", "spacing": "none",
                                "contents": [
                                                    section("總經數據"),
                                                    row("CPI 年增率（美）", _v(report.cpi_latest, "%"), cpi_c),
                                                    row("核心 CPI", _v(report.cpi_core, "%"), "#555"),
                                                    row("失業率", _v(report.unemployment, "%"), "#555"),
                                                    row("非農就業月增", _v(report.nonfarm_payrolls, "K", 0), "#555"),
                                                    row("聯邦基金利率", _v(report.fed_funds_rate, "%"), "#555"),
                                                    sep(),
                                                    section("專業預測機構"),
                                                    row("GDPNow（本季）", _v(report.gdpnow, "%"), gdp_c),
                                                    row("Cleveland CPI 預測", _v(report.cleveland_cpi_forecast, "%"), cpi_c),
                                                    row("NY Fed 衰退機率", _v(report.ny_fed_recession_prob, "%"), rec_c),
                                                    row("降息機率（代理）", _v(report.rate_cut_prob, "%"), "#378ADD"),
                                                    sep(),
                                                    section("Smart Beta 配置"),
                                                    beta_row("VOO", report.voo_weight, "#378ADD"),
                                                    beta_row("0050", report.etf_0050_weight, "#1D9E75"),
                                                    beta_row("00679B", report.etf_00679b_weight, "#EF9F27"),
                                                    {
                                                                            "type": "box", "layout": "vertical",
                                                                            "backgroundColor": "#f0faf5", "cornerRadius": "6px",
                                                                            "paddingAll": "7px", "margin": "sm",
                                                                            "contents": [{"type": "text", "text": report.allocation_reason, "size": "xxs", "color": "#085041", "wrap": True}]
                                                    },
                                                    *link_button,
                                ]
                }
    }

    return [text_msg, {"type": "flex", "altText": f"宏觀早報 {report.date}", "contents": bubble}]
