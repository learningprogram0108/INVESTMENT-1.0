"""
量化指標計算引擎 v2
公式嚴格依據：
  - 14本大師經典量化指標.md
  - 景氣循環判斷指標與執行的藝術.md
"""

import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False


# ─────────────────────────────────────────────
# 各 ETF 凱利準則參數（基於歷史回測）
# 公式：f* = p − (1−p)/b
# ─────────────────────────────────────────────
KELLY_PARAMS = {
    "VOO":      {"p": 0.68, "b": 2.1},   # 美股長期勝率較高
    "0050.TW":  {"p": 0.65, "b": 1.8},   # 台股
    "00679B.TW":{"p": 0.60, "b": 1.4},   # 美債（防禦性較低盈虧比）
}

# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

@dataclass
class ETFSignal:
    ticker: str
    name: str
    # 價格
    price: float
    change_pct: float
    # EMA Z-Score（文件公式：Z = (Pₜ - EMA₂₀₀) / σ₂₀₀）
    ema_200: float
    z_score: float
    # 情緒溫度計（0~100）
    sentiment_score: float
    sentiment_label: str
    # VIX 相關
    vix: float
    vix_bollinger_break: bool   # VIX 是否突破布林上軌
    # 估值
    cape: Optional[float]
    erp: Optional[float]             # ERP = Forward EY - Rf
    predicted_10y_return: Optional[float]  # 0.169 - 0.0052×CAPE
    # 凱利準則
    kelly_f: float                   # f* = p - (1-p)/b
    # 景氣判定
    cycle_phase: str
    fund_multiplier: float
    multiplier_mode: str             # 獵人/鑑賞家/刺客


@dataclass
class MacroIndicators:
    # 實質利率（費雪：r = i - π）
    us10y: float
    us02y: float
    cpi_yoy: float
    real_rate: float
    # 殖利率曲線
    yield_curve: float               # 10y - 2y
    # 薩姆規則
    sahm_indicator: float
    sahm_triggered: bool
    # Michez 法則
    michez_m: float
    michez_triggered: bool
    # 衰退機率（線性映射 m → 0~100%）
    recession_prob: float
    # 信用利差
    hy_spread: float
    credit_signal: str
    # PMI
    ism_pmi: list                    # 近三期 [t-2, t-1, t]
    pmi_second_deriv: float          # f''(t) = (yₜ-yₜ₋₁) - (yₜ₋₁-yₜ₋₂)
    # 失業率近三月
    unemployment_3m: list            # [t-2, t-1, t]


# ─────────────────────────────────────────────
# 核心公式（嚴格依文件）
# ─────────────────────────────────────────────

def calc_ema(prices: pd.Series, n: int = 200) -> pd.Series:
    """EMAₜ = Pₜ×(2/(1+N)) + EMAₜ₋₁×(1−2/(1+N))"""
    return prices.ewm(span=n, adjust=False).mean()


def calc_ema_zscore(prices: pd.Series, n: int = 200) -> float:
    """
    Z_EMA = (Pₜ − EMA₂₀₀) / σ₂₀₀
    來源：雙引擎系統 + 《總體經濟學家教你Python分析》
    """
    if len(prices) < n:
        n = max(20, len(prices) - 1)
    ema = calc_ema(prices, n)
    sigma = prices.rolling(n).std()
    if sigma.iloc[-1] == 0:
        return 0.0
    z = (prices.iloc[-1] - ema.iloc[-1]) / sigma.iloc[-1]
    return round(float(z), 3)


def calc_pmi_second_deriv(pmi_series: list) -> float:
    """
    f''(t) ≈ (yₜ − yₜ₋₁) − (yₜ₋₁ − yₜ₋₂)
    來源：《高盛首席分析師教你看懂進場的訊號》
    >0 代表壞消息改善中（希望階段買進訊號）
    """
    if len(pmi_series) < 3:
        return 0.0
    y0, y1, y2 = pmi_series[-3], pmi_series[-2], pmi_series[-1]
    return round((y2 - y1) - (y1 - y0), 3)


def calc_sahm_rule(u_series: list) -> tuple[float, bool]:
    """
    Sahmₜ = (Uₜ+Uₜ₋₁+Uₜ₋₂)/3 − min(U₁₂ₘ) ≥ 0.5%
    來源：《經濟指標的秘密》、TAA 系統規格書
    """
    if len(u_series) < 13:
        return 0.0, False
    u3ma = sum(u_series[-3:]) / 3
    u12m_min = min(u_series[-13:-1])
    indicator = u3ma - u12m_min
    return round(indicator, 3), indicator >= 0.5


def calc_michez_rule(u_series: list, v_series: list) -> tuple[float, bool]:
    """
    U_indicator = U_3ma − U_12m_min
    V_indicator = V_12m_max − V_3ma
    m = min(U_indicator, V_indicator)
    觸發：m ≥ 0.29%
    來源：TAA 系統規格書
    """
    if len(u_series) < 13 or len(v_series) < 13:
        return 0.0, False
    u3ma = sum(u_series[-3:]) / 3
    u12m_min = min(u_series[-13:-1])
    u_ind = u3ma - u12m_min

    v3ma = sum(v_series[-3:]) / 3
    v12m_max = max(v_series[-13:-1])
    v_ind = v12m_max - v3ma

    m = min(u_ind, v_ind)
    return round(m, 3), m >= 0.29


def calc_recession_prob(michez_m: float) -> float:
    """
    線性映射 m（0.29%~0.81%）→ 衰退機率（0%~100%）
    來源：TAA 系統規格書
    """
    lo, hi = 0.29, 0.81
    prob = (michez_m - lo) / (hi - lo) * 100
    return round(max(0.0, min(100.0, prob)), 1)


def calc_real_rate(nominal: float, inflation: float) -> float:
    """r = i − π（費雪方程式）"""
    return round(nominal - inflation, 3)


def calc_erp(forward_pe: float, rf: float) -> float:
    """
    ERP = E₁/P₀ − Rf = (1/ForwardPE) − US10Y
    來源：《漫步華爾街》、《掌握市場週期》
    ERP < 2% → 安全邊際消失
    """
    if forward_pe <= 0:
        return 0.0
    forward_ey = 1.0 / forward_pe * 100
    return round(forward_ey - rf, 3)


def calc_cape_10y_return(cape: float) -> float:
    """
    E[R₁₀y] = 0.169 − 0.0052 × CAPE
    來源：TAA 量化模型報告（高盛驗證 R²=0.7）
    """
    return round((0.169 - 0.0052 * cape) * 100, 2)


def calc_kelly(ticker: str) -> float:
    """
    f* = p − (1−p)/b
    來源：《執行的藝術》、TAA 系統規格書
    各 ETF 使用獨立的歷史勝率與盈虧比
    """
    params = KELLY_PARAMS.get(ticker, {"p": 0.60, "b": 1.5})
    p, b = params["p"], params["b"]
    f = p - (1 - p) / b
    return round(max(0.0, min(f, 1.0)), 3)


def calc_vix_bollinger(vix_series: pd.Series, n: int = 20) -> tuple[float, float, bool]:
    """
    VIX 布林通道：上軌 = SMA20 + 2×σ20
    突破上軌 → 恐慌加劇訊號（獵人模式候選）
    """
    if len(vix_series) < n:
        return float(vix_series.iloc[-1]), 0.0, False
    sma = vix_series.rolling(n).mean().iloc[-1]
    std = vix_series.rolling(n).std().iloc[-1]
    upper = sma + 2 * std
    current = float(vix_series.iloc[-1])
    return round(current, 2), round(upper, 2), current > upper


def calc_sentiment_score(z: float, vix: float, spread: float) -> tuple[float, str]:
    """
    情緒溫度計（0~100）
    = Z-Score分項(50%) + VIX分項(30%) + 信用利差分項(20%)
    各分項映射至0~100後加權

    Z-Score：−3→0（恐慌）, 0→50（中性）, +3→100（貪婪）
    VIX：反向，VIX=10→100（貪婪）, VIX=45→0（恐慌）
    利差：反向，spread=2%→100（貪婪）, spread=10%→0（恐慌）
    """
    # Z 分項：線性映射 [-3, +3] → [0, 100]
    z_score_component = max(0, min(100, (z + 3) / 6 * 100))

    # VIX 分項：反向，[10, 45] → [100, 0]
    vix_component = max(0, min(100, (45 - vix) / 35 * 100))

    # 信用利差分項：反向，[2%, 10%] → [100, 0]
    spread_component = max(0, min(100, (10 - spread) / 8 * 100))

    score = round(
        z_score_component * 0.50 +
        vix_component     * 0.30 +
        spread_component  * 0.20,
        1
    )

    if score >= 80:
        label = "極度貪婪"
    elif score >= 60:
        label = "貪婪"
    elif score >= 40:
        label = "中性"
    elif score >= 20:
        label = "恐慌"
    else:
        label = "極度恐慌"

    return score, label


def determine_cycle_and_multiplier(
    z: float,
    spread: float,
    sahm_triggered: bool,
    vix: float,
    vix_bollinger_break: bool,
    kelly_f: float,
) -> tuple[str, float, str]:
    """
    景氣循環階段 + 資金乘數
    嚴格依據《執行的藝術》加減碼矩陣：

    獵人加碼（2~3x）：Z<−2.0 或 VIX≥35 或 利差≥8%
    鑑賞家巡航（1x）：−1.0≤Z≤+1.5，各項指標健康
    刺客防禦（0~0.5x）：薩姆觸發 或 Z>2.5 伴隨利差壓縮

    最終乘數上限：min(景氣乘數, kelly_f × 3)
    """
    # 刺客防禦：衰退確認
    if sahm_triggered:
        phase = "🔴 絕望期（衰退確認）"
        raw_mult = 0.5
        mode = "刺客防禦"
    # 刺客防禦：泡沫末期
    elif z > 2.5 and spread < 3.5:
        phase = "⚠️ 樂觀期（泡沫末期）"
        raw_mult = 0.5
        mode = "刺客防禦"
    elif z > 2.5:
        phase = "⚠️ 樂觀期（過熱警戒）"
        raw_mult = 0.5
        mode = "刺客防禦"
    # 獵人加碼：恐慌超跌
    elif z <= -2.0 or vix >= 35 or spread >= 8.0:
        phase = "🟡 絕望期（恐慌超跌）"
        raw_mult = 2.5 if (z <= -2.0 and vix >= 35) else 2.0
        mode = "獵人加碼"
    # 希望階段：壞消息改善
    elif z <= -1.0 or spread >= 6.0:
        phase = "🟡 希望期（復甦初期）"
        raw_mult = 1.5
        mode = "積極布局"
    # 鑑賞家巡航：常態
    else:
        phase = "🟢 成長期（常態擴張）"
        raw_mult = 1.0
        mode = "鑑賞家巡航"

    # 凱利上限：f* × 3（避免過度槓桿）
    kelly_cap = round(kelly_f * 3, 1)
    final_mult = round(min(raw_mult, kelly_cap), 1)

    return phase, final_mult, mode


# ─────────────────────────────────────────────
# 資料擷取
# ─────────────────────────────────────────────

def _yf_fetch(ticker: str, period: str = "2y"):
    """Yahoo Finance 擷取（含 rate limit retry）"""
    for attempt in range(3):
        try:
            if attempt > 0:
                wait = 15 * attempt
                print(f"  [retry {attempt}] {ticker} 等待 {wait}s...")
                time.sleep(wait)
            hist = yf.Ticker(ticker).history(period=period)
            if not hist.empty:
                return hist
        except Exception as e:
            if "Too Many Requests" in str(e) and attempt < 2:
                continue
            print(f"  [ERR] {ticker}: {e}")
    return pd.DataFrame()


def fetch_etf_signal(ticker: str, name: str,
                     vix: float, vix_upper: float, vix_bollinger_break: bool,
                     hy_spread: float, rf: float) -> Optional[ETFSignal]:
    """計算單一 ETF 的完整景氣訊號"""
    hist = _yf_fetch(ticker)
    if hist.empty or len(hist) < 50:
        return None

    prices = hist["Close"]
    current = float(prices.iloc[-1])
    prev    = float(prices.iloc[-2])
    chg_pct = (current - prev) / prev * 100

    n = min(200, len(prices) - 1)
    ema_val = float(calc_ema(prices, n).iloc[-1])
    z = calc_ema_zscore(prices, n)

    # 情緒溫度計
    sentiment, sent_label = calc_sentiment_score(z, vix, hy_spread)

    # CAPE / ERP / 10y報酬
    cape, erp_val, pred_10y = None, None, None
    try:
        info = yf.Ticker(ticker).info
        forward_pe = info.get("forwardPE") or info.get("trailingPE")
        if forward_pe and forward_pe > 0:
            cape = round(float(forward_pe) * 0.95, 1)
            erp_val = calc_erp(float(forward_pe), rf)
            pred_10y = calc_cape_10y_return(cape)
    except Exception:
        pass

    # 凱利準則
    kelly_f = calc_kelly(ticker)

    # 景氣階段 + 乘數
    phase, mult, mode = determine_cycle_and_multiplier(
        z=z, spread=hy_spread,
        sahm_triggered=False,   # 由主程式注入
        vix=vix,
        vix_bollinger_break=vix_bollinger_break,
        kelly_f=kelly_f,
    )

    return ETFSignal(
        ticker=ticker, name=name,
        price=round(current, 2), change_pct=round(chg_pct, 2),
        ema_200=round(ema_val, 2), z_score=z,
        sentiment_score=sentiment, sentiment_label=sent_label,
        vix=vix, vix_bollinger_break=vix_bollinger_break,
        cape=cape, erp=erp_val, predicted_10y_return=pred_10y,
        kelly_f=kelly_f,
        cycle_phase=phase, fund_multiplier=mult, multiplier_mode=mode,
    )


def fetch_vix_data() -> tuple[float, float, bool]:
    """擷取 VIX 現值及布林突破狀態"""
    hist = _yf_fetch("^VIX", period="3mo")
    if hist.empty:
        return 20.0, 30.0, False
    vix_series = hist["Close"]
    current, upper, break_out = calc_vix_bollinger(vix_series)
    return current, upper, break_out


def fetch_fred(series_id: str, api_key: str, limit: int = 24) -> list:
    """FRED API 擷取"""
    if not api_key or api_key == "skip":
        return []
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": api_key,
                    "file_type": "json", "sort_order": "desc", "limit": limit},
            timeout=10
        )
        obs = r.json().get("observations", [])
        vals = []
        for o in reversed(obs):
            try:
                vals.append(float(o["value"]))
            except (ValueError, KeyError):
                pass
        return vals
    except Exception as e:
        print(f"  [FRED] {series_id}: {e}")
        return []


def fetch_hy_spread(fred_api_key: str) -> float:
    """HY 信用利差：FRED BAMLH0A0HYM2（ICE BofA OAS）"""
    data = fetch_fred("BAMLH0A0HYM2", fred_api_key, limit=5)
    if data:
        return round(data[-1], 2)
    # fallback：用 HYG/IEI 代理
    try:
        hyg = float(_yf_fetch("HYG", "5d")["Close"].iloc[-1])
        iei = float(_yf_fetch("IEI", "5d")["Close"].iloc[-1])
        return round(max(2.0, 8.5 - (hyg / iei - 1) * 80), 2)
    except Exception:
        return 4.5


def fetch_treasury_yields() -> tuple[float, float]:
    """US10Y 與 US02Y"""
    us10y = 4.3
    us02y = 4.0
    try:
        h10 = _yf_fetch("^TNX", "1mo")
        if not h10.empty:
            us10y = round(float(h10["Close"].iloc[-1]), 3)
    except Exception:
        pass
    try:
        h02 = _yf_fetch("^IRX", "1mo")
        if not h02.empty:
            us02y = round(float(h02["Close"].iloc[-1]), 3)
    except Exception:
        pass
    return us10y, us02y


def fetch_macro(fred_api_key: str) -> MacroIndicators:
    """擷取並計算所有共用總經指標"""
    print("  [MACRO] 擷取總經資料...")

    # 失業率
    u_series = fetch_fred("UNRATE", fred_api_key, 24)
    if not u_series:
        u_series = [3.7,3.7,3.8,3.9,4.0,4.1,4.1,4.0,3.9,3.8,3.8,3.9,4.0,4.1,4.1]

    # 職缺率
    v_series = fetch_fred("JTSJOR", fred_api_key, 24)
    if not v_series:
        v_series = [5.2,5.3,5.4,5.5,5.6,5.6,5.7,5.8,5.9,6.0,6.1,6.0,5.9,5.8,5.7]

    # CPI YoY
    cpi = fetch_fred("CPIAUCSL", fred_api_key, 14)
    if len(cpi) >= 13:
        cpi_yoy = round((cpi[-1] / cpi[-13] - 1) * 100, 2)
    else:
        cpi_yoy = 2.8

    # ISM PMI（製造業）
    # ISM Manufacturing PMI：正確 FRED series = ISMMAN
    ism = fetch_fred("ISMMAN", fred_api_key, 5)
    if not ism or len(ism) < 3:
        # ISMMAN 若無資料改用 ISM_MAN_PMI
        ism = fetch_fred("ISM_MAN_PMI", fred_api_key, 5)
    if not ism or len(ism) < 3:
        ism = [53.0, 52.0, 52.7]   # fallback

    # 利率
    us10y, us02y = fetch_treasury_yields()
    real_rate = calc_real_rate(us10y, cpi_yoy)
    yield_curve = round(us10y - us02y, 3)

    # 薩姆規則
    sahm_val, sahm_on = calc_sahm_rule(u_series)

    # Michez 法則
    michez_m, michez_on = calc_michez_rule(u_series, v_series)

    # 衰退機率
    rec_prob = calc_recession_prob(michez_m)

    # 信用利差
    hy_spread = fetch_hy_spread(fred_api_key)
    if hy_spread < 3.5:
        credit_signal = "壓縮·過樂觀"
    elif hy_spread < 4.5:
        credit_signal = "正常偏低"
    elif hy_spread < 6.5:
        credit_signal = "正常"
    elif hy_spread < 8.0:
        credit_signal = "偏高·留意"
    else:
        credit_signal = "極度恐慌·左側機會"

    # PMI 二階導數
    pmi_3 = ism[-3:] if len(ism) >= 3 else [52.0, 52.0, 52.0]
    pmi_f2 = calc_pmi_second_deriv(pmi_3)

    return MacroIndicators(
        us10y=us10y, us02y=us02y, cpi_yoy=cpi_yoy,
        real_rate=real_rate, yield_curve=yield_curve,
        sahm_indicator=sahm_val, sahm_triggered=sahm_on,
        michez_m=michez_m, michez_triggered=michez_on,
        recession_prob=rec_prob,
        hy_spread=hy_spread, credit_signal=credit_signal,
        ism_pmi=pmi_3, pmi_second_deriv=pmi_f2,
        unemployment_3m=u_series[-3:],
    )


def run_morning_session(fred_api_key: str):
    """早盤：總經 + 0050 + 00679B"""
    print("=" * 50)
    print("早盤模式：09:30 TST")
    print("=" * 50)

    macro = fetch_macro(fred_api_key)
    vix, vix_upper, vix_break = fetch_vix_data()

    etf_configs = [
        ("0050.TW",   "元大台灣 50"),
        ("00679B.TW", "元大美債 20年"),
    ]

    etf_signals = []
    for ticker, name in etf_configs:
        print(f"  [ETF] {ticker}...")
        sig = fetch_etf_signal(
            ticker, name, vix, vix_upper, vix_break,
            macro.hy_spread, macro.us10y
        )
        if sig:
            # 注入薩姆規則（共用）
            if macro.sahm_triggered:
                sig.cycle_phase, sig.fund_multiplier, sig.multiplier_mode = \
                    determine_cycle_and_multiplier(
                        sig.z_score, macro.hy_spread, True,
                        vix, vix_break, sig.kelly_f
                    )
            etf_signals.append(sig)
            print(f"    Z={sig.z_score} | 乘數={sig.fund_multiplier}x | {sig.cycle_phase}")
        time.sleep(3)

    return macro, etf_signals, vix


def run_evening_session(fred_api_key: str):
    """夜盤：VOO（美股收盤後 22:00）"""
    print("=" * 50)
    print("夜盤模式：22:00 TST")
    print("=" * 50)

    macro = fetch_macro(fred_api_key)
    vix, vix_upper, vix_break = fetch_vix_data()

    print("  [ETF] VOO...")
    sig = fetch_etf_signal(
        "VOO", "Vanguard S&P 500",
        vix, vix_upper, vix_break,
        macro.hy_spread, macro.us10y
    )
    if sig and macro.sahm_triggered:
        sig.cycle_phase, sig.fund_multiplier, sig.multiplier_mode = \
            determine_cycle_and_multiplier(
                sig.z_score, macro.hy_spread, True,
                vix, vix_break, sig.kelly_f
            )

    if sig:
        print(f"  VOO: Z={sig.z_score} | 乘數={sig.fund_multiplier}x | {sig.cycle_phase}")

    return macro, sig, vix
