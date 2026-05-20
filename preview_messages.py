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
    news_headlines=[
        {
            "title":      "S&P 500 hits record high on tech rally",
            "url":        "https://finance.yahoo.com/news/sp500-record",
            "publisher":  "Reuters",
            "summary":    "The S&P 500 rose 0.8% to close at an all-time high, driven by megacap technology stocks, as investors grew optimistic about potential Fed rate cuts later this year.",
            "title_zh":   "標普500因科技反彈創歷史新高",
            "summary_zh": "標普500上漲0.8%收創歷史新高，受大型科技股帶動，投資人對聯準會今年稍晚降息樂觀預期升溫。",
        },
        {
            "title":      "Fed signals rate cuts ahead",
            "url":        "https://finance.yahoo.com/news/fed-cuts",
            "publisher":  "Bloomberg",
            "summary":    "Federal Reserve officials signaled openness to cutting interest rates as inflation continues to slow toward the 2% target, with markets pricing in two cuts by year end.",
            "title_zh":   "聯準會暗示即將降息",
            "summary_zh": "聯準會官員暗示，隨著通膨持續朝2%目標回落，有意降息，市場已定價年底前降息兩次。",
        },
    ],
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
    news_headlines=[
        {
            "title":      "Nasdaq tech rally continues",
            "url":        "https://finance.yahoo.com/news/nasdaq-rally",
            "publisher":  "CNBC",
            "summary":    "The Nasdaq Composite extended gains for a third consecutive session, led by semiconductor and AI-related names, with the index up 1.2% and approaching its all-time high.",
            "title_zh":   "那斯達克科技反彈持續",
            "summary_zh": "那斯達克綜合指數連續第三個交易日上漲，由半導體和AI相關個股領漲，指數上漲1.2%，逼近歷史高點。",
        },
        {
            "title":      "AI stocks lead market gains",
            "url":        "https://finance.yahoo.com/news/ai-stocks",
            "publisher":  "MarketWatch",
            "summary":    "Artificial intelligence-related stocks outperformed the broader market, with major chipmakers and cloud providers posting gains of 2-4% on strong earnings guidance and expanding AI adoption.",
            "title_zh":   "AI股票領漲市場",
            "summary_zh": "人工智慧相關股票表現超越大盤，主要晶片製造商和雲端業者受強勁獲利指引及AI應用擴展帶動，上漲2-4%。",
        },
    ],
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
    news_headlines=[
        {
            "title":      "Gold surges amid geopolitical uncertainty",
            "url":        "https://finance.yahoo.com/news/gold-surge",
            "publisher":  "Reuters",
            "summary":    "Gold prices climbed 0.9% to $2,350/oz as escalating Middle East tensions and persistent inflation concerns drove safe-haven demand, with technical momentum also supporting the move.",
            "title_zh":   "黃金因地緣政治不確定性飆升",
            "summary_zh": "金價上漲0.9%至每盎司2,350美元，中東局勢升溫及持續通膨疑慮推動避險需求，技術面動能亦提供支撐。",
        },
        {
            "title":      "Central banks increase gold reserves",
            "url":        "https://finance.yahoo.com/news/cb-gold",
            "publisher":  "Financial Times",
            "summary":    "Global central banks purchased a net 290 tonnes of gold in Q1 2026, marking the highest quarterly total since 2023, as reserve diversification away from the US dollar continued.",
            "title_zh":   "各國央行增加黃金儲備",
            "summary_zh": "全球央行2026年第一季淨購入黃金290公噸，為2023年以來最高季度購金量，去美元化的儲備多元化趨勢持續。",
        },
    ],
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
    news_headlines=[
        {
            "title":      "Smart grid investment surges on energy transition",
            "url":        "https://finance.yahoo.com/news/grid-surge",
            "publisher":  "Seeking Alpha",
            "summary":    "Federal infrastructure spending and surging electricity demand from AI data centers are driving a wave of smart grid upgrades, with utilities announcing $45B in planned grid modernization for 2026-2028.",
            "title_zh":   "能源轉型推動智慧電網投資激增",
            "summary_zh": "聯邦基礎建設支出及AI數據中心帶動電力需求激增，正推動大規模智慧電網升級，各公用事業宣布2026至2028年共計450億美元的電網現代化計畫。",
        },
    ],
)

sig_tyd = ETFSignal(
    ticker="TYD", name="Direxion Daily 7-10 Year Treasury Bull 3x",
    price=21.34, change_pct=0.38,
    ema_200=20.15, z_score=0.58,
    sentiment_score=52.0, sentiment_label="中性",
    vix=18.4, vix_bollinger_break=False,
    cape=None, erp=None, predicted_10y_return=None,
    kelly_f=0.1543,
    cycle_phase="🟢 成長期（常態擴張）",
    fund_multiplier=1.0, multiplier_mode="鑑賞家巡航",
    macd_line=0.0312, macd_signal=0.0198, macd_hist=0.0114,
    # 技術指標
    rsi=52.1, bb_pct=0.61, sharpe_1y=0.38, max_drawdown=-12.4,
    # 多 Agent 信心（無估值）
    technical_score=53.8, value_score=None, macro_score=61.1,
    combined_confidence=56.8, confidence_signal="買  入",
    news_headlines=[
        {
            "title":      "Treasury yields rise as Fed holds rates",
            "url":        "https://finance.yahoo.com/news/treasury-yields",
            "publisher":  "Wall Street Journal",
            "summary":    "The 10-year Treasury yield rose 6bps to 4.45% after the Fed held its benchmark rate steady, with Chair Powell citing still-elevated services inflation as a reason to remain patient before easing.",
            "title_zh":   "聯準會維持利率下美債殖利率上升",
            "summary_zh": "聯準會維持基準利率不變後，10年期美債殖利率上升6個基點至4.45%，鮑威爾主席指出服務業通膨仍偏高，寬鬆前需保持耐心。",
        },
    ],
)

# ── 夜盤訊息 ─────────────────────────────────────────────
evening_signals = [sig_voo, sig_qqq, sig_gld, sig_vgit, sig_tyd, sig_grid]
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
