# LINE 投資每日早報

每日自動推送兩次量化投資訊號至 LINE，包含台股 ETF（早盤）與美股 ETF（夜盤）的技術面、總經面與 AI 大師摘要。

## 架構總覽

```
Cloudflare Worker（cron 精準觸發）
    → GitHub Actions workflow_dispatch
        → Python：資料擷取 + 量化計算 + Gemini AI 摘要
            → LINE Messaging API → 手機
```

---

## 推送時間與內容

| Session | 時間（TST） | ETF | 訊息數 |
|---------|------------|-----|--------|
| 早盤 | 09:30（週一～週五） | 0050、00679B | 最多 5 則 |
| 夜盤 | 22:00（週一～週五） | VOO、GLD | 最多 4 則 |

### 早盤訊息（5 則）
1. 文字摘要（總覽）
2. Gemini AI 大師觀點
3. 總經指標卡（殖利率曲線、CPI、薩姆規則、景氣衰退機率等）
4. 元大台灣 50（0050）ETF 卡
5. 元大美債 20年（00679B）ETF 卡

### 夜盤訊息（4 則）
1. 文字摘要（總覽）
2. Gemini AI 大師觀點
3. Vanguard S&P 500（VOO）ETF 卡
4. SPDR Gold Shares（GLD）ETF 卡

### ETF 卡包含指標
- 現價 / 漲跌幅 / EMA200 / Z-Score
- MACD (12/26/9)：MACD 線、訊號線、柱狀圖
- 情緒溫度計（0～100）
- VIX 恐慌指數 / 布林突破訊號
- CAPE 代理值 / ERP / 預估 10 年報酬（股票 ETF）
- 凱利建議乘數 / 景氣階段

---

## 資料來源

| 資料 | 來源 | 備援 |
|------|------|------|
| VOO、GLD 日線 | Stooq.com | Alpha Vantage |
| VIX 指數 | Stooq.com（^vix） | Alpha Vantage（VIXY） |
| 0050、00679B 日線 | TWSE Open API | Stooq → Alpha Vantage |
| 美國公債殖利率 | Alpha Vantage | — |
| CAPE / ERP | Alpha Vantage（COMPANY_OVERVIEW） | — |
| 失業率、CPI、PMI | FRED API | 內建估算值 |
| HY 信用利差 | FRED API（BAMLH0A0HYM2） | 內建估算值 |
| AI 摘要 | Gemini 3.1 Flash Lite | 略過（不影響其他訊息） |

---

## 步驟一：GitHub Secrets 設定

前往 **GitHub Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret 名稱 | 必填 | 說明 | 取得方式 |
|---|---|---|---|
| `LINE_CHANNEL_TOKEN` | ✅ | Channel Access Token | LINE Developers Console → Messaging API |
| `LINE_USER_ID` | ✅ | 你的 LINE User ID | 對 Bot 傳訊後查 webhook log |
| `AV_API_KEY` | ✅ | Alpha Vantage API Key（主） | https://www.alphavantage.co/support/#api-key |
| `AV_API_KEY_2` | 建議 | Alpha Vantage API Key（備） | 同上，另申請一組備援 |
| `GEMINI_API_KEY` | 建議 | Gemini API Key | https://aistudio.google.com/app/apikey |
| `FRED_API_KEY` | 建議 | FRED API Key | https://fred.stlouisfed.org/docs/api/api_key.html |

> **AV 免費方案限制**：25 req/day、1 req/s。建議申請兩組 key，當第一組配額耗盡時自動切換第二組。

---

## 步驟二：取得 LINE User ID

1. 進入 LINE Developers Console → 你的 Messaging API Channel
2. 在「Webhook URL」填入任意 HTTPS URL（例如 https://httpbin.org/post）
3. 用你的 LINE 帳號傳訊息給 Bot
4. 在 webhook log 中找到 `"userId": "Uxxxxxxxxxx"`
5. 將此值存入 `LINE_USER_ID` Secret

---

## 步驟三：Cloudflare Worker 部署

```bash
cd cloudflare_worker

# 安裝 Wrangler CLI（若未安裝）
npm install -g wrangler

# 登入 Cloudflare
wrangler login

# 設定 Secrets（互動式輸入，不會明文存檔）
wrangler secret put GH_PAT     # GitHub PAT（需 workflow 觸發權限）
wrangler secret put GH_REPO    # 填入 "your-username/your-repo"

# 部署
wrangler deploy
```

**GitHub PAT 所需權限**：Settings → Developer settings → Personal access tokens → Fine-grained tokens → `Actions: Write`

---

## 步驟四：驗證

在 GitHub Repo → Actions → **LINE 投資每日早報** → **Run workflow** 手動執行，選擇 `morning` 或 `evening`，確認 LINE 收到訊息。

---

## 常見狀況說明

| 狀況 | 原因 | 影響 |
|------|------|------|
| ETF 卡未出現 | 週末 Stooq 無美股資料 / AV 配額耗盡 | 僅文字摘要 + Gemini 發出，其餘跳過 |
| VIX 顯示預設值 20.0 | 同上 | 情緒與景氣階段計算使用預設值 |
| Gemini 摘要未出現 | API Key 未設定或呼叫失敗 | 自動略過，不影響其他訊息 |
| 00679B 無 CAPE / ERP | 債券 ETF 不適用 PE 估值 | 正常，該欄位固定顯示 N/A |
