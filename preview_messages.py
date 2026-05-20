"""
LINE Flex Message 預覽腳本
產生夜盤的所有 Flex Message JSON，輸出到 preview_evening.json
"""
import json
from src.quant_engine import ETFSignal, MacroIndicators
from src.line_builder import (
    build_text_message, build_macro_card,
    build_etf_card,
)

DATE = "2026/05/20"

# ── 模擬總經指標 ──────────────────────────────────────────
macro = MacroIndicators(
    us10y=4.45, us02y=4.88, cpi_yoy=3.2,
    breakeven=2.31, real_rate=2.14, yield_curve=-0.43,
    sahm_indicator=0.18, sahm_triggered=False,
    michez_m=0.12, michez_triggered=False,
    recession_prob=14.0,
    hy_spread=3.8, credit_signal="正常",
    ism_pmi=[48.5, 49.1, 49.8], pmi_second_deriv=0.35,
    unemployment_3m=[4.0, 4.1, 4.1],
)

# ── 模擬 ETF 訊號 ─────────────────────────────────────────
sig_voo = ETFSignal(
    ticker="VOO", name="Vanguard S&P 500",
    price=512.74, change_pct=1.15,
    ema_200=488.2, z_score=1.02,
    sentiment_score=63.0, sentiment_label="偏多",
    vix=18.4, vix_bollinger_break=False,
    cape=27.8, erp=1.95, predicted_10y_return=3.2,
    kelly_f=0.3619,
    cycle_phase="🟢 成長期（常態擴張）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=3.2145, macd_signal=2.1087, macd_hist=1.1058,
    # 技術指標
    rsi=61.5, bb_pct=0.72, sharpe_1y=1.45, max_drawdown=-6.2,
    # 多 Agent 信心
    technical_score=58.8, value_score=47.3, macro_score=61.1,
    combined_confidence=55.4, confidence_signal="買  入",
    news_headlines=["S&P 500 hits record high on tech rally", "Fed signals rate cuts ahead"],
)

sig_qqq = ETFSignal(
    ticker="QQQ", name="Invesco QQQ Trust",
    price=448.32, change_pct=1.42,
    ema_200=421.5, z_score=1.18,
    sentiment_score=65.0, sentiment_label="偏多",
    vix=18.4, vix_bollinger_break=False,
    cape=35.2, erp=0.84, predicted_10y_return=2.1,
    kelly_f=0.3517,
    cycle_phase="🟢 成長期（常態擴張）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=2.8134, macd_signal=1.9245, macd_hist=0.8889,
    # 技術指標
    rsi=63.2, bb_pct=0.75, sharpe_1y=1.52, max_drawdown=-7.1,
    # 多 Agent 信心
    technical_score=60.1, value_score=38.2, macro_score=61.1,
    combined_confidence=53.8, confidence_signal="買  入",
    news_headlines=["Nasdaq tech rally continues", "AI stocks lead market gains"],
)

sig_gld = ETFSignal(
    ticker="GLD", name="SPDR Gold Shares",
    price=234.56, change_pct=-0.31,
    ema_200=218.3, z_score=1.48,
    sentiment_score=55.0, sentiment_label="中性",
    vix=18.4, vix_bollinger_break=False,
    cape=None, erp=None, predicted_10y_return=None,
    kelly_f=0.1833,
    cycle_phase="🟢 成長期（常態擴張）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=1.0234, macd_signal=1.2156, macd_hist=-0.1922,
    # 技術指標
    rsi=54.7, bb_pct=0.81, sharpe_1y=0.92, max_drawdown=-4.8,
    # 多 Agent 信心（無估值）
    technical_score=52.4, value_score=None, macro_score=61.1,
    combined_confidence=55.8, confidence_signal="買  入",
    news_headlines=["Gold surges amid geopolitical uncertainty", "Central banks increase gold reserves"],
)

sig_vgit = ETFSignal(
    ticker="VGIT", name="Vanguard Intermediate-Term Treasury",
    price=58.42, change_pct=0.12,
    ema_200=57.80, z_score=0.21,
    sentiment_score=48.0, sentiment_label="中性",
    vix=18.4, vix_bollinger_break=False,
    cape=None, erp=None, predicted_10y_return=None,
    kelly_f=0.2833,
    cycle_phase="🟢 成長期（常態擴張）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=0.0412, macd_signal=0.0287, macd_hist=0.0125,
    # 技術指標
    rsi=50.3, bb_pct=0.55, sharpe_1y=0.45, max_drawdown=-3.2,
    # 多 Agent 信心（無估值）
    technical_score=51.2, value_score=None, macro_score=58.4,
    combined_confidence=54.1, confidence_signal="持  有",
    news_headlines=[],
)

sig_grid = ETFSignal(
    ticker="GRID", name="First Trust NASDAQ Clean Edge Smart Grid",
    price=112.75, change_pct=0.68,
    ema_200=105.30, z_score=0.87,
    sentiment_score=57.0, sentiment_label="中性偏多",
    vix=18.4, vix_bollinger_break=False,
    cape=None, erp=None, predicted_10y_return=None,
    kelly_f=0.1917,
    cycle_phase="🟢 成長期（常態擴張）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=0.6234, macd_signal=0.4812, macd_hist=0.1422,
    # 技術指標
    rsi=56.8, bb_pct=0.68, sharpe_1y=0.78, max_drawdown=-9.4,
    # 多 Agent 信心（無估值）
    technical_score=55.3, value_score=None, macro_score=58.4,
    combined_confidence=56.5, confidence_signal="買  入",
    news_headlines=["Smart grid investment surges on energy transition"],
)

# ── 夜盤訊息 ─────────────────────────────────────────────
evening_signals = [sig_voo, sig_qqq, sig_gld, sig_vgit, sig_grid]
evening_messages = [
    build_text_message("evening", macro, evening_signals, DATE),
    build_macro_card(macro, DATE),
    build_etf_card(sig_voo, DATE, "evening"),
    build_etf_card(sig_qqq, DATE, "evening"),
]

# ── 輸出 JSON ─────────────────────────────────────────────
with open("preview_evening.json", "w", encoding="utf-8") as f:
    json.dump(evening_messages, f, ensure_ascii=False, indent=2)

print("OK preview_evening.json")

# 印出文字訊息讓 terminal 確認
print("\n── 夜盤文字 ──")
print(evening_messages[0]["text"])
