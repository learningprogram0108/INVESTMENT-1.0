// 夜盤 Worker：22:00 TST（UTC 14:00）週一～週五
// wrangler.toml crons = ["0 14 * * 1-5"]
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(trigger(env, "evening"));
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/trigger") {
      const r = await trigger(env, "evening");
      return new Response(JSON.stringify(r), {headers:{"Content-Type":"application/json"}});
    }
    return new Response("Evening Worker /trigger");
  }
};

async function trigger(env, session) {
  const now = new Date(Date.now() + 8*3600*1000).toISOString().replace("Z","+08:00");
  const resp = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/line-investment-bot.yml/dispatches`,
    {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GH_PAT}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "LINE-Bot-CF/2.0",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({
        ref: env.GH_BRANCH ?? "main",
        inputs: { session, triggered_at: now }
      })
    }
  );
  return resp.status === 204
    ? {success:true, session, triggered_at:now}
    : {success:false, status:resp.status, body:await resp.text()};
}
