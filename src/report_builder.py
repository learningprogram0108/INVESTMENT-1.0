"""
宏觀報告 GitHub Pages 生成器 + LINE 訊息建構器
每日生成 docs/report/YYYY-MM-DD.md
LINE 發送短摘要 + GitHub Pages 連結
"""

import os
from datetime import datetime, timezone, timedelta
from src.report_fetcher import MacroReport

TST = timezone(timedelta(hours=8))


# ─────────────────────────────────────────────
# Markdown 報告生成（GitHub Pages）
# ─────────────────────────────────────────────

def _fmt(val, suffix="", decimals=1, na="N/A") -> str:
    if val is None:
        return na
    return f"{val:.{decimals}f}{suffix}"

def _chg(now, prev, suffix="%") -> str:
    if now is None or prev is None:
        return ""
    diff = now - prev
    arrow = "↑" if diff > 0 else "↓"
    return f"（{arrow}{abs(diff):.1f}{suffix}）"

def _bar(pct: float, max_pct: float = 100, width: int = 20) -> str:
    filled = round(pct / max_pct * width)
    return "█" * filled + "░" * (width - filled)


def generate_markdown_report(report: MacroReport, repo: str) -> str:
    """生成完整 Markdown 報告"""
    now_tst = datetime.now(TST)
    date_str = now_tst.strftime("%Y/%m/%d")
    weekday = ["週一","週二","週三","週四","週五","週六","週日"][now_tst.weekday()]

    # Smart Beta 長條圖
    voo_bar  = _bar(report.voo_weight, 100, 15)
    tw_bar   = _bar(report.etf_0050_weight, 100, 15)
    bond_bar = _bar(report.etf_00679b_weight, 100, 15)

    md = f"""# 宏觀經濟每日早報

**{date_str}（{weekday}）** · 發布時間：07:30 TST · 資料來源：FRED / Atlanta Fed / NY Fed

---

## 今日核心事件

### {report.top_event_title}

{report.top_event_body}

**標籤：** {" · ".join([f"`{t}`" for t in report.top_event_tags])}

---

## 總體經濟數據儀表板

| 指標 | 最新值 | 前期值 | 變化 |
|------|--------|--------|------|
| CPI 年增率（美國）| {_fmt(report.cpi_latest)}% | {_fmt(report.cpi_prev)}% | {_chg(report.cpi_latest, report.cpi_prev)} |
| 核心 CPI | {_fmt(report.cpi_core)}% | — | — |
| PCE 年增率 | {_fmt(report.pce_latest)}% | — | — |
| 失業率 | {_fmt(report.unemployment)}% | {_fmt(report.unemployment_prev)}% | {_chg(report.unemployment, report.unemployment_prev)} |
| 非農就業月增 | {_fmt(report.nonfarm_payrolls, "K", 0)} | — | — |
| 聯邦基金利率 | {_fmt(report.fed_funds_rate)}% | — | — |

---

## 專業預測機構更新

| 機構 | 指標 | 最新預測 | 說明 |
|------|------|----------|------|
| Atlanta Fed | GDPNow（本季 GDP）| {_fmt(report.gdpnow)}% {_chg(report.gdpnow, report.gdpnow_prev)} | 即時更新，每週三 |
| Cleveland Fed | 未來 1 年 CPI 預期 | {_fmt(report.cleveland_cpi_forecast)}% | 基於市場與調查合成 |
| NY Fed | 12 個月衰退機率 | {_fmt(report.ny_fed_recession_prob)}% | 基於殖利率曲線模型 |
| 市場隱含 | 降息機率（代理值）| {_fmt(report.rate_cut_prob)}% | CPI/FFR 差值推算 |

---

## Smart Beta 動態資產配置建議

> **調整理由：** {report.allocation_reason}

```
VOO   （美股 S&P500）{voo_bar} {report.voo_weight:.0f}%
0050  （台灣 50）    {tw_bar} {report.etf_0050_weight:.0f}%
00679B（美債 20年）  {bond_bar} {report.etf_00679b_weight:.0f}%
```

### 因子評分說明

| Smart Beta 因子 | 訊號方向 | 影響 |
|----------------|---------|------|
| 價值（CAPE/ERP）| 估值過高 | 降低 VOO 權重 |
| 動能（EMA Z-Score）| 依當日計算 | 動態調整 |
| 通膨（CPI 趨勢）| CPI {_fmt(report.cpi_latest)}% | {"降息有利美債" if (report.cpi_latest or 3) < 3.0 else "通膨仍高，債券承壓"} |
| 衰退風險（NY Fed）| {_fmt(report.ny_fed_recession_prob)}% | {"防禦偏重" if (report.ny_fed_recession_prob or 20) > 30 else "成長偏重"} |
| 信用週期（HY 利差）| 依當日計算 | 動態調整 |

---

## 深度分析

### Fed 政策展望

聯邦基金利率目前為 **{_fmt(report.fed_funds_rate)}%**。根據今日數據與市場預期：

- **CPI 走向：** {_fmt(report.cpi_latest)}%，{"持續下行有助建立降息信心" if (report.cpi_latest or 3) < 3.0 else "仍高於 Fed 2% 目標，降息時程受限"}
- **就業市場：** 失業率 {_fmt(report.unemployment)}%，{"勞動市場韌性強，Fed 不急於降息" if (report.unemployment or 4) < 4.0 else "開始降溫，有利降息空間"}
- **經濟成長：** GDPNow {_fmt(report.gdpnow)}%，{"超預期強勁" if (report.gdpnow or 2) > 2.5 else "成長放緩" if (report.gdpnow or 2) < 1.5 else "溫和擴張"}

### 投資含義

基於上述分析，本日建議：

1. **VOO（{report.voo_weight:.0f}%）：** {"估值偏高，建議維持基礎倉位，等待回調再加碼" if report.voo_weight < 35 else "指標正常，維持定期扣款"}
2. **0050（{report.etf_0050_weight:.0f}%）：** 台股受惠於{"全球風險偏好改善" if report.etf_0050_weight >= 30 else "美股走弱的相對優勢"}
3. **00679B（{report.etf_00679b_weight:.0f}%）：** {"通膨下行 + 衰退機率上升，美債為當前最佳避險工具" if report.etf_00679b_weight > 40 else "維持基礎防禦倉位"}

---

*本報告由量化系統自動生成，所有建議僅供參考，不構成投資建議。*
*資料來源：FRED、Atlanta Fed、NY Fed（均為公開免費資料）*
*生成時間：{now_tst.strftime("%Y-%m-%d %H:%M")} TST*
"""
    return md


def save_markdown_report(report: MacroReport, repo: str = "") -> str:
    """
    將報告存為 docs/report/YYYY-MM-DD.md
    GitHub Pages 會自動渲染為網頁
    回傳檔案路徑
    """
    now_tst = datetime.now(TST)
    filename = now_tst.strftime("%Y-%m-%d") + ".md"

    # 建立目錄
    os.makedirs("docs/report", exist_ok=True)

    # 寫入 Markdown
    md_content = generate_markdown_report(report, repo)
    filepath = f"docs/report/{filename}"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 更新 docs/report/index.md（最新報告重導向）
    latest_content = f"""# 最新宏觀經濟報告

自動跳轉至今日報告...

[點此查看 {now_tst.strftime('%Y/%m/%d')} 報告](./{filename})

---

{md_content}
"""
    with open("docs/report/index.md", "w", encoding="utf-8") as f:
        f.write(latest_content)

    print(f"  [REPORT] 報告已存至 {filepath}")
    return filepath


# ─────────────────────────────────────────────
# LINE 訊息建構
# ─────────────────────────────────────────────

def build_report_line_messages(report: MacroReport, pages_url: str) -> list:
    """
    建構 LINE 訊息：短摘要文字 + Flex 卡片 + 連結
    """
    now_tst = datetime.now(TST)
    weekday = ["週一","週二","週三","週四","週五","週六","週日"][now_tst.weekday()]

    # ── 訊息 1：文字摘要（簡短）──
    cpi_str = f"{report.cpi_latest:.1f}%" if report.cpi_latest else "N/A"
    u_str   = f"{report.unemployment:.1f}%" if report.unemployment else "N/A"
    gdp_str = f"{report.gdpnow:.1f}%" if report.gdpnow else "N/A"
    rec_str = f"{report.ny_fed_recession_prob:.0f}%" if report.ny_fed_recession_prob else "N/A"

    text_msg = {
        "type": "text",
        "text": (
            f"📰 {report.date}（{weekday}）宏觀早報\n\n"
            f"【今日焦點】{report.top_event_title}\n\n"
            f"CPI {cpi_str}  失業率 {u_str}\n"
            f"GDPNow {gdp_str}  衰退機率 {rec_str}\n\n"
            f"完整分析與配置建議請點下方連結 👇"
        )
    }

    # ── 訊息 2：Flex 卡片 ──
    def _val(v, suffix="", dec=1):
        return f"{v:.{dec}f}{suffix}" if v is not None else "N/A"

    def _chg_label(now, prev):
        if now is None or prev is None:
            return ""
        diff = now - prev
        c = "#E24B4A" if diff > 0 else "#1D9E75"
        sym = "▲" if diff > 0 else "▼"
        return {"type": "text", "text": f"{sym}{abs(diff):.1f}", "size": "xxs", "color": c}

    def row(label, value, color="#333333"):
        return {
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "text", "text": label, "size": "xxs", "color": "#888888", "flex": 5},
                {"type": "text", "text": value,  "size": "xxs", "color": color,    "flex": 4,
                 "align": "end", "weight": "bold"},
            ],
            "paddingTop": "3px", "paddingBottom": "3px",
        }

    def sep():
        return {"type": "separator", "margin": "sm", "color": "#eeeeee"}

    def section(label):
        return {"type": "text", "text": label, "size": "xxs",
                "color": "#aaaaaa", "weight": "bold", "margin": "md"}

    # Smart Beta 視覺長條
    def beta_row(name, pct, color):
        bar_filled = max(1, round(pct / 100 * 10))
        bar = "▓" * bar_filled + "░" * (10 - bar_filled)
        return {
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "text", "text": name,  "size": "xxs", "color": "#444", "flex": 3},
                {"type": "text", "text": bar,   "size": "xxs", "color": color,  "flex": 5},
                {"type": "text", "text": f"{pct:.0f}%", "size": "xxs",
                 "color": color, "weight": "bold", "flex": 2, "align": "end"},
            ],
            "paddingTop": "2px", "paddingBottom": "2px",
        }

    rec_color = ("#E24B4A" if (report.ny_fed_recession_prob or 0) > 35
                 else "#EF9F27" if (report.ny_fed_recession_prob or 0) > 20
                 else "#1D9E75")
    cpi_color = ("#1D9E75" if (report.cpi_latest or 3) < 2.5
                 else "#EF9F27" if (report.cpi_latest or 3) < 3.5
                 else "#E24B4A")

    bubble = {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#1a3a5c", "paddingAll": "12px",
            "contents": [
                {"type": "text", "text": "宏觀經濟每日早報",
                 "color": "#ffffff", "size": "sm", "weight": "bold"},
                {"type": "text", "text": f"{report.date}  ·  07:30 TST  ·  FRED / Atlanta / NY Fed",
                 "color": "#cccccc", "size": "xxs", "margin": "xs"},
                {"type": "text", "text": f"📌 {report.top_event_title}",
                 "color": "#7fb8f0", "size": "xs", "margin": "sm", "wrap": True},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "none",
            "contents": [
                section("總經數據"),
                row("CPI 年增率（美）", _val(report.cpi_latest, "%"), cpi_color),
                row("核心 CPI",         _val(report.cpi_core,   "%"), "#555"),
                row("PCE 年增率",        _val(report.pce_latest, "%"), "#555"),
                row("失業率",            _val(report.unemployment, "%"), "#555"),
                row("非農就業月增",       _val(report.nonfarm_payrolls, "K", 0), "#555"),
                row("聯邦基金利率",       _val(report.fed_funds_rate, "%"), "#555"),
                sep(),
                section("專業預測機構"),
                row("GDPNow（本季）",    _val(report.gdpnow, "%"),
                    "#1D9E75" if (report.gdpnow or 2) > 2.0 else "#EF9F27"),
                row("Cleveland CPI 預測", _val(report.cleveland_cpi_forecast, "%"), cpi_color),
                row("NY Fed 衰退機率",    _val(report.ny_fed_recession_prob, "%"), rec_color),
                row("降息機率（代理）",    _val(report.rate_cut_prob, "%"), "#378ADD"),
                sep(),
                section("Smart Beta 配置建議"),
                beta_row("VOO",    report.voo_weight,         "#378ADD"),
                beta_row("0050",   report.etf_0050_weight,    "#1D9E75"),
                beta_row("00679B", report.etf_00679b_weight,  "#EF9F27"),
                {
                    "type": "box", "layout": "vertical",
                    "backgroundColor": "#f0faf5", "cornerRadius": "6px",
                    "paddingAll": "7px", "margin": "sm",
                    "contents": [
                        {"type": "text", "text": f"調整理由：{report.allocation_reason}",
                         "size": "xxs", "color": "#085041", "wrap": True},
                    ]
                },
                sep(),
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "查看完整報告與圖表",
                        "uri": pages_url
                    },
                    "style": "primary",
                    "color": "#1a3a5c",
                    "margin": "md",
                    "height": "sm",
                }
            ]
        }
    }

    flex_msg = {
        "type": "flex",
        "altText": f"宏觀早報 {report.date}",
        "contents": bubble
    }

    return [text_msg, flex_msg]
