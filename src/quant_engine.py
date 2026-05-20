"""
量化指標計算引擎 v4 — ai-hedge-fund 多 Agent 架構
資料來源：Alpha Vantage + TWSE + FRED（完全移除 yfinance）
新增：RSI、布林帶 %B、Sharpe Ratio、Max Drawdown
      三 Agent 信心評分（技術/基本面/總經）+ 綜合信心
"""

import time
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

from src.data_fetcher import (
    av_daily_close, av_quote,
    fetch_vix_av, fetch_treasury_av,
    twse_daily_close, twse_latest_close,
    yahoo_daily_close, finmind_daily_close,
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
    # ── 方向一：技術指標強化 ──
    rsi:          float = 50.0          # RSI(14)
    bb_pct:       float = 0.5           # Bollinger Band %B（0~1，可超出）
    sharpe_1y:    float = 0.0           # 滾動一年 Sharpe Ratio
    max_drawdown: float = 0.0           # 滾動一年最大回撤（負值，%）
    # ── 方向二：多 Agent 信心分數 ──
    technical_score:    float          = 50.0
    value_score:        Optional[float] = None   # 無估值（債券）時為 None
    macro_score:        float          = 50.0
    combined_confidence: float         = 50.0
    confidence_signal:  str            = "持  有"
    # ── 方向四：新聞情緒 ──
    news_headlines: list = field(default_factory=list)  # 供 Gemini 分析用


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
# 方向一：技術指標強化
# ─────────────────────────────────────────────

def calc_rsi(prices: pd.Series, n: int = 14) -> float:
    """RSI(n) = 100 − 100/(1 + RS)，RS = EMA(漲幅)/EMA(跌幅)"""
    if len(prices) < n + 1:
        return 50.0
    delta = prices.diff()
    gain  = delta.clip(lower=0).ewm(span=n, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=n, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return round(float(100 - 100 / (1 + rs.iloc[-1])), 1)


def calc_bb_pct(prices: pd.Series, n: int = 20) -> float:
    """Bollinger Band %B = (P − Lower) / (Upper − Lower)"""
    if len(prices) < n:
        return 0.5
    sma   = prices.rolling(n).mean()
    std   = prices.rolling(n).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    band_width = (upper - lower + 1e-9)
    pct   = (prices - lower) / band_width
    return round(float(pct.iloc[-1]), 3)


def calc_sharpe_1y(prices: pd.Series) -> float:
    """滾動 252 交易日 Sharpe = 年化超額報酬 / 年化波動率"""
    r = prices.pct_change().dropna().tail(252)
    if len(r) < 20:
        return 0.0
    ann_r = float(r.mean()) * 252
    ann_s = float(r.std()) * (252 ** 0.5)
    return round(ann_r / ann_s if ann_s else 0.0, 2)


def calc_max_drawdown(prices: pd.Series) -> float:
    """滾動 252 交易日最大回撤（%，負值）"""
    p    = prices.tail(252)
    peak = p.cummax()
    dd   = (p - peak) / (peak + 1e-9)
    return round(float(dd.min()) * 100, 1)


# ─────────────────────────────────────────────
# 方向二：多 Agent 信心分數
# ─────────────────────────────────────────────

def calc_technical_score(z: float, macd_hist: float,
                          rsi: float, bb_pct: float) -> float:
    """
    TechnicalAgent (0~100)
    原始分 -90 ~ +80 → 線性縮放至 0~100
    """
    raw = 0.0
    # RSI（-25 ~ +25）
    if rsi < 30:        raw += 25
    elif rsi < 50:      raw += 15
    elif rsi <= 70:     raw +=  0
    else:               raw -= 25
    # MACD 柱狀圖（-20 ~ +20）
    raw += 20 if macd_hist > 0 else -20
    # EMA Z-Score（-30 ~ +20）
    if z < -1:          raw += 20
    elif z < 1:         raw += 10
    elif z < 2:         raw -= 20
    else:               raw -= 30
    # Bollinger Band %B（-15 ~ +15）
    if bb_pct < 0.2:    raw += 15
    elif bb_pct > 0.8:  raw -= 15
    # 原始分範圍 [-90, +80]，線性映射至 [0, 100]
    return round(max(0.0, min(100.0, (raw + 90) / 170 * 100)), 1)


def calc_value_score(erp: Optional[float],
                     cape: Optional[float],
                     pred_10y: Optional[float]) -> Optional[float]:
    """
    ValueAgent (0~100)
    原始分 -75 ~ +80 → 線性縮放
    如三者皆 None（債券等）回傳 None
    """
    raw, count = 0.0, 0
    if erp is not None:
        if erp > 3:     raw += 30
        elif erp > 2:   raw += 15
        elif erp > 1:   raw +=  0
        elif erp > 0:   raw -= 15
        else:           raw -= 30
        count += 1
    if cape is not None:
        if cape < 15:   raw += 30
        elif cape < 20: raw += 15
        elif cape < 25: raw +=  0
        elif cape < 30: raw -= 15
        else:           raw -= 25
        count += 1
    if pred_10y is not None:
        if pred_10y > 6:   raw += 20
        elif pred_10y > 4: raw += 10
        elif pred_10y > 2: raw +=  0
        else:              raw -= 20
        count += 1
    if count == 0:
        return None
    return round(max(0.0, min(100.0, (raw + 75) / 155 * 100)), 1)


def calc_macro_score(sahm: bool, hy_spread: float,
                     pmi_f2: float, yield_curve: float) -> float:
    """
    MacroAgent (0~100)
    原始分 -70 ~ +70 → 線性縮放
    """
    raw = 0.0
    # 薩姆規則（-30 ~ +20）
    raw += -30 if sahm else 20
    # HY 信用利差（-20 ~ +25）
    if hy_spread < 3.5:     raw -= 20   # 壓縮・過樂觀
    elif hy_spread < 6.5:   raw += 15   # 正常
    elif hy_spread < 8.0:   raw += 20
    else:                   raw += 25   # 極度恐慌・左側機會
    # PMI 二階導數（-10 ~ +15）
    raw += 15 if pmi_f2 > 0 else -10
    # 殖利率曲線（-10 ~ +10）
    raw += 10 if yield_curve > 0 else -10
    # 原始分範圍 [-70, +70]
    return round(max(0.0, min(100.0, (raw + 70) / 140 * 100)), 1)


def calc_confidence(tech: float, val: Optional[float],
                    macro: float) -> tuple[float, str]:
    """
    Portfolio Manager 加權合成信心分數
    有估值：Tech 40% + Val 35% + Macro 25%
    無估值：Tech 60% + Macro 40%
    """
    if val is not None:
        score = tech * 0.40 + val * 0.35 + macro * 0.25
    else:
        score = tech * 0.60 + macro * 0.40
    score = round(score, 1)
    if score >= 72:   label = "強力買入"
    elif score >= 58: label = "買  入"
    elif score >= 42: label = "持  有"
    elif score >= 28: label = "賣  出"
    else:             label = "強力賣出"
    return score, label


# ─────────────────────────────────────────────
# ETF 訊號計算
# ─────────────────────────────────────────────

def _calc_etf_signal(ticker: str, name: str,
                     prices: pd.Series, current: float, prev: float,
                     vix: float, vix_break: bool,
                     hy_spread: float, us10y: float,
                     sahm_triggered: bool,
                     pmi_f2: float = 0.0,
                     yield_curve: float = 0.0) -> ETFSignal:
    chg_pct = round((current - prev) / prev * 100, 2) if prev else 0.0
    n = min(200, len(prices) - 1)
    ema_val = float(calc_ema(prices, n).iloc[-1]) if len(prices) > 1 else current
    z = calc_ema_zscore(prices, n)
    sentiment, sent_label = calc_sentiment(z, vix, hy_spread)
    kelly_f = calc_kelly(ticker)
    phase, mult, mode = determine_phase(z, hy_spread, sahm_triggered, vix, kelly_f)

    # CAPE / ERP：00679B 債券不適用
    cape, erp, pred = None, None, None
    if "00679B" not in ticker:
        try:
            from src.data_fetcher import fetch_cape_erp
            if hasattr(_calc_etf_signal, "_av_key") and _calc_etf_signal._av_key:
                cape, erp, pred = fetch_cape_erp(
                    ticker.replace(".TW", ""),
                    _calc_etf_signal._av_key,
                    us10y
                )
        except Exception as e:
            print(f"  [CAPE] {ticker}: {e}")

    # MACD (12/26/9)
    ema12   = prices.ewm(span=12, adjust=False).mean()
    ema26   = prices.ewm(span=26, adjust=False).mean()
    _macd   = ema12 - ema26
    _msig   = _macd.ewm(span=9, adjust=False).mean()
    _mhist  = _macd - _msig
    macd_l  = round(float(_macd.iloc[-1]),  4)
    macd_s  = round(float(_msig.iloc[-1]),  4)
    macd_h  = round(float(_mhist.iloc[-1]), 4)

    # 方向一：技術指標
    rsi          = calc_rsi(prices)
    bb_pct       = calc_bb_pct(prices)
    sharpe_1y    = calc_sharpe_1y(prices)
    max_drawdown = calc_max_drawdown(prices)

    # 方向二：多 Agent 信心分數
    tech_score = calc_technical_score(z, macd_h, rsi, bb_pct)
    val_score  = calc_value_score(erp, cape, pred)
    mac_score  = calc_macro_score(sahm_triggered, hy_spread, pmi_f2, yield_curve)
    conf, conf_label = calc_confidence(tech_score, val_score, mac_score)

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
        rsi=rsi, bb_pct=bb_pct, sharpe_1y=sharpe_1y, max_drawdown=max_drawdown,
        technical_score=tech_score, value_score=val_score,
        macro_score=mac_score,
        combined_confidence=conf, confidence_signal=conf_label,
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
    real_rate    = round(us10y - breakeven, 3)
    yield_curve  = round(us10y - us02y, 3)

    print("  [MACRO] 擷取 VIX...")
    vix, vix_upper, vix_break = fetch_vix_av(av_key, fred_key)

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

    _calc_etf_signal._av_key = av_key

    etf_signals = []
    for ticker, name, stock_no in etf_configs:
        print(f"  [ETF] {ticker}...")
        prices = twse_daily_close(stock_no, months=14)
        print(f"    TWSE 取得 {len(prices)} 筆")
        if prices.empty or len(prices) < 2:
            print(f"  [ETF] {ticker} TWSE 無資料，改用 FinMind...")
            prices = finmind_daily_close(stock_no, days=400)
        if prices.empty or len(prices) < 2:
            print(f"  [ETF] {ticker} FinMind 無資料，改用 Yahoo Finance...")
            prices = yahoo_daily_close(f"{stock_no}.TW", days=400)
        if prices.empty or len(prices) < 2:
            print(f"  [ETF] {ticker} Yahoo 無資料，改用 Alpha Vantage...")
            prices = av_daily_close(f"{stock_no}.TW", av_key, days=400)
            if prices.empty or len(prices) < 2:
                print(f"  [WARN] {ticker} 所有來源均無資料，跳過")
                continue

        current, prev = float(prices.iloc[-1]), float(prices.iloc[-2])
        sig = _calc_etf_signal(
            ticker, name, prices, current, prev,
            vix, vix_break, macro.hy_spread, macro.us10y,
            macro.sahm_triggered,
            pmi_f2=macro.pmi_second_deriv,
            yield_curve=macro.yield_curve,
        )
        etf_signals.append(sig)
        print(f"    Z={sig.z_score} | 信心={sig.combined_confidence:.0f}% {sig.confidence_signal} | 乘數={sig.fund_multiplier}x")

    return macro, etf_signals, vix


# ─────────────────────────────────────────────
# 夜盤：VOO + GLD
# ─────────────────────────────────────────────

def run_evening_session(fred_key: str, av_key: str):
    print("=" * 50)
    print("夜盤模式：22:00 TST")
    print("=" * 50)

    macro, vix, vix_upper, vix_break = fetch_macro(fred_key, av_key)

    _calc_etf_signal._av_key = av_key

    etf_configs = [
        ("VOO", "Vanguard S&P 500"),
        ("GLD", "SPDR Gold Shares"),
    ]

    etf_signals = []
    for ticker, name in etf_configs:
        print(f"  [ETF] {ticker} (Yahoo Finance)...")
        prices = yahoo_daily_close(ticker, days=400)
        if prices.empty or len(prices) < 5:
            print(f"  [ETF] {ticker} Yahoo 無資料，改用 Alpha Vantage...")
            prices = av_daily_close(ticker, av_key, days=400)
        if prices.empty or len(prices) < 5:
            print(f"  [WARN] {ticker} 無資料，跳過")
            continue

        current = float(prices.iloc[-1])
        prev    = float(prices.iloc[-2])
        sig = _calc_etf_signal(
            ticker, name, prices, current, prev,
            vix, vix_break, macro.hy_spread, macro.us10y,
            macro.sahm_triggered,
            pmi_f2=macro.pmi_second_deriv,
            yield_curve=macro.yield_curve,
        )
        etf_signals.append(sig)
        print(f"  {ticker}: Z={sig.z_score} | 信心={sig.combined_confidence:.0f}% {sig.confidence_signal} | 乘數={sig.fund_multiplier}x")

    return macro, etf_signals, vix
