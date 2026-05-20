"""
DCC-GARCH(1,1) 量化資產配置分析
改編自 github.com/learningprogram0108/macro_news/blob/main/dcc_garch.py
TLT → VGIT（Vanguard Intermediate-Term Treasury）
資料來源改用內部 yahoo_daily_close()，移除 yfinance 依賴
"""
from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd
from arch import arch_model
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from src.data_fetcher import yahoo_daily_close, av_daily_close

logger = logging.getLogger(__name__)

TICKERS = ["VOO", "VGIT", "GLD"]
TRADING_DAYS = 252


# ── 1. 資料抓取 ───────────────────────────────────────────────────────────────

def fetch_price_data(tickers: list[str] = TICKERS, av_key=None) -> pd.DataFrame:
    """使用 yahoo_daily_close 取得 2 年日線（約 504 筆）"""
    frames = {}
    for t in tickers:
        s = yahoo_daily_close(t, days=520)
        if s.empty or len(s) < 100:
            if av_key:
                s = av_daily_close(t, av_key, days=520)
        if s.empty or len(s) < 100:
            raise ValueError(f"{t} 價格資料不足，無法估計 DCC-GARCH")
        frames[t] = s

    # 對齊至共同日期（inner join）
    df = pd.DataFrame(frames)
    df = df.dropna()
    if len(df) < 100:
        raise ValueError(f"對齊後資料不足（{len(df)} 筆），無法估計 DCC-GARCH")
    return df


def compute_log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return np.log(prices / prices.shift(1)).dropna()


# ── 2. Stage 1：單變量 GARCH(1,1) ─────────────────────────────────────────────

def fit_garch(series: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """
    回傳 (conditional_vol_daily, standardized_residuals)
    arch_model 以百分比為單位，輸出除以 100 還原
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        am = arch_model(series * 100, vol="GARCH", p=1, q=1, dist="normal")
        res = am.fit(disp="off", show_warning=False)
    cond_vol = res.conditional_volatility / 100
    std_resid = res.std_resid
    return cond_vol.values, std_resid.values


# ── 3. Stage 2：DCC(1,1) ─────────────────────────────────────────────────────

def _dcc_loglikelihood(
    params: np.ndarray,
    std_resids: np.ndarray,
    Q_bar: np.ndarray,
) -> float:
    a, b = params
    if a <= 0 or b <= 0 or a + b >= 1:
        return 1e10

    T, n = std_resids.shape
    Q = Q_bar.copy()
    ll = 0.0

    for t in range(1, T):
        z = std_resids[t - 1]
        Q = (1 - a - b) * Q_bar + a * np.outer(z, z) + b * Q
        diag_sqrt_inv = 1.0 / np.sqrt(np.diag(Q))
        R = Q * np.outer(diag_sqrt_inv, diag_sqrt_inv)
        z_t = std_resids[t]
        try:
            sign, logdet = np.linalg.slogdet(R)
            if sign <= 0:
                return 1e10
            R_inv = np.linalg.inv(R)
            ll += logdet + z_t @ R_inv @ z_t - z_t @ z_t
        except np.linalg.LinAlgError:
            return 1e10

    return 0.5 * ll


def estimate_dcc_params(std_resids: np.ndarray) -> tuple[float, float]:
    T = len(std_resids)
    Q_bar = std_resids.T @ std_resids / T

    result = minimize(
        _dcc_loglikelihood,
        x0=[0.05, 0.90],
        args=(std_resids, Q_bar),
        method="SLSQP",
        bounds=[(1e-6, 0.3), (1e-6, 0.999)],
        constraints={"type": "ineq", "fun": lambda p: 1 - p[0] - p[1] - 1e-6},
        options={"maxiter": 500, "ftol": 1e-9},
    )
    if not result.success:
        logger.warning("DCC 最佳化未完全收斂，使用目前最佳解")
    return float(result.x[0]), float(result.x[1])


def compute_dcc_correlation(
    std_resids: np.ndarray,
    alpha: float,
    beta: float,
    Q_bar: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """回傳 (R_series T×n×n, R_current n×n)"""
    T, n = std_resids.shape
    Q = Q_bar.copy()
    R_series = np.zeros((T, n, n))

    for t in range(T):
        if t > 0:
            z = std_resids[t - 1]
            Q = (1 - alpha - beta) * Q_bar + alpha * np.outer(z, z) + beta * Q
        diag_sqrt_inv = 1.0 / np.sqrt(np.diag(Q))
        R = Q * np.outer(diag_sqrt_inv, diag_sqrt_inv)
        R_series[t] = R

    return R_series, R_series[-1]


# ── 4. 共變異數矩陣 ────────────────────────────────────────────────────────────

def build_covariance(sigmas_current: np.ndarray, R_current: np.ndarray) -> np.ndarray:
    """年化共變異數矩陣： H = D × R × D × 252"""
    D = np.diag(sigmas_current)
    return D @ R_current @ D * TRADING_DAYS


# ── 5. HRP：Hierarchical Risk Parity ─────────────────────────────────────────

def _get_quasi_diag(node, leaves: list[int]) -> None:
    """遞迴取得葉節點排序（quasi-diagonalization）"""
    if node.is_leaf():
        leaves.append(node.id)
    else:
        _get_quasi_diag(node.get_left(), leaves)
        _get_quasi_diag(node.get_right(), leaves)


def _cluster_var(weights: np.ndarray, H: np.ndarray, indices: list[int]) -> float:
    """子叢集的組合變異數（以逆變異數加權）"""
    sub_H = H[np.ix_(indices, indices)]
    inv_var = 1.0 / np.diag(sub_H)
    w = inv_var / inv_var.sum()
    return float(w @ sub_H @ w)


def _recursive_bisection(node, H: np.ndarray, weights: np.ndarray) -> None:
    """遞迴二分：依左右子叢集的組合變異數比例分配權重"""
    if node.is_leaf():
        return

    left_leaves: list[int] = []
    right_leaves: list[int] = []
    _get_quasi_diag(node.get_left(), left_leaves)
    _get_quasi_diag(node.get_right(), right_leaves)

    v_left = _cluster_var(weights, H, left_leaves)
    v_right = _cluster_var(weights, H, right_leaves)

    alpha = 1 - v_left / (v_left + v_right)  # 分給右側的比例

    weights[left_leaves] *= (1 - alpha)
    weights[right_leaves] *= alpha

    _recursive_bisection(node.get_left(), H, weights)
    _recursive_bisection(node.get_right(), H, weights)


def optimize_hrp(H_annual: np.ndarray, R_current: np.ndarray) -> np.ndarray:
    """
    Hierarchical Risk Parity (Lopez de Prado 2016)

    1. 距離矩陣：d_ij = sqrt((1 - rho_ij) / 2)
    2. 單連結階層聚類（single linkage）
    3. 遞迴二分配置
    """
    n = H_annual.shape[0]

    # 距離矩陣
    dist = np.sqrt(np.clip((1 - R_current) / 2, 0, 1))
    np.fill_diagonal(dist, 0.0)
    condensed = squareform(dist, checks=False)

    # 階層聚類（single linkage）
    Z = linkage(condensed, method="single")
    root, _ = to_tree(Z, rd=True)

    # 初始權重均等，遞迴二分調整
    weights = np.ones(n)
    _recursive_bisection(root, H_annual, weights)

    weights = np.maximum(weights, 0)
    return weights / weights.sum()


# ── 6. Risk Parity（等風險貢獻） ──────────────────────────────────────────────

def optimize_risk_parity(H_annual: np.ndarray) -> np.ndarray:
    n = H_annual.shape[0]
    x0 = np.ones(n) / n

    def risk_parity_obj(w: np.ndarray) -> float:
        portfolio_vol_sq = w @ H_annual @ w
        if portfolio_vol_sq < 1e-20:
            return 1e10
        marginal_rc = H_annual @ w
        rc = w * marginal_rc
        target = portfolio_vol_sq / n
        return float(np.sum((rc - target) ** 2))

    result = minimize(
        risk_parity_obj, x0, method="SLSQP",
        bounds=[(1e-6, 1.0)] * n,
        constraints={"type": "eq", "fun": lambda w: np.sum(w) - 1},
        options={"ftol": 1e-14, "maxiter": 2000},
    )
    w = np.maximum(result.x, 0)
    return w / w.sum()


# ── 7. 主入口 ─────────────────────────────────────────────────────────────────

def run_dcc_analysis(tickers: list[str] = TICKERS, av_key=None) -> dict[str, Any]:
    """
    執行完整 DCC-GARCH(1,1) 兩階段分析
    Returns dict with keys: tickers, corr, corr_30d_avg, vol_annual, hrp, risk_parity,
                             dcc_alpha, dcc_beta
    """
    prices = fetch_price_data(tickers, av_key=av_key)
    returns = compute_log_returns(prices)

    # Stage 1：單變量 GARCH(1,1)
    cond_vols, std_resids_list = [], []
    for ticker in tickers:
        vol, sr = fit_garch(returns[ticker])
        min_len = min(len(vol), len(sr))
        cond_vols.append(vol[-min_len:])
        std_resids_list.append(sr[-min_len:])

    min_len = min(len(v) for v in cond_vols)
    cond_vols = [v[-min_len:] for v in cond_vols]
    std_resids_list = [sr[-min_len:] for sr in std_resids_list]
    std_resids = np.column_stack(std_resids_list)  # T × n

    # Stage 2：DCC(1,1)
    Q_bar = std_resids.T @ std_resids / len(std_resids)
    alpha, beta = estimate_dcc_params(std_resids)
    R_series, R_current = compute_dcc_correlation(std_resids, alpha, beta, Q_bar)

    # 當前條件波動率（年化）
    sigmas_current = np.array([v[-1] for v in cond_vols])
    vol_annual = sigmas_current * np.sqrt(TRADING_DAYS)

    # 動態共變異數（年化）
    H_annual = build_covariance(sigmas_current, R_current)

    # 近 30 日平均相關係數
    last30 = min(30, len(R_series))
    R_30d = R_series[-last30:].mean(axis=0)

    # 最佳化配置
    w_hrp = optimize_hrp(H_annual, R_current)
    w_rp  = optimize_risk_parity(H_annual)

    def _pair(R: np.ndarray, i: int, j: int) -> float:
        return float(np.clip(R[i, j], -1, 1))

    # Build pair keys using actual tickers
    t0, t1, t2 = tickers[0], tickers[1], tickers[2]

    return {
        "tickers": tickers,
        "corr": {
            f"{t0}_{t1}": _pair(R_current, 0, 1),
            f"{t0}_{t2}": _pair(R_current, 0, 2),
            f"{t1}_{t2}": _pair(R_current, 1, 2),
        },
        "corr_30d_avg": {
            f"{t0}_{t1}": _pair(R_30d, 0, 1),
            f"{t0}_{t2}": _pair(R_30d, 0, 2),
            f"{t1}_{t2}": _pair(R_30d, 1, 2),
        },
        "vol_annual": {t: float(v) for t, v in zip(tickers, vol_annual)},
        "hrp":        {t: float(w) for t, w in zip(tickers, w_hrp)},
        "risk_parity":{t: float(w) for t, w in zip(tickers, w_rp)},
        "dcc_alpha": alpha,
        "dcc_beta":  beta,
    }


# ── 8. Gemini Prompt 格式化 ────────────────────────────────────────────────────

def _trend_label(current: float, avg30: float) -> str:
    diff = current - avg30
    if abs(diff) < 0.02:
        return "持平"
    return "↑ 上升" if diff > 0 else "↓ 下降"


def format_dcc_for_prompt(d: dict[str, Any]) -> str:
    """格式化 DCC-GARCH 分析結果供 Gemini prompt 使用"""
    tickers = d["tickers"]
    t0, t1, t2 = tickers[0], tickers[1], tickers[2]
    c   = d["corr"]
    c30 = d["corr_30d_avg"]
    v   = d["vol_annual"]
    hrp = d["hrp"]
    rp  = d["risk_parity"]

    k01, k02, k12 = f"{t0}_{t1}", f"{t0}_{t2}", f"{t1}_{t2}"

    lines = [
        "【量化資產配置分析 — DCC-GARCH(1,1)】",
        f"回溯 2 年日線（{' / '.join(tickers)}），DCC α={d['dcc_alpha']:.4f} β={d['dcc_beta']:.4f}",
        "",
        "▌動態條件相關係數（今日估計 vs 近 30 日均值）",
        f"• {t0} ↔ {t1}：{c[k01]:+.3f}（30日均：{c30[k01]:+.3f}）→ {_trend_label(c[k01], c30[k01])}",
        f"• {t0} ↔ {t2}：{c[k02]:+.3f}（30日均：{c30[k02]:+.3f}）→ {_trend_label(c[k02], c30[k02])}",
        f"• {t1} ↔ {t2}：{c[k12]:+.3f}（30日均：{c30[k12]:+.3f}）→ {_trend_label(c[k12], c30[k12])}",
        "",
        "▌條件波動率（年化）",
        f"• {t0}：{v[t0]:.1%}　{t1}：{v[t1]:.1%}　{t2}：{v[t2]:.1%}",
        "",
        "▌最佳化配置建議",
        f"階層風險平價（HRP，Lopez de Prado 2016，d_ij=√((1-ρ)/2)，單連結聚類）：",
        f"  {t0} {hrp[t0]:.1%} / {t1} {hrp[t1]:.1%} / {t2} {hrp[t2]:.1%}",
        f"等風險貢獻（Risk Parity）：",
        f"  {t0} {rp[t0]:.1%} / {t1} {rp[t1]:.1%} / {t2} {rp[t2]:.1%}",
        "",
        "請在分析中直接引用以上量化數字，說明目前配置是否偏離最佳化建議，",
        f"並分析今日總經主線對 {t0}-{t1} 動態相關係數的影響方向。",
    ]
    return "\n".join(lines)


# ── 快速驗證入口 ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_dcc_analysis()
    print(format_dcc_for_prompt(result))
    print("\n--- Raw dict ---")
    for k, v in result.items():
        if k != "tickers":
            print(f"  {k}: {v}")
