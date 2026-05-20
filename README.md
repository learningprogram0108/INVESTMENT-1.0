# LINE 投資夜盤報 + 投資儀表板

每日 22:00 TST 自動推送美股 ETF 量化訊號至 LINE，並同步更新 Cloudflare Pages 響應式投資儀表板。

**Web App（即時儀表板）**：[https://investment-dashboard-cva.pages.dev](https://investment-dashboard-cva.pages.dev)

---

## 系統架構

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions（ubuntu-latest）每日 22:00 TST 手動觸發     │
│                                                             │
│  python main.py  SESSION=evening                            │
│                                                             │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ data_fetcher │  │  quant_engine    │  │gemini_summary│  │
│  │              │  │                  │  │              │  │
│  │ OpenBB SDK   │  │ EMA200 / Z-Score │  │ Druckenmiller│  │
│  │  ↳ yfinance  │→ │ MACD(12/26/9)    │→ │ Buffett/Graham│ │
│  │  ↳ FRED      │  │ RSI / BB%B       │  │ Soros        │  │
│  │  ↳ AV        │  │ Sharpe / MDD     │  │ Munger 決策  │  │
│  │ Direct API   │  │ 多 Agent 信心分數│  │ 繁中新聞翻譯 │  │
│  │  fallback    │  │ TYD 時機評分     │  │              │  │
│  └──────────────┘  │ 分組 HRP 配置    │  └──────────────┘  │
│                    │ DCC-GARCH(1,1)   │                    │
│                    └──────────────────┘                    │
│                             │                              │
│              ┌──────────────┴──────────────┐               │
│              ▼                             ▼               │
│       line_builder                  docs/data/             │
│       Flex Message 組裝             report.json 輸出       │
└──────┬───────────────────────────────────┬────────────────-┘
       │ LINE Push API                     │ git push
       ▼                                   ▼
  你的 LINE 手機              Cloudflare Pages 自動重部署
                              investment-dashboard-cva.pages.dev
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

| 群組 | ETF | 名稱 |
|------|-----|------|
| EQUITY | VOO | Vanguard S&P 500 ETF |
| EQUITY | QQQ | Invesco QQQ Trust（Nasdaq-100） |
| FIXED INCOME | VGIT | Vanguard Intermediate-Term Treasury ETF |
| FIXED INCOME | TYD | Direxion Daily 7-10Y Treasury Bull 3x Shares |
| ALTERNATIVES | GLD | SPDR Gold Shares |
| ALTERNATIVES | GRID | First Trust NASDAQ Clean Edge Smart Grid |

---

## 推送內容（22:00 TST，5 則 LINE 訊息上限）

| 訊息 | 內容 | 條件 |
|------|------|------|
| 1 | 文字摘要（6 ETF 總覽 + 儀表板連結） | 永遠 |
| 2 | Gemini AI 傳奇投資人分析（含 DCC 數據） | GEMINI_API_KEY 有效 |
| 3 | 總經指標 Flex 卡（US10Y / 衰退機率 / VIX…） | 永遠 |
| 4 | VOO ETF Flex 卡 | 資料存在 |
| 5 | QQQ ETF Flex 卡 | 資料存在 |

GLD / VGIT / TYD / GRID → 文字摘要提及 + Web 儀表板完整顯示

---

## 投資儀表板（Cloudflare Pages Web App）

響應式單頁應用，支援手機（375px）到桌面（1024px+）。

| 卡片 | 內容 |
|------|------|
| Macro Strip | US10Y、衰退機率、HY 利差、ISM PMI、實質利率、Sahm（顏色標示） |
| AI 分析 | Gemini 傳奇投資人全文（純文字展示） |
| HRP 配置圖 | Chart.js Doughnut + TYD 時機指示器 |
| DCC-GARCH | VOO/VGIT/GLD 動態條件相關係數、年化波動率、配置對比 |
| ETF 信號（6 張卡） | 現價、漲跌幅、Z-Score、RSI、Sharpe、Max DD、MACD、信心分數 |
| 新聞 | 各 ETF 最新 3 則新聞（繁體中文標題 + 可點擊連結） |

---

## 指標說明

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
| > 70 | 超買，注意回調風險 |
| 50 ～ 70 | 健康多頭動能（德魯肯米勒理想進場帶） |
| 30 ～ 50 | 弱勢整理 |
| < 30 | 超賣，潛在反彈機會 |

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

三個 Agent 各自評 0～100 分，Portfolio Manager 加權合成最終決策。

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

| 信心分數 | 建議標籤 |
|---------|---------|
| ≥ 72 | 強力買入 |
| 58 ～ 71 | 買  入 |
| 42 ～ 57 | 持  有 |
| 28 ～ 41 | 賣  出 |
| < 28 | 強力賣出 |

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

### Gemini AI 傳奇投資人分析

採用 ai-hedge-fund 開源架構，四位傳奇投資人角色委員會分工：

| 角色 | 投資人 | 核心框架 |
|------|--------|---------|
| 技術分析師 | Stanley Druckenmiller | 順勢動能；MACD 擴大 + RSI 50～70 = 最佳進場信號 |
| 價值投資師 | Warren Buffett + Ben Graham | 安全邊際；ERP > 3% 且 CAPE 合理方可進場 |
| 總經分析師 | George Soros | 反射性理論；HY 利差擴大為信用緊縮信號 |
| 投資組合經理 | Charlie Munger | 逆向三問：失敗情境？最大不確定因子？機會成本？ |

Gemini 輸入包含：技術面 / 估值面 / 總經面結構化數據 + 繁中新聞標題 + DCC 動態相關係數。

---

### 新聞翻譯與連結

- Yahoo Finance Search API 回傳英文標題 + 原文連結
- Gemini 批量翻譯（一次 API 呼叫），輸出 ≤15 字繁中標題
- Web 儀表板顯示繁中標題，點擊直達 Yahoo Finance 原文

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

GitHub Repo → Actions → **LINE 投資夜盤報** → **Run workflow** → 確認 LINE 收到訊息，並前往 Web App 確認儀表板更新。

---

## 常見狀況說明

| 狀況 | 原因 | 影響 |
|------|------|------|
| ETF 卡未出現 | Yahoo Finance + Alpha Vantage 均失敗 | 僅文字摘要 + AI 分析發出，ETF 卡自動跳過 |
| VIX 顯示預設值 20.0 | FRED / Yahoo / AV 全部失敗 | 情緒溫度計與景氣階段使用預設值計算 |
| Gemini 摘要未出現 | API Key 未設定或 429/503 超過 4 次重試 | 自動略過，不影響其他訊息 |
| 新聞無繁中翻譯 | GEMINI_API_KEY 未設定 | 新聞仍顯示英文原標題 |
| VGIT / TYD / GLD / GRID 無 CAPE/ERP | 非股票 ETF 不適用 PE 估值 | ValueAgent 顯示 N/A，信心分數改為 Tech 60% + Macro 40% |
| AV key 1 配額已滿 | 免費方案 25 req/day | 自動切換 AV_API_KEY_2，隔日 UTC 00:00 重置 |
| TYD 顯示低分 | US10Y < 4%、殖利率曲線正常、VGIT RSI 健康 | 正常行為，HRP 固定收益組內 VGIT 佔主導（95%） |
| Cloudflare Pages 未更新 | wrangler 部署步驟失敗 | 查看 Actions log 中「Deploy to Cloudflare Pages」步驟 |

---

## 本地開發

```bash
# 安裝依賴
pip install -r requirements.txt

# 驗證依賴
python -c "from scipy.cluster.hierarchy import linkage; print('scipy OK')"

# 預覽 Flex Message（不需 API Key）
python preview_messages.py
# → 產生 preview_evening.json

# 驗證分組 HRP 不偏科
python -c "
from src.quant_engine import calc_hrp_weights, calc_tyd_timing
import pandas as pd, numpy as np
np.random.seed(42)
prices = {t: pd.Series(100*(1+np.random.randn(300)*0.01).cumprod())
          for t in ['VOO','QQQ','GLD','VGIT','TYD','GRID']}
w = calc_hrp_weights(prices, tyd_score=60)
print(w)
"

# 完整執行（需設定環境變數）
SESSION=evening python main.py
```

---

## 技術棧

| 層次 | 技術 |
|------|------|
| 語言 | Python 3.12 |
| 資料 | OpenBB Platform v4（yfinance / fred / alpha_vantage）+ 直接 API fallback |
| 量化 | pandas 2.2、numpy 2.1、scipy（HRP 聚類）、arch（GARCH） |
| AI | Google Gemini 3.1 Flash Lite（分析 + 批量翻譯） |
| CI/CD | GitHub Actions |
| Web | Vanilla JS + Chart.js 4.4 + Cloudflare Pages |
| 推送 | LINE Messaging API（Flex Message） |
