"""
量化指標計算引擎
基於：14本大師經典量化指標 + 雙引擎自動化量化投資系統
指標來源：EMA Z-Score, 薩姆規則, Michez法則, 信用利差, CAPE, ERP, 凱利準則
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

# ─────────────────────────────────────────────
# 資料結構定義
# ─────────────────────────────────────────────

@dataclass
class ETFData:
    ticker: str
    name: str
    price: float
    prev_close: float
    change_pct: float
    ema_200: float
    z_score: float          # EMA Z-Score (情緒偏離度)
    signal: str             # 獵人/鑑賞家/刺客


@dataclass
class MacroIndicators:
    # EMA Z-Score
    voo_z: float
    etf_0050_z: float
    etf_00679b_z: float

    # 信用利差 (Credit Spread)
    credit_spread: float    # HY spread (%)
    credit_signal: str

    # 實質利率 (Fisher Equation)
    nominal_rate: float     # 10y Treasury
    inflation: float        # CPI YoY
    real_rate: float        # r = i - π

    # 薩姆規則 (Sahm Rule)
    sahm_indicator: float
    sahm_triggered: bool

    # 景氣循環階段
    cycle_phase: str        # 絕望/希望/成長/樂觀
    fund_multiplier: float  # 資金乘數 0x ~ 3x
    kelly_fraction: float   # 凱利準則建議比例

    # CAPE (Shiller PE)
    cape_ratio: Optional[float]
    cape_10y_return: Optional[float]  # 預估10年年化報酬


# ─────────────────────────────────────────────
# 核心計算函式
# ─────────────────────────────────────────────

def calc_ema(prices: pd.Series, n: int = 200) -> pd.Series:
    """
    指數移動平均 (EMA)
    公式：EMAₜ = Pₜ × (2/(1+N)) + EMAₜ₋₁ × (1 - 2/(1+N))
    來源：雙引擎自動化量化投資系統
    """
    return prices.ewm(span=n, adjust=False).mean()


def calc_ema_zscore(prices: pd.Series, n: int = 200) -> float:
    """
    EMA Z-Score 情緒偏離度
    公式：Z_EMA = (Pₜ - EMA₂₀₀) / σ₂₀₀
    來源：雙引擎系統 + 《總體經濟學家教你Python分析》

    判讀：
      Z < -2.0  → 極度恐慌，獵人模式
      -2.0 ~ +1.5 → 常態，鑑賞家巡航
      > +2.5    → 泡沫預警，刺客防禦
    """
    if len(prices) < n:
        return 0.0
    ema = calc_ema(prices, n)
    sigma = prices.rolling(n).std()
    z = (prices.iloc[-1] - ema.iloc[-1]) / sigma.iloc[-1]
    return round(float(z), 3)


def interpret_zscore(z: float) -> str:
    """判斷 EMA Z-Score 對應的操作模式"""
    if z <= -2.0:
        return "🔴 獵人加碼 (2~3x)"
    elif z <= -1.0:
        return "🟡 開始布局 (1.5x)"
    elif z <= 1.5:
        return "🟢 鑑賞家巡航 (1x)"
    elif z <= 2.5:
        return "🟠 泡沫預警 (0.5x)"
    else:
        return "⛔ 刺客防禦 (0x)"


def calc_sahm_rule(unemployment_data: list) -> tuple[float, bool]:
    """
    薩姆規則 (Sahm Rule)
    公式：Sahmₜ = (Uₜ + Uₜ₋₁ + Uₜ₋₂)/3 - min(U₍ₜ₋₁₂..ₜ₋₁₎) ≥ 0.5%
    來源：《經濟指標的秘密》、TAA 系統規格書
    觸發 → 衰退確認，啟動刺客模式
    """
    if len(unemployment_data) < 13:
        return 0.0, False
    recent = unemployment_data[-3:]
    three_month_avg = sum(recent) / 3
    twelve_month_min = min(unemployment_data[-13:-1])
    indicator = three_month_avg - twelve_month_min
    triggered = indicator >= 0.5
    return round(indicator, 3), triggered


def calc_michez_rule(u_series: list, v_series: list) -> tuple[float, bool]:
    """
    雙向防噪衰退指標 (Michez 法則)
    改良版薩姆規則，需失業率上升 + 職缺率下降同步發生
    公式：
      U_indicator = U_3ma - U_12m_min
      V_indicator = V_12m_max - V_3ma
      m = min(U_indicator, V_indicator)
    觸發閾值：m ≥ 0.29%
    來源：TAA 系統規格書
    """
    if len(u_series) < 13 or len(v_series) < 13:
        return 0.0, False

    u_3ma = sum(u_series[-3:]) / 3
    u_12m_min = min(u_series[-13:-1])
    u_indicator = u_3ma - u_12m_min

    v_3ma = sum(v_series[-3:]) / 3
    v_12m_max = max(v_series[-13:-1])
    v_indicator = v_12m_max - v_3ma

    m = min(u_indicator, v_indicator)
    triggered = m >= 0.29
    return round(m, 3), triggered


def calc_kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """
    凱利準則 (Kelly Criterion)
    公式：f* = p - (1-p)/b
    p = 勝率, b = 盈虧比
    來源：《執行的藝術》、TAA 系統規格書
    用於：獵人模式啟動時計算最佳資金投入比例
    """
    if win_loss_ratio <= 0:
        return 0.0
    f = win_rate - (1 - win_rate) / win_loss_ratio
    return round(max(0.0, min(f, 1.0)), 3)


def calc_cape_regression(cape: float) -> float:
    """
    估值衰減迴歸模型 (Shiller PE)
    公式：E[R₁₀y] = 0.169 - 0.0052 × CAPE
    來源：《漫步華爾街》、TAA 量化模型報告
    高盛驗證：R² = 0.7，與未來10年報酬高度負相關
    """
    predicted_return = 0.169 - 0.0052 * cape
    return round(predicted_return * 100, 2)  # 轉為百分比


def calc_erp(forward_earnings_yield: float, risk_free_rate: float) -> float:
    """
    股權風險溢酬 (ERP - Equity Risk Premium)
    公式：ERP = E₁/P₀ - Rf
    來源：《漫步華爾街》、《掌握市場週期》
    ERP < 2% → 股票相對公債過貴，安全邊際消失
    """
    return round(forward_earnings_yield - risk_free_rate, 3)


def determine_cycle_phase(
    z_score: float,
    credit_spread: float,
    sahm_triggered: bool,
    real_rate: float,
    cape_10y_return: Optional[float] = None
) -> tuple[str, float]:
    """
    景氣循環階段判定（奧本海默四階段模型）
    結合：EMA Z-Score + 信用利差 + 薩姆規則 + 實質利率
    輸出：(循環階段, 資金乘數)

    ┌──────────────┬──────────────┬──────────────┐
    │ 階段         │ 乘數         │ 觸發條件     │
    ├──────────────┼──────────────┼──────────────┤
    │ 絕望/衰退    │ 2.0x ~ 3.0x  │ 薩姆觸發     │
    │ 希望/復甦    │ 1.5x ~ 2.0x  │ Z < -1.0     │
    │ 成長/擴張    │ 1.0x         │ 常態         │
    │ 樂觀/繁榮末  │ 0.0x ~ 0.5x  │ Z > 2.5 或   │
    │              │              │ 利差 < 3.5%  │
    └──────────────┴──────────────┴──────────────┘
    """
    # 衰退確認 → 刺客防禦 + 底部建倉 (獵人)
    if sahm_triggered:
        if z_score <= -2.0:
            return "🔴 絕望期（衰退底部）", 3.0
        return "🔴 絕望期（衰退中）", 0.5   # 衰退中不輕易加碼

    # 泡沫末期
    if z_score > 2.5 or credit_spread < 3.5:
        return "⚠️ 樂觀期（繁榮末期）", 0.5

    # 恐慌超跌
    if z_score <= -2.0 or credit_spread >= 8.0:
        return "🟡 絕望期（恐慌超跌）", 2.5

    # 復甦初期
    if -2.0 < z_score <= -1.0 or credit_spread >= 6.0:
        return "🟡 希望期（復甦初期）", 1.5

    # 成長期（常態）
    return "🟢 成長期（常態擴張）", 1.0


# ─────────────────────────────────────────────
# 資料擷取
# ─────────────────────────────────────────────

def fetch_etf_data(ticker: str, name: str, period: str = "2y") -> Optional[ETFData]:
    """擷取 ETF 歷史價格並計算 EMA Z-Score"""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty or len(hist) < 50:
            return None

        prices = hist["Close"]
        current = float(prices.iloc[-1])
        prev = float(prices.iloc[-2])
        change_pct = (current - prev) / prev * 100

        n = min(200, len(prices) - 1)
        ema_val = float(calc_ema(prices, n).iloc[-1])
        z = calc_ema_zscore(prices, n)
        signal = interpret_zscore(z)

        return ETFData(
            ticker=ticker,
            name=name,
            price=round(current, 2),
            prev_close=round(prev, 2),
            change_pct=round(change_pct, 2),
            ema_200=round(ema_val, 2),
            z_score=z,
            signal=signal,
        )
    except Exception as e:
        print(f"[ETF 擷取失敗] {ticker}: {e}")
        return None


def fetch_fred_series(series_id: str, api_key: str) -> list:
    """從 FRED API 擷取總經數據（失業率、CPI等）"""
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 24,
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        values = []
        for obs in reversed(data.get("observations", [])):
            try:
                values.append(float(obs["value"]))
            except (ValueError, KeyError):
                pass
        return values
    except Exception as e:
        print(f"[FRED 擷取失敗] {series_id}: {e}")
        return []


def fetch_treasury_yield(tenor: str = "^TNX") -> float:
    """擷取10年期美債殖利率（%）"""
    try:
        t = yf.Ticker(tenor)
        hist = t.history(period="5d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 3)
    except Exception:
        pass
    return 4.3  # fallback


def fetch_credit_spread() -> float:
    """
    擷取高收益債 OAS 利差
    使用 HYG (iShares HY Corp Bond ETF) 做為代理指標
    實際生產環境建議接 ICE BofA 或 FRED 的 BAMLH0A0HYM2 series
    """
    # 若有 FRED API Key，使用：BAMLH0A0HYM2 (ICE BofA US High Yield OAS)
    # 這裡用 HYG vs IEI 利差估算（簡化版）
    try:
        hyg = yf.Ticker("HYG").history(period="5d")["Close"].iloc[-1]
        iei = yf.Ticker("IEI").history(period="5d")["Close"].iloc[-1]
        # 粗估：HYG 殖利率 vs IEI 殖利率差（非精確，僅做方向性參考）
        # 生產環境請直接用 FRED BAMLH0A0HYM2
        spread_proxy = round(8.0 - (hyg / iei - 1) * 100, 2)
        return max(2.0, min(spread_proxy, 15.0))
    except Exception:
        return 4.5  # fallback: 正常水準


def fetch_vix() -> float:
    """擷取 VIX 恐慌指數"""
    try:
        vix = yf.Ticker("^VIX").history(period="5d")
        return round(float(vix["Close"].iloc[-1]), 2)
    except Exception:
        return 20.0


# ─────────────────────────────────────────────
# 主計算流程
# ─────────────────────────────────────────────

def run_quant_engine(fred_api_key: str = "") -> tuple[list[ETFData], MacroIndicators]:
    """
    執行完整量化分析
    Returns: (ETF列表, 總經指標)
    """
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 啟動量化引擎...")

    # ── ETF 資料 ──
    etfs_config = [
        ("VOO",    "Vanguard S&P 500 ETF"),
        ("0050.TW", "元大台灣50 ETF"),
        ("00679B.TW", "元大美債20年 ETF"),
    ]
    etf_results = []
    for ticker, name in etfs_config:
        data = fetch_etf_data(ticker, name)
        if data:
            etf_results.append(data)
            print(f"  ✓ {ticker}: {data.price} | Z={data.z_score}")

    # ── 總經指標 ──
    treasury_10y = fetch_treasury_yield()
    credit_spread = fetch_credit_spread()
    vix = fetch_vix()

    # 失業率與職缺率（FRED）
    unemployment = fetch_fred_series("UNRATE", fred_api_key) if fred_api_key else [4.1, 4.1, 4.0, 4.0, 3.9, 3.9, 3.8, 3.8, 3.7, 3.7, 3.8, 3.9, 4.0]
    job_openings = fetch_fred_series("JTSJOR", fred_api_key) if fred_api_key else [5.2, 5.3, 5.4, 5.5, 5.6, 5.6, 5.7, 5.8, 5.9, 6.0, 6.1, 6.0, 5.9]
    cpi_data = fetch_fred_series("CPIAUCSL", fred_api_key) if fred_api_key else []

    # 薩姆規則
    sahm_val, sahm_triggered = calc_sahm_rule(unemployment)

    # Michez 法則
    michez_val, michez_triggered = calc_michez_rule(unemployment, job_openings)

    # 實質利率（費雪方程式）
    if len(cpi_data) >= 13:
        inflation = (cpi_data[-1] / cpi_data[-13] - 1) * 100
    else:
        inflation = 2.8  # fallback
    real_rate = round(treasury_10y - inflation, 3)

    # Z-Score 彙整
    voo_z = next((e.z_score for e in etf_results if e.ticker == "VOO"), 0.0)
    etf_0050_z = next((e.z_score for e in etf_results if "0050" in e.ticker), 0.0)
    etf_00679b_z = next((e.z_score for e in etf_results if "00679B" in e.ticker), 0.0)

    # 信用利差訊號
    if credit_spread >= 8.0:
        credit_signal = "🔴 極度恐慌 → 左側建倉"
    elif credit_spread >= 6.0:
        credit_signal = "🟠 市場恐慌 → 防禦留倉"
    elif credit_spread >= 4.5:
        credit_signal = "🟡 正常偏高 → 留意風險"
    elif credit_spread >= 3.5:
        credit_signal = "🟢 正常水準 → 常態投資"
    else:
        credit_signal = "⚠️ 過度樂觀 → 減碼防禦"

    # 景氣循環階段
    cycle_phase, fund_multiplier = determine_cycle_phase(
        z_score=voo_z,
        credit_spread=credit_spread,
        sahm_triggered=sahm_triggered or michez_triggered,
        real_rate=real_rate,
    )

    # 凱利準則（以VOO歷史勝率 ~65%、盈虧比 ~1.8 為基準）
    kelly = calc_kelly_fraction(win_rate=0.65, win_loss_ratio=1.8)

    # CAPE（使用代理：VOO 的 P/E 估算）
    cape_ratio = None
    cape_10y_return = None
    try:
        voo_info = yf.Ticker("VOO").info
        pe = voo_info.get("trailingPE")
        if pe and pe > 0:
            cape_ratio = round(float(pe) * 0.95, 1)  # 簡化估算（非精確 Shiller PE）
            cape_10y_return = calc_cape_regression(cape_ratio)
    except Exception:
        pass

    macro = MacroIndicators(
        voo_z=voo_z,
        etf_0050_z=etf_0050_z,
        etf_00679b_z=etf_00679b_z,
        credit_spread=credit_spread,
        credit_signal=credit_signal,
        nominal_rate=treasury_10y,
        inflation=round(inflation, 2),
        real_rate=real_rate,
        sahm_indicator=sahm_val,
        sahm_triggered=sahm_triggered,
        cycle_phase=cycle_phase,
        fund_multiplier=fund_multiplier,
        kelly_fraction=kelly,
        cape_ratio=cape_ratio,
        cape_10y_return=cape_10y_return,
    )

    print(f"  景氣階段：{cycle_phase}，資金乘數：{fund_multiplier}x")
    print(f"  VIX={vix}, 信用利差={credit_spread}%, 實質利率={real_rate}%")
    return etf_results, macro
