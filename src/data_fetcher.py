"""
資料擷取層 v5 — OpenBB Platform 統一 API 串接
Primary  : OpenBB SDK (yfinance / fred / alpha_vantage providers)
Fallback : 直接呼叫各 API（保留可靠性）

四種資料類型全覆蓋：
  1. ETF/股票歷史價格  → obb.equity.price.historical()
  2. 總經指標 (FRED)   → obb.economy.fred_series()
  3. 財務新聞標題      → obb.equity.news()
  4. 殖利率 / 利差     → obb.fixedincome.government.treasury_rates() + FRED BAMLH0A0HYM2
"""

import time
import logging
import warnings
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

logging.getLogger("openbb").setLevel(logging.WARNING)
logging.getLogger("openbb_core").setLevel(logging.WARNING)
logging.getLogger("openbb_yfinance").setLevel(logging.WARNING)

AV_BASE = "https://www.alphavantage.co/query"

# ── OpenBB singleton ─────────────────────────────────────────────────────────
# _obb = None  → 尚未嘗試載入
# _obb = False → 已嘗試但 import 失敗（不再重試）
# _obb = <obj> → 已載入，可用

_obb = None


def _get_obb():
    """懶載入 OpenBB，失敗後不再重試。回傳 obb 物件或 None。"""
    global _obb
    if _obb is None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from openbb import obb as _o
            _obb = _o
            print("  [OBB] OpenBB Platform 載入成功")
        except Exception:
            _obb = False
            print("  [OBB] openbb 未安裝，使用直接 API fallback")
    return _obb if _obb is not False else None


def setup_openbb_credentials(fred_key: str = "", av_key: str = "") -> None:
    """
    在 main() 啟動時呼叫一次，設定 OpenBB 全域憑證。
    之後各函式仍會在每次呼叫時確保憑證正確（多 key 輪換支援）。
    """
    obb = _get_obb()
    if not obb:
        return
    try:
        if fred_key:
            obb.user.credentials.fred_api_key = fred_key
            print(f"  [OBB] FRED key 設定 ...{fred_key[-4:]}")
        if av_key:
            k = av_key[0] if isinstance(av_key, list) else av_key
            obb.user.credentials.alpha_vantage_api_key = k
            print(f"  [OBB] AV key 設定 ...{k[-4:]}")
    except Exception as e:
        print(f"  [OBB] 憑證設定失敗：{e}")


# ── ① ETF/股票歷史價格 ──────────────────────────────────────────────────────

def yahoo_daily_close(symbol: str, days: int = 400) -> pd.Series:
    """
    OpenBB (yfinance) → Yahoo Finance 日線收盤價 → pd.Series（時間升冪）
    Fallback: 直接呼叫 Yahoo Finance v8 Chart API
    """
    obb = _get_obb()
    if obb:
        try:
            start = (datetime.today() - timedelta(days=max(days + 60, 600))).strftime("%Y-%m-%d")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = obb.equity.price.historical(
                    symbol, start_date=start, provider="yfinance"
                )
            df = result.to_dataframe()
            if "date" in df.columns:
                df = df.set_index("date")
            if not df.empty and "close" in df.columns:
                df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
                series = pd.Series(df["close"].values, index=df.index).sort_index()
                series = series[pd.to_numeric(series, errors="coerce").gt(0)].tail(days)
                if len(series) >= 5:
                    print(f"  [OBB] {symbol} {len(series)} 筆，最新={series.iloc[-1]:.2f}")
                    return series
        except Exception as e:
            print(f"  [OBB] {symbol} price: {e}，fallback")

    return _yahoo_direct(symbol, days)


def av_daily_close(symbol: str, av_key, days: int = 400) -> pd.Series:
    """
    OpenBB (alpha_vantage) → Alpha Vantage 日線收盤價
    Fallback: 直接呼叫 AV TIME_SERIES_DAILY
    """
    obb = _get_obb()
    if obb:
        try:
            k = (av_key[0] if isinstance(av_key, list) else av_key) or ""
            if k:
                obb.user.credentials.alpha_vantage_api_key = k
            start = (datetime.today() - timedelta(days=max(days + 60, 600))).strftime("%Y-%m-%d")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = obb.equity.price.historical(
                    symbol, start_date=start, provider="alpha_vantage"
                )
            df = result.to_dataframe()
            if "date" in df.columns:
                df = df.set_index("date")
            if not df.empty and "close" in df.columns:
                df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
                series = pd.Series(df["close"].values, index=df.index).sort_index()
                series = series[pd.to_numeric(series, errors="coerce").gt(0)].tail(days)
                if len(series) >= 5:
                    print(f"  [OBB/AV] {symbol} {len(series)} 筆")
                    return series
        except Exception as e:
            print(f"  [OBB/AV] {symbol}: {e}，fallback")

    return _av_direct_daily(symbol, av_key, days)


# ── ② 總經指標（FRED）─────────────────────────────────────────────────────────

def fetch_fred(series_id: str, api_key: str, limit: int = 24) -> list:
    """
    OpenBB (fred) → FRED series → list[float]（時間升冪）
    Fallback: 直接呼叫 FRED REST API
    """
    if not api_key or api_key.lower() == "skip":
        return []

    obb = _get_obb()
    if obb:
        try:
            obb.user.credentials.fred_api_key = api_key
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = obb.economy.fred_series(series_id, limit=limit, provider="fred")
            df = result.to_dataframe().sort_index()
            # OpenBB 回傳欄位名稱可能是 series_id 或 "value"
            numeric = df.select_dtypes(include="number")
            if not numeric.empty:
                vals = pd.to_numeric(
                    numeric.iloc[:, 0], errors="coerce"
                ).dropna().tolist()
                if vals:
                    print(f"  [OBB/FRED] {series_id} {len(vals)} 筆")
                    return [float(v) for v in vals[-limit:]]
        except Exception as e:
            print(f"  [OBB/FRED] {series_id}: {e}，fallback")

    return _fred_direct(series_id, api_key, limit)


# ── ③ 財務新聞標題 ────────────────────────────────────────────────────────────

def yahoo_news_headlines(symbol: str, limit: int = 5) -> list:
    """
    OpenBB (yfinance) → equity.news → list[dict{"title","url"}]
    Fallback: 直接呼叫 Yahoo Finance Search API
    """
    obb = _get_obb()
    if obb:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = obb.equity.news(symbol, limit=limit, provider="yfinance")
            df = result.to_dataframe()
            items = []
            title_col = next((c for c in ("title", "headline", "summary", "text") if c in df.columns), None)
            url_col   = next((c for c in ("url", "link", "article_url") if c in df.columns), None)
            if title_col:
                rows = df.head(limit)
                for _, row in rows.iterrows():
                    t = str(row[title_col]) if pd.notna(row[title_col]) else ""
                    u = str(row[url_col]) if url_col and pd.notna(row[url_col]) else ""
                    if t:
                        items.append({"title": t, "url": u})
            if items:
                print(f"  [OBB/News] {symbol} {len(items)} 則")
                return items
        except Exception as e:
            print(f"  [OBB/News] {symbol}: {e}，fallback")

    return _yahoo_news_direct(symbol, limit)


# ── ④ 殖利率 / 利差 ────────────────────────────────────────────────────────────

def fetch_treasury_av(av_key) -> tuple[float, float]:
    """
    OpenBB (alpha_vantage) → treasury_rates → (US10Y%, US02Y%)
    Fallback: 直接呼叫 AV TREASURY_YIELD × 2
    """
    obb = _get_obb()
    if obb:
        try:
            k = (av_key[0] if isinstance(av_key, list) else av_key) or ""
            if k:
                obb.user.credentials.alpha_vantage_api_key = k
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = obb.fixedincome.government.treasury_rates(
                    provider="alpha_vantage"
                )
            df = result.to_dataframe().sort_index()
            if not df.empty:
                row = df.iloc[-1]
                # AV 欄位名稱可能是 year_10, 10year, T10YIE 等
                _try = lambda keys: next(
                    (float(row[c]) for c in keys if c in row.index and pd.notna(row[c]) and float(row[c]) > 0),
                    None,
                )
                us10y = _try(["year_10", "10year", "10y", "T10Y", "year10"])
                us02y = _try(["year_2", "2year", "2y", "T2Y", "year2"])
                if us10y and us02y:
                    print(f"  [OBB/Treasury] US10Y={us10y:.3f}% US02Y={us02y:.3f}%")
                    return round(us10y, 3), round(us02y, 3)
        except Exception as e:
            print(f"  [OBB/Treasury] {e}，fallback")

    return _treasury_direct(av_key)


def fetch_hy_spread_fred(fred_key: str) -> float:
    """HY 信用利差（BAMLH0A0HYM2）— 透過 fetch_fred（已含 OpenBB）"""
    data = fetch_fred("BAMLH0A0HYM2", fred_key, limit=5)
    return round(data[-1], 2) if data else 4.5


# ── VIX ───────────────────────────────────────────────────────────────────────

def _vix_bollinger(series: pd.Series) -> tuple[float, float, bool]:
    current = float(series.iloc[-1])
    if len(series) >= 20:
        sma20 = float(series.rolling(20).mean().iloc[-1])
        std20 = float(series.rolling(20).std().iloc[-1])
        upper = sma20 + 2 * std20
        bb_break = current > upper
    else:
        upper = current * 1.3
        bb_break = False
    return round(current, 2), round(upper, 2), bb_break


def fetch_vix_av(av_key, fred_key: str = "") -> tuple[float, float, bool]:
    """
    VIX 擷取優先順序：
    1. FRED VIXCLS（已含 OpenBB wrapper）
    2. Yahoo Finance ^VIX（已含 OpenBB wrapper）
    3. AV VIXY 代理
    """
    # 1. FRED VIXCLS（OpenBB/FRED or direct）
    if fred_key and fred_key.lower() != "skip":
        vix_data = fetch_fred("VIXCLS", fred_key, limit=30)
        if vix_data and len(vix_data) >= 5:
            series = pd.Series(vix_data)
            print(f"  [VIX] FRED VIXCLS {len(series)} 筆，最新={series.iloc[-1]:.2f}")
            return _vix_bollinger(series)

    # 2. Yahoo Finance ^VIX（OpenBB/yfinance or direct）
    series = yahoo_daily_close("^VIX", days=60)
    if not series.empty:
        return _vix_bollinger(series)

    # 3. AV VIXY 代理
    print("  [VIX] 改用 AV VIXY...")
    series = av_daily_close("VIXY", av_key, days=60)
    if series.empty:
        series = av_daily_close("UVXY", av_key, days=60)
    if series.empty:
        print("  [VIX] 所有來源失敗，使用預設值 20.0")
        return 20.0, 30.0, False
    return _vix_bollinger(series)


# ── AV 估值（CAPE/ERP）— 保留直接呼叫（OpenBB 無完整對應端點）──────────────────

def av_quote(symbol: str, av_key) -> dict:
    data = _av_get({"function": "GLOBAL_QUOTE", "symbol": symbol}, av_key)
    return data.get("Global Quote", {})


def av_company_overview(symbol: str, av_key) -> dict:
    data = _av_get({"function": "COMPANY_OVERVIEW", "symbol": symbol}, av_key)
    return data


def fetch_cape_erp(symbol: str, av_key, rf: float) -> tuple:
    overview = av_company_overview(symbol, av_key)
    if not overview:
        return None, None, None
    fpe = None
    for key in ["ForwardPE", "TrailingPE", "PERatio"]:
        val = overview.get(key)
        if val and val not in ("None", "-"):
            try:
                fpe = float(val)
                break
            except ValueError:
                pass
    if not fpe or fpe <= 0:
        return None, None, None
    from src.quant_engine import calc_erp, calc_cape_10y
    cape = round(fpe * 0.95, 1)
    erp  = calc_erp(fpe, rf)
    pred = calc_cape_10y(cape)
    return cape, erp, pred


# ─────────────────────────────────────────────────────────────────────────────
# Fallback：直接 API 實作（OpenBB 失敗時使用）
# ─────────────────────────────────────────────────────────────────────────────

def _av_get(params: dict, av_key, retries: int = 3) -> dict:
    """Alpha Vantage 多 key 輪換 + 指數退避"""
    keys = [av_key] if isinstance(av_key, str) else [k for k in av_key if k]
    for key in keys:
        p = {k: v for k, v in params.items() if k != "apikey"}
        p["apikey"] = key
        for attempt in range(retries):
            try:
                if attempt > 0:
                    time.sleep(15 * attempt)
                r = requests.get(AV_BASE, params=p, timeout=15)
                time.sleep(1.5)
                data = r.json()
                if "Note" in data or "Information" in data:
                    print(f"  [AV] key ...{key[-4:]} 配額已滿，換 key")
                    break
                return data
            except Exception as e:
                print(f"  [AV ERR] {e}")
                if attempt < retries - 1:
                    time.sleep(10)
    print("  [AV] 所有 key 均達配額，跳過")
    return {}


def _av_direct_daily(symbol: str, av_key, days: int = 400) -> pd.Series:
    data = _av_get({
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "full",
    }, av_key)
    ts = data.get("Time Series (Daily)", {})
    if not ts:
        print(f"  [AV direct] {symbol} 無資料")
        return pd.Series(dtype=float)
    records = {date: float(v["4. close"]) for date, v in ts.items()}
    series = pd.Series(records).sort_index()
    return series.tail(days)


def _yahoo_direct(symbol: str, days: int = 400) -> pd.Series:
    """Yahoo Finance v8 Chart API 直接呼叫"""
    range_str = "5y" if days > 500 else "2y"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, params={"interval": "1d", "range": range_str},
                         headers=headers, timeout=15)
        if r.status_code != 200:
            return pd.Series(dtype=float)
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return pd.Series(dtype=float)
        chart = result[0]
        timestamps = chart.get("timestamp", [])
        closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        records = {}
        for ts, c in zip(timestamps, closes):
            if c is None:
                continue
            records[datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")] = float(c)
        if not records:
            return pd.Series(dtype=float)
        series = pd.Series(records).sort_index()
        series = series[series > 0]
        print(f"  [Yahoo direct] {symbol} {len(series)} 筆，最新={series.iloc[-1]:.2f}")
        return series.tail(days)
    except Exception as e:
        print(f"  [Yahoo direct] {symbol}: {e}")
        return pd.Series(dtype=float)


def _fred_direct(series_id: str, api_key: str, limit: int = 24) -> list:
    """FRED REST API 直接呼叫"""
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(5 * attempt)
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": api_key,
                        "file_type": "json", "sort_order": "desc", "limit": limit},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            if not r.text or r.text.strip() == "":
                continue
            obs = r.json().get("observations", [])
            vals = []
            for o in reversed(obs):
                try:
                    v = o.get("value", ".")
                    if v != ".":
                        vals.append(float(v))
                except (ValueError, KeyError):
                    pass
            if vals:
                return vals
        except Exception as e:
            print(f"  [FRED direct] {series_id} attempt {attempt+1}: {e}")
    return []


def _treasury_direct(av_key) -> tuple[float, float]:
    """AV TREASURY_YIELD 直接呼叫"""
    us10y, us02y = 4.3, 4.0
    try:
        d10 = _av_get({"function": "TREASURY_YIELD", "interval": "daily", "maturity": "10year"}, av_key)
        vals10 = d10.get("data", [])
        if vals10:
            us10y = round(float(vals10[0]["value"]), 3)
    except Exception as e:
        print(f"  [AV direct] US10Y: {e}")
    try:
        d02 = _av_get({"function": "TREASURY_YIELD", "interval": "daily", "maturity": "2year"}, av_key)
        vals02 = d02.get("data", [])
        if vals02:
            us02y = round(float(vals02[0]["value"]), 3)
    except Exception as e:
        print(f"  [AV direct] US02Y: {e}")
    return us10y, us02y


def _yahoo_news_direct(symbol: str, limit: int = 5) -> list:
    """Yahoo Finance Search API 直接呼叫 → list[dict{"title","url"}]"""
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    params = {"q": symbol, "newsCount": limit, "enableFuzzyQuery": False, "quotesCount": 0}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for n in data.get("news", [])[:limit]:
            title = n.get("title", "")
            if not title:
                continue
            items.append({
                "title": title,
                "url":   n.get("link", ""),
            })
        if items:
            print(f"  [News direct] {symbol} {len(items)} 則")
        return items
    except Exception as e:
        print(f"  [News direct] {symbol}: {e}")
        return []
