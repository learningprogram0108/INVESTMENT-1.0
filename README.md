# LINE 投資每日早報 — 部署指南

## 架構總覽

```
Cloudflare Worker (09:30 TST 精準觸發)
    → GitHub Actions workflow_dispatch
        → Python (抓資料 + 量化計算)
            → LINE Messaging API → 你的手機
```

---

## 步驟一：GitHub Secrets 設定

前往 GitHub Repo → Settings → Secrets and variables → Actions → New repository secret

| Secret 名稱 | 說明 | 取得方式 |
|---|---|---|
| `LINE_CHANNEL_TOKEN` | Channel Access Token | LINE Developers Console → 你的 Channel → Messaging API |
| `LINE_USER_ID` | 你的 LINE User ID | 對 Bot 傳訊後，在 Channel 的 webhook log 查看 `userId` |
| `FRED_API_KEY` | FRED API Key（可空） | https://fred.stlouisfed.org/docs/api/api_key.html |

---

## 步驟二：取得你的 LINE User ID

1. 進入 LINE Developers Console → 你的 Messaging API Channel
2. 在「Webhook URL」填入任意 HTTPS URL（可用 https://httpbin.org/post）
3. 用你的 LINE 帳號傳訊息給 Bot
4. 在 webhook log 或 httpbin 回應中找到 `"userId": "Uxxxxxxxxxx"`
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
wrangler secret put GH_PAT        # 輸入你的 GitHub PAT（需 workflow 權限）
wrangler secret put GH_REPO       # 輸入 "your-username/your-repo"

# 部署
wrangler deploy
```

**GitHub PAT 所需權限：**
- `repo` → Actions → `workflow`

前往 GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens

---

## 步驟四：驗證

```bash
# 測試 Cloudflare Worker（手動觸發）
curl https://your-worker.workers.dev/trigger

# 測試 GitHub Actions（在 repo Actions 頁面手動執行）
# Actions → LINE 投資每日早報 → Run workflow
```

---

## 發送時間

| 日期 | 時間 | 內容 |
|---|---|---|
| 週一～週五 | 09:30 TST | 平日早報：即時行情 + 量化訊號 + 操作建議 |
| 週六 | 09:30 TST | 本週回顧：週漲跌幅 + 下週展望 |
| 週日 | 不發送 | — |

---

## FRED API（可選但建議）

免費申請：https://fred.stlouisfed.org/docs/api/api_key.html

有 FRED Key 可抓取精確數據：
- `UNRATE`：美國失業率（薩姆規則）
- `JTSJOR`：職缺率（Michez 法則）
- `CPIAUCSL`：CPI（實質利率計算）
- `BAMLH0A0HYM2`：高收益債 OAS 利差

沒有 FRED Key 時系統使用估算值，仍可正常運作，精準度略降。
