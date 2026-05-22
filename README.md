# LINE 投資夜盤報 + 投資儀表板

每日 22:00 TST 自動推送美股 ETF 量化訊號至 LINE，並同步更新 Cloudflare Pages 響應式投資儀表板。  
另提供按需執行的**凸最佳化配置引擎**，以 Riskfolio-Lib 產出 MV / CVaR / CDaR / HRP 四種配置方案。

**Web App（即時儀表板）**：[https://investment-dashboard-cva.pages.dev](https://investment-dashboard-cva.pages.dev)

---

## 系統架構

```
┌─────────────────────────────────────────────────────────────────────┐
│  GitHub Actions（ubuntu-latest）手動觸發 / 22:00 TST               │
│                                                                     │
│  python main.py  SESSION=evening                                    │
│                                                                     │
│  ┌──────────────┐  ┌───────────────────────────┐  ┌─────────────┐  │
│  │ data_fetcher │  │       quant_engine         │  │   gemini_   │  │
│  │              │  │                            │  │   summary   │  │
│  │ OpenBB SDK   │  │ EMA200 / Z-Score           │  │             │  │
│  │  ↳ yfinance  │→ │ MACD(12/26/9) / RSI / BB%B │  │ Layer 1     │  │
│  │  ↳ FRED      │  │ Sharpe / Max Drawdown      │→ │ 體制敘事    │  │
│  │  ↳ AV        │  │ 多 Agent 信心評分           │  │ (100字事實) │  │
│  │ Direct API   │  │ ─────────────────────────  │  │             │  │
│  │  fallback    │  │ detect_market_regime()     │  │ Layer 2     │  │
│  └──────────────┘  │  → RISK_ON/TRANSITION/OFF  │  │ ETF 逐行    │  │
│                    │ calc_signal_light()         │  │ 信號說明    │  │
│                    │  → 🟢加碼/🟡維持/🔴減碼   │  │             │  │
│                    │ eval_tail_risks()           │  │ 新聞批量    │  │
│                    │  → 7 項硬編碼警報           │  │ 繁中翻譯    │  │
│                    │ TYD 時機評分 / 分組 HRP     │  └─────────────┘  │
│                    │ DCC-GARCH(1,1)              │                   │
│                    └───────────────────────────┘                   │
│                                    │                               │
│               ┌────────────────────┴─────────────────┐             │
│               ▼                                      ▼             │
│        line_builder                          docs/data/            │
│        Flex Message 組裝                     report.json 輸出      │
└──────┬────────────────────────────────────────────┬───────────────-┘
       │ LINE Push API                              │ git push
       ▼                                            ▼
  你的 LINE 手機                       Cloudflare Pages 自動重部署
                                       investment-dashboard-cva.pages.dev

┌─────────────────────────────────────────────────────────────────────┐
│  GitHub Actions（手動觸發）— 量化投資組合最佳化                     │
│                                                                     │
│  python portfolio_optimizer.py --no-plot --start 2020-01-01        │
│                                                                     │
│  Riskfolio-Lib v7                                                   │
│  ┌────────────┐  ┌──────────────────────────────────────────────┐   │
│  │ yfinance   │→ │ 模式A MV  — Markowitz 均值-方差最佳化        │   │
│  │ 5 資產     │  │ 模式B CVaR — Rockafellar & Uryasev           │   │
│  │ 2020-今    │  │ 模式C CDaR — Chekhlov 最大回撤風險           │   │
│  └────────────┘  │ 模式D HRP  — Ward 聚類層次風險平價           │   │
│                  └──────────────────────────────────────────────┘   │
│                         │                                           │
│            ┌────────────┴──────────────────┐                        │
│            ▼                               ▼                        │
│  portfolio_reports/YYYY-MM-DD/        docs/data/                    │
│  ├─ *.md（Obsidian 報告）             portfolio_optimization.json   │
│  └─ portfolio_optimization.json  ────→ 儀表板凸最佳化卡片           │
└─────────────────────────────────────────────────────────────────────┘
```

### 資料擷取優先順序

| 資料 | 第一優先（OpenBB SDK） | 第二備援（直接 API） |
|------|---------------------|-------------------|
| ETF 日線價格 | OpenBB yfinance | Yahoo Finance v8 Chart API |
| FRED 指標 | OpenBB fred | FRED REST API |
| 美國公債殖利率 | OpenBB alpha_vantage | Alpha Vantage TREASURY_YIELD |
| VIX | OpenBB/FRED VIXCLS → OpenBB/Yahoo ^VIX | Alpha Vantage VIXY 代理 |
| 新聞標題 | OpenBB yfinance equity.news | Yahoo Finance Search API |
| HY 信用利差 | FRED BAMLH0A0HYM2 | 內建估算值 4.5% |
| AI 分析 / 新聞翻譯 | Gemini 3.1 Flash Lite | 自動略過 |

> **OpenBB 安裝說明**：CI 環境不安裝 openbb（pip 解析時間過長），程式自動使用直接 API fallback，無功能差異。如欲在本地使用 OpenBB，執行 `pip install openbb`。
>
> **Alpha Vantage 免費方案**：25 req/day。系統支援雙 key 自動切換（`AV_API_KEY` → `AV_API_KEY_2`），第一組配額耗盡時無縫切換。

---

## ETF 標的宇宙

### 每日夜盤報（LINE + 儀表板）

| 群組 | ETF | 名稱 |
|------|-----|------|
| EQUITY | VOO | Vanguard S&P 500 ETF |
| EQUITY | QQQ | Invesco QQQ Trust（Nasdaq-100） |
| FIXED INCOME | VGIT | Vanguard Intermediate-Term Treasury ETF |
| FIXED INCOME | TYD | Direxion Daily 7-10Y Treasury Bull 3x Shares |
| ALTERNATIVES | GLD | SPDR Gold Shares |
| ALTERNATIVES | GRID | First Trust NASDAQ Clean Edge Smart Grid |

### 凸最佳化引擎（portfolio_optimizer.py）

| 類型 | 代碼 | 名稱 | 部位下限 | 部位上限 | 設計說明 |
|------|------|------|---------|---------|---------|
| 美股大盤 | VOO | Vanguard S&P 500 ETF | 30% | 55% | 核心護城河，確保底層美股曝險 |
| 台灣大盤 | 0050.TW | 元大台灣50 | 20% | 35% | 拉住韁繩，防過度壓注單一市場 |
| 台灣衛星 | 00875.TW | 國泰網路資安 | 5% | 15% | 衛星倉位，上限收緊 |
| 潔淨能源 | GRID | First Trust Smart Grid | 5% | 20% | 半年手動下單，避免過度集中 |
| 美債中期 | VGIT | Vanguard Intermediate Treasury | 5% | 20% | 儲蓄池 / TYD 觸發前緩衝 |

> ⚠️ 0050.TW / 00875.TW 以 TWD 計價，VOO / GRID / VGIT 以 USD 計價。最佳化在未調整匯率的混合貨幣報酬序列上進行，最佳化結果反映幣別間的報酬與波動差異，使用時應留意外匯風險。
>
> **v2 邊界更新說明：** 舊版 0050.TW 上限 50%、VGIT 上限 40% 導致優化器在 2020–2026 樣本中出現「三體收斂」（MV/CVaR/CDaR 全部壓注 0050.TW 上限）與「HRP 盲目避險」（HRP 將 ~85% 配入美債）。新版邊界解決上述貪婪偏誤。

---

## 推送內容（22:00 TST，5 則 LINE 訊息上限）

| 訊息 | 內容 | 條件 |
|------|------|------|
| 1 | 文字摘要（6 ETF 總覽 + 儀表板連結） | 永遠 |
| 2 | AI 市場解讀（體制敘事 + ETF 信號逐行 + 尾端警示） | GEMINI_API_KEY 有效 |
| 3 | 總經指標 Flex 卡（US10Y / 衰退機率 / VIX…） | 永遠 |
| 4 | VOO ETF Flex 卡 | 資料存在 |
| 5 | QQQ ETF Flex 卡 | 資料存在 |

GLD / VGIT / TYD / GRID → 文字摘要提及 + Web 儀表板完整顯示

---

## 投資儀表板（Cloudflare Pages Web App）

響應式單頁應用，支援手機（375px）到桌面（1024px+）。

| 元件 | 內容 |
|------|------|
| 體制橫幅 | RISK_ON / TRANSITION / RISK_OFF 色碼標籤 + 信用/PMI/曲線摘要 |
| 尾端風險列 | 當日觸發的宏觀警示（7 項硬編碼清單，未觸發時隱藏） |
| Macro Strip | US10Y、衰退機率、HY 利差、ISM PMI、實質利率、Sahm（顏色標示） |
| AI 市場解讀 | Gemini 兩層分析全文（純文字展示） |
| HRP 配置圖 | Chart.js Doughnut + TYD 時機指示器 |
| DCC-GARCH | VOO/VGIT/GLD 動態條件相關係數、年化波動率、配置對比 |
| **凸最佳化配置** | MV / CVaR / CDaR / HRP 四模型 Tab 切換；Sharpe / 年化報酬 / 波動率 / MDD 指標列；資產配置長條圖；再平衡建議面板（當前 → 目標進度條 + 觸發標籤 + 摩擦力靜態觀望標記） |
| ETF 信號（6 張卡） | 🟢🟡🔴 信號燈 + delta（本次 vs 前次）+ 現價/Z-Score/RSI/Sharpe/MACD/DD |
| 新聞 | 各 ETF 最新 3 則新聞（繁體中文標題 + publisher 來源 + 可點擊連結） |

---

## 凸最佳化配置引擎（portfolio_optimizer.py）

基於 Dany Cajas《Advanced Portfolio Optimization》的凸最佳化邏輯，使用 Riskfolio-Lib v7 實作。

### 四種最佳化模式

| 模式 | 方法 | 目標函數 | 風險度量 |
|------|------|---------|---------|
| 模式A｜MV | Classic | 最大化 Sharpe Ratio | 方差（Markowitz 均值-方差） |
| 模式B｜CVaR | Classic | 最大化 Sharpe Ratio | CVaR（95% 尾端期望損失） |
| 模式C｜CDaR | Classic | 最大化 Sharpe Ratio | CDaR（95% 條件最大回撤） |
| 模式D｜HRP | Hierarchical | 風險平衡 | 方差（Ward 聚類 + Pearson 相依） |

### 最佳化設定（v2）

```python
TICKERS        = ["VOO", "0050.TW", "00875.TW", "GRID", "VGIT"]
START_DATE     = "2020-01-01"        # 訓練資料起始
RF_ANNUAL      = 0.04                # 年化無風險利率
ALPHA          = 0.05                # CVaR / CDaR 信賴水準（95%）
CURRENT_WEIGHTS = {                  # 當前部位（再平衡基準）
    "VOO": 0.35, "0050.TW": 0.25, "00875.TW": 0.10,
    "GRID": 0.15, "VGIT": 0.15
}
REBALANCE_THRESHOLD  = 0.05          # ±5% 觸發再平衡

# ── v2 新增 ────────────────────────────────────────────────────
HRP_LABEL            = "模式D｜HRP"  # HRP 排除於最優模型決策
SHARPE_IMPROVEMENT_MIN = 0.05        # Δ Sharpe 低於此值 → 全部觸發標的靜態觀望
TX_COST_USD          = 3.0           # GRID 美股下單手續費 USD（摩擦力估算）
TX_COST_TWD          = 1.0           # 台股 DCA 手續費 TWD
TYD_ALGO_SIGNAL      = False         # True = 將 VGIT 超出最低倉位的預算轉入 TYD
```

### 輸出

| 輸出 | 路徑 | 說明 |
|------|------|------|
| Obsidian 報告 | `portfolio_reports/YYYY-MM-DD/*.md` | 含指標對比表、再平衡建議、HRP 免責聲明、摩擦力表格、TYD 信號 |
| JSON 資料 | `portfolio_reports/YYYY-MM-DD/portfolio_optimization.json` | 儀表板資料來源 |
| 儀表板資料 | `docs/data/portfolio_optimization.json` | 由 CI 自動複製 |

### JSON 結構（v2）

```json
{
  "date": "2026-05-22",
  "optimal_model": "模式A｜MV",
  "weights": {
    "模式A｜MV": { "VOO": 0.412, "0050.TW": 0.350, "00875.TW": 0.05, ... }
  },
  "metrics": [
    { "label": "模式A｜MV", "ann_ret": 24.94, "ann_vol": 14.34,
      "sharpe": 1.461, "cvar_ann": 30.66, "mdd": -28.02 }
  ],
  "rebalance": {
    "VOO": {
      "current": 0.35, "optimal": 0.412, "deviation": 0.062,
      "triggered": true, "direction": "buy",
      "friction_blocked": false, "tyd_note": "",
      "action": "🔼 增持（立即）"
    }
  },
  "sharpe_current":   1.203,
  "sharpe_optimal":   1.461,
  "sharpe_delta":     0.258,
  "friction_blocked": false,
  "tyd_algo_signal":  false,
  "turnover":         0.142
}
```

> `friction_blocked`：若 `sharpe_delta < 0.05`，所有觸發標的的 `action` 改為「靜態觀望」，避免手續費吞噬改善幅度。  
> `tyd_algo_signal`：設為 `true` 時，VGIT 超出最低倉位的資金將在報告中提示轉入 TYD 做波段槓桿。  
> `optimal_model` 永遠不會是 `"模式D｜HRP"`（HRP 為啟發式演算法，僅供基準參考）。

### v2 重構：防貪婪偏誤與摩擦力過濾

#### 問題背景

舊版優化器在 2020–2026 樣本資料下存在兩個已知缺陷：

| 缺陷 | 現象 | 根因 |
|------|------|------|
| 三體收斂 | MV/CVaR/CDaR 三模式均將 0050.TW 推至 50% 上限 | 台股樣本期報酬優異，優化器貪婪集中 |
| HRP 盲目避險 | HRP 將 ~85% 配入 VGIT | 無報酬約束，低波動資產自然主導 |

#### 六項修改

| 項目 | 變更前 | 變更後 |
|------|-------|-------|
| VOO 邊界 | 10%–50% | **30%–55%**（確保核心美股護城河） |
| 0050.TW 邊界 | 10%–50% | **20%–35%**（拉住韁繩） |
| 00875.TW 邊界 | 5%–20% | **5%–15%**（衛星上限收緊） |
| VGIT 邊界 | 5%–40% | **5%–20%**（儲蓄池，不過度擴張） |
| HRP 在最優決策中 | 可能被選為 optimal_model | **永遠排除**，僅基準參考 |
| 摩擦力過濾 | 不存在 | **Δ Sharpe < 0.05 → 全部靜態觀望** |

#### TYD 演算法旗標

`TYD_ALGO_SIGNAL = True` 可手動觸發戰術美債信號：VGIT 最低倉位（5%）以外的預算將在報告中標記為「轉入 TYD 進行波段槓桿抄底」。正常狀態維持 `False`，VGIT 遵循凸最佳化目標配置。

#### Obsidian 報告新增段落

| 新段落 | 說明 |
|--------|------|
| HRP 免責聲明 callout | 提醒 HRP 權重不可作為再平衡依據 |
| §4.5 摩擦力過濾表格 | 顯示當前夏普、最優夏普、Δ、是否靜態觀望、週轉率、手續費估算 |
| §4.6 TYD 戰術信號 callout | 顯示 TYD_ALGO_SIGNAL 狀態與 VGIT → TYD 轉換提示 |

---

### CLI 用法

```bash
# 基本執行（無圖表，適合 CI）
python portfolio_optimizer.py --no-plot

# 自訂起始日與無風險利率
python portfolio_optimizer.py --no-plot --start 2020-01-01 --rf 0.04

# 含圖表（本地執行，需 matplotlib GUI）
python portfolio_optimizer.py
```

---

## GitHub Actions Workflows

| Workflow | 觸發 | 功能 |
|---------|------|------|
| `line-investment-bot.yml` | 22:00 TST 定時 / 手動 | 夜盤報 + 儀表板 `report.json` 更新 |
| `portfolio-optimizer.yml` | 手動（workflow_dispatch） | 凸最佳化 → 複製 JSON 至 `docs/data/` → commit & push |

### 凸最佳化 Workflow 輸入參數

| 參數 | 預設值 | 說明 |
|------|-------|------|
| `start_date` | `2020-01-01` | 歷史資料起始日 |
| `rf_annual` | `0.04` | 年化無風險利率（例：0.045） |

---

## 指標說明

### 市場體制偵測（Regime Detection）

純規則邏輯，不依賴 LLM，由 `quant_engine.detect_market_regime()` 計算。

```
輸入四個維度：
  信用面：HY 利差  > 5% → 緊縮 / 4~5% → 偏緊 / < 4% → 正常
  景氣面：PMI 水位 + 二階導數 → 加速擴張 / 減速擴張 / 觸底回升 / 加速收縮
  就業面：薩姆法則 or 米切茲法則 是否觸發
  殖利率曲線：10Y-2Y  < -0.5% → 深度倒掛 / < 0 → 倒掛 / ≥ 0 → 正常

裁決：
  就業觸發 OR (信用緊縮 AND 曲線深度倒掛)  →  RISK_OFF
  信用正常 AND PMI 正面 AND 就業健康        →  RISK_ON
  其餘                                      →  TRANSITION
```

體制是信號燈系統的「乘數」：RISK_OFF 強制將任何 ETF 的信號壓低至不超過 🟡 維持。

---

### 三色信號燈（Signal Light）

`quant_engine.calc_signal_light()` 純規則輸出，禁止 LLM 更改結論。

```
得分計算（初始 0）：
  MACD 柱 > 0                  → +1（趨勢動能）
  RSI 45~68（健康動能區間）    → +1
  Z-Score -0.5~1.5（回歸區間） → +1
  ERP < 1%（估值過高懲罰）     → -1
  VIX 突破布林上軌（恐慌）     → -1

體制乘數：
  RISK_OFF → score = min(score, 1)，強制壓低
  RISK_ON  → 不干預
```

| 總分 | 信號 | 含義 |
|------|------|------|
| ≥ 3 | 🟢 加碼 | 技術、估值、體制三方一致看多 |
| 1～2 | 🟡 維持 | 部分條件成立，持倉觀察 |
| ≤ 0 | 🔴 減碼 | 多項條件轉差，控制部位 |

每次執行會對比前次 report.json 的信號，若改變則在儀表板顯示 **delta**（如：🟡 → 🟢）。

---

### 尾端風險清單（Tail Risk Checklist）

`quant_engine.eval_tail_risks()` 執行 7 項硬編碼已知宏觀風險偵測，觸發則顯示於儀表板頂端與 LINE 訊息。

| ID | 觸發條件 | 警示內容 |
|----|---------|---------|
| `yield_curve_deep_invert` | 殖利率曲線 < -0.5% | 歷史上 12-18 個月後衰退機率 > 70% |
| `sahm_trigger` | 薩姆法則觸發 | 就業惡化反射性循環已啟動 |
| `hy_spread_spike` | HY 利差 > 5% | 信用市場恐慌，股市距底部通常仍遠 |
| `hy_spread_elevated` | HY 利差 4~5% | 信用市場偏緊，留意擴散風險 |
| `cape_extreme` | 任一 ETF CAPE > 32x | 估值歷史極高，未來 10 年期望報酬偏低 |
| `vix_bollinger` | VIX 突破布林上軌 | 市場恐慌情緒急升，短期波動率風險高 |
| `recession_high_prob` | 衰退機率 > 40% | 建議審視風險資產部位 |

---

### EMA200（200 日指數移動平均）

```
EMAₜ = 收盤價ₜ × k + EMAₜ₋₁ × (1 - k)，  k = 2/(200+1) ≈ 0.00995
```

- EMA200 以上：長期多頭趨勢
- EMA200 以下：長期空頭趨勢，需提高風險意識
- 系統抓取 400 筆歷史日線確保充分暖機

---

### Z-Score（EMA 標準分數）

```
Z-Score = (現價 - EMA_N) / 滾動標準差_N
```

| Z-Score 範圍 | 解讀 |
|-------------|------|
| > +2.5 | 嚴重超漲，過熱警戒，乘數降至 0.5x |
| +1.0 ～ +2.5 | 溫和偏貴，正常成長期 |
| -1.0 ～ +1.0 | 合理區間 |
| -2.0 ～ -1.0 | 偏低估，希望復甦期，乘數升至 1.5x |
| < -2.0 | 嚴重超跌，恐慌底部，乘數最高 2.5x |

---

### MACD（12/26/9）

```
MACD 線 = EMA12 - EMA26
訊號線  = MACD 線的 9 日 EMA
柱狀圖  = MACD 線 - 訊號線
```

- 柱狀圖 **▲擴大（正值且增大）**：多頭動能加速
- 柱狀圖 **▼收縮（正值轉負）**：動能減弱，留意反轉

---

### RSI（14 日相對強弱指數）

```
RSI = 100 - (100 / (1 + EMA漲幅/EMA跌幅))
```

| RSI 範圍 | 解讀 |
|---------|------|
| > 70 | 超買，注意回調風險（信號燈扣分） |
| 45 ～ 68 | 健康動能區間（信號燈加分） |
| 30 ～ 45 | 弱勢整理 |
| < 30 | 超賣，潛在反彈機會（TYD 時機計分加分） |

---

### 布林帶 %B

```
BB%B = (現價 - 下軌) / (上軌 - 下軌)
上/下軌 = SMA20 ± 2 × STD20
```

| BB%B | 解讀 |
|------|------|
| > 1.0 | 突破上軌，短線過熱 |
| 0.2 ～ 0.8 | 帶內正常 |
| < 0 | 突破下軌，短線超賣 |

---

### Sharpe Ratio（1 年期滾動）

```
Sharpe = (日報酬均值 × 252) / (日報酬標準差 × √252)  ← 取近 252 個交易日
```

| Sharpe | 評等 |
|--------|------|
| > 1.0 | 優異 |
| 0 ～ 1.0 | 尚可 |
| < 0 | 不佳 |

---

### Max Drawdown（1 年期最大回撤）

```
MDD = min[(現價 - 滾動峰值) / 滾動峰值] × 100  ← 近 252 日
```

| MDD | 評等 |
|-----|------|
| 0 ～ -10% | 正常 |
| -10 ～ -20% | 中等回撤 |
| < -20% | 嚴重回撤 |

---

### TYD 買入時機評分

當美債殖利率處於高位、殖利率曲線倒掛且 VGIT 超賣時，是槓桿做多 7-10 年美債（TYD）的有利時機。

```
TYD 時機分 = US10Y 水位（0~40）+ 殖利率曲線（0~35）+ VGIT RSI 超賣（0~25）
```

| US10Y | 得分 | 殖利率曲線 | 得分 | VGIT RSI | 得分 |
|-------|------|-----------|------|---------|------|
| ≥ 4.8% | 40 | < -0.3% | 35 | < 35 | 25 |
| ≥ 4.5% | 30 | < 0% | 25 | < 45 | 15 |
| ≥ 4.2% | 15 | < 0.3% | 10 | < 55 | 5 |
| ≥ 4.0% | 5 | ≥ 0.3% | 0 | ≥ 55 | 0 |

| 總分 | 標籤 | FIXED_INCOME 群組內 TYD 比例 |
|------|------|---------------------------|
| ≥ 70 | 強烈買入 TYD | 70% |
| ≥ 50 | 可考慮 TYD | 40% |
| ≥ 30 | 觀望 | 20% |
| < 30 | 持有 VGIT | 5% |

---

### 分組 HRP（三群組層次風險平價）

解決單層 HRP 因低波動資產（VGIT）主導整體配置的問題，改以群組為單位做兩階段配置。

```
Step 1：各群組 equal-weight 計算群組報酬序列
Step 2：對三群組做 inter-group HRP → W_EQUITY / W_FI / W_ALT
Step 3：群組內配置
  EQUITY       → 兩資產 HRP（VOO vs QQQ）
  FIXED_INCOME → VGIT:TYD 動態比例（依 TYD 時機評分）
  ALTERNATIVES → 兩資產 HRP（GLD vs GRID）
```

效果：單資產最大權重從 ~89%（VGIT 舊版）降至 ~30%，各群組均衡分配。

---

### 多 Agent 信心評分系統

三個 Agent 各自評 0～100 分，Portfolio Manager 加權合成最終決策。信心分數作為參考指標顯示於 ETF 卡片，主要決策信號改由**三色信號燈**呈現。

#### TechnicalAgent（技術面，權重 40%）

| 指標 | 評分標準 |
|------|---------|
| RSI | < 30 → +25；30～50 → +15；50～70 → 0；> 70 → -25 |
| MACD 柱 | > 0 → +20；< 0 → -20 |
| Z-Score | < -1 → +20；-1～1 → +10；> 2 → -20；> 2.5 → -30 |
| BB%B | < 0.2 → +15；> 0.8 → -15 |

#### ValueAgent（估值面，權重 35%；僅 VOO / QQQ；無 CAPE 時降為 0）

| 指標 | 評分標準 |
|------|---------|
| ERP | > 3% → +30；2～3% → +15；< 0 → -30 |
| CAPE | < 15 → +30；20～25 → 0；> 30 → -25 |
| 預估 10Y 報酬 | > 6% → +20；< 2% → -20 |

#### MacroAgent（總經面，權重 25%）

| 指標 | 評分標準 |
|------|---------|
| 薩姆法則 | 未觸發 → +20；觸發 → -30 |
| HY 利差 | < 3.5% → -20；3.5～6.5% → +15；≥ 8% → +25 |
| PMI 動能 | > 0（加速）→ +15；< 0（減速）→ -10 |
| 殖利率曲線 | > 0 → +10；< 0 → -10 |

#### 合成邏輯

```
有估值：信心 = Tech×40% + Val×35% + Macro×25%
無估值：信心 = Tech×60% + Macro×40%
```

---

### DCC-GARCH(1,1) 動態條件相關係數

對 VOO / VGIT / GLD 三資產進行兩階段估計，計算當前動態相關係數與年化波動率。

```
Step 1（GARCH）：對每個資產估計條件異方差
  σ²ₜ = ω + α × ε²ₜ₋₁ + β × σ²ₜ₋₁

Step 2（DCC）：對標準化殘差估計動態相關
  Qₜ = (1-a-b)Q̄ + a × εₜ₋₁εᵀₜ₋₁ + b × Qₜ₋₁
  Rₜ = diag(Qₜ)^(-½) Qₜ diag(Qₜ)^(-½)

距離矩陣 d_{ij} = √((1-ρ_{ij})/2)，Ward 聚類
```

儀表板顯示：當期相關係數、30 日平均、趨勢方向、年化波動率、DCC-HRP 配置。

---

### AI 市場解讀（Gemini 兩層架構）

**設計原則：Gemini 是文字翻譯器，量化邏輯由 quant_engine 負責。**

```
Layer 1 — 體制敘事（一次 API 呼叫）
  輸入：regime 結果 + 尾端風險清單 + 宏觀數據 + DCC 文本
  輸出：100 字以內，說明「今日市場體制對散戶的含義」
  規則：只陳述事實，不給具體買賣建議

Layer 2 — ETF 逐行信號說明（一次 API 呼叫）
  輸入：每個 ETF 的信號燈結論 + 計算依據
  輸出：每個 ETF 一行（≤15 字）解釋「為何是這個信號」
  規則：禁止更改信號燈顏色，禁止新增信心百分比
```

**新聞處理**：Yahoo Finance API 取回 `title + publisher + summary`，Gemini 批量翻譯（一次呼叫），`1.` 格式對應標題、`1s.` 後綴對應摘要，分別回寫 `title_zh` / `summary_zh`。

---

## 部署步驟

### 步驟一：GitHub Secrets 設定

前往 **GitHub Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret 名稱 | 必填 | 說明 |
|---|---|---|
| `LINE_CHANNEL_TOKEN` | ✅ | LINE Channel Access Token |
| `LINE_USER_ID` | ✅ | 你的 LINE User ID |
| `AV_API_KEY` | ✅ | Alpha Vantage API Key（主） |
| `AV_API_KEY_2` | 建議 | Alpha Vantage API Key（備援） |
| `GEMINI_API_KEY` | 建議 | Gemini API Key（未設定則略過 AI 摘要與新聞翻譯） |
| `FRED_API_KEY` | 建議 | FRED API Key（未設定則使用估算值） |
| `CLOUDFLARE_API_TOKEN` | ✅（部署用） | Cloudflare API Token（Pages 部署） |
| `CLOUDFLARE_ACCOUNT_ID` | ✅（部署用） | Cloudflare Account ID |

### 步驟二：Cloudflare Pages 初次建立

1. Cloudflare Dashboard → **Workers & Pages → Create → Pages → Connect GitHub**
2. Repo：`learningprogram0108/INVESTMENT-1.0`
3. Build command：**留空**，Output directory：`docs`
4. Save & Deploy

之後每次 GitHub Actions 執行完畢，`wrangler pages deploy` 會自動重新部署。

### 步驟三：驗證

```
夜盤報：GitHub Repo → Actions → LINE 投資夜盤報 → Run workflow
         → 確認 LINE 收到訊息，並前往 Web App 確認儀表板更新

凸最佳化：GitHub Repo → Actions → 量化投資組合最佳化 → Run workflow
           → 確認儀表板「凸最佳化配置分析」卡片資料更新
```

---

## 常見狀況說明

| 狀況 | 原因 | 影響 |
|------|------|------|
| ETF 卡未出現 | Yahoo Finance + Alpha Vantage 均失敗 | 僅文字摘要 + AI 解讀發出，ETF 卡自動跳過 |
| VIX 顯示預設值 20.0 | FRED / Yahoo / AV 全部失敗 | 情緒溫度計與景氣階段使用預設值計算 |
| AI 市場解讀未出現 | API Key 未設定或 429/503 超過 4 次重試 | 自動略過，不影響其他訊息 |
| 新聞無繁中翻譯 | GEMINI_API_KEY 未設定 | 新聞仍顯示英文原標題 |
| VGIT / TYD / GLD / GRID 無 CAPE/ERP | 非股票 ETF 不適用 PE 估值 | ValueAgent 顯示 N/A，信心分數改為 Tech 60% + Macro 40% |
| AV key 1 配額已滿 | 免費方案 25 req/day | 自動切換 AV_API_KEY_2，隔日 UTC 00:00 重置 |
| TYD 信號燈顯示 🔴 | RISK_ON 體制下債券承壓，Z-Score 負離均 | 正常行為，非錯誤 |
| 尾端風險列不顯示 | 當日無任何警示條件觸發 | 正常行為，表示市場無立即已知風險 |
| 信號燈無 delta 標示 | 首次執行或前次 report.json 不存在 | 第二次執行後開始顯示變化 |
| Cloudflare Pages 未更新 | wrangler 部署步驟失敗 | 查看 Actions log 中「Deploy to Cloudflare Pages」步驟 |
| 凸最佳化卡片顯示「資料尚未生成」 | portfolio_optimization.json 不存在 | 手動觸發「量化投資組合最佳化」workflow |
| HRP 模式權重集中 VGIT | HRP 無上下界約束，低波動資產自然主導 | 預期行為；MV/CVaR/CDaR 模式有 ±10% 上下界約束 |

---

## 本地開發

```bash
# 安裝依賴
pip install -r requirements.txt

# 驗證依賴
python -c "from scipy.cluster.hierarchy import linkage; print('scipy OK')"
python -c "import riskfolio as rp; print('riskfolio-lib', rp.__version__, 'OK')"

# 預覽 Flex Message（不需 API Key）
python preview_messages.py
# → 產生 preview_evening.json

# 驗證體制偵測與信號燈（純規則，不需 API Key）
python -c "
from src.quant_engine import MacroIndicators, ETFSignal
from src.quant_engine import detect_market_regime, calc_signal_light, eval_tail_risks

macro = MacroIndicators(
    us10y=4.45, us02y=4.88, cpi_yoy=3.2,
    breakeven=2.31, real_rate=2.14, yield_curve=-0.43,
    sahm_indicator=0.18, sahm_triggered=False,
    michez_m=0.12, michez_triggered=False,
    recession_prob=14.0, hy_spread=3.8, credit_signal='正常',
    ism_pmi=[48.5, 49.1, 49.8], pmi_second_deriv=0.35,
    unemployment_3m=[4.0, 4.1, 4.1],
)
regime = detect_market_regime(macro)
print('Regime:', regime['regime'])   # → RISK_ON
"

# 執行凸最佳化（需網路，約 2-3 分鐘）
python portfolio_optimizer.py --no-plot --start 2020-01-01 --rf 0.04
# → portfolio_reports/YYYY-MM-DD/portfolio_optimization.json

# 完整夜盤報執行（需設定環境變數）
SESSION=evening python main.py
```

---

## 技術棧

| 層次 | 技術 |
|------|------|
| 語言 | Python 3.12 |
| 資料 | OpenBB Platform v4（yfinance / fred / alpha_vantage）+ 直接 API fallback |
| 量化（夜盤） | pandas 2.2、numpy 2.1、scipy（HRP 聚類）、arch（GARCH） |
| 量化（最佳化） | Riskfolio-Lib v7（MV / CVaR / CDaR / HRP）、scikit-learn（Ledoit-Wolf）、statsmodels |
| AI | Google Gemini 3.1 Flash Lite（兩層分析 + 批量翻譯，共 3 次 API 呼叫） |
| CI/CD | GitHub Actions（ubuntu-latest）× 2 workflows |
| Web | Vanilla JS + Chart.js 4.4 + Cloudflare Pages |
| 推送 | LINE Messaging API（Flex Message，5 則上限） |
