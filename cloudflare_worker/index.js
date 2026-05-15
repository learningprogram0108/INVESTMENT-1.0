/**
 * Cloudflare Worker — 投資早報精準時鐘
 * 每天台灣時間 09:30（UTC 01:30）觸發 GitHub Actions
 * 週一～週六：30 1 * * 1-6
 *
 * 部署步驟：
 *   1. wrangler deploy
 *   2. 在 Cloudflare Dashboard → Workers → Settings → Variables
 *      加入 GH_PAT 與 GH_REPO（或透過 wrangler secret put）
 */

export default {
  /**
   * Cron 觸發入口
   * scheduled event 由 Cloudflare 全球節點精準觸發，誤差 < 5 秒
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(triggerGitHubAction(env));
  },

  /**
   * HTTP 入口（供手動測試用）
   * curl https://<your-worker>.workers.dev/trigger
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/trigger") {
      // 手動觸發，回傳結果
      const result = await triggerGitHubAction(env);
      return new Response(JSON.stringify(result, null, 2), {
        headers: { "Content-Type": "application/json" },
        status: result.success ? 200 : 500,
      });
    }

    if (url.pathname === "/health") {
      return new Response(
        JSON.stringify({
          status: "ok",
          time_utc: new Date().toISOString(),
          time_tst: new Date(Date.now() + 8 * 3600 * 1000).toISOString().replace("Z", "+08:00"),
          next_trigger: "Daily 01:30 UTC (09:30 TST)",
          schedule: "30 1 * * 1-6 (Mon-Sat)",
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    return new Response("LINE Investment Bot — Cloudflare Trigger\n/health  /trigger", {
      status: 200,
    });
  },
};

/**
 * 呼叫 GitHub Actions workflow_dispatch API
 * 觸發 line-investment-bot.yml
 */
async function triggerGitHubAction(env) {
  const repo   = env.GH_REPO;   // "your-username/your-repo"
  const token  = env.GH_PAT;    // GitHub Personal Access Token
  const branch = env.GH_BRANCH ?? "main";
  const workflow = env.GH_WORKFLOW ?? "line-investment-bot.yml";

  if (!repo || !token) {
    console.error("[ERROR] GH_REPO or GH_PAT not set");
    return { success: false, error: "Missing GH_REPO or GH_PAT secret" };
  }

  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  const now_tst = new Date(Date.now() + 8 * 3600 * 1000).toISOString().replace("Z", "+08:00");

  const body = {
    ref: branch,
    inputs: {
      triggered_at: now_tst,
      // Python 端可從 inputs 讀取觸發時間，方便 log 追蹤
    },
  };

  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Accept":        "application/vnd.github+json",
        "Content-Type":  "application/json",
        "User-Agent":    "LINE-Investment-Bot-Cloudflare-Trigger/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.error("[ERROR] fetch failed:", err.message);
    return { success: false, error: err.message };
  }

  // GitHub workflow_dispatch 成功回傳 204 No Content
  if (response.status === 204) {
    console.log(`[OK] Triggered ${workflow} at ${now_tst}`);
    return { success: true, triggered_at: now_tst, workflow, repo };
  }

  const text = await response.text();
  console.error(`[ERROR] GitHub API ${response.status}: ${text}`);
  return { success: false, status: response.status, body: text };
}
