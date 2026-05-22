#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_optimizer.py — 量化投資組合凸最佳化引擎
===================================================
基於 Dany Cajas《Advanced Portfolio Optimization》
底層：Riskfolio-Lib + CVXPY 凸最佳化求解器

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
組合標的
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VOO       美股標普500       USD  核心大盤
  0050.TW   元大台灣50        TWD  核心台股
  00875.TW  國泰網路資安      TWD  資安衛星
  GRID      全球智慧電網      USD  基建衛星 (半年手動下單)
  VGIT      美國中天期公債    USD  戰術美債 / TYD 儲蓄池

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四種最佳化模式
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  模式 A  Classic/MV    均值-變異數（Markowitz 原始框架）
  模式 B  Classic/CVaR  條件風險價值（最極端 5% 行情下平均損失）
  模式 C  Classic/CDaR  條件最大回撤（最極端 5% 情境連續跌幅）
  模式 D  HRP/Ward      層次風險平價（防科技/AI共線性結構崩潰）

Usage：
  python portfolio_optimizer.py              # 執行全部四種模式
  python portfolio_optimizer.py --no-plot    # 跳過圖表生成（CI 環境）
"""

# ══════════════════════════════════════════════════════════════════════════
# §0  Import & 全域設定
# ══════════════════════════════════════════════════════════════════════════

import argparse
import json
import os
import sys
import warnings
from datetime import date, datetime
from pathlib import Path

# ── 強制 stdout/stderr 使用 UTF-8（跨平台：Windows cp950、CI 環境）──────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis

try:
    import yfinance as yf
    # CI 環境下，main.py 可能已佔用預設快取目錄的 SQLite 鎖；
    # 讓 portfolio_optimizer 使用獨立的快取目錄以避免 OperationalError。
    _yf_cache = Path(os.environ.get("RUNNER_TEMP", os.path.expanduser("~"))) / ".yf_cache_opt"
    _yf_cache.mkdir(parents=True, exist_ok=True)
    try:
        yf.set_tz_cache_location(str(_yf_cache))
    except Exception:
        pass  # 舊版 yfinance 不支援此方法，忽略
except ImportError:
    print("Missing yfinance. Run: pip install yfinance>=0.2.40")
    sys.exit(1)

try:
    import riskfolio as rp
    _RISKFOLIO_VER = getattr(rp, "__version__", "unknown")
except ImportError:
    print("❌ 缺少 riskfolio-lib，請執行：pip install riskfolio-lib>=6.0.0")
    sys.exit(1)

import matplotlib
matplotlib.use("Agg")          # 非互動後端，適合排程 / CI 環境
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ── 標的清單與元資料 ───────────────────────────────────────────────────────
TICKERS: list[str] = ["VOO", "0050.TW", "00875.TW", "GRID", "VGIT"]

TICKER_META: dict = {
    "VOO":      {"name": "Vanguard S&P500",          "ccy": "USD", "group": "Equity",    "rebal": "auto"},
    "0050.TW":  {"name": "元大台灣50",                 "ccy": "TWD", "group": "Equity",    "rebal": "auto"},
    "00875.TW": {"name": "國泰網路資安",               "ccy": "TWD", "group": "Satellite", "rebal": "auto"},
    "GRID":     {"name": "Global X Smart Grid",       "ccy": "USD", "group": "Satellite", "rebal": "semi-annual"},
    "VGIT":     {"name": "Vanguard Interm-Treasury",  "ccy": "USD", "group": "Bond",      "rebal": "auto"},
}

# ── 時間範圍 ───────────────────────────────────────────────────────────────
START_DATE: str = "2020-01-01"
END_DATE:   str = date.today().strftime("%Y-%m-%d")

# ── 風險參數 ───────────────────────────────────────────────────────────────
RF_ANNUAL: float = 0.04          # 年化無風險利率（建議使用當前聯邦基金利率）
RF_DAILY:  float = RF_ANNUAL / 252
ALPHA:     float = 0.05          # CVaR / CDaR 顯著水準（尾端最差 5% 情境）
T_FACTOR:  int   = 252           # 年化乘數（交易日）

# ── 個別資產權重上下限（凸最佳化約束）────────────────────────────────────────
#   格式：{ ticker: (lower_bound, upper_bound) }
#   設計原則：
#     - 核心標的下限 ≥ 10%，確保核心曝險
#     - GRID 上限 20%：因半年才手動下單，避免過度集中
#     - VGIT 上限放寬至 40%：作為 TYD 觸發前的彈性儲蓄池
ASSET_BOUNDS: dict = {
    "VOO":      (0.10, 0.50),
    "0050.TW":  (0.10, 0.50),
    "00875.TW": (0.05, 0.20),
    "GRID":     (0.05, 0.20),
    "VGIT":     (0.05, 0.40),
}

# ── 當前實際持有部位（請依實際情況修改）──────────────────────────────────────
CURRENT_WEIGHTS: dict = {
    "VOO":      0.35,
    "0050.TW":  0.25,
    "00875.TW": 0.10,
    "GRID":     0.15,
    "VGIT":     0.15,
}

# ── 再平衡閾值 ─────────────────────────────────────────────────────────────
REBALANCE_THRESHOLD: float = 0.05   # 任一資產絕對偏差 > 5% 才觸發信號

# ── 輸出路徑 ────────────────────────────────────────────────────────────────
OUTPUT_DIR:  Path = Path("./portfolio_reports")
REPORT_DATE: str  = date.today().strftime("%Y-%m-%d")

# ── 深色主題色盤（與 LINE Bot 儀表板保持一致）───────────────────────────────
_C = {
    "bg":      "#0f1117",
    "surface": "#1a1d27",
    "border":  "#2a2f45",
    "text":    "#e8eaf0",
    "text2":   "#9aa0b5",
    "grn":     "#1D9E75",
    "yel":     "#EF9F27",
    "red":     "#E24B4A",
    "blu":     "#378ADD",
    "pur":     "#9B59B6",
}
TICKER_COLORS: dict = {
    "VOO":      _C["grn"],
    "0050.TW":  _C["blu"],
    "00875.TW": _C["yel"],
    "GRID":     _C["red"],
    "VGIT":     _C["pur"],
}


# ══════════════════════════════════════════════════════════════════════════
# §1  資料下載與前處理
# ══════════════════════════════════════════════════════════════════════════

def download_prices(tickers: list, start: str, end: str) -> pd.DataFrame:
    """
    下載調整後收盤價（auto_adjust=True，除息/除權還原後價格）。

    ⚠ 貨幣異質性警告
    ──────────────────
    TWD 標的（0050.TW, 00875.TW）以新台幣計價；
    USD 標的（VOO, GRID, VGIT）以美元計價。
    本腳本在「本地貨幣報酬率」基礎上做風險分析（比較相對波動與相關性）。
    若需要統一換算為 USD 基準報酬，請自行加入 TWD/USD 即期匯率調整。

    ⚠ 上市日期不一致
    ──────────────────
    00875.TW 上市日期為 2021-07-01，
    因此 2020~2021 上半年期間將產生 NaN，
    腳本以「有效共同交易日」作為最終樣本期間。
    """
    print(f"\n{'='*58}")
    print(f"  § 1  資料下載  [{start} → {end}]")
    print(f"{'='*58}")

    # CI 環境下 yfinance SQLite 快取可能被前一個 step 鎖住，
    # 改用 threads=False（序列下載）並加重試邏輯以避免 OperationalError。
    import time as _time
    import yfinance as _yf_retry

    def _try_download(attempt: int) -> pd.DataFrame:
        """單次下載嘗試，失敗回傳空 DataFrame。"""
        try:
            return _yf_retry.download(
                tickers,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                threads=False,   # 序列下載，避免 SQLite lock
            )
        except Exception as exc:
            print(f"  [download attempt {attempt}] 失敗：{exc}", flush=True)
            return pd.DataFrame()

    raw = pd.DataFrame()
    for _attempt in range(1, 4):          # 最多重試 3 次
        raw = _try_download(_attempt)
        if not raw.empty:
            break
        _time.sleep(5 * _attempt)         # 等 5s / 10s / 15s 再重試

    if raw.empty:
        raise RuntimeError(
            "download_prices: 所有重試均失敗，請確認網路連線或標的代碼正確。"
        )

    # yfinance 回傳 MultiIndex 欄位時取 Close 層
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"][tickers].copy()
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]}).copy()

    # 前值填充（最多 3 個交易日，橋接美台市場假日不同步）
    prices = prices.ffill(limit=3)

    # 丟棄任何標的仍有缺值的日期（確保報酬率矩陣完整）
    n_before = len(prices)
    prices   = prices.dropna()
    n_after  = len(prices)

    if n_after == 0:
        raise RuntimeError(
            "download_prices: dropna 後沒有共同交易日，"
            "請檢查標的代碼或縮短資料期間。"
        )

    print(f"  有效共同交易日：{prices.index[0].date()} -> {prices.index[-1].date()}")
    print(f"     總交易日：{n_after}（原始 {n_before}，丟棄 {n_before - n_after} 列含 NaN）")
    print(f"     各標的末收盤價：")
    for t in tickers:
        if t in prices.columns:
            print(f"       {t:<12}  {prices[t].iloc[-1]:.4f}")

    return prices


def compute_returns(prices: pd.DataFrame, method: str = "log") -> pd.DataFrame:
    """
    計算日報酬率。

    method='log'  → 對數報酬 r = ln(P_t/P_{t-1})
                    適合統計建模（時間可加性、常態近似）
    method='pct'  → 簡單報酬 r = (P_t-P_{t-1})/P_{t-1}
                    適合績效歸因（Arithmetic compounding）
    """
    if method == "log":
        returns = np.log(prices / prices.shift(1)).dropna()
    else:
        returns = prices.pct_change().dropna()

    # ── 報酬率統計摘要（用於識別厚尾 / 非常態分佈）──────────────────────────
    stats = pd.DataFrame({
        "年化報酬":   returns.mean() * T_FACTOR,
        "年化波動":   returns.std()  * np.sqrt(T_FACTOR),
        "偏態係數":   returns.apply(skew),
        "超額峰態":   returns.apply(kurtosis),   # > 0 表示厚尾
    })
    print(f"\n  📊 日報酬率統計摘要（對數報酬）：")
    print(stats.round(4).to_string(float_format=lambda x: f"{x:+.4f}"))

    # 厚尾警告（超額峰態 > 3，非常態性顯著）
    fat_tail = stats["超額峰態"][stats["超額峰態"] > 3]
    if not fat_tail.empty:
        print(f"\n  ⚠  厚尾資產（超額峰態 > 3）：{fat_tail.index.tolist()}")
        print("     建議使用 CVaR / CDaR 模型以控制極端下行風險")

    return returns


# ══════════════════════════════════════════════════════════════════════════
# §2  建立 Portfolio 物件 & 套用凸約束
# ══════════════════════════════════════════════════════════════════════════

def build_portfolio(returns: pd.DataFrame) -> "rp.Portfolio":
    """
    建立 Riskfolio-Lib Portfolio 物件，估計統計參數。

    method_mu='hist'
        歷史樣本均值估計預期報酬（最直觀，無假設偏差）。

    method_cov='ledoit'
        Ledoit-Wolf 收縮估計共變異數矩陣。
        解決「樣本共變異數矩陣奇異」問題——當 N（資產數）相對 T（觀測數）
        不夠小時，樣本共變異數會過度放大估計誤差，
        Ledoit-Wolf 透過最佳收縮係數 α 將其往單位矩陣拉近：
        Σ̂ = (1-α)·S + α·μ·I
        本組合 5 資產 / 1000+ 觀測，效果有限但仍提升數值穩定性。
    """
    port = rp.Portfolio(returns=returns)
    port.assets_stats(
        method_mu="hist",
        method_cov="ledoit",
        # d= 參數已在 riskfolio-lib v7 移除
    )
    # v7 中 alpha（CVaR/CDaR 顯著水準）改為 Portfolio 屬性，不再是 optimization() 的參數
    port.alpha = ALPHA
    return port


def build_constraint_matrix(tickers: list) -> tuple[pd.DataFrame, pd.Series]:
    """
    手動建立 CVXPY 相容的線性不等式約束矩陣（Ax ≤ b 形式）。

    對每個資產 i 設定上下限約束：
      wi ≥ lo_i  →  -wi ≤ -lo_i  → 矩陣列：A_row = -e_i,  b_val = -lo_i
      wi ≤ hi_i  →  +wi ≤  hi_i  → 矩陣列：A_row = +e_i,  b_val =  hi_i

    這種顯式構建方式確保凸性（每個約束都是線性的），
    避免使用 Riskfolio 的 assets_constraints() 在不同版本間 API 不一致的問題。
    """
    n = len(tickers)
    idx = {t: i for i, t in enumerate(tickers)}
    rows_A, vals_b, row_labels = [], [], []

    for t in tickers:
        lo, hi = ASSET_BOUNDS.get(t, (0.0, 1.0))

        # 下限：wi ≥ lo  →  -wi ≤ -lo
        row = [0.0] * n
        row[idx[t]] = -1.0
        rows_A.append(row)
        vals_b.append(-lo)
        row_labels.append(f"{t}_lo")

        # 上限：wi ≤ hi
        row = [0.0] * n
        row[idx[t]] = 1.0
        rows_A.append(row)
        vals_b.append(hi)
        row_labels.append(f"{t}_hi")

    A = pd.DataFrame(rows_A, index=row_labels, columns=tickers)
    # riskfolio-lib v7 binequality setter 需要 2D DataFrame（shape n×1），不接受 Series
    b = pd.DataFrame(vals_b, index=row_labels, columns=["b"])
    return A, b


def apply_constraints(port: "rp.Portfolio", tickers: list) -> None:
    """
    將約束矩陣注入 Portfolio 物件。

    Riskfolio-Lib 在求解時讀取：
      port.ainequality  →  矩陣 A（n_constraints × n_assets）
      port.binequality  →  向量 b（n_constraints）
    並在 CVXPY 中生成：A @ w ≤ b
    """
    A, b = build_constraint_matrix(tickers)
    port.ainequality = A
    port.binequality = b

    print(f"\n  ✅ 約束矩陣套用成功：{len(A)} 條線性不等式約束（CVXPY 凸性保證）")
    print("     個別資產上下限：")
    for t in tickers:
        lo, hi = ASSET_BOUNDS.get(t, (0, 1))
        print(f"       {t:<12}  [{lo*100:.0f}%,  {hi*100:.0f}%]")


# ══════════════════════════════════════════════════════════════════════════
# §3  四種最佳化模式
# ══════════════════════════════════════════════════════════════════════════

def _run_classic(port: "rp.Portfolio", rm: str, label: str) -> pd.DataFrame | None:
    """
    Classic 模型最佳化（共用內核）。

    目標：最大化風險調整後夏普比率
      Sharpe(rm) = (μ_p - r_f) / ρ_p(rm)
    其中 ρ_p 依 rm 選擇：
      rm='MV'   → 標準差 σ_p  （變異數最小化等價於夏普最大化）
      rm='CVaR' → Conditional Value-at-Risk（歷史場景法，alpha=5%）
      rm='CDaR' → Conditional Drawdown-at-Risk（歷史場景法，alpha=5%）

    hist=True：使用歷史場景模擬計算風險（非參數法），
               對肥尾分佈更穩健，不假設報酬率服從常態分佈。
    """
    print(f"\n  🔄  {label}  (rm='{rm}')  求解中...", end="", flush=True)
    try:
        w = port.optimization(
            model="Classic",
            rm=rm,
            obj="Sharpe",
            rf=RF_DAILY,
            l=2,
            hist=True,
            # alpha 已移至 port.alpha 屬性（v7 API 變更）
        )
        if w is None or w.empty:
            print(f"  ⚠  求解器返回空結果（可能約束太緊或資料不足）")
            return None
        print(f"  ✅")
        return w
    except Exception as e:
        print(f"  ❌  求解失敗：{e}")
        return None


def optimize_mv(port: "rp.Portfolio") -> pd.DataFrame | None:
    """
    模式 A：均值-變異數最佳化（Markowitz 1952）。

    數學目標：
      min  w'Σw   (最小化組合方差)
      s.t. w'μ = μ_target，Σw_i = 1，w_i ≥ 0
           加上 ASSET_BOUNDS 個別限制

    等價地，最大化 Sharpe = (w'μ - r_f) / √(w'Σw)。

    ⚠ 侷限：假設報酬率服從多元常態分佈，
       對 VOO/0050 的厚尾 / 跳空特性估計不足。
    """
    return _run_classic(port, rm="MV", label="模式A｜MV  均值-變異數")


def optimize_cvar(port: "rp.Portfolio") -> pd.DataFrame | None:
    """
    模式 B：CVaR 最佳化（Rockafellar & Uryasev 2000）。

    數學目標：
      min  CVaR_α(w) = E[-R | -R ≥ VaR_α]   (最差 α% 情境的平均損失)
      s.t. Σw_i = 1，w_i ≥ 0，ASSET_BOUNDS 限制

    CVaR 是凸函數，可以用線性規劃精確求解——
    這是 CVaR 比 VaR 更受最佳化青睞的核心原因。

    對半導體與科技 ETF 的肥尾特性（Black Monday、COVID崩潰）
    比 MV 提供更好的尾端保護。
    """
    return _run_classic(port, rm="CVaR", label="模式B｜CVaR 條件風險價值")


def optimize_cdar(port: "rp.Portfolio") -> pd.DataFrame | None:
    """
    模式 C：CDaR 最佳化（Chekhlov et al. 2005）。

    數學目標：
      min  CDaR_α(w) = E[DD(t) | DD(t) ≥ DaR_α]
    其中 DD(t) = (Peak_t - P_t) / Peak_t  (某時刻的回撤深度)

    控制的是「最極端 α% 情境下的平均最大回撤」，
    對「長期持倉、無法頻繁交割」的配置策略特別重要。
    GRID 半年手動下單的場景，CDaR 提供更貼近實務的風險控制視角。
    """
    return _run_classic(port, rm="CDaR", label="模式C｜CDaR 條件最大回撤")


def optimize_hrp(port: "rp.Portfolio", returns: pd.DataFrame) -> pd.DataFrame | None:
    """
    模式 D：層次風險平價 HRP（López de Prado 2016）。

    核心步驟（不需要求解凸最佳化問題）：
      ① 相關性距離矩陣：d_ij = √((1 - ρ_ij) / 2)
         ρ_ij 越高（同向漲跌）→ 距離越小 → 歸入同一叢集
      ② Ward 層次聚類：最小化「合併後群內離差平方和增量」
         比 single/complete linkage 更穩定，不易受孤立點影響
      ③ 最佳葉節點排序（Murtagh 2003）
      ④ 遞迴二分（Bisection）：
         依各子樹的 cluster variance 反比分配資金
         → 波動高的子樹分得少，波動低的子樹分得多

    優點：對估計誤差（共變異數矩陣不準確）的穩健性遠超 MV。
    弱點：不保證全域最優，無法嵌入 ASSET_BOUNDS 等式約束
          （HRP 是啟發式演算法，非凸最佳化）。
    """
    print(f"\n  🔄  模式D｜HRP  層次風險平價  求解中...", end="", flush=True)
    try:
        # riskfolio-lib v7+：HRP 由獨立的 HCPortfolio 類別處理
        # （Classic Portfolio.optimization 已不支援 model='HRP'）
        hc_port = rp.HCPortfolio(returns=port.returns)
        w = hc_port.optimization(
            model="HRP",
            codependence="pearson",
            rm="MV",
            linkage="ward",
            max_k=10,
            leaf_order=True,
        )
    except Exception as e:
        print(f"  ❌  HRP 求解失敗：{e}")
        return None

    if w is None or w.empty:
        print("  ⚠  HRP 返回空結果")
        return None
    print("  ✅")
    return w


# ══════════════════════════════════════════════════════════════════════════
# §4  績效指標計算
# ══════════════════════════════════════════════════════════════════════════

def compute_metrics(w: pd.DataFrame | None, returns: pd.DataFrame,
                    label: str) -> dict:
    """
    計算組合績效 KPI：

    - 年化預期報酬  = E[r] × 252
    - 年化波動率    = σ(r) × √252
    - 夏普比率      = (年化報酬 - 無風險利率) / 年化波動率
    - CVaR(5%)      = 最差 5% 日報酬的均值（年化）
    - 最大回撤      = max over t { (Peak_t - P_t) / Peak_t }

    注意：使用對數報酬計算，年化報酬非複利意義的幾何報酬。
    """
    if w is None or w.empty:
        return {"label": label}

    # 對齊欄位順序
    valid_tickers = [t for t in TICKERS if t in w.index and t in returns.columns]
    w_arr  = np.array([float(w.loc[t, w.columns[0]]) for t in valid_tickers])
    ret    = returns[valid_tickers].values

    port_r = ret @ w_arr                          # 組合日報酬序列

    # 年化指標
    ann_ret = float(port_r.mean() * T_FACTOR)
    ann_vol = float(port_r.std()  * np.sqrt(T_FACTOR))
    sharpe  = (ann_ret - RF_ANNUAL) / ann_vol if ann_vol > 1e-9 else 0.0

    # CVaR（歷史法）
    n_tail  = max(1, int(len(port_r) * ALPHA))
    cvar    = float(-np.sort(port_r)[:n_tail].mean() * np.sqrt(T_FACTOR))

    # 最大回撤
    cum          = np.cumprod(1 + port_r)
    rolling_peak = np.maximum.accumulate(cum)
    mdd          = float(((cum - rolling_peak) / rolling_peak).min())

    return {
        "label":     label,
        "ann_ret":   round(ann_ret * 100, 2),
        "ann_vol":   round(ann_vol * 100, 2),
        "sharpe":    round(sharpe, 3),
        "cvar_ann":  round(cvar  * 100, 2),
        "mdd":       round(mdd   * 100, 2),
    }


def _safe_weight(w: pd.DataFrame | None, ticker: str) -> float:
    """安全讀取權重 DataFrame 中某資產的比例。"""
    if w is None or w.empty or ticker not in w.index:
        return 0.0
    col = w.columns[0]
    return float(w.loc[ticker, col])


# ══════════════════════════════════════════════════════════════════════════
# §5  視覺化
# ══════════════════════════════════════════════════════════════════════════

def _dark_ax(ax: plt.Axes, title: str = "") -> None:
    """套用深色主題到 Axes 物件。"""
    ax.set_facecolor(_C["surface"])
    if ax.figure:
        ax.figure.set_facecolor(_C["bg"])
    ax.tick_params(colors=_C["text2"], labelsize=9)
    ax.xaxis.label.set_color(_C["text2"])
    ax.yaxis.label.set_color(_C["text2"])
    for spine in ax.spines.values():
        spine.set_edgecolor(_C["border"])
    if title:
        ax.set_title(title, color=_C["text"], fontsize=12, fontweight="bold", pad=10)


def plot_dendrogram(returns: pd.DataFrame, save_path: Path) -> None:
    """
    Ward 層次聚類樹狀圖（Dendrogram）。

    解讀方式：
    - 縱軸距離越小 → 資產越「親近」（同向漲跌機率高）
    - VOO / 0050 / 00875 若歸入同一早期分支，
      代表三者存在共同 AI / 科技因子驅動的結構性連動
    - 確認 VGIT 與股票類資產距離遠（負相關，真正分散）

    呼叫 rp.plot_dendrogram() 確保使用與 HRP 最佳化相同的聚類邏輯。
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(_C["bg"])
    ax.set_facecolor(_C["surface"])

    try:
        rp.plot_dendrogram(
            returns,
            codependence="pearson",
            linkage="ward",
            k=None,
            max_k=10,
            leaf_order=True,
            ax=ax,
        )
    except TypeError:
        # 舊版 riskfolio 不支援全部參數
        try:
            rp.plot_dendrogram(
                returns,
                codependence="pearson",
                linkage="ward",
                ax=ax,
            )
        except Exception as e:
            ax.text(0.5, 0.5, f"Dendrogram 無法生成\n{e}",
                    ha="center", va="center", color=_C["text2"],
                    transform=ax.transAxes, fontsize=10)

    # 修正配色（rp.plot_dendrogram 會重設部分樣式）
    ax.set_facecolor(_C["surface"])
    ax.tick_params(colors=_C["text"])
    for line in ax.get_lines():
        line.set_color(_C["blu"])
    for spine in ax.spines.values():
        spine.set_edgecolor(_C["border"])
    ax.set_title("Ward 層次聚類樹狀圖 — 資產共線性視覺化",
                 color=_C["text"], fontsize=12, fontweight="bold", pad=10)

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=_C["bg"])
    plt.close(fig)
    print(f"  💾  Dendrogram → {save_path.name}")


def plot_risk_contributions(
    w: pd.DataFrame | None,
    port: "rp.Portfolio",
    returns: pd.DataFrame,
    rm: str,
    label: str,
    save_path: Path,
) -> None:
    """
    邊際風險貢獻圖（Marginal Risk Contribution）。

    MRC_i = ∂ρ_p / ∂w_i × w_i

    等風險貢獻（ERC）目標：MRC_i ≈ ρ_p / N
    用以驗證：雖然 VOO/0050 的資金權重高，
    但其風險貢獻是否被控制在合理邊界。

    rp.plot_risk_con() 使用與 port.optimization() 完全相同的風險測度計算，
    確保視覺化與最佳化結果一致。
    """
    if w is None or w.empty:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(_C["bg"])

    try:
        # v7 簽名：plot_risk_con(w, returns, cov=None, ...)
        rp.plot_risk_con(
            w,
            returns,
            cov=port.cov,
            rm=rm,
            rf=RF_DAILY,
            alpha=ALPHA,
            color=_C["blu"],
            height=5,
            width=10,
            t_factor=T_FACTOR,
            ax=ax,
        )
    except Exception as e:
        ax.text(0.5, 0.5, f"風險貢獻圖無法生成\n{e}",
                ha="center", va="center", color=_C["text2"],
                transform=ax.transAxes, fontsize=10)

    ax.set_facecolor(_C["surface"])
    fig.patch.set_facecolor(_C["bg"])
    ax.tick_params(colors=_C["text2"])
    for bar in ax.patches:
        bar.set_edgecolor(_C["border"])
        bar.set_linewidth(0.5)
    for spine in ax.spines.values():
        spine.set_edgecolor(_C["border"])
    ax.set_title(f"邊際風險貢獻 — {label}",
                 color=_C["text"], fontsize=12, fontweight="bold", pad=10)
    ax.set_xlabel("資產", color=_C["text2"])
    ax.set_ylabel(f"風險貢獻（{rm}，年化）", color=_C["text2"])

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=_C["bg"])
    plt.close(fig)
    print(f"  💾  風險貢獻圖 [{label}] → {save_path.name}")


def plot_weights_comparison(
    weights_dict: dict[str, pd.DataFrame | None],
    tickers: list,
    current: dict,
    save_path: Path,
) -> None:
    """
    水平條形圖：四種模型最佳化權重 vs 當前持有部位並排比較。
    每格顏色對應資產色系，灰色虛線標出 20% 均值參考線。
    """
    all_entries = list(weights_dict.items()) + [("當前持有", None)]
    n = len(all_entries)

    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 6.5), sharey=True)
    fig.patch.set_facecolor(_C["bg"])
    if n == 1:
        axes = [axes]

    for ax, (label, w) in zip(axes, all_entries):
        ax.set_facecolor(_C["surface"])

        if label == "當前持有":
            values = [current.get(t, 0) * 100 for t in tickers]
            colors = [_C["yel"]] * len(tickers)
        elif w is not None and not w.empty:
            values = [_safe_weight(w, t) * 100 for t in tickers]
            colors = [TICKER_COLORS.get(t, _C["blu"]) for t in tickers]
        else:
            values = [0.0] * len(tickers)
            colors = [_C["border"]] * len(tickers)

        short_labels = [t.replace(".TW", "") for t in tickers]
        bars = ax.barh(short_labels, values, color=colors, alpha=0.82,
                       edgecolor=_C["border"], linewidth=0.6)
        ax.set_xlim(0, 62)
        ax.axvline(x=20, color=_C["text2"], linestyle="--", alpha=0.35, linewidth=0.8)
        _dark_ax(ax, label)

        for bar, val in zip(bars, values):
            if val > 0.5:
                ax.text(val + 0.8, bar.get_y() + bar.get_height() / 2,
                        f"{val:.1f}%", va="center", ha="left",
                        color=_C["text"], fontsize=8.5)

    fig.suptitle("各模型最佳化權重比較", color=_C["text"],
                 fontsize=14, fontweight="bold", y=1.01)
    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=_C["bg"])
    plt.close(fig)
    print(f"  💾  權重比較圖 → {save_path.name}")


def plot_cumulative_returns(
    returns: pd.DataFrame,
    weights_dict: dict[str, pd.DataFrame | None],
    save_path: Path,
) -> None:
    """
    各模型歷史回測累積報酬率曲線（In-Sample Backtest）。

    ⚠ 注意：這是訓練集內回測，存在樣本內偏差（in-sample bias）。
    僅供風險特性視覺化參考，不代表未來績效。
    """
    colors_seq = [_C["grn"], _C["blu"], _C["yel"], _C["pur"]]

    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor(_C["bg"])
    ax.set_facecolor(_C["surface"])

    for (label, w), color in zip(weights_dict.items(), colors_seq):
        if w is None or w.empty:
            continue
        valid_t = [t for t in TICKERS if t in w.index and t in returns.columns]
        w_arr   = np.array([_safe_weight(w, t) for t in valid_t])
        port_r  = returns[valid_t].values @ w_arr
        cum_r   = np.cumprod(1 + port_r)
        ax.plot(returns.index, cum_r, label=label, color=color,
                linewidth=1.6, alpha=0.9)

    ax.axhline(y=1.0, color=_C["text2"], linestyle="--", alpha=0.4, linewidth=0.8)
    _dark_ax(ax, "歷史回測累積報酬率（In-Sample）")
    ax.set_xlabel("日期", color=_C["text2"])
    ax.set_ylabel("累積報酬（初始淨值 = 1）", color=_C["text2"])

    leg = ax.legend(facecolor=_C["surface"], edgecolor=_C["border"],
                    labelcolor=_C["text"], fontsize=10, loc="upper left")

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=_C["bg"])
    plt.close(fig)
    print(f"  💾  累積報酬圖 → {save_path.name}")


def plot_correlation_heatmap(returns: pd.DataFrame, save_path: Path) -> None:
    """
    動態相關係數熱力圖：視覺化 VOO / 0050 / 00875 的結構性連動程度。
    深紅色 = 高正相關（分散效果差），深藍色 = 高負相關（分散效果好）。
    """
    corr = returns.corr()

    fig, ax = plt.subplots(figsize=(7, 6))
    fig.patch.set_facecolor(_C["bg"])
    ax.set_facecolor(_C["surface"])

    n = len(corr)
    # 自製熱力圖（避免依賴 seaborn）
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(colors=_C["text2"], labelsize=8)
    cbar.set_label("相關係數 ρ", color=_C["text2"], fontsize=9)

    short_labels = [t.replace(".TW", "") for t in corr.columns]
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(short_labels, color=_C["text"], fontsize=9)
    ax.set_yticklabels(short_labels, color=_C["text"], fontsize=9)

    for i in range(n):
        for j in range(n):
            val = corr.iloc[i, j]
            text_color = _C["text"] if abs(val) < 0.7 else "white"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=9, color=text_color, fontweight="bold")

    _dark_ax(ax, "資產相關係數矩陣（Pearson）")
    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=_C["bg"])
    plt.close(fig)
    print(f"  💾  相關係數熱力圖 → {save_path.name}")


# ══════════════════════════════════════════════════════════════════════════
# §6  再平衡邏輯（含 GRID 摩擦力優化）
# ══════════════════════════════════════════════════════════════════════════

def compute_rebalance_signals(
    current: dict,
    optimal: dict,
    threshold: float = REBALANCE_THRESHOLD,
) -> dict:
    """
    比對當前實際部位與最佳化目標，生成再平衡行動信號。

    GRID 特殊處理（摩擦力優化）：
    - 因複委託摩擦力，GRID 採「定期定額累積、每半年手動下單一次」
    - 若 GRID 偏差超過閾值，標記為「半年排程」而非「立即執行」
    - GRID 的資金差額在下次排程日前，暫時累積於 VGIT（TYD 儲蓄池）

    VGIT 特殊提示：
    - 若 VGIT 信號為「減碼」，提示可將多餘資金轉換為 TYD 觸發資金
    """
    tickers = [t for t in TICKERS if t in current or t in optimal]
    signals = {}

    print(f"\n{'='*58}")
    print(f"  § 6  再平衡分析  閾值：±{threshold*100:.0f}%")
    print(f"{'='*58}")
    print(f"  {'資產':<13} {'當前':>7} {'目標':>7} {'偏差':>8} {'信號'}")
    print(f"  {'-'*55}")

    for t in tickers:
        cur  = current.get(t, 0.0)
        opt  = optimal.get(t, 0.0)
        dev  = opt - cur
        trig = abs(dev) > threshold
        is_grid = TICKER_META.get(t, {}).get("rebal") == "semi-annual"

        if not trig:
            action = "✅ 無需調整"
        elif is_grid:
            action = "📅 半年排程（累積中）"
        elif dev > 0:
            action = "🔼 增持（立即）"
        else:
            action = "🔽 減持（立即）"

        extra = ""
        if t == "VGIT" and trig and dev < 0:
            extra = "  → 考慮轉為 TYD 儲蓄"
        if t == "GRID" and trig:
            extra = f"  → 差額 {abs(dev)*100:.1f}% 暫存 VGIT"

        print(f"  {t:<13} {cur*100:>6.1f}% {opt*100:>6.1f}% {dev*100:>+7.1f}%  {action}{extra}")

        signals[t] = {
            "current":     cur,
            "optimal":     opt,
            "deviation":   dev,
            "triggered":   trig,
            "direction":   "buy" if dev > 0 else "sell",
            "semi_annual": is_grid,
            "action":      action,
        }

    triggered = [t for t, s in signals.items() if s["triggered"]]
    print(f"\n  ⚡ 觸發再平衡：{len(triggered)} / {len(tickers)} 個資產")
    if triggered:
        print(f"     標的：{triggered}")

    return signals


# ══════════════════════════════════════════════════════════════════════════
# §7  Obsidian Markdown 自動化報告
# ══════════════════════════════════════════════════════════════════════════

def generate_obsidian_report(
    weights_dict: dict,
    metrics_list: list,
    rebal_signals: dict,
    optimal_key: str,
    report_dir: Path,
    prices: pd.DataFrame,
) -> Path:
    """
    生成 Obsidian 相容的 Markdown 報告。

    格式規範：
    - YAML frontmatter（date, tags, optimal_model）
    - Obsidian callout（> [!info], > [!warning]）
    - 內嵌圖片（![[filename.png]]）
    - WikiLink 風格標籤
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{REPORT_DATE}-portfolio-optimization.md"

    L = []   # lines buffer

    # ── YAML Frontmatter ──────────────────────────────────────────────────
    L += [
        "---",
        f"date: {REPORT_DATE}",
        f"tags: [portfolio, optimization, riskfolio, quant, 量化投資]",
        f"data_start: {START_DATE}",
        f"data_end: {str(prices.index[-1].date())}",
        f"optimal_model: \"{optimal_key}\"",
        f"rf_annual: {RF_ANNUAL}",
        f"alpha: {ALPHA}",
        f"riskfolio_version: \"{_RISKFOLIO_VER}\"",
        "---",
        "",
    ]

    # ── 標題 ──────────────────────────────────────────────────────────────
    L += [
        f"# 投資組合最佳化報告 ── {REPORT_DATE}",
        "",
        "> [!info] 方法論摘要",
        "> **底層框架**：Riskfolio-Lib + CVXPY 凸最佳化求解器",
        "> **理論依據**：Dany Cajas《Advanced Portfolio Optimization》",
        "> **警告**：所有結果為歷史樣本內分析，不構成任何投資建議。",
        "",
    ]

    # ── §1  組合標的 ──────────────────────────────────────────────────────
    L += ["## 一、組合標的", ""]
    L.append("| 代碼 | 名稱 | 幣別 | 類型 | 下限 | 上限 | 再平衡方式 |")
    L.append("|------|------|:----:|:----:|:----:|:----:|:----------:|")
    for t in TICKERS:
        m       = TICKER_META[t]
        lo, hi  = ASSET_BOUNDS[t]
        rb      = "半年手動" if m["rebal"] == "semi-annual" else "演算法觸發"
        L.append(f"| `{t}` | {m['name']} | {m['ccy']} | {m['group']} "
                 f"| {lo*100:.0f}% | {hi*100:.0f}% | {rb} |")
    L.append("")

    # ── §2  績效指標 ──────────────────────────────────────────────────────
    L += ["## 二、各模型績效指標比較", ""]
    L.append("| 模型 | 年化報酬 | 年化波動 | 夏普比率 | CVaR(年化,5%) | 最大回撤 |")
    L.append("|------|:-------:|:-------:|:-------:|:-------------:|:-------:|")
    for m in metrics_list:
        if not m or "ann_ret" not in m:
            continue
        star = " ⭐" if m["label"] == optimal_key else ""
        L.append(
            f"| **{m['label']}{star}** "
            f"| {m['ann_ret']:+.2f}% "
            f"| {m['ann_vol']:.2f}% "
            f"| {m['sharpe']:.3f} "
            f"| {m['cvar_ann']:.2f}% "
            f"| {m['mdd']:.2f}% |"
        )
    L.append("")
    L.append(f"> [!tip] 最優模型（最高夏普比率）：**{optimal_key}**")
    L.append("")

    # ── §3  各模型權重 ────────────────────────────────────────────────────
    L += ["## 三、各模型最佳化權重", ""]
    model_keys = list(weights_dict.keys())
    L.append("| 資產 | " + " | ".join(model_keys) + " | 當前持有 |")
    L.append("|------|" + "|".join(["------:"] * (len(model_keys) + 1)) + "|")
    for t in TICKERS:
        row = f"| `{t}` |"
        for k in model_keys:
            w = weights_dict.get(k)
            row += f" {_safe_weight(w, t)*100:.1f}% |"
        row += f" {CURRENT_WEIGHTS.get(t, 0)*100:.1f}% |"
        L.append(row)
    L.append("")

    # ── §4  再平衡信號 ────────────────────────────────────────────────────
    L += [f"## 四、再平衡信號（基準：{optimal_key}）", ""]
    L.append(f"> [!note] 觸發閾值：偏差絕對值 > {REBALANCE_THRESHOLD*100:.0f}%")
    L.append("")
    L.append("| 資產 | 當前 | 目標 | 偏差 | 信號 |")
    L.append("|------|:----:|:----:|:----:|------|")
    for t, sig in rebal_signals.items():
        arrow = "▲" if sig["direction"] == "buy" else "▼"
        L.append(
            f"| `{t}` "
            f"| {sig['current']*100:.1f}% "
            f"| {sig['optimal']*100:.1f}% "
            f"| {arrow} {abs(sig['deviation'])*100:.1f}% "
            f"| {sig['action']} |"
        )
    L.append("")

    # ── §5  分析圖表（Obsidian ![[]] 語法）─────────────────────────────────
    L += ["## 五、分析圖表", ""]
    charts = [
        ("correlation_heatmap.png", "相關係數矩陣（Pearson）"),
        ("dendrogram.png",          "Ward 層次聚類樹狀圖"),
        ("weights_comparison.png",  "各模型權重比較"),
        ("cumulative_returns.png",  "歷史回測累積報酬率"),
        ("risk_con_mv.png",         "邊際風險貢獻 — MV"),
        ("risk_con_cvar.png",       "邊際風險貢獻 — CVaR"),
        ("risk_con_cdar.png",       "邊際風險貢獻 — CDaR"),
        ("risk_con_hrp.png",        "邊際風險貢獻 — HRP"),
    ]
    for fname, caption in charts:
        L.append(f"### {caption}")
        L.append(f"![[{fname}]]")
        L.append("")

    # ── §6  批判性方法論檢視 ──────────────────────────────────────────────
    L += [
        "## 六、方法論批判性檢視",
        "",
        "> [!warning] 已知限制與使用注意事項",
        ">",
        "> **1. 貨幣異質性（最重要）**",
        ">    TWD 標的（0050、00875）與 USD 標的以本地幣計算報酬率。",
        ">    此分析隱含「TWD/USD 匯率不影響相對風險」的假設，",
        ">    台幣大幅升值時，USD 標的的 TWD 換算報酬率將系統性降低。",
        ">",
        "> **2. 科技/AI 結構性共線性**",
        ">    VOO、0050、00875 同時包含大量半導體與 AI 曝險。",
        ">    樹狀圖若顯示三者歸入同一早期分支（距離 < 0.3），",
        ">    代表現有衛星配置未能真正分散核心科技風險。",
        ">",
        "> **3. Ledoit-Wolf 收縮偏差**",
        ">    5 個資產 / 1000+ 觀測，收縮比例估計趨向 0，",
        ">    等同退化為樣本共變異數矩陣，收縮優勢有限。",
        ">",
        "> **4. HRP 約束相容性**",
        ">    HRP 是啟發式演算法（非凸最佳化），",
        ">    ASSET_BOUNDS 的個別下限約束**不被 HRP 遵守**，",
        ">    HRP 結果可能低於設定下限，須在事後截斷並重新正規化。",
        ">",
        "> **5. In-Sample 回測偏差**",
        ">    最佳化用同一段歷史數據估計參數並計算績效，",
        ">    必然存在過擬合傾象，實際 Out-of-Sample 績效通常更低。",
        ">",
        "> **6. CVaR/CDaR 黑天鵝盲點**",
        ">    歷史場景法只能告訴你「過去的尾端風險」。",
        ">    台海地緣衝擊、半導體禁運等未出現於歷史的情境，",
        ">    這兩種模型都無法量化。",
        "",
        "---",
        f"*自動生成 by `portfolio_optimizer.py` | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | riskfolio-lib {_RISKFOLIO_VER}*",
    ]

    path.write_text("\n".join(L), encoding="utf-8")
    print(f"\n  📝  Obsidian 報告 → {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════
# §8  主流程
# ══════════════════════════════════════════════════════════════════════════

def main(no_plot: bool = False) -> dict:
    print(f"\n{'='*58}")
    print(f"  量化投資組合凸最佳化引擎")
    print(f"  riskfolio-lib {_RISKFOLIO_VER}  |  {REPORT_DATE}")
    print(f"  資料期間：{START_DATE} → {END_DATE}")
    print(f"{'='*58}")

    # ── 建立輸出目錄 ─────────────────────────────────────────────────────
    report_dir = OUTPUT_DIR / REPORT_DATE
    report_dir.mkdir(parents=True, exist_ok=True)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 1  資料準備
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    prices  = download_prices(TICKERS, START_DATE, END_DATE)
    returns = compute_returns(prices, method="log")

    if len(returns) < 200:
        print("❌ 有效觀測值不足 200 筆，無法進行可靠的共變異數估計。")
        sys.exit(1)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 2  建立 Portfolio & 約束
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print(f"\n{'='*58}")
    print(f"  § 2  建立 Portfolio 物件 & 套用 CVXPY 約束")
    print(f"{'='*58}")
    port = build_portfolio(returns)
    apply_constraints(port, TICKERS)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 3  四種最佳化
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print(f"\n{'='*58}")
    print(f"  § 3  執行四種最佳化模式")
    print(f"{'='*58}")
    w_mv   = optimize_mv(port)
    w_cvar = optimize_cvar(port)
    w_cdar = optimize_cdar(port)
    w_hrp  = optimize_hrp(port, returns)

    weights_dict: dict[str, pd.DataFrame | None] = {
        "模式A｜MV":   w_mv,
        "模式B｜CVaR": w_cvar,
        "模式C｜CDaR": w_cdar,
        "模式D｜HRP":  w_hrp,
    }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 4  績效指標
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print(f"\n{'='*58}")
    print(f"  § 4  計算績效指標")
    print(f"{'='*58}")
    metrics_list = [
        compute_metrics(w_mv,   returns, "模式A｜MV"),
        compute_metrics(w_cvar, returns, "模式B｜CVaR"),
        compute_metrics(w_cdar, returns, "模式C｜CDaR"),
        compute_metrics(w_hrp,  returns, "模式D｜HRP"),
    ]

    print(f"\n  {'模型':<15} {'夏普':>7} {'年化報酬':>10} {'年化波動':>10} {'MDD':>8}")
    print(f"  {'-'*53}")
    for m in metrics_list:
        if "sharpe" in m:
            print(f"  {m['label']:<15} {m['sharpe']:>7.3f} "
                  f"{m['ann_ret']:>+9.2f}% {m['ann_vol']:>9.2f}% {m['mdd']:>+7.2f}%")

    # 選出最高夏普比率的模型
    valid_m = [m for m in metrics_list if "sharpe" in m]
    best    = max(valid_m, key=lambda x: x["sharpe"]) if valid_m else {"label": "模式D｜HRP", "sharpe": 0}
    optimal_key = best["label"]
    optimal_w   = weights_dict.get(optimal_key)
    print(f"\n  🏆 最優模型：{optimal_key}（Sharpe = {best.get('sharpe', 'N/A'):.3f}）")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 5  視覺化
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not no_plot:
        print(f"\n{'='*58}")
        print(f"  § 5  生成分析圖表")
        print(f"{'='*58}")

        plot_correlation_heatmap(returns, report_dir / "correlation_heatmap.png")
        plot_dendrogram(returns, report_dir / "dendrogram.png")
        plot_weights_comparison(weights_dict, TICKERS, CURRENT_WEIGHTS,
                                report_dir / "weights_comparison.png")
        plot_cumulative_returns(returns, weights_dict,
                                report_dir / "cumulative_returns.png")

        rm_map = [
            ("模式A｜MV",   "MV",   w_mv,   "risk_con_mv.png"),
            ("模式B｜CVaR", "CVaR", w_cvar, "risk_con_cvar.png"),
            ("模式C｜CDaR", "CDaR", w_cdar, "risk_con_cdar.png"),
            ("模式D｜HRP",  "MV",   w_hrp,  "risk_con_hrp.png"),
        ]
        for label, rm, w, fname in rm_map:
            plot_risk_contributions(w, port, returns, rm, label,
                                    report_dir / fname)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 6  再平衡分析
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if optimal_w is not None and not optimal_w.empty:
        optimal_dict = {t: _safe_weight(optimal_w, t) for t in TICKERS}
        # 正規化（避免浮點誤差導致總和非 1）
        total = sum(optimal_dict.values())
        if total > 1e-6:
            optimal_dict = {t: v / total for t, v in optimal_dict.items()}
    else:
        # Fallback：等權配置
        optimal_dict = {t: 1.0 / len(TICKERS) for t in TICKERS}
        print(f"  ⚠  最優模型無效，再平衡基準改用等權配置")

    rebal_signals = compute_rebalance_signals(
        current=CURRENT_WEIGHTS,
        optimal=optimal_dict,
        threshold=REBALANCE_THRESHOLD,
    )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # § 7  Obsidian 報告 & JSON 輸出
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print(f"\n{'='*58}")
    print(f"  § 7  輸出報告")
    print(f"{'='*58}")

    generate_obsidian_report(
        weights_dict  = weights_dict,
        metrics_list  = metrics_list,
        rebal_signals = rebal_signals,
        optimal_key   = optimal_key,
        report_dir    = report_dir,
        prices        = prices,
    )

    # JSON 供外部系統（LINE Bot / 儀表板）消費
    def _serialize(v):
        if isinstance(v, (np.floating, np.integer)):
            return float(v)
        if isinstance(v, bool):
            return v
        return v

    json_payload = {
        "date":          REPORT_DATE,
        "optimal_model": optimal_key,
        "weights": {
            k: {t: round(_safe_weight(w, t), 6) for t in TICKERS}
            for k, w in weights_dict.items()
            if w is not None and not w.empty
        },
        "metrics": metrics_list,
        "rebalance": {
            t: {k: _serialize(v) for k, v in sig.items()}
            for t, sig in rebal_signals.items()
        },
        "asset_bounds":    ASSET_BOUNDS,
        "current_weights": CURRENT_WEIGHTS,
        "rf_annual":       RF_ANNUAL,
        "alpha":           ALPHA,
    }

    json_path = report_dir / "portfolio_optimization.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=2)
    print(f"  💾  JSON → {json_path}")

    print(f"\n{'='*58}")
    print(f"  ✅ 完成！報告目錄：{report_dir}")
    print(f"{'='*58}\n")

    return json_payload


# ══════════════════════════════════════════════════════════════════════════
# §9  CLI 入口
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="量化投資組合凸最佳化引擎 (Riskfolio-Lib)"
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="跳過圖表生成（適合 CI / 無 GUI 環境）",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=START_DATE,
        help=f"資料起始日期（預設：{START_DATE}）",
    )
    parser.add_argument(
        "--rf",
        type=float,
        default=RF_ANNUAL,
        help=f"年化無風險利率（預設：{RF_ANNUAL}）",
    )
    args = parser.parse_args()

    # 覆蓋全域設定（允許 CLI 動態傳入）
    START_DATE = args.start
    RF_ANNUAL  = args.rf
    RF_DAILY   = RF_ANNUAL / 252

    result = main(no_plot=args.no_plot)
