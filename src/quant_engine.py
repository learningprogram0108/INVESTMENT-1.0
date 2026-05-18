"""
量化指標計算引擎 v3
資料來源：Alpha Vantage + TWSE + FRED（完全移除 yfinance）
公式嚴格依據兩份大師文件
"""

import time
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional

from src.data_fetcher import (
    av_daily_close, av_quote,
    fetch_vix_av, fetch_treasury_av,
    twse_daily_close, twse_latest_close,
    stooq_daily_close,
    fetch_fred, fetch_hy_spread_fred,
)

# ─────────────────────────────────────────────
# 凱利準則參數（各 ETF 獨立）
# ─────────────────────────────────────────────
KELLY_PARAMS = {
    "VOO":       {"p": 0.68, "b": 2.1},
    "GLD":       {"p": 0.55, "b": 1.5},
    "0050.TW":   {"p": 0.65, "b": 1.8},
    "00679B.TW": {"p": 0.60, "b": 1.4},
}

# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

@dataclass
class ETFSignal:
    ticker: str
    name: str
    price: float
    change_pct: float
    ema_200: float
    z_score: float
    sentiment_score: float
    sentiment_label: str
    vix: float
    vix_bollinger_break: bool
    cape: Optional[float]
    erp: Optional[float]
    predicted_10y_return: Optional[float]
    kelly_f: float
    cycle_phase: str
    fund_multiplier: float
    multiplier_mode: str
    macd_line:   Optional[float]
    macd_signal: Optional[float]
    macd_hist:   Optional[float]


@dataclass
class MacroIndicators:
    us10y: float
    us02y: float
    cpi_yoy: float
    breakeven: float   # 10Y Breakeven Inflation Rate（FRED T10YIE）
    real_rate: float   # US10Y − Breakeven（TIPS 實質利率）
    yield_curve: float
    sahm_indicator: float
    sahm_triggered: bool
    michez_m: float
    michez_triggered: bool
    recession_prob: float
    hy_spread: float
    credit_signal: str
    ism_pmi: list
    pmi_second_deriv: float
    unemployment_3m: list


# ─────────────────────────────────────────────
# 核心公式（嚴格依文件）
# ─────────────────────────────────────────────

def calc_ema(prices: pd.Series, n: int = 200) -> pd.Series:
    """EMAₜ = Pₜ×(2/(1+N)) + EMAₜ₋₁×(1−2/(1+N))"""
    return prices.ewm(span=n, adjust=False).mean()


def calc_ema_zscore(prices: pd.Series, n: int = 200) -> float:
    """Z_EMA = (Pₜ − EMA₂₀₀) / σ₂₀₀"""
    n = min(n, len(prices) - 1)
    if n < 10:
        return 0.0
    ema = calc_ema(prices, n)
    sigma = prices.rolling(n).std().iloc[-1]
    if sigma == 0:
        return 0.0
    return round(float((prices.iloc[-1] - ema.iloc[-1]) / sigma), 3)


def calc_pmi_second_deriv(pmi: list) -> float:
    """f''(t) = (yₜ−yₜ₋₁) − (yₜ₋₁−yₜ₋₂)"""
    if len(pmi) < 3:
        return 0.0
    return round((pmi[-1] - pmi[-2]) - (pmi[-2] - pmi[-3]), 3)


def calc_sahm_rule(u: list) -> tuple[float, bool]:
    """Sahmₜ = (Uₜ+Uₜ₋₁+Uₜ₋₂)/3 − min(U₁₂ₘ) ≥ 0.5%"""
    if len(u) < 13:
        return 0.0, False
    u3ma = sum(u[-3:]) / 3
    u12m_min = min(u[-13:-1])
    ind = u3ma - u12m_min
    return round(ind, 3), ind >= 0.5


def calc_michez_rule(u: list, v: list) -> tuple[float, bool]:
    """m = min(U_3ma−U_12m_min, V_12m_max−V_3ma) ≥ 0.29%"""
    if len(u) < 13 or len(v) < 13:
        return 0.0, False
    u_ind = sum(u[-3:]) / 3 - min(u[-13:-1])
    v_ind = max(v[-13:-1]) - sum(v[-3:]) / 3
    m = min(u_ind, v_ind)
    return round(m, 3), m >= 0.29


def calc_recession_prob(m: float) -> float:
    """線性映射 m(0.29%~0.81%) → 衰退機率(0%~100%)"""
    return round(max(0.0, min(100.0, (m - 0.29) / 0.52 * 100)), 1)


def calc_erp(forward_pe: float, rf: float) -> float:
    """ERP = E₁/P₀ − Rf = (1/ForwardPE×100) − US10Y"""
    if forward_pe <= 0:
        return 0.0
    return round(1.0 / forward_pe * 100 - rf, 3)


def calc_cape_10y(cape: float) -> float:
    """E[R₁₀y] = 0.169 − 0.0052×CAPE（×100 轉百分比）"""
    return round((0.169 - 0.0052 * cape) * 100, 2)


def calc_kelly(ticker: str) -> float:
    """f* = p − (1−p)/b"""
    p = KELLY_PARAMS.get(ticker, {"p": 0.60, "b": 1.5})["p"]
    b = KELLY_PARAMS.get(ticker, {"p": 0.60, "b": 1.5})["b"]
    return round(max(0.0, p - (1 - p) / b), 3)


def calc_sentiment(z: float, vix: float, spread: float) -> tuple[float, str]:
    """
    情緒溫度計(0~100)
    = Z-Score(50%) + VIX反向(30%) + 利差反向(20%)
    """
    z_comp      = max(0, min(100, (z + 3) / 6 * 100))
    vix_comp    = max(0, min(100, (45 - vix) / 35 * 100))
    spread_comp = max(0, min(100, (10 - spread) / 8 * 100))
    score = round(z_comp * 0.5 + vix_comp * 0.3 + spread_comp * 0.2, 1)
    label = ("極度貪婪" if score >= 80 else "貪婪" if score >= 60
             else "中性" if score >= 40 else "恐慌" if score >= 20
             else "極度恐慌")
    return score, label


def determine_phase(z: float, spread: float, sahm: bool,
                    vix: float, kelly_f: float) -> tuple[str, float, str]:
    """
    景氣階段 + 資金乘數（依《執行的藝術》矩陣）
    最終乘數 = min(景氣乘數, f*×3)
    """
    if sahm:
        phase, raw, mode = "🔴 絕望期（衰退確認）", 0.5, "刺客防禦"
    elif z > 2.5 or (z > 2.5 and spread < 3.5):
        phase, raw, mode = "⚠️ 樂觀期（過熱警戒）", 0.5, "刺客防禦"
    elif spread < 3.5:
        phase, raw, mode = "⚠️ 樂觀期（利差壓縮）", 0.5, "刺客防禦"
    elif z <= -2.0 or vix >= 35 or spread >= 8.0:
        raw   = 2.5 if (z <= -2.0 and vix >= 35) else 2.0
        phase, mode = "🟡 絕望期（恐慌超跌）", "獵人加碼"
    elif z <= -1.0 or spread >= 6.0:
        phase, raw, mode = "🟡 希望期（復甦初期）", 1.5, "積極布局"
    else:
        phase, raw, mode = "🟢 成長期（常態擴張）", 1.0, "鑑賞家巡航"

    final = round(min(raw, kelly_f * 3), 1)
    return phase, final, mode


# ─────────────────────────────────────────────
# ETF 訊號計算
# ─────────────────────────────────────────────

def _calc_etf_signal(ticker: str, name: str,
                     prices: pd.Series, current: float, prev: float,
                     vix: float, vix_break: bool,
                     hy_spread: float, us10y: float,
                     sahm_triggered: bool) -> ETFSignal:
    chg_pct = round((current - prev) / prev * 100, 2) if prev else 0.0
    n = min(200, len(prices) - 1)
    ema_val = float(calc_ema(prices, n).iloc[-1]) if len(prices) > 1 else current
    z = calc_ema_zscore(prices, n)
    sentiment, sent_label = calc_sentiment(z, vix, hy_spread)
    kelly_f = calc_kelly(ticker)
    phase, mult, mode = determine_phase(z, hy_spread, sahm_triggered, vix, kelly_f)

    # CAPE / ERP：00679B 債券不適用，其他 ETF 從 Alpha Vantage 抓真實 PE
    cape, erp, pred = None, None, None
    if "00679B" not in ticker:
        try:
            from src.data_fetcher import fetch_cape_erp
            # av_key 從 module level 取得（由 run_*_session 傳入）
            # 這裡用 _av_key_cache（由外層注入）
            if hasattr(_calc_etf_signal, "_av_key") and _calc_etf_signal._av_key:
                cape, erp, pred = fetch_cape_erp(
                    ticker.replace(".TW", ""),
                    _calc_etf_signal._av_key,
                    us10y
                )
        except Exception as e:
            print(f"  [CAPE] {ticker}: {e}")

    ema12   = prices.ewm(span=12, adjust=False).mean()
    ema26   = prices.ewm(span=26, adjust=False).mean()
    _macd   = ema12 - ema26
    _msig   = _macd.ewm(span=9, adjust=False).mean()
    _mhist  = _macd - _msig
    macd_l  = round(float(_macd.iloc[-1]),  4)
    macd_s  = round(float(_msig.iloc[-1]),  4)
    macd_h  = round(float(_mhist.iloc[-1]), 4)

    return ETFSignal(
        ticker=ticker, name=name,
        price=round(current, 2), change_pct=chg_pct,
        ema_200=round(ema_val, 2), z_score=z,
        sentiment_score=sentiment, sentiment_label=sent_label,
        vix=vix, vix_bollinger_break=vix_break,
        cape=cape, erp=erp, predicted_10y_return=pred,
        kelly_f=kelly_f,
        cycle_phase=phase, fund_multiplier=mult, multiplier_mode=mode,
        macd_line=macd_l, macd_signal=macd_s, macd_hist=macd_h,
    )


# ─────────────────────────────────────────────
# 總經指標擷取
# ─────────────────────────────────────────────

def fetch_macro(fred_key: str, av_key: str) -> tuple[MacroIndicators, float, float, bool]:
    """回傳 (MacroIndicators, vix, vix_upper, vix_break)"""
    print("  [MACRO] 擷取 FRED 總經...")
    u_series = fetch_fred("UNRATE", fred_key, 24) or \
        [3.7]*3 + [3.8]*3 + [3.9]*3 + [4.0]*3 + [4.1, 4.1, 4.1]
    v_series = fetch_fred("JTSJOR", fred_key, 24) or \
        [6.0]*6 + [5.5]*6 + [5.0]*6 + [4.8, 4.7, 4.6]
    cpi_data = fetch_fred("CPIAUCSL", fred_key, 14)
    cpi_yoy  = round((cpi_data[-1] / cpi_data[-13] - 1) * 100, 2) \
               if len(cpi_data) >= 13 else 2.8

    # 10Y Breakeven Inflation Rate（市場預期通膨，FRED T10YIE，日頻）
    breakeven_data = fetch_fred("T10YIE", fred_key, 5)
    breakeven = round(breakeven_data[-1], 2) if breakeven_data else round(cpi_yoy, 2)

    # ISM PMI（FRED: ISM_MAN_PMI 或 NAPM）
    ism = fetch_fred("ISM_MAN_PMI", fred_key, 5)
    if not ism or len(ism) < 3:
        ism = fetch_fred("NAPM", fred_key, 5)
    if not ism or len(ism) < 3:
        ism = [53.0, 52.0, 52.7]

    sahm_val, sahm_on  = calc_sahm_rule(u_series)
    michez_m, mich_on  = calc_michez_rule(u_series, v_series)
    rec_prob           = calc_recession_prob(michez_m)

    print("  [MACRO] 擷取 Alpha Vantage 殖利率...")
    us10y, us02y = fetch_treasury_av(av_key)
    real_rate    = round(us10y - breakeven, 3)   # TIPS 實質利率 = 名目利率 − 預期通膨
    yield_curve  = round(us10y - us02y, 3)

    print("  [MACRO] 擷取 VIX...")
    vix, vix_upper, vix_break = fetch_vix_av(av_key)

    print("  [MACRO] 擷取 HY 信用利差...")
    hy_spread = fetch_hy_spread_fred(fred_key)

    if hy_spread < 3.5:    credit_signal = "壓縮·過樂觀"
    elif hy_spread < 4.5:  credit_signal = "正常偏低"
    elif hy_spread < 6.5:  credit_signal = "正常"
    elif hy_spread < 8.0:  credit_signal = "偏高·留意"
    else:                  credit_signal = "極度恐慌·左側機會"

    pmi_3  = ism[-3:]
    pmi_f2 = calc_pmi_second_deriv(pmi_3)

    macro = MacroIndicators(
        us10y=us10y, us02y=us02y, cpi_yoy=cpi_yoy,
        breakeven=breakeven, real_rate=real_rate, yield_curve=yield_curve,
        sahm_indicator=sahm_val, sahm_triggered=sahm_on,
        michez_m=michez_m, michez_triggered=mich_on,
        recession_prob=rec_prob,
        hy_spread=hy_spread, credit_signal=credit_signal,
        ism_pmi=pmi_3, pmi_second_deriv=pmi_f2,
        unemployment_3m=u_series[-3:],
    )
    return macro, vix, vix_upper, vix_break


# ─────────────────────────────────────────────
# 早盤：0050 + 00679B
# ─────────────────────────────────────────────

def run_morning_session(fred_key: str, av_key: str):
    print("=" * 50)
    print("早盤模式：09:30 TST")
    print("=" * 50)

    macro, vix, vix_upper, vix_break = fetch_macro(fred_key, av_key)

    etf_configs = [
        ("0050.TW",   "元大台灣 50",   "0050"),
        ("00679B.TW", "元大美債 20年", "00679B"),
    ]

    # 注入 av_key 供 _calc_etf_signal 使用
    _calc_etf_signal._av_key = av_key

    etf_signals = []
    for ticker, name, stock_no in etf_configs:
        print(f"  [ETF] {ticker}...")
        # 1. 優先 TWSE（14 個月 ≈ 280 筆，stooq fallback 補足至 500 筆）
        prices = twse_daily_close(stock_no, months=14)
        if prices.empty or len(prices) < 50:
            # 2. Stooq 備援（免費、支援台股 ETF）
            print(f"  [ETF] {ticker} TWSE 無資料，改用 Stooq...")
            prices = stooq_daily_close(f"{stock_no}.tw", days=500)
        if prices.empty or len(prices) < 10:
            # 3. Alpha Vantage 最後備援
            print(f"  [ETF] {ticker} Stooq 無資料，改用 Alpha Vantage...")
            prices = av_daily_close(f"{stock_no}.TW", av_key, days=400)
            if prices.empty or len(prices) < 10:
                print(f"  [WARN] {ticker} 所有來源均無資料，跳過")
                continue

        current, prev = float(prices.iloc[-1]), float(prices.iloc[-2])
        sig = _calc_etf_signal(
            ticker, name, prices, current, prev,
            vix, vix_break, macro.hy_spread, macro.us10y,
            macro.sahm_triggered
        )
        etf_signals.append(sig)
        print(f"    Z={sig.z_score} | 乘數={sig.fund_multiplier}x | {sig.cycle_phase}")

    return macro, etf_signals, vix


# ─────────────────────────────────────────────
# 夜盤：VOO
# ─────────────────────────────────────────────

def run_evening_session(fred_key: str, av_key: str):
    print("=" * 50)
    print("夜盤模式：22:00 TST")
    print("=" * 50)

    macro, vix, vix_upper, vix_break = fetch_macro(fred_key, av_key)

    # 注入 av_key 供 _calc_etf_signal 使用
    _calc_etf_signal._av_key = av_key

    etf_configs = [
        ("VOO", "Vanguard S&P 500", "voo.us"),
        ("GLD", "SPDR Gold Shares",  "gld.us"),
    ]

    etf_signals = []
    for ticker, name, stooq_sym in etf_configs:
        print(f"  [ETF] {ticker} (Stooq)...")
        prices = stooq_daily_close(stooq_sym, days=400)
        if prices.empty or len(prices) < 5:
            print(f"  [ETF] {ticker} Stooq 無資料，改用 Alpha Vantage...")
            prices = av_daily_close(ticker, av_key, days=400)
        if prices.empty or len(prices) < 5:
            print(f"  [WARN] {ticker} 無資料，跳過")
            continue

        current = float(prices.iloc[-1])
        prev    = float(prices.iloc[-2])
        sig = _calc_etf_signal(
            ticker, name, prices, current, prev,
            vix, vix_break, macro.hy_spread, macro.us10y,
            macro.sahm_triggered
        )
        etf_signals.append(sig)
        print(f"  {ticker}: Z={sig.z_score} | 乘數={sig.fund_multiplier}x | {sig.cycle_phase}")

    return macro, etf_signals, vix
