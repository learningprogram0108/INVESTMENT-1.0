"""
資料擷取層 v3
完全移除 yfinance，改用：
  - Alpha Vantage API → VOO、VIX、US10Y、US02Y
  - TWSE Open API    → 0050、00679B（台灣證交所官方）
  - FRED API         → 總經指標（不變）
"""

import time
import requests
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

AV_BASE = "https://www.alphavantage.co/query"
TWSE_BASE = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"


# ─────────────────────────────────────────────
# Alpha Vantage 通用擷取
# ─────────────────────────────────────────────

def _av_get(params: dict, av_key: str, retries: int = 3) -> dict:
    params["apikey"] = av_key
    for attempt in range(retries):
        try:
            if attempt > 0:
                time.sleep(15 * attempt)
            r = requests.get(AV_BASE, params=params, timeout=15)
            data = r.json()
            if "Note" in data or "Information" in data:
                print(f"  [AV] rate limit，等待 60s...")
                time.sleep(60)
                continue
            return data
        except Exception as e:
            print(f"  [AV ERR] {e}")
            if attempt < retries - 1:
                time.sleep(10)
    return {}


def av_daily_close(symbol: str, av_key: str, days: int = 300) -> pd.Series:
    """Alpha Vantage 每日收盤價 → pd.Series（時間升冪）"""
    data = _av_get({
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "compact",   # 最近 100 筆，compact 較快
    }, av_key)

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        # 嘗試 full 模式
        data = _av_get({
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "full",
        }, av_key)
        ts = data.get("Time Series (Daily)", {})

    if not ts:
        print(f"  [AV] {symbol} 無資料")
        return pd.Series(dtype=float)

    records = {date: float(v["4. close"]) for date, v in ts.items()}
    series = pd.Series(records).sort_index()
    return series.tail(days)


def av_quote(symbol: str, av_key: str) -> dict:
    """Alpha Vantage 即時報價"""
    data = _av_get({
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
    }, av_key)
    return data.get("Global Quote", {})


# ─────────────────────────────────────────────
# TWSE 台灣證交所（0050、00679B）
# ─────────────────────────────────────────────

def twse_daily_close(stock_no: str, months: int = 3) -> pd.Series:
    """
    台灣證交所 Open API 擷取歷史日收盤價
    免費、官方、不需 API Key
    """
    all_records = {}
    now = datetime.now()

    for i in range(months):
        target = now - timedelta(days=30 * i)
        date_str = target.strftime("%Y%m01")
        try:
            r = requests.get(
                TWSE_BASE,
                params={"stockNo": stock_no, "date": date_str},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            data = r.json()
            if data.get("stat") != "OK":
                continue
            fields = data.get("fields", [])
            rows   = data.get("data", [])
            # 找日期與收盤價欄位索引
            try:
                date_idx  = fields.index("日期")
                close_idx = fields.index("收盤價")
            except ValueError:
                date_idx, close_idx = 0, 6
            for row in rows:
                try:
                    raw_date  = row[date_idx]   # 民國年，如 "115/01/02"
                    raw_close = row[close_idx].replace(",", "")
                    # 民國年轉西元
                    parts = raw_date.split("/")
                    year  = int(parts[0]) + 1911
                    date_key = f"{year}-{parts[1]}-{parts[2]}"
                    all_records[date_key] = float(raw_close)
                except Exception:
                    continue
            time.sleep(0.5)   # 避免打太快
        except Exception as e:
            print(f"  [TWSE] {stock_no} {date_str}: {e}")

    if not all_records:
        return pd.Series(dtype=float)
    series = pd.Series(all_records).sort_index()
    return series


def twse_latest_close(stock_no: str) -> tuple[float, float]:
    """取最新兩日收盤價 (current, prev)"""
    series = twse_daily_close(stock_no, months=2)
    if len(series) < 2:
        return 0.0, 0.0
    return float(series.iloc[-1]), float(series.iloc[-2])


# ─────────────────────────────────────────────
# Alpha Vantage VIX
# ─────────────────────────────────────────────

def fetch_vix_av(av_key: str) -> tuple[float, float, bool]:
    """
    VIX 從 Alpha Vantage 擷取
    計算 20 日布林上軌，判斷是否突破
    """
    series = av_daily_close("VIX", av_key, days=60)
    if series.empty:
        # fallback：用 CBOE VIX ETF (VIXY) 代理
        series = av_daily_close("VIXY", av_key, days=60)
    if series.empty:
        return 20.0, 30.0, False

    current = float(series.iloc[-1])
    if len(series) >= 20:
        sma20  = float(series.rolling(20).mean().iloc[-1])
        std20  = float(series.rolling(20).std().iloc[-1])
        upper  = sma20 + 2 * std20
        bb_break = current > upper
    else:
        upper    = current * 1.3
        bb_break = False

    return round(current, 2), round(upper, 2), bb_break


# ─────────────────────────────────────────────
# Alpha Vantage 殖利率（TREASURY_YIELD）
# ─────────────────────────────────────────────

def fetch_treasury_av(av_key: str) -> tuple[float, float]:
    """US10Y 與 US02Y 殖利率（%）"""
    us10y, us02y = 4.3, 4.0
    try:
        data10 = _av_get({
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": "10year",
        }, av_key)
        vals10 = data10.get("data", [])
        if vals10:
            us10y = round(float(vals10[0]["value"]), 3)
    except Exception as e:
        print(f"  [AV] US10Y: {e}")

    time.sleep(13)   # AV 免費版 5 req/min → 每次間隔 13s

    try:
        data02 = _av_get({
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": "2year",
        }, av_key)
        vals02 = data02.get("data", [])
        if vals02:
            us02y = round(float(vals02[0]["value"]), 3)
    except Exception as e:
        print(f"  [AV] US02Y: {e}")

    return us10y, us02y


# ─────────────────────────────────────────────
# FRED（不變）
# ─────────────────────────────────────────────

def fetch_fred(series_id: str, api_key: str, limit: int = 24) -> list:
    if not api_key or api_key.lower() == "skip":
        return []
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": api_key,
                    "file_type": "json", "sort_order": "desc",
                    "limit": limit},
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


def fetch_hy_spread_fred(fred_key: str) -> float:
    data = fetch_fred("BAMLH0A0HYM2", fred_key, limit=5)
    return round(data[-1], 2) if data else 4.5
