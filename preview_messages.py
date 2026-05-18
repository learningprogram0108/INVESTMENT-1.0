"""
LINE Flex Message 預覽腳本
產生早盤與夜盤的所有 Flex Message JSON，輸出到 preview_morning.json / preview_evening.json
"""
import json
from src.quant_engine import ETFSignal, MacroIndicators
from src.line_builder import (
    build_text_message, build_macro_card,
    build_etf_card, build_bond_card,
)

DATE = "2026/05/17"

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
sig_0050 = ETFSignal(
    ticker="0050.TW", name="元大台灣 50",
    price=195.3, change_pct=0.82,
    ema_200=183.5, z_score=0.64,
    sentiment_score=58.0, sentiment_label="中性偏多",
    vix=18.4, vix_bollinger_break=False,
    cape=22.1, erp=2.38, predicted_10y_return=4.7,
    kelly_f=0.3667,
    cycle_phase="🟢 希望（成長）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=0.8432, macd_signal=0.5217, macd_hist=0.3215,
)

sig_bond = ETFSignal(
    ticker="00679B.TW", name="元大美債 20年",
    price=26.18, change_pct=-0.23,
    ema_200=27.42, z_score=-0.89,
    sentiment_score=41.0, sentiment_label="中性",
    vix=18.4, vix_bollinger_break=False,
    cape=None, erp=None, predicted_10y_return=None,
    kelly_f=0.2857,
    cycle_phase="🟢 希望（成長）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=None, macd_signal=None, macd_hist=None,
)

sig_voo = ETFSignal(
    ticker="VOO", name="Vanguard S&P 500",
    price=512.74, change_pct=1.15,
    ema_200=488.2, z_score=1.02,
    sentiment_score=63.0, sentiment_label="偏多",
    vix=18.4, vix_bollinger_break=False,
    cape=27.8, erp=1.95, predicted_10y_return=3.2,
    kelly_f=0.3619,
    cycle_phase="🟢 希望（成長）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=3.2145, macd_signal=2.1087, macd_hist=1.1058,
)

sig_gld = ETFSignal(
    ticker="GLD", name="SPDR Gold Shares",
    price=234.56, change_pct=-0.31,
    ema_200=218.3, z_score=1.48,
    sentiment_score=55.0, sentiment_label="中性",
    vix=18.4, vix_bollinger_break=False,
    cape=None, erp=None, predicted_10y_return=None,
    kelly_f=0.1833,
    cycle_phase="🟢 希望（成長）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=1.0234, macd_signal=1.2156, macd_hist=-0.1922,
)

# ── 早盤訊息 ─────────────────────────────────────────────
morning_signals = [sig_0050, sig_bond]
morning_messages = [
    build_text_message("morning", macro, morning_signals, DATE),
    build_macro_card(macro, DATE),
    build_etf_card(sig_0050, DATE, "morning"),
    build_bond_card(sig_bond, macro, DATE),
]

# ── 夜盤訊息 ─────────────────────────────────────────────
evening_signals = [sig_voo, sig_gld]
evening_messages = [
    build_text_message("evening", macro, evening_signals, DATE),
    build_etf_card(sig_voo, DATE, "evening"),
    build_etf_card(sig_gld, DATE, "evening"),
]

# ── 輸出 JSON ─────────────────────────────────────────────
with open("preview_morning.json", "w", encoding="utf-8") as f:
    json.dump(morning_messages, f, ensure_ascii=False, indent=2)

with open("preview_evening.json", "w", encoding="utf-8") as f:
    json.dump(evening_messages, f, ensure_ascii=False, indent=2)

print("OK preview_morning.json")
print("OK preview_evening.json")

# 印出文字訊息讓 terminal 確認
print("\n── 早盤文字 ──")
print(morning_messages[0]["text"])
print("\n── 夜盤文字 ──")
print(evening_messages[0]["text"])
