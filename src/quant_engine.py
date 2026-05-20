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
    yahoo_daily_close,
    fetch_fred, fetch_hy_spread_fred,
)
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform

# ─────────────────────────────────────────────
# 凱利準則參數（各 ETF 獨立）
# ─────────────────────────────────────────────
KELLY_PARAMS = {
    "VOO":  {"p": 0.68, "b": 2.1},
    "GLD":  {"p": 0.55, "b": 1.5},
    "QQQ":  {"p": 0.67, "b": 2.0},
    "VGIT": {"p": 0.62, "b": 1.2},
    "GRID": {"p": 0.55, "b": 1.6},
    "TYD":  {"p": 0.53, "b": 2.2},
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


def calc_tyd_timing(us10y: float, yield_curve: float, vgit_rsi: float) -> tuple:
    """
    TYD 買入時機評分（0-100）
    us10y:       美債10年期殖利率（%）
    yield_curve: 10Y-2Y 殖利率曲線（%）
    vgit_rsi:    VGIT RSI(14)
    Returns: (score: int, label: str)
    """
    score = 0
    # US10Y 水位（0-40 分）
    if us10y >= 4.8:    score += 40
    elif us10y >= 4.5:  score += 30
    elif us10y >= 4.2:  score += 15
    elif us10y >= 4.0:  score += 5
    # 殖利率曲線（0-35 分）
    if yield_curve < -0.3:   score += 35
    elif yield_curve < 0:    score += 25
    elif yield_curve < 0.3:  score += 10
    # VGIT RSI 超賣（0-25 分）
    if vgit_rsi < 35:    score += 25
    elif vgit_rsi < 45:  score += 15
    elif vgit_rsi < 55:  score += 5
    # 標籤
    if score >= 70:   label = "強烈買入 TYD"
    elif score >= 50: label = "可考慮 TYD"
    elif score >= 30: label = "觀望"
    else:             label = "持有 VGIT"
    return score, label


# ─────────────────────────────────────────────
# HRP：三群組分層配置
# EQUITY=[VOO,QQQ] / FIXED_INCOME=[VGIT,TYD] / ALTERNATIVES=[GLD,GRID]
# ─────────────────────────────────────────────

GROUPS = {
    "EQUITY":       ["VOO", "QQQ"],
    "FIXED_INCOME": ["VGIT", "TYD"],
    "ALTERNATIVES": ["GLD", "GRID"],
}


def calc_hrp_weights(prices_dict: dict, tyd_score: int = 0) -> dict:
    """
    三群組 HRP：
    1. 各群組內 equal-weight 計算群組報酬序列
    2. 對三群組做 inter-group HRP → W_eq, W_fi, W_alt
    3. 群組內配置：
       - EQUITY / ALTERNATIVES: 兩資產 HRP
       - FIXED_INCOME: VGIT:TYD 動態分配（依 tyd_score）
    prices_dict: {ticker: pd.Series of close prices}
    tyd_score:   calc_tyd_timing() 回傳的分數
    Returns: {ticker: float} 合計 ~1.0
    """
    available = set(prices_dict.keys())

    group_returns = {}
    valid_groups  = {}
    for g_name, members in GROUPS.items():
        avail = [m for m in members if m in available]
        if not avail:
            continue
        rets = pd.DataFrame(
            {t: prices_dict[t].pct_change().dropna() for t in avail}
        ).dropna()
        if len(rets) < 20:
            continue
        group_returns[g_name] = rets.mean(axis=1)   # equal-weight 群組報酬
        valid_groups[g_name]  = avail

    if len(group_returns) < 2:
        n = len(available)
        return {t: round(1.0/n, 4) for t in available}

    # ── Inter-group HRP ──
    grp_df = pd.DataFrame(group_returns).dropna()
    try:
        inter_weights = _hrp_on_returns(grp_df)
    except Exception:
        k = len(group_returns)
        inter_weights = {g: 1.0/k for g in group_returns}

    # ── Intra-group allocation ──
    result: dict = {}
    for g_name, members in valid_groups.items():
        grp_w = inter_weights.get(g_name, 0.0)
        if len(members) == 1:
            result[members[0]] = grp_w
        elif g_name == "FIXED_INCOME":
            # TYD 動態分配（依買入時機評分）
            if tyd_score >= 70:   tyd_ratio = 0.70
            elif tyd_score >= 50: tyd_ratio = 0.40
            elif tyd_score >= 30: tyd_ratio = 0.20
            else:                  tyd_ratio = 0.05
            vgit_ratio = 1.0 - tyd_ratio
            for t in members:
                ratio = tyd_ratio if t == "TYD" else vgit_ratio
                result[t] = grp_w * ratio
        else:
            # 兩資產 HRP
            avail_m = [t for t in members if t in available]
            rets = pd.DataFrame(
                {t: prices_dict[t].pct_change().dropna() for t in avail_m}
            ).dropna()
            if len(rets) < 20 or len(avail_m) < 2:
                for t in avail_m:
                    result[t] = grp_w / max(len(avail_m), 1)
            else:
                intra = _hrp_on_returns(rets)
                for t in avail_m:
                    result[t] = grp_w * intra.get(t, 1.0/len(avail_m))

    total = sum(result.values()) + 1e-12
    return {t: round(w / total, 4) for t, w in result.items()}


def _hrp_on_returns(returns_df: pd.DataFrame) -> dict:
    """對 DataFrame（columns=tickers）執行 HRP，返回 {ticker: weight}"""
    tickers = list(returns_df.columns)
    n = len(tickers)
    if n == 1:
        return {tickers[0]: 1.0}
    cov  = returns_df.cov().values
    corr = returns_df.corr().values
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0, 1))
    np.fill_diagonal(dist, 0.0)
    condensed = squareform(dist, checks=False)
    link = linkage(condensed, method="ward")
    sorted_idx = leaves_list(link)
    sorted_tickers = [tickers[i] for i in sorted_idx]
    cov_sorted = cov[np.ix_(sorted_idx, sorted_idx)]
    weights = _hrp_recursive_bisect(cov_sorted, list(range(n)))
    res = {sorted_tickers[i]: float(weights[i]) for i in range(n)}
    total = sum(res.values()) + 1e-12
    return {t: w / total for t, w in res.items()}


def _hrp_recursive_bisect(cov: np.ndarray, items: list) -> np.ndarray:
    n_total = cov.shape[0]
    weights = np.ones(n_total)

    def _bisect(items_subset, alpha):
        if len(items_subset) <= 1:
            return
        mid = len(items_subset) // 2
        left, right = items_subset[:mid], items_subset[mid:]

        def _cluster_var(idx_list):
            sub = cov[np.ix_(idx_list, idx_list)]
            inv_diag = 1.0 / (np.diag(sub) + 1e-12)
            w = inv_diag / inv_diag.sum()
            return float(w @ sub @ w)

        v_l, v_r = _cluster_var(left), _cluster_var(right)
        denom = v_l + v_r + 1e-12
        for i in left:  weights[i] *= (1.0 - v_l / denom) * alpha
        for i in right: weights[i] *= (1.0 - v_r / denom) * alpha
        _bisect(left, 1.0)
        _bisect(right, 1.0)

    _bisect(items, 1.0)
    return weights / (weights.sum() + 1e-12)


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

    # CAPE / ERP：僅適用於股票 ETF（VOO / QQQ）
    cape, erp, pred = None, None, None
    if ticker in ("VOO", "QQQ"):
        try:
            from src.data_fetcher import fetch_cape_erp
            if hasattr(_calc_etf_signal, "_av_key") and _calc_etf_signal._av_key:
                cape, erp, pred = fetch_cape_erp(
                    ticker,
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
# 單一夜盤：VOO + QQQ + GLD + VGIT + GRID
# ─────────────────────────────────────────────

ETF_UNIVERSE = [
    ("VOO",  "Vanguard S&P 500"),
    ("QQQ",  "Invesco QQQ Trust"),
    ("GLD",  "SPDR Gold Shares"),
    ("VGIT", "Vanguard Intermediate-Term Treasury"),
    ("TYD",  "Direxion Daily 7-10 Year Treasury Bull 3x"),
    ("GRID", "First Trust NASDAQ Clean Edge Smart Grid"),
]


def run_evening_session(fred_key: str, av_key) -> tuple:
    """
    夜盤模式：22:00 TST（單一會話，覆蓋全部 6 檔 ETF）
    Returns: (MacroIndicators, list[ETFSignal], float vix, dict hrp_weights, tuple tyd_timing)
    """
    print("=" * 50)
    print("夜盤模式：22:00 TST（VOO / QQQ / GLD / VGIT / TYD / GRID）")
    print("=" * 50)

    macro, vix, vix_upper, vix_break = fetch_macro(fred_key, av_key)
    _calc_etf_signal._av_key = av_key

    prices_dict = {}   # 供 HRP 計算
    etf_signals = []

    for ticker, name in ETF_UNIVERSE:
        print(f"  [ETF] {ticker} (Yahoo Finance)...")
        prices = yahoo_daily_close(ticker, days=400)
        if prices.empty or len(prices) < 5:
            print(f"  [ETF] {ticker} Yahoo 無資料，改用 Alpha Vantage...")
            prices = av_daily_close(ticker, av_key, days=400)
        if prices.empty or len(prices) < 5:
            print(f"  [WARN] {ticker} 所有來源均無資料，跳過")
            continue

        prices_dict[ticker] = prices
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

    # TYD 買入時機評分（需 VGIT RSI）
    vgit_sig  = next((s for s in etf_signals if s.ticker == "VGIT"), None)
    vgit_rsi  = vgit_sig.rsi if vgit_sig else 50.0
    tyd_score, tyd_label = calc_tyd_timing(macro.us10y, macro.yield_curve, vgit_rsi)
    tyd_timing = (tyd_score, tyd_label)
    print(f"  [TYD] 時機評分={tyd_score} → {tyd_label}")

    # 三群組 HRP 配置
    hrp_weights = {}
    if len(prices_dict) >= 2:
        try:
            hrp_weights = calc_hrp_weights(prices_dict, tyd_score=tyd_score)
            print(f"  [HRP] {hrp_weights}")
        except Exception as e:
            print(f"  [HRP] 計算失敗：{e}")
            n = len(prices_dict)
            hrp_weights = {t: round(1.0/n, 4) for t in prices_dict}

    return macro, etf_signals, vix, hrp_weights, tyd_timing


# ─────────────────────────────────────────────
# 市場體制偵測器（純規則，不依賴 LLM）
# ─────────────────────────────────────────────

def detect_market_regime(macro: MacroIndicators) -> dict:
    """
    依信用面、景氣面、就業面、殖利率曲線四維度判斷市場體制。
    Returns:
        regime:         "RISK_ON" | "TRANSITION" | "RISK_OFF"
        credit:         "正常" | "偏緊" | "緊縮"
        pmi_trend:      "加速擴張" | "減速擴張" | "觸底回升" | "加速收縮"
        curve:          "正常" | "倒掛" | "深度倒掛"
        recession_flag: bool
    """
    # 信用面
    if macro.hy_spread > 5.0:
        credit, credit_score = "緊縮", 0
    elif macro.hy_spread > 4.0:
        credit, credit_score = "偏緊", 1
    else:
        credit, credit_score = "正常", 2

    # 景氣面（PMI 水位 + 二階導數）
    pmi_val = macro.ism_pmi[-1] if macro.ism_pmi else 50.0
    if macro.pmi_second_deriv > 0 and pmi_val >= 50:
        pmi_trend, pmi_score = "加速擴張", 2
    elif macro.pmi_second_deriv < 0 and pmi_val >= 50:
        pmi_trend, pmi_score = "減速擴張", 1
    elif macro.pmi_second_deriv > 0 and pmi_val < 50:
        pmi_trend, pmi_score = "觸底回升", 1
    else:
        pmi_trend, pmi_score = "加速收縮", 0

    # 就業面（薩姆 or 米切茲）
    recession_flag = macro.sahm_triggered or macro.michez_triggered

    # 殖利率曲線
    if macro.yield_curve < -0.5:
        curve, curve_score = "深度倒掛", 0
    elif macro.yield_curve < 0:
        curve, curve_score = "倒掛", 1
    else:
        curve, curve_score = "正常", 2

    # 綜合體制裁決
    if recession_flag or (credit_score == 0 and curve_score == 0):
        regime = "RISK_OFF"
    elif credit_score == 2 and pmi_score >= 1 and not recession_flag:
        regime = "RISK_ON"
    else:
        regime = "TRANSITION"

    return {
        "regime":         regime,
        "credit":         credit,
        "pmi_trend":      pmi_trend,
        "curve":          curve,
        "recession_flag": recession_flag,
    }


def calc_signal_light(sig: ETFSignal, regime: dict) -> dict:
    """
    三色信號燈 — 純量化規則，禁止 LLM 介入結論。
    Returns {"light": "🟢 加碼"|"🟡 維持"|"🔴 減碼", "reason": str}
    """
    score   = 0
    reasons = []

    # ① MACD 柱（趨勢動能）
    if (sig.macd_hist or 0) > 0:
        score += 1
        reasons.append("MACD擴大")

    # ② RSI 健康動能區間 45~68
    if 45 <= sig.rsi <= 68:
        score += 1
        reasons.append(f"RSI{sig.rsi:.0f}正常")

    # ③ Z-Score 回歸區間 -0.5~1.5
    if -0.5 <= sig.z_score <= 1.5:
        score += 1
        reasons.append(f"Z={sig.z_score:+.1f}")

    # ④ 體制乘數（最關鍵）
    if regime["regime"] == "RISK_OFF":
        score = min(score, 1)          # 強制壓低至不超過「維持」
        reasons.append("RISK_OFF壓制")
    elif regime["regime"] == "RISK_ON":
        reasons.append("RISK_ON順風")
    else:
        reasons.append("TRANSITION觀望")

    # ⑤ 估值懲罰（ERP < 1%）
    if sig.erp is not None and sig.erp < 1.0:
        score -= 1
        reasons.append(f"ERP{sig.erp:+.2f}%低")

    # ⑥ VIX 恐慌
    if sig.vix_bollinger_break:
        score -= 1
        reasons.append("VIX破布林上軌")

    if score >= 3:
        light = "🟢 加碼"
    elif score >= 1:
        light = "🟡 維持"
    else:
        light = "🔴 減碼"

    return {"light": light, "reason": "、".join(reasons[:4])}


# ─────────────────────────────────────────────
# 尾端風險清單（硬編碼，可枚舉的已知風險）
# ─────────────────────────────────────────────

TAIL_RISK_CHECKLIST = [
    {
        "id":        "yield_curve_deep_invert",
        "condition": lambda m, _s: m.yield_curve < -0.5,
        "warning":   lambda m, _s: (
            f"殖利率曲線深度倒掛（{m.yield_curve:+.2f}%），"
            f"歷史上12-18個月後衰退機率>70%"
        ),
    },
    {
        "id":        "sahm_trigger",
        "condition": lambda m, _s: m.sahm_triggered,
        "warning":   lambda m, _s: (
            f"薩姆法則觸發（{m.sahm_indicator:.2f}%），"
            f"就業惡化反射性循環已啟動"
        ),
    },
    {
        "id":        "hy_spread_spike",
        "condition": lambda m, _s: m.hy_spread > 5.0,
        "warning":   lambda m, _s: (
            f"HY利差={m.hy_spread:.2f}%（信用恐慌），"
            f"歷史上股市距底部通常仍遠"
        ),
    },
    {
        "id":        "hy_spread_elevated",
        "condition": lambda m, _s: 4.0 < m.hy_spread <= 5.0,
        "warning":   lambda m, _s: (
            f"HY利差偏高（{m.hy_spread:.2f}%），"
            f"信用市場偏緊，留意擴散風險"
        ),
    },
    {
        "id":        "cape_extreme",
        "condition": lambda _m, sigs: any((s.cape or 0) > 32 for s in sigs),
        "warning":   lambda _m, sigs: "；".join(
            f"{s.ticker} CAPE={s.cape:.1f}x（估值歷史極高）"
            for s in sigs if (s.cape or 0) > 32
        ),
    },
    {
        "id":        "vix_bollinger",
        "condition": lambda _m, sigs: any(s.vix_bollinger_break for s in sigs),
        "warning":   lambda _m, sigs: (
            f"VIX突破布林上軌（{sigs[0].vix:.1f}），"
            f"市場恐慌情緒急升，短期波動率風險高"
        ),
    },
    {
        "id":        "recession_high_prob",
        "condition": lambda m, _s: m.recession_prob > 40,
        "warning":   lambda m, _s: (
            f"衰退機率={m.recession_prob:.0f}%（超40%警戒線），"
            f"建議審視風險資產部位"
        ),
    },
]


def eval_tail_risks(macro: MacroIndicators, etf_signals: list) -> list:
    """
    評估所有尾端風險，返回已觸發的警報列表。
    Returns: list[{"id": str, "warning": str}]
    """
    triggered = []
    for item in TAIL_RISK_CHECKLIST:
        try:
            if item["condition"](macro, etf_signals):
                triggered.append({
                    "id":      item["id"],
                    "warning": item["warning"](macro, etf_signals),
                })
        except Exception:
            pass
    return triggered
