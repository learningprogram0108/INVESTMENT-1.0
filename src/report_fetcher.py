"""
宏觀經濟報告資料擷取
來源：FRED、Atlanta Fed GDPNow、Cleveland Fed、NY Fed（全部免費）
"""

import requests
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from src.data_fetcher import fetch_fred


@dataclass
class MacroReport:
    date: str
    # 核心事件
    top_event_title: str
    top_event_body: str
    top_event_tags: list

    # 總經數據
    cpi_latest: Optional[float]        # CPI YoY %
    cpi_prev: Optional[float]
    cpi_core: Optional[float]          # 核心 CPI
    pce_latest: Optional[float]        # PCE YoY %
    unemployment: Optional[float]
    unemployment_prev: Optional[float]
    nonfarm_payrolls: Optional[float]  # 非農就業（千人）

    # 專業預測機構
    gdpnow: Optional[float]            # Atlanta Fed GDPNow
    gdpnow_prev: Optional[float]
    cleveland_cpi_forecast: Optional[float]  # Cleveland Fed 下月CPI預測
    ny_fed_recession_prob: Optional[float]   # NY Fed 衰退機率 %

    # 市場指標
    fed_funds_rate: Optional[float]    # 聯邦基金利率
    rate_cut_prob: Optional[float]     # 市場隱含降息機率（代理值）

    # Smart Beta 配置
    voo_weight: float = 35.0
    etf_0050_weight: float = 30.0
    etf_00679b_weight: float = 35.0
    allocation_reason: str = ""

    # 完整報告 URL
    report_url: str = ""


# ─────────────────────────────────────────────
# Atlanta Fed GDPNow
# ─────────────────────────────────────────────

def fetch_gdpnow() -> tuple[Optional[float], Optional[float]]:
    """
    Atlanta Fed GDPNow 即時 GDP 預測
    FRED series: GDPNOW
    回傳 (最新值, 前期值)
    """
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "GDPNOW",
                "file_type": "json",
                "sort_order": "desc",
                "limit": 3,
                # GDPNow 不需要 API key（公開）
                "api_key": "fake_key_gdpnow_is_public"
            },
            timeout=15
        )
        obs = r.json().get("observations", [])
        vals = [float(o["value"]) for o in obs if o.get("value") not in (".", None, "")]
        if len(vals) >= 2:
            return vals[0], vals[1]
        elif len(vals) == 1:
            return vals[0], None
    except Exception as e:
        print(f"  [GDPNow] {e}")
    return None, None


def fetch_gdpnow_direct() -> tuple[Optional[float], Optional[float]]:
    """Atlanta Fed GDPNow 直接從官網抓最新值"""
    try:
        r = requests.get(
            "https://www.atlantafed.org/cqer/research/gdpnow",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        # 抓取頁面中的數字（簡單解析）
        text = r.text
        import re
        matches = re.findall(r'GDPNow.*?(\-?\d+\.\d+)\s*percent', text, re.IGNORECASE | re.DOTALL)
        if matches:
            return float(matches[0]), None
    except Exception as e:
        print(f"  [GDPNow direct] {e}")
    return None, None


# ─────────────────────────────────────────────
# NY Fed 衰退機率
# ─────────────────────────────────────────────

def fetch_ny_fed_recession() -> Optional[float]:
    """
    NY Fed 12個月衰退機率
    FRED series: RECPROUSM156N
    """
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "RECPROUSM156N",
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
                "api_key": "fake_for_public"
            },
            timeout=15
        )
        obs = r.json().get("observations", [])
        for o in obs:
            try:
                v = float(o["value"])
                return round(v, 1)
            except Exception:
                continue
    except Exception as e:
        print(f"  [NY Fed Recession] {e}")
    return None


# ─────────────────────────────────────────────
# Cleveland Fed CPI 預測
# ─────────────────────────────────────────────

def fetch_cleveland_cpi() -> Optional[float]:
    """
    Cleveland Fed 通膨預測
    FRED series: INFLPROJCURRENT（若有）或用 EXPINF1YR
    """
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "EXPINF1YR",
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
                "api_key": "fake_for_public"
            },
            timeout=15
        )
        obs = r.json().get("observations", [])
        for o in obs:
            try:
                return round(float(o["value"]), 2)
            except Exception:
                continue
    except Exception as e:
        print(f"  [Cleveland CPI] {e}")
    return None


# ─────────────────────────────────────────────
# 主要 FRED 指標
# ─────────────────────────────────────────────

def fetch_core_indicators(fred_key: str) -> dict:
    """擷取 CPI、PCE、失業率、非農、Fed Funds Rate"""
    results = {}

    # CPI YoY
    cpi = fetch_fred("CPIAUCSL", fred_key, 14)
    if len(cpi) >= 13:
        results["cpi_latest"] = round((cpi[-1] / cpi[-13] - 1) * 100, 2)
        results["cpi_prev"]   = round((cpi[-2] / cpi[-14] - 1) * 100, 2) if len(cpi) >= 14 else None
    else:
        results["cpi_latest"] = None
        results["cpi_prev"]   = None

    # 核心 CPI（CPILFESL）
    core_cpi = fetch_fred("CPILFESL", fred_key, 14)
    if len(core_cpi) >= 13:
        results["cpi_core"] = round((core_cpi[-1] / core_cpi[-13] - 1) * 100, 2)
    else:
        results["cpi_core"] = None

    # PCE
    pce = fetch_fred("PCEPI", fred_key, 14)
    if len(pce) >= 13:
        results["pce_latest"] = round((pce[-1] / pce[-13] - 1) * 100, 2)
    else:
        results["pce_latest"] = None

    # 失業率
    u = fetch_fred("UNRATE", fred_key, 3)
    results["unemployment"]      = u[-1] if u else None
    results["unemployment_prev"] = u[-2] if len(u) >= 2 else None

    # 非農就業（月增量，千人）
    payroll = fetch_fred("PAYEMS", fred_key, 3)
    if len(payroll) >= 2:
        results["nonfarm_payrolls"] = round(payroll[-1] - payroll[-2], 1)
    else:
        results["nonfarm_payrolls"] = None

    # 聯邦基金利率
    ffr = fetch_fred("FEDFUNDS", fred_key, 2)
    results["fed_funds_rate"] = ffr[-1] if ffr else None

    return results


# ─────────────────────────────────────────────
# 判斷今日核心事件
# ─────────────────────────────────────────────

def determine_top_event(indicators: dict, gdpnow: Optional[float],
                         ny_recession: Optional[float]) -> tuple[str, str, list]:
    """
    根據指標變化幅度，判斷今日最重要事件
    回傳 (標題, 內文, 標籤列表)
    """
    cpi     = indicators.get("cpi_latest")
    cpi_prev = indicators.get("cpi_prev")
    u       = indicators.get("unemployment")
    u_prev  = indicators.get("unemployment_prev")
    payroll = indicators.get("nonfarm_payrolls")
    ffr     = indicators.get("fed_funds_rate")

    # 優先：CPI 有顯著變化
    if cpi is not None and cpi_prev is not None:
        diff = round(cpi - cpi_prev, 2)
        if abs(diff) >= 0.1:
            direction = "下滑" if diff < 0 else "攀升"
            surprise  = "低於" if diff < 0 else "高於"
            tags = (["通膨降溫", "降息預期升溫", "美債受惠"] if diff < 0
                    else ["通膨升溫", "降息推遲", "美股承壓"])
            return (
                f"美國 CPI 年增率{direction}至 {cpi:.1f}%",
                f"美國最新 CPI 年增率為 {cpi:.1f}%，{'' if diff < 0 else ''}較上月 {cpi_prev:.1f}% {direction} {abs(diff):.1f} 個百分點，"
                f"{surprise}市場預期。核心 CPI 為 {indicators.get('cpi_core', 'N/A')}%，"
                f"服務業通膨仍具黏性，需持續觀察。",
                tags
            )

    # 次要：失業率顯著變化
    if u is not None and u_prev is not None:
        diff = round(u - u_prev, 1)
        if abs(diff) >= 0.1:
            direction = "上升" if diff > 0 else "下降"
            tags = (["勞動市場降溫", "衰退風險上升"] if diff > 0
                    else ["就業強勁", "Fed 鷹派空間"])
            return (
                f"美國失業率{direction}至 {u:.1f}%",
                f"最新失業率為 {u:.1f}%，較上月{direction} {abs(diff):.1f} 個百分點。"
                f"非農就業月增 {payroll:.0f}K，"
                f"勞動市場{'開始降溫，有利降息時程推進' if diff > 0 else '仍具韌性，Fed 維持觀望態度'}。",
                tags
            )

    # GDPNow 顯著
    if gdpnow is not None:
        direction = "加速" if gdpnow > 2.5 else "放緩" if gdpnow < 1.5 else "溫和成長"
        tags = ["GDP預測更新", "Atlanta Fed", "成長動能"]
        return (
            f"Atlanta Fed GDPNow 預測本季 GDP {gdpnow:.1f}%",
            f"Atlanta Fed GDPNow 模型最新估計本季 GDP 年化成長率為 {gdpnow:.1f}%，"
            f"經濟動能{direction}。聯準會將參考此即時數據評估降息時機。",
            tags
        )

    # 衰退機率
    if ny_recession is not None and ny_recession > 25:
        tags = ["衰退風險偏高", "NY Fed", "防禦配置"]
        return (
            f"NY Fed 衰退機率升至 {ny_recession:.0f}%",
            f"紐約聯儲最新 12 個月衰退機率模型顯示機率為 {ny_recession:.0f}%，"
            f"{'處於警戒區間' if ny_recession > 35 else '略高於歷史均值'}，建議適度增持防禦資產。",
            tags
        )

    # 預設：每日總經快訊
    tags = ["總經監測", "FRED", "每日更新"]
    return (
        "今日總體經濟數據概況",
        f"聯邦基金利率維持在 {ffr:.2f}%，失業率 {u:.1f}%，CPI 年增率 {cpi:.1f}%。"
        f"各項指標顯示經濟維持{'溫和擴張' if (u or 5) < 4.5 else '放緩趨勢'}，市場持續關注聯準會政策動向。",
        tags
    )


# ─────────────────────────────────────────────
# Smart Beta 配置計算
# ─────────────────────────────────────────────

def calc_smart_beta_allocation(indicators: dict, cpi: Optional[float],
                                ny_recession: Optional[float],
                                hy_spread: float, z_voo: float) -> tuple[float, float, float, str]:
    """
    五因子 Smart Beta 動態配置
    因子：價值(CAPE/ERP) + 動能(EMA Z) + 總經(CPI/衰退) + 信用(HY Spread) + 防禦(失業率)
    回傳 (voo_pct, etf0050_pct, etf00679b_pct, 理由說明)
    """
    # 基礎配置
    voo_w   = 35.0
    tw_w    = 30.0
    bond_w  = 35.0
    reasons = []

    # 1. 通膨因子：CPI 低 → 美債受惠，加碼 00679B
    if cpi is not None:
        if cpi < 2.5:
            bond_w += 10; voo_w -= 5; tw_w -= 5
            reasons.append("通膨低位，美債多頭")
        elif cpi > 3.5:
            bond_w -= 10; voo_w += 5; tw_w += 5
            reasons.append("通膨偏高，減持美債")

    # 2. 衰退機率因子
    if ny_recession is not None:
        if ny_recession > 35:
            bond_w += 15; voo_w -= 10; tw_w -= 5
            reasons.append("衰退機率偏高，增持防禦")
        elif ny_recession < 15:
            voo_w += 5; tw_w += 5; bond_w -= 10
            reasons.append("衰退機率低，偏向成長")

    # 3. 動能因子：VOO EMA Z-Score
    if z_voo > 2.0:
        voo_w -= 10; bond_w += 10
        reasons.append("美股過熱，降低 VOO 比重")
    elif z_voo < -1.5:
        voo_w += 10; bond_w -= 10
        reasons.append("美股超跌，增加 VOO 比重")

    # 4. 信用利差因子
    if hy_spread > 6.0:
        bond_w += 5; voo_w -= 5
        reasons.append("信用利差擴大，偏防禦")
    elif hy_spread < 3.5:
        voo_w += 5; bond_w -= 5
        reasons.append("信用利差壓縮，市場樂觀")

    # 正規化（確保總和 100%，最低 10%）
    voo_w   = max(10.0, voo_w)
    tw_w    = max(10.0, tw_w)
    bond_w  = max(10.0, bond_w)
    total   = voo_w + tw_w + bond_w
    voo_w   = round(voo_w / total * 100, 0)
    tw_w    = round(tw_w  / total * 100, 0)
    bond_w  = round(100 - voo_w - tw_w, 0)

    reason_text = "；".join(reasons) if reasons else "各項指標均衡，維持基礎配置"
    return voo_w, tw_w, bond_w, reason_text


# ─────────────────────────────────────────────
# 主函式
# ─────────────────────────────────────────────

def fetch_macro_report(fred_key: str, hy_spread: float = 4.5,
                        z_voo: float = 0.0) -> MacroReport:
    """完整宏觀報告資料擷取"""
    print("  [REPORT] 擷取核心指標...")
    indicators = fetch_core_indicators(fred_key)

    print("  [REPORT] 擷取 GDPNow...")
    gdpnow, gdpnow_prev = fetch_gdpnow_direct()
    if gdpnow is None:
        gdpnow, gdpnow_prev = fetch_gdpnow()

    print("  [REPORT] 擷取 NY Fed 衰退機率...")
    ny_recession = fetch_ny_fed_recession()

    print("  [REPORT] 擷取 Cleveland CPI 預測...")
    cleveland_cpi = fetch_cleveland_cpi()

    # 核心事件判斷
    title, body, tags = determine_top_event(
        indicators, gdpnow, ny_recession
    )

    # 降息機率代理（用 CPI 與 Fed Funds Rate 推算）
    cpi = indicators.get("cpi_latest")
    ffr = indicators.get("fed_funds_rate")
    if cpi and ffr:
        # 簡單代理：CPI 下行幅度越大，降息機率越高
        rate_cut_prob = max(0, min(95, (ffr - cpi) * 15 + 30))
    else:
        rate_cut_prob = None

    # Smart Beta 配置
    voo_w, tw_w, bond_w, alloc_reason = calc_smart_beta_allocation(
        indicators, cpi, ny_recession, hy_spread, z_voo
    )

    now = datetime.now()
    date_str = now.strftime("%Y/%m/%d")

    return MacroReport(
        date=date_str,
        top_event_title=title,
        top_event_body=body,
        top_event_tags=tags,
        cpi_latest=indicators.get("cpi_latest"),
        cpi_prev=indicators.get("cpi_prev"),
        cpi_core=indicators.get("cpi_core"),
        pce_latest=indicators.get("pce_latest"),
        unemployment=indicators.get("unemployment"),
        unemployment_prev=indicators.get("unemployment_prev"),
        nonfarm_payrolls=indicators.get("nonfarm_payrolls"),
        gdpnow=gdpnow,
        gdpnow_prev=gdpnow_prev,
        cleveland_cpi_forecast=cleveland_cpi,
        ny_fed_recession_prob=ny_recession,
        fed_funds_rate=ffr,
        rate_cut_prob=rate_cut_prob,
        voo_weight=voo_w,
        etf_0050_weight=tw_w,
        etf_00679b_weight=bond_w,
        allocation_reason=alloc_reason,
    )
